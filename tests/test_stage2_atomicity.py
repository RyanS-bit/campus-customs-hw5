"""Stage 2, group T: a record and its audit.db event commit together or not at all.

Failures are injected with ops._fault_hook (test-only) or by crashing a subprocess mid-transaction.
"""

import json
import os
import stat
import subprocess
import sys
import time
import uuid
from contextlib import closing

import pytest

import audit_db
import maintenance
import ops
import paths
from conftest import REAL_ROOT, counts, privileged

PYTHON = sys.executable
PAY = dict(kind="invoice", ref_id=501, amount=840, ticket_id=101, rationale="Pay overdue invoice 501")


def test_t1_record_and_event_commit_together(work, ident):
    who = ident("accounting")
    result = ops.propose_payment(work, who, call_id="call-1", **PAY)
    action_id, event_id = result["links"]["action_id"], result["links"]["audit_db_event_id"]
    assert counts(work) == {"pending_actions": 1, "drafts": 0, "events": 1}
    with privileged(work) as con:
        row = con.execute("SELECT proposed_by, run_id, call_id FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
        ev = con.execute("SELECT actor, actor_kind, run_id, run_mode, call_id, event_type, ref_table, ref_id, payload "
                         "FROM audit.events WHERE id = ?", (event_id,)).fetchone()
    assert row == ("accounting", who.run_id, "call-1")
    assert ev[:8] == ("accounting", "agent", who.run_id, "simulated", "call-1", "proposal_created", "pending_actions", action_id)
    assert json.loads(ev[8])["id"] == action_id
    assert audit_db.verify_chain(work.audit_db) == {"events": 1, "valid": True, "problems": []}


@pytest.mark.parametrize("stage", ["after_row", "after_audit"])
def test_t2_failure_between_writes_persists_nothing(work, ident, monkeypatch, stage):
    def boom(s):
        if s == stage:
            raise RuntimeError(f"injected failure at {s}")
    monkeypatch.setattr(ops, "_fault_hook", boom)
    with pytest.raises(RuntimeError, match="injected"):
        ops.propose_payment(work, ident("accounting"), **PAY)
    assert counts(work) == {"pending_actions": 0, "drafts": 0, "events": 0}
    monkeypatch.setattr(ops, "_fault_hook", None)
    ops.propose_payment(work, ident("accounting"), **PAY)          # system still usable afterwards
    assert counts(work) == {"pending_actions": 1, "drafts": 0, "events": 1}


CRASH = r'''
import os, sys, uuid
sys.path.insert(0, sys.argv[1])
import ops, paths
from pathlib import Path
p = paths.resolve(env={}, root=Path(sys.argv[2]), use_dotenv=False)
ops._fault_hook = lambda s: os._exit(1) if s == sys.argv[3] else None
ops.propose_payment(p, ops.RunIdentity(str(uuid.uuid4()), "accounting", "simulated"),
                    kind="invoice", ref_id=501, amount=840, rationale="crash test proposal")
'''


@pytest.mark.parametrize("stage", ["after_row", "after_audit"])
def test_t3_process_crash_mid_transaction_leaves_neither_record(work, ident, stage):
    proc = subprocess.run([PYTHON, "-c", CRASH, str(REAL_ROOT / "mcp_server"), str(work.root), stage])
    assert proc.returncode == 1
    journals = [f.name for f in work.active_dir.iterdir() if "journal" in f.name]
    assert journals, "precondition: the crash must leave a hot journal"
    # Recovery happens when the databases are next opened.
    assert counts(work) == {"pending_actions": 0, "drafts": 0, "events": 0}
    assert audit_db.verify_chain(work.audit_db)["valid"]
    ops.propose_payment(work, ident("accounting"), **PAY)
    assert counts(work) == {"pending_actions": 1, "drafts": 0, "events": 1}
    assert audit_db.verify_chain(work.audit_db) == {"events": 1, "valid": True, "problems": []}


def test_t4_audit_write_failure_rolls_back_the_record(work, ident):
    with privileged(work) as con:
        con.execute("CREATE TRIGGER audit.simulated_failure BEFORE INSERT ON events "
                    "BEGIN SELECT RAISE(ABORT, 'simulated audit failure'); END")
    with pytest.raises(ops.OpsError) as err:
        ops.propose_payment(work, ident("accounting"), **PAY)
    assert err.value.code == "AUDIT_FAILED"
    assert counts(work) == {"pending_actions": 0, "drafts": 0, "events": 0}


def test_t4_unwritable_audit_file_stops_the_operation(work, ident):
    audit_db.ensure(work.audit_db)
    os.chmod(work.audit_db, stat.S_IREAD)
    try:
        with pytest.raises(ops.OpsError) as err:
            ops.propose_payment(work, ident("accounting"), **PAY)
        assert err.value.code in ("AUDIT_FAILED", "DB_UNAVAILABLE")
    finally:
        os.chmod(work.audit_db, stat.S_IREAD | stat.S_IWRITE)
    assert counts(work) == {"pending_actions": 0, "drafts": 0, "events": 0}


def test_t5_wal_mode_held_by_another_connection_is_refused(work, ident):
    import sqlite3
    holder = sqlite3.connect(work.working_db, isolation_level=None)
    try:
        assert holder.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        holder.execute("BEGIN")
        holder.execute("SELECT COUNT(*) FROM tickets").fetchone()     # keeps the WAL in use
        with pytest.raises(ops.OpsError) as err:
            ops.propose_payment(work, ident("accounting"), **PAY)
        assert err.value.code == "DB_UNAVAILABLE"
    finally:
        holder.execute("ROLLBACK")
        holder.execute("PRAGMA journal_mode = DELETE")
        holder.close()
    assert counts(work)["pending_actions"] == 0


def test_t6_missing_working_database_is_reported(sandbox, ident):
    p = paths.resolve(env={}, root=sandbox, use_dotenv=False)      # never reset: no working copy
    with pytest.raises(ops.OpsError) as err:
        ops.propose_payment(p, ident("accounting"), **PAY)
    assert err.value.code == "DB_UNAVAILABLE"
    assert not p.working_db.exists()


def test_t7_tampered_audit_event_is_detected(work, ident):
    ops.propose_payment(work, ident("accounting"), **PAY)
    ops.propose_payment(work, ident("accounting"), kind="lease", ref_id=1, amount=2400, rationale="Rent due 2026-09-02")
    with privileged(work) as con:   # simulate someone bypassing the application: drop the guard, edit, restore
        con.execute("DROP TRIGGER audit.events_no_update")
        con.execute("UPDATE audit.events SET payload = '{\"forged\": true}' WHERE id = 1")
    report = audit_db.verify_chain(work.audit_db)
    assert not report["valid"] and any("event 1" in p for p in report["problems"])


# ---- concurrency ------------------------------------------------------------------

WORKER = r'''
import json, os, sys, time, uuid
sys.path.insert(0, sys.argv[1])
import ops, paths
from pathlib import Path
p = paths.resolve(env={}, root=Path(sys.argv[2]), use_dotenv=False)
go, task, n = Path(sys.argv[3]), sys.argv[4], int(sys.argv[5])
while not go.exists():
    time.sleep(0.005)
try:
    if task == "same_payment":
        r = ops.propose_payment(p, ops.RunIdentity(str(uuid.uuid4()), "accounting", "simulated"),
                                kind="invoice", ref_id=501, amount=840, rationale="concurrent duplicate attempt")
    else:
        r = ops.draft_message(p, ops.RunIdentity(str(uuid.uuid4()), "customer_service", "simulated"),
                              ticket_id=103, subject=f"Draft {n}", body=f"Concurrent draft number {n} for review.")
    print(json.dumps({"ok": True, "links": r["links"]}))
except ops.OpsError as e:
    print(json.dumps({"ok": False, "code": e.code}))
'''


def _race(work, tmp_path, task, n):
    go = tmp_path / f"go_{task}"
    procs = [subprocess.Popen([PYTHON, "-c", WORKER, str(REAL_ROOT / "mcp_server"), str(work.root), str(go), task, str(i)],
                              stdout=subprocess.PIPE, text=True) for i in range(n)]
    time.sleep(1.0)        # let every worker reach the barrier
    go.touch()
    return [json.loads(p.communicate(timeout=60)[0]) for p in procs]


def test_k1_concurrent_identical_payments_create_exactly_one(work, tmp_path):
    results = _race(work, tmp_path, "same_payment", 6)
    assert sum(r["ok"] for r in results) == 1
    assert sorted(r.get("code") for r in results if not r["ok"]) == ["DUPLICATE"] * 5
    assert counts(work) == {"pending_actions": 1, "drafts": 0, "events": 1}
    assert audit_db.verify_chain(work.audit_db)["valid"]


def test_k2_concurrent_distinct_drafts_all_commit_with_an_unforked_chain(work, tmp_path):
    results = _race(work, tmp_path, "draft", 8)
    assert all(r["ok"] for r in results), results
    assert counts(work) == {"pending_actions": 0, "drafts": 8, "events": 8}
    assert audit_db.verify_chain(work.audit_db) == {"events": 8, "valid": True, "problems": []}
