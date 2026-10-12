"""Stage 4, group G: the JSON trail together with real MCP calls, audit.db, reconciliation,
the run lock, and the reset/snapshot scripts (temporary databases and files only)."""

import asyncio
import json
import re
import subprocess
import sys
import uuid

import pytest
from fastmcp import Client

import audit_reconcile
import audit_trail
import locks
import maintenance
import paths
import server
from conftest import REAL_ROOT, counts, privileged, make_writable
from mcp_helpers import text, tool_names

PYTHON = sys.executable
PAY = dict(kind="invoice", ref_id=501, amount=840, ticket_id=101, rationale="Pay overdue invoice 501")


def invoker(srv, tool, args):
    async def invoke(call_id):
        async with Client(srv) as c:
            r = await c.call_tool(tool, args, raise_on_error=False, meta={"call_id": call_id})
            return (r.is_error, text(r) if r.is_error else r.structured_content)
    return invoke


@pytest.fixture
def setup(work, ident):
    trail = audit_trail.AuditTrail(work.audit_trail_json)
    who = ident("accounting")
    rec = audit_trail.RunRecorder(trail, who.run_id, "simulated", business_date="2026-08-31")
    return work, trail, rec, ident


def run_call(rec, srv, agent, tool, args, chain=None):
    return asyncio.run(rec.tool_call(agent=agent, chain=chain or ["boss", agent], tool=tool, args=args,
                                     invoke=invoker(srv, tool, args)))


# ---- recording real MCP calls ----------------------------------------------------------

def test_g1_successful_write_is_linked_across_json_auditdb_and_row(setup):
    work, trail, rec, ident = setup
    srv = server.create_server(ident("accounting"), work)
    rec.run_started(model="gpt-6-luna", details={"note": "offline test"})
    is_error, payload = run_call(rec, srv, "accounting", "propose_payment", PAY)
    assert not is_error
    started, finished = trail.read()[1:3]
    assert (started["event_type"], finished["event_type"]) == ("tool_call_started", "tool_call_finished")
    assert started["call_id"] == finished["call_id"] and finished["outcome"] == {"status": "ok"}
    assert finished["links"] == payload["links"] and finished["chain"] == ["boss", "accounting"]
    with privileged(work) as con:
        row = con.execute("SELECT call_id, run_id, proposed_by FROM pending_actions WHERE id = ?",
                          (payload["links"]["action_id"],)).fetchone()
        db_ev = con.execute("SELECT call_id FROM audit.events WHERE id = ?", (payload["links"]["audit_db_event_id"],)).fetchone()
    assert row == (finished["call_id"], rec.run_id, "accounting") and db_ev == (finished["call_id"],)
    assert audit_reconcile.check(work, trail)["consistent"]


@pytest.mark.parametrize("agent,tool,args,code", [
    ("customer_service", "draft_message", dict(ticket_id=102, subject="Rent", body="Wrong agent for the landlord."), "NOT_PERMITTED"),
    ("accounting", "propose_payment", {**PAY, "amount": 800}, "REFUSED_RULE"),
])
def test_g2_denials_are_recorded_with_their_codes(setup, agent, tool, args, code):
    work, trail, rec, ident = setup
    srv = server.create_server(ident(agent), work)
    is_error, _ = run_call(rec, srv, agent, tool, args)
    finished = trail.read()[-1]
    assert is_error and finished["outcome"]["status"] == "refused" and finished["outcome"]["code"] == code
    assert counts(work) == {"pending_actions": 0, "drafts": 0, "events": 0}


def test_g2_development_mode_refusal_is_recorded(setup):
    work, trail, rec, _ = setup
    run_call(rec, server.create_server(None, work), "accounting", "propose_payment", PAY)
    assert trail.read()[-1]["outcome"]["code"] == "NO_RUN_CONTEXT"


def test_g3_recorded_actor_comes_from_the_trusted_launch_not_from_arguments(setup):
    work, trail, rec, ident = setup
    srv = server.create_server(ident("customer_service"), work)
    forged = {"ticket_id": 103, "sku": "CC-HOOD-NAVY", "size": "M", "qty": 20, "unit_price": 45,
              "rationale": "Bulk discount within policy", "agent": "accounting", "run_id": str(uuid.uuid4())}
    is_error, _ = run_call(rec, srv, "customer_service", "propose_quote", forged)
    assert is_error                                                   # rejected by the tool schema
    events = trail.read()
    assert {e["actor"] for e in events} == {"customer_service"} and {e["run_id"] for e in events} == {rec.run_id}
    assert counts(work)["pending_actions"] == 0


# ---- fail-closed and partial operations ------------------------------------------------

def test_g4_if_the_started_event_cannot_be_saved_the_tool_is_not_called(setup, monkeypatch):
    work, trail, rec, ident = setup
    called = []
    real_append = trail.append_with

    def failing(build):
        if any(e["event_type"] == "tool_call_started" for e in build([])):
            raise audit_trail.AuditTrailError("simulated JSON failure")
        return real_append(build)
    monkeypatch.setattr(trail, "append_with", failing)

    async def invoke(call_id):
        called.append(call_id)
        return False, {}
    with pytest.raises(audit_trail.AuditTrailError):
        asyncio.run(rec.tool_call(agent="accounting", chain=["accounting"], tool="propose_payment", args=PAY, invoke=invoke))
    assert called == [] and counts(work)["pending_actions"] == 0


def _fail_finished(trail, monkeypatch):
    real_append = trail.append_with

    def failing(build):
        if any(e["event_type"] == "tool_call_finished" for e in build([])):
            raise audit_trail.AuditTrailError("simulated JSON failure after the database committed")
        return real_append(build)
    monkeypatch.setattr(trail, "append_with", failing)


def test_g5_committed_change_without_completion_event_is_detected_and_reconciled_once(setup, monkeypatch):
    work, trail, rec, ident = setup
    srv = server.create_server(ident("accounting"), work)
    _fail_finished(trail, monkeypatch)
    with pytest.raises(audit_trail.AuditTrailError):
        run_call(rec, srv, "accounting", "propose_payment", PAY)
    monkeypatch.undo()
    assert counts(work)["pending_actions"] == 1                       # the database change DID commit
    report = audit_reconcile.check(work, trail)
    assert not report["consistent"] and len(report["incomplete"]) == 1
    gap = report["incomplete"][0]
    assert gap["db_outcome"] == "committed" and gap["ref_table"] == "pending_actions" and gap["ref_id"] == 1
    history = trail.read()
    added = audit_reconcile.reconcile(work, trail)
    assert len(added) == 1 and added[0]["outcome"] == {"status": "incomplete", "db_outcome": "committed"}
    assert "rolled back" not in json.dumps(added[0]).replace("does not claim a rollback", "")
    assert trail.read()[: len(history)] == history                    # original history untouched
    assert audit_reconcile.reconcile(work, trail) == []               # never recorded twice
    assert audit_reconcile.check(work, trail)["consistent"]


def test_g6_crash_before_commit_is_reported_as_not_found_never_as_rolled_back(setup):
    work, trail, rec, _ = setup
    rec.record("tool_call_started", "accounting", chain=["accounting"], tool="propose_payment", args=PAY,
               call_id=str(uuid.uuid4()))                               # process then died before the database write
    added = audit_reconcile.reconcile(work, trail)
    assert added[0]["outcome"]["db_outcome"] == "not_found"
    assert "no matching change was found" in added[0]["details"]["reason"]
    assert counts(work)["pending_actions"] == 0


def test_g7_unreadable_audit_db_is_indeterminate(setup):
    work, trail, rec, _ = setup
    work.audit_db.write_bytes(b"this is not a sqlite database at all" * 10)
    rec.record("tool_call_started", "accounting", chain=["accounting"], tool="propose_payment", args=PAY,
               call_id=str(uuid.uuid4()))
    assert audit_reconcile.reconcile(work, trail)[0]["outcome"]["db_outcome"] == "indeterminate"


def test_g8_incomplete_read_tool_is_explained(setup):
    work, trail, rec, _ = setup
    rec.record("tool_call_started", "boss", chain=["boss"], tool="get_open_tickets", args={}, call_id=str(uuid.uuid4()))
    added = audit_reconcile.reconcile(work, trail)
    assert added[0]["details"]["db_outcome"] == "not_found" and "read-only tool" in added[0]["details"]["reason"]


def test_g9_mismatched_cross_reference_is_reported(setup):
    work, trail, rec, ident = setup
    run_call(rec, server.create_server(ident("accounting"), work), "accounting", "propose_payment", PAY)
    cid = str(uuid.uuid4())
    rec.record("tool_call_started", "accounting", chain=["accounting"], tool="propose_payment", args=PAY, call_id=cid)
    rec.record("tool_call_finished", "accounting", chain=["accounting"], tool="propose_payment", call_id=cid,
               outcome={"status": "ok"}, links={"action_id": 1, "audit_db_event_id": 1})   # wrongly claims event 1
    report = audit_reconcile.check(work, trail)
    assert not report["consistent"] and any("does not match" in p for p in report["cross_reference_problems"])


# ---- run lock, reset and snapshot ------------------------------------------------------

def test_g10_reset_and_snapshot_refuse_while_a_run_is_active(sandbox, runtime_dir):
    p = paths.resolve(env={"CC_RUNTIME_DIR": str(runtime_dir)}, root=sandbox, use_dotenv=False)
    maintenance.reset_working_db(p)
    trail = audit_trail.AuditTrail(p.audit_trail_json)
    with audit_trail.RunRecorder(trail, str(uuid.uuid4()), "simulated", active_dir=p.active_dir):
        assert locks.run_active(p.active_dir)
        with pytest.raises(maintenance.ResetError, match="run is active"):
            maintenance.reset_working_db(p)
        with pytest.raises(maintenance.SnapshotError, match="run is active"):
            maintenance.snapshot_runtime(p)
    assert not locks.run_active(p.active_dir)
    maintenance.snapshot_runtime(p)
    maintenance.reset_working_db(p)


def _cli(script, root, *extra):
    return subprocess.run([PYTHON, str(REAL_ROOT / "scripts" / script), "--root", str(root), *extra],
                          capture_output=True, text=True, timeout=120,
                          env={"SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""), "PATH": ""})


def test_g11_reset_records_a_system_event_and_never_erases_history(sandbox):
    trail = audit_trail.AuditTrail(sandbox / "output" / "audit_trail.json")
    trail.append([audit_trail.make_event("agent_started", actor="boss", actor_kind="agent", mode="simulated",
                                         run_id=str(uuid.uuid4()))])
    history = trail.read()
    assert _cli("reset_db.py", sandbox, "--confirm").returncode == 0
    assert _cli("reset_db.py", sandbox, "--confirm").returncode == 0
    events = trail.read()
    assert events[:1] == history and [e["event_type"] for e in events[1:]] == ["db_reset", "db_reset"]
    assert all(e["actor_kind"] == "system" and e["outcome"]["status"] == "ok" for e in events[1:])
    assert events[1]["details"]["original_sha256"] == paths.ORIGINAL_SHA256
    assert _cli("reset_db.py", sandbox).returncode == 2 and len(trail.read()) == 3     # dry run records nothing


def test_g11_failed_reset_and_refused_snapshot_are_recorded_as_failures(sandbox):
    original = sandbox / "data" / "campus_customs.db"
    make_writable(original)
    with open(original, "ab") as f:
        f.write(b"tamper")
    assert _cli("reset_db.py", sandbox, "--confirm").returncode == 1
    assert _cli("snapshot_runtime.py", sandbox).returncode == 1
    events = audit_trail.AuditTrail(sandbox / "output" / "audit_trail.json").read()
    assert [(e["event_type"], e["outcome"]["status"]) for e in events] == [("db_reset", "error"), ("db_snapshot", "error")]


def test_g11_reset_reports_when_its_event_cannot_be_recorded(sandbox):
    out = sandbox / "output" / "audit_trail.json"
    out.write_text('{"not": "an array"}', encoding="utf-8")            # damaged trail: writer must refuse
    proc = _cli("reset_db.py", sandbox, "--confirm")
    assert proc.returncode == 3 and "RESET COMPLETED, BUT ITS AUDIT EVENT WAS NOT RECORDED" in proc.stderr
    assert out.read_text(encoding="utf-8") == '{"not": "an array"}'


def test_g12_verify_cli(sandbox):
    assert _cli("reset_db.py", sandbox, "--confirm").returncode == 0
    check = _cli("verify_audit.py", sandbox)
    assert check.returncode == 0 and json.loads(check.stdout)["consistent"] is True
    assert _cli("verify_audit.py", sandbox, "reconcile").returncode == 2           # needs --confirm
    assert _cli("verify_audit.py", sandbox, "init", "--confirm").returncode == 0   # file exists: no-op
    assert len(audit_trail.AuditTrail(sandbox / "output" / "audit_trail.json").read()) == 1


def test_g12_init_creates_the_trail_once(sandbox):
    assert _cli("verify_audit.py", sandbox, "init", "--confirm").returncode == 0
    assert _cli("verify_audit.py", sandbox, "init", "--confirm").returncode == 0
    events = audit_trail.AuditTrail(sandbox / "output" / "audit_trail.json").read()
    assert [e["event_type"] for e in events] == ["trail_initialized"]


# ---- nothing audit-related is exposed to agents -----------------------------------------

def test_g13_no_audit_capability_is_an_mcp_tool(work, ident):
    names = tool_names(server.create_server(None, work))
    assert names == set(server.READ_TOOLS + server.WRITE_TOOLS)
    assert not any(w in n for n in names for w in ("audit", "reset", "reconcile", "init", "delete", "repair", "snapshot"))
    src = (REAL_ROOT / "mcp_server" / "server.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import (audit_trail|audit_reconcile|maintenance|locks)\b", src, re.MULTILINE)
