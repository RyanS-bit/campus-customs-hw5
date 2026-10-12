"""Stage 3: protections for the test-only failure switch, the privileged audit initializer,
and static checks that agent-facing code has no modification path.
"""

import re
import runpy
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest

import audit_db
import ops
import server
from conftest import REAL_ROOT, counts, privileged
from mcp_helpers import call, tool_schemas

CODE_DIRS = [REAL_ROOT / "mcp_server", REAL_ROOT / "scripts", REAL_ROOT / "backend"]
PAY = dict(kind="invoice", ref_id=501, amount=840, ticket_id=101, rationale="Pay overdue invoice 501")


def sources():
    return {f: f.read_text(encoding="utf-8") for d in CODE_DIRS if d.exists() for f in d.rglob("*.py")}


# ---- F: test-only failure switch ------------------------------------------------------

def test_f1_only_the_definition_and_the_offline_evidence_script_touch_the_switch():
    users = {f.relative_to(REAL_ROOT).as_posix() for f, src in sources().items() if "_fault_hook" in src}
    assert users == {"mcp_server/ops.py", "scripts/stage2_evidence.py", "mcp_server/server.py"}
    assert re.findall(r"_fault_hook\s*=", (REAL_ROOT / "mcp_server" / "ops.py").read_text()) == ["_fault_hook ="]
    # server.py only READS it, to refuse to start if it were ever set.
    assert not re.search(r"_fault_hook\s*=(?!=)", (REAL_ROOT / "mcp_server" / "server.py").read_text())


def test_f2_switch_is_off_after_importing_the_server_and_unreachable_through_tools(work, ident):
    assert ops._fault_hook is None
    for name, schema in tool_schemas(server.create_server(None, work)).items():
        params = set(schema.get("properties", {}))
        assert not any("fault" in p or "hook" in p or p.startswith("_") for p in params), (name, params)
    srv = server.create_server(ident("accounting"), work)
    r = call(srv, "propose_payment", {**PAY, "_fault_hook": "after_row"}, meta={"_fault_hook": "after_row"})
    assert r.is_error and ops._fault_hook is None
    assert not call(srv, "propose_payment", PAY, meta={"_fault_hook": "after_row", "fault": "after_audit"}).is_error
    assert ops._fault_hook is None and counts(work)["pending_actions"] == 1


def test_f3_live_run_refuses_to_write_while_the_switch_is_set(work, ident, monkeypatch):
    calls = []
    monkeypatch.setattr(ops, "_fault_hook", lambda stage: calls.append(stage))
    with pytest.raises(ops.OpsError) as err:
        ops.propose_payment(work, ident("accounting", mode="live"), **PAY)
    assert err.value.code == "DB_UNAVAILABLE" and "fault injection is active" in err.value.message
    assert calls == [] and counts(work)["pending_actions"] == 0
    ops.propose_payment(work, ident("accounting", mode="simulated"), **PAY)     # tests (simulated) still work
    assert calls == ["after_row", "after_audit"]


def test_f4_server_refuses_to_start_if_the_switch_was_set_in_its_process(tmp_path):
    code = (f"import sys; sys.path.insert(0, {str(REAL_ROOT / 'mcp_server')!r}); import ops, runpy; "
            "ops._fault_hook = lambda s: None; "
            f"runpy.run_path({str(REAL_ROOT / 'mcp_server' / 'server.py')!r}, run_name='__main__')")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL, env={"SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
                                                         "CC_RUNTIME_DIR": str(tmp_path / "rt")})
    assert proc.returncode == 2 and "fault injection is set" in proc.stderr


# ---- AI: privileged audit initializer --------------------------------------------------

def test_ai1_schema_file_contains_only_additive_fixed_statements():
    sql = (REAL_ROOT / "mcp_server" / "audit_schema.sql").read_text(encoding="utf-8")
    body = re.sub(r"--[^\n]*", "", sql)
    assert not re.search(r"\bDROP\b|\bDELETE\s+FROM\b|\bUPDATE\s+\w+\s+SET\b|\bREPLACE\b|\bALTER\b|\bVACUUM\b|\bATTACH\b",
                         body, re.IGNORECASE)
    creates = re.findall(r"CREATE\s+(TABLE|TRIGGER)\s+(IF NOT EXISTS\s+)?", body, re.IGNORECASE)
    assert creates and all(c[1] for c in creates)
    assert re.findall(r"INSERT INTO \w+\s+SELECT .*? WHERE NOT EXISTS", body, re.IGNORECASE | re.DOTALL)


def test_ai2_initializer_restores_missing_objects_without_touching_history(work, ident):
    ops.propose_payment(work, ident("accounting"), **PAY)
    ops.propose_payment(work, ident("accounting"), kind="lease", ref_id=1, amount=2400, rationale="Rent due 2026-09-02")
    with privileged(work) as con:
        before = con.execute("SELECT * FROM audit.events ORDER BY id").fetchall()
        con.execute("DROP TRIGGER audit.events_no_delete")        # simulate a damaged schema
    audit_db.ensure(work.audit_db)
    with privileged(work) as con:
        assert con.execute("SELECT * FROM audit.events ORDER BY id").fetchall() == before
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("DELETE FROM audit.events")
    assert audit_db.verify_chain(work.audit_db) == {"events": 2, "valid": True, "problems": []}


def test_ai3_initializer_is_a_no_op_on_a_complete_schema(work, ident):
    ops.propose_payment(work, ident("accounting"), **PAY)
    before = work.audit_db.read_bytes()
    audit_db.ensure(work.audit_db)
    assert work.audit_db.read_bytes() == before


def test_ai4_initializer_refuses_other_paths(work):
    before = work.working_db.read_bytes()
    for target in (work.working_db, work.original_db, work.active_dir / "other.db"):
        with pytest.raises(ValueError, match="audit.db"):
            audit_db.ensure(target)
    assert work.working_db.read_bytes() == before and not (work.active_dir / "other.db").exists()


def test_ai5_agent_facing_code_calls_the_initializer_only_with_the_resolved_audit_path():
    for f, src in sources().items():
        for arg in re.findall(r"audit_db\.ensure\(([^)]*)\)", src):
            assert arg.strip().endswith(".audit_db"), (f.name, arg)


# ---- S: static checks on agent-facing code ---------------------------------------------

def test_s1_agent_facing_code_contains_no_update_or_delete_statements():
    for name in ("ops.py", "server.py", "authz.py", "audit_db.py"):
        src = (REAL_ROOT / "mcp_server" / name).read_text(encoding="utf-8")
        assert not re.search(r"\bUPDATE\s+\w+(\.\w+)?\s+SET\b|\bDELETE\s+FROM\b", src, re.IGNORECASE), name


HUMAN_PATH = {"mcp_server/approval.py", "backend/main.py"}          # Problem 7: the only human decision path
AGENT_FACING = ("mcp_server/server.py", "mcp_server/ops.py", "mcp_server/authz.py", "mcp_server/audit_db.py",
                "backend/agents.py", "backend/delegation.py", "backend/runner.py", "backend/models.py", "backend/config.py")


def test_s2_approval_and_execution_code_exists_only_in_the_human_path():
    # Problem 5 asserted that no such code existed yet; Problem 7 adds it, in approval.py and its HTTP routes only.
    for f, src in sources().items():
        if f.relative_to(REAL_ROOT).as_posix() not in HUMAN_PATH:
            assert not re.search(r"def\s+(approve|reject|execute)\w*\s*\(", src), f.name


def test_s2b_agent_facing_code_never_imports_the_human_path():
    # Architecture section 13, layer 2: agent code cannot even import approval, the API or its read helpers.
    for rel in AGENT_FACING:
        src = (REAL_ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|from)\s+(approval|main|desk_reads)\b", src, re.MULTILINE), rel
