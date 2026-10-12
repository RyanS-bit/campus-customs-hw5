"""Regression tests for the remediation of audit findings M1–M5 and minor fixes (output/p5_remediation_report.md).
Each test is written to FAIL if the original defect is reintroduced. Temporary databases and files only."""

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import uuid
from contextlib import closing

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

import audit_reconcile
import audit_trail
import config
import locks
import maintenance
import models
import ops
import paths
import runner
import server
from agent_script import AGENTS, Scripted
from conftest import REAL_ROOT, counts, privileged

PAY = {"kind": "invoice", "ref_id": 501, "amount": 840, "ticket_id": 101, "rationale": "Pay overdue invoice 501"}
SERVER_PY = str(REAL_ROOT / "mcp_server" / "server.py")


def sim(work, script_or_models, entry="boss", budget=None, launcher=runner.in_memory_launcher, **kw):
    m = script_or_models.models() if isinstance(script_or_models, Scripted) else script_or_models
    return asyncio.run(runner.run_task("task", p=work, model=m, mode="simulated", entry=entry, budget=budget,
                                       launcher=launcher, **kw))


def events(work):
    return audit_trail.AuditTrail(work.audit_trail_json).read()


# ---- M1: the agent servers use exactly the database the runner selected -----------------------

def test_r1_stdio_agents_on_an_isolated_default_layout_root_never_touch_the_real_database(work):
    """Before M1 this exact call wrote the proposal to the REAL data/campus_customs_new.db."""
    real_working, real_audit = REAL_ROOT / "data" / "campus_customs_new.db", REAL_ROOT / "data" / "audit.db"
    before = (paths.sha256_file(real_working), real_audit.exists())
    s = Scripted({"boss": [[("delegate", "accounting", "Propose 501")]], "accounting": [[("tool", "propose_payment", PAY)]]})
    out = sim(work, s, launcher=runner.stdio_launcher)                  # real subprocess servers
    assert out.status == "completed" and work.mode == "default"
    assert counts(work) == {"pending_actions": 1, "drafts": 0, "events": 1}   # landed in the ISOLATED copy
    assert (paths.sha256_file(real_working), real_audit.exists()) == before


GOOD_ID = {"CC_RUN_ID": str(uuid.uuid4()), "CC_AGENT": "accounting", "CC_RUN_MODE": "simulated"}


def _start(env):
    clean = {k: v for k, v in os.environ.items() if not k.startswith("CC_")}
    clean.update(env)
    return subprocess.run([sys.executable, SERVER_PY], env=clean, capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL)


@pytest.mark.parametrize("paths_env", [
    {},                                                                    # no launch paths at all
    {"CC_HW5_ROOT": "{root}"},                                             # working db not stated
    {"CC_HW5_ROOT": "{root}", "CC_WORKING_DB": "{elsewhere}"},             # mismatch
])
def test_r2_agent_server_refuses_to_start_without_exact_launch_paths(work, tmp_path, paths_env):
    env = {k: v.format(root=work.root, elsewhere=tmp_path / "other.db") for k, v in paths_env.items()}
    proc = _start({**GOOD_ID, **env})
    assert proc.returncode == 2 and "REFUSING TO START" in proc.stderr


def test_r3_launch_paths_ignore_hw5_env_and_defaults(sandbox, tmp_path):
    (sandbox / ".env").write_text(f"CC_RUNTIME_DIR={tmp_path / 'hijack'}\n")
    expected = sandbox.resolve() / "data" / "campus_customs_new.db"
    p = server.launch_paths_from_env({"CC_HW5_ROOT": str(sandbox), "CC_RUNTIME_DIR": "", "CC_WORKING_DB": str(expected)})
    assert p.working_db == expected and p.mode == "default"


def test_r4_agent_server_without_explicit_paths_is_refused(ident):
    with pytest.raises(ValueError, match="explicit paths"):
        server.create_server(ident("accounting"))


# ---- M2: simulated mode can never call a real model --------------------------------------------

class _NotAModel:
    pass


@pytest.mark.parametrize("bad", ["openai:gpt-4o", "gpt-6-luna", _NotAModel(),
                                 config.build_model(config.ModelConfig("gpt-6-luna", "http://127.0.0.1:9/v1", "dummy-offline-key"))])
def test_r5_simulated_mode_accepts_only_offline_test_models(work, bad):
    with pytest.raises(ValueError, match="offline test models"):
        sim(work, {a: bad for a in AGENTS})
    assert not work.audit_trail_json.exists()


def test_r5_testmodel_is_accepted(work):
    out = sim(work, {a: TestModel(call_tools=[]) for a in AGENTS})
    assert out.status == "completed"


def test_r6_offline_guard_blocks_network_access_during_simulated_runs(work):
    def phone_home(messages, info):
        socket.getaddrinfo("api.portkey.ai", 443)                      # what a real client would do first
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"summary": "should not get here"})])
    models_ = {a: FunctionModel(phone_home) for a in AGENTS}
    out = sim(work, models_)
    assert out.status == "error" and "OfflineViolation" in out.error and "api.portkey.ai" in out.error
    assert socket.getaddrinfo.__name__ == "getaddrinfo"                # guard removed after the run


def test_r6_guard_blocks_raw_ip_connections_and_allows_loopback():
    with runner.offline_network_guard():
        with pytest.raises(runner.OfflineViolation):
            socket.create_connection(("203.0.113.7", 443), timeout=1)
        with pytest.raises(runner.OfflineViolation):
            s = socket.socket(); s.connect(("203.0.113.7", 443))
        assert socket.getaddrinfo("localhost", 80)                     # loopback still resolvable


# ---- M3: reconciliation never acts on an active run --------------------------------------------

def test_r7_reconcile_is_refused_while_a_run_is_active(work):
    trail = audit_trail.AuditTrail(work.audit_trail_json)
    with audit_trail.RunRecorder(trail, str(uuid.uuid4()), "simulated", active_dir=work.active_dir) as rec:
        rec.record("tool_call_started", "accounting", chain=["accounting"], tool="propose_payment", args=PAY,
                   call_id=str(uuid.uuid4()))                           # genuinely in progress
        before = trail.path.read_bytes()
        with pytest.raises(audit_reconcile.ReconcileRefused):
            audit_reconcile.reconcile(work, trail)
        assert trail.path.read_bytes() == before
        report = audit_reconcile.check(work, trail)
        assert report["run_active"] is True and report["incomplete"][0]["in_progress"] is True


def test_r8_no_run_can_start_while_reconcile_holds_the_lock(work, monkeypatch):
    seen = []
    real = audit_reconcile._reconcile_locked

    def during(p, trail):
        seen.append(locks.run_active(work.active_dir))
        with pytest.raises(locks.LockTimeout):
            with audit_trail.RunRecorder(trail, str(uuid.uuid4()), "simulated", active_dir=work.active_dir):
                pass
        return real(p, trail)
    monkeypatch.setattr(audit_reconcile, "_reconcile_locked", during)
    audit_reconcile.reconcile(work)
    assert seen == [True] and not locks.run_active(work.active_dir)


# ---- M4: no false "committed" after a reset ------------------------------------------------------

def test_r9_reconcile_after_reset_never_matches_another_runs_row(work, monkeypatch):
    trail = audit_trail.AuditTrail(work.audit_trail_json)
    real_append = trail.append_with

    def drop_finished(build):
        if any(e["event_type"] == "tool_call_finished" for e in build([])):
            raise audit_trail.AuditTrailError("simulated JSON failure")
        return real_append(build)
    monkeypatch.setattr(trail, "append_with", drop_finished)
    with pytest.raises(audit_trail.AuditTrailError):                    # run A: committed, completion event lost
        asyncio.run(runner.run_task("A", p=work, model=Scripted({"accounting": [[("tool", "propose_payment", PAY)]]}).models(),
                                    mode="simulated", entry="accounting", launcher=runner.in_memory_launcher, trail=trail))
    monkeypatch.undo()
    maintenance.reset_working_db(work)                                  # ids restart; audit.db keeps history
    sim(work, Scripted({"accounting": [[("tool", "propose_payment", PAY)]]}), entry="accounting")   # run B gets id 1
    with privileged(work) as con:
        assert con.execute("SELECT id FROM pending_actions").fetchall() == [(1,)]
    gap = audit_reconcile.check(work)["incomplete"]
    assert len(gap) == 1 and gap[0]["db_outcome"] == "committed_row_no_longer_present", gap


# ---- M5: enforceable limits ----------------------------------------------------------------------

def test_r10_total_token_limit_stops_the_run(work):
    s = Scripted({"boss": [[("delegate", "inventory", "a")]], "inventory": [[("delegate", "accounting", "b")]]})
    out = sim(work, s, budget=models.RunBudget(total_tokens_limit=300))
    assert out.status == "stopped" and "total_tokens_limit" in out.error
    assert [e for e in events(work) if e["event_type"] == "budget_exceeded"][-1]["outcome"]["code"] == "USAGE_LIMIT"


def test_r11_tool_call_limit_stops_the_run(work):
    s = Scripted({"boss": [[("tool", "get_open_tickets", {}), ("tool", "list_vendors", {}), ("tool", "get_ticket", {"ticket_id": 101})]]})
    out = sim(work, s, budget=models.RunBudget(tool_calls_limit=1))
    assert out.status == "stopped" and "tool_calls_limit" in out.error


def test_r12_wall_clock_limit_stops_the_run_cleanly(work):
    async def slow(messages, info):
        await asyncio.sleep(5)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"summary": "late"})])
    out = sim(work, {a: FunctionModel(slow) for a in AGENTS}, budget=models.RunBudget(wall_clock_seconds=0.5))
    assert out.status == "stopped" and "wall-clock" in out.error
    evs = events(work)
    assert [e for e in evs if e["event_type"] == "budget_exceeded"][-1]["outcome"]["code"] == "WALL_CLOCK"
    assert evs[-1]["event_type"] == "run_finished" and evs[-1]["outcome"]["run_status"] == "stopped"
    assert not locks.run_active(work.active_dir)


def test_r13_output_token_cap_reaches_every_agent_call(work):
    seen = {}

    def model_for(agent, plan):
        def fn(messages, info):
            seen.setdefault(agent, []).append((info.model_settings or {}).get("max_tokens"))
            turn = sum(isinstance(m, ModelResponse) for m in messages)
            if turn < len(plan):
                return ModelResponse(parts=[ToolCallPart("delegate", {"to": plan[turn], "task": "x"})])
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"summary": "ok"})])
        return FunctionModel(fn)
    m = {a: model_for(a, ["inventory"] if a == "boss" else []) for a in AGENTS}
    assert sim(work, m, budget=models.RunBudget(max_output_tokens=321)).status == "completed"
    assert set(seen) == {"boss", "inventory"} and all(v == 321 for vals in seen.values() for v in vals)


def test_r14_live_model_must_respect_the_budget_client_limits(work):
    cfg = config.ModelConfig("gpt-6-luna", "http://127.0.0.1:9/v1", "dummy-offline-key")
    budget = models.RunBudget()
    with pytest.raises(ValueError, match="exceeds the run budget"):
        runner._check_mode(config.build_model(cfg), "live", "test authorization", budget)       # default: 600 s, 2 retries
    runner._check_mode(config.build_live_model(cfg, budget), "live", "test authorization", budget)   # compliant: no error
    with pytest.raises(ValueError, match="exceeds the run budget"):
        asyncio.run(runner.run_task("x", p=work, model=config.build_model(cfg), mode="live", live_authorization="t"))
    assert not work.audit_trail_json.exists()                            # refused before anything ran


def test_r15_run_started_records_every_limit(work):
    sim(work, Scripted())
    budget = events(work)[0]["details"]["budget"]
    assert set(budget) >= {"request_limit", "total_tokens_limit", "tool_calls_limit", "max_output_tokens",
                           "max_delegations", "wall_clock_seconds", "request_timeout_seconds", "max_http_retries"}
    assert events(work)[0]["details"]["offline_network_guard"] is True


# ---- minor fixes ---------------------------------------------------------------------------------

def test_m1_root_scripts_ignore_a_hostile_runtime_setting(sandbox, tmp_path):
    hostile = tmp_path / "hostile_runtime"
    env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PATH": "", "CC_RUNTIME_DIR": str(hostile)}
    proc = subprocess.run([sys.executable, str(REAL_ROOT / "scripts" / "reset_db.py"), "--root", str(sandbox), "--confirm"],
                          capture_output=True, text=True, timeout=120, env=env)
    assert proc.returncode == 0, proc.stderr
    assert (sandbox / "data" / "campus_customs_new.db").exists() and not hostile.exists()


def test_m2_keys_loaded_from_env_files_are_guarded(tmp_path, work):
    env_file = tmp_path / "x.env"
    env_file.write_text("PORTKEY_API_KEY=file-only-secret-0123456789\n")
    config.load_config(env={}, env_files=(env_file,))
    assert "PORTKEY_API_KEY" not in os.environ or os.environ["PORTKEY_API_KEY"] != "file-only-secret-0123456789"
    with pytest.raises(audit_trail.AuditTrailError, match="credential"):
        audit_trail.AuditTrail(work.audit_trail_json).append([audit_trail.make_event(
            "agent_started", actor="boss", actor_kind="agent", mode="simulated", run_id=str(uuid.uuid4()),
            details={"leak": "file-only-secret-0123456789"})])


def test_m3_setup_failure_still_records_run_finished(work, monkeypatch):
    def broken(*a, **k):
        raise FileNotFoundError("prompt file missing")
    monkeypatch.setattr(runner, "build_team", broken)
    out = sim(work, Scripted())
    assert out.status == "error" and "prompt file missing" in out.error
    assert events(work)[-1]["event_type"] == "run_finished"


def test_m4_bad_runtime_setting_gives_a_clean_error_not_a_traceback():
    env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PATH": "", "CC_RUNTIME_DIR": "relative/not/allowed"}
    proc = subprocess.run([sys.executable, str(REAL_ROOT / "scripts" / "reset_db.py")],     # dry run; nothing written
                          capture_output=True, text=True, timeout=60, env=env)
    assert proc.returncode == 1 and "RESET FAILED" in proc.stderr and "Traceback" not in proc.stderr


def test_m6_subprocess_environment_excludes_all_credential_names(work, ident, monkeypatch):
    for name in ("SOME_API_KEY", "GITHUB_TOKEN", "DB_PASSWORD", "CLIENT_SECRET", "AZURE_CREDENTIAL"):
        monkeypatch.setenv(name, "x" * 20)
    client = runner.stdio_launcher(work)("accounting", ident("accounting"))
    env = client.transport.env
    assert not any(audit_trail.CREDENTIAL_NAME.search(k) for k in env)
    assert env["CC_WORKING_DB"] == str(work.working_db) and env["CC_AGENT"] == "accounting"


def test_m8_cancelled_tool_call_is_recorded_and_still_reconcilable(work):
    trail = audit_trail.AuditTrail(work.audit_trail_json)
    rec = audit_trail.RunRecorder(trail, str(uuid.uuid4()), "simulated")

    async def cancelled(call_id):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(rec.tool_call(agent="accounting", chain=["accounting"], tool="propose_payment", args=PAY, invoke=cancelled))
    finished = trail.read()[-1]
    assert finished["outcome"] == {"status": "incomplete", "code": "CANCELLED", "message": finished["outcome"]["message"]}
    assert len(audit_reconcile.check(work, trail)["incomplete"]) == 1      # still examined against the databases


def test_m9_schema_failure_during_reset_leaves_the_working_copy_untouched(work, monkeypatch):
    before = work.working_db.read_bytes()
    def broken(db):
        raise sqlite3.OperationalError("simulated schema failure")
    monkeypatch.setattr(maintenance, "apply_schema_ext", broken)
    with pytest.raises(maintenance.ResetError, match="Schema extension failed"):
        maintenance.reset_working_db(work)
    assert work.working_db.read_bytes() == before
    assert not list(work.active_dir.glob("*.resetting*"))


def test_m10_read_only_checks_create_no_files(work):
    lock = locks.run_lock_path(work.active_dir)
    lock.unlink(missing_ok=True)                                        # the fixture's reset legitimately created it
    assert locks.run_active(work.active_dir) is False
    audit_reconcile.check(work)
    assert not lock.exists()                                            # a check must not leave files behind
