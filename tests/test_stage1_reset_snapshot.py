"""Stage 1, group S: reset (R8) and runtime snapshot, observed on real files in a sandbox."""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest

import maintenance
import paths
from conftest import REAL_ROOT, make_writable

PYTHON = sys.executable


def resolved(sandbox, runtime_dir=None):
    env = {"CC_RUNTIME_DIR": str(runtime_dir)} if runtime_dir else {}
    return paths.resolve(env=env, root=sandbox, use_dotenv=False)


def ro(path):
    return closing(maintenance.connect_ro(path))


def test_s3_reset_creates_verified_extended_working_copy(sandbox):
    p = resolved(sandbox)
    result = maintenance.reset_working_db(p)

    assert result["working_sha256_before_extension"] == paths.ORIGINAL_SHA256
    assert result["working_sha256_after_extension"] != paths.ORIGINAL_SHA256  # expected: tables were added
    assert result["original_sha256"] == paths.ORIGINAL_SHA256
    assert paths.sha256_file(p.original_db) == paths.ORIGINAL_SHA256
    with ro(p.original_db) as o, ro(p.working_db) as w:
        names = {n for _, n, _ in maintenance.schema_objects(o)}
        assert maintenance.content_digest(w, only=names) == maintenance.content_digest(o)
        assert w.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0] == 0
        assert w.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0
        assert w.execute("SELECT balance FROM cash_accounts").fetchone()[0] == 3400.0
        assert w.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_s3_reset_restores_after_changes(sandbox):
    p = resolved(sandbox)
    maintenance.reset_working_db(p)
    with closing(maintenance.connect_rw(p.working_db)) as con:
        con.execute("UPDATE cash_accounts SET balance = 1")
        con.execute("UPDATE tickets SET status = 'closed'")
        con.execute("""INSERT INTO pending_actions (kind, invoice_id, amount, amount_basis, account,
                       fundable_at_proposal, rationale, proposed_by, run_id, business_date, created_at)
                       VALUES ('payment', 501, 840, 'invoice_amount', 'checking', 1, 'pay overdue invoice',
                               'accounting', 'r', '2026-08-31', 't')""")
    maintenance.reset_working_db(p)
    with ro(p.working_db) as w:
        assert w.execute("SELECT balance FROM cash_accounts").fetchone()[0] == 3400.0
        assert {s for (s,) in w.execute("SELECT status FROM tickets")} == {"open"}
        assert w.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0] == 0


@pytest.mark.parametrize("use_runtime", [False, True])
def test_s3_reset_never_touches_audit_files(sandbox, runtime_dir, use_runtime):
    p = resolved(sandbox, runtime_dir if use_runtime else None)
    p.active_dir.mkdir(parents=True, exist_ok=True)
    p.audit_db.write_bytes(b"stand-in audit.db bytes")
    p.audit_trail_json.write_text('[{"event_type": "earlier_run"}]')
    before = (p.audit_db.read_bytes(), p.audit_trail_json.read_bytes())
    maintenance.reset_working_db(p)
    maintenance.reset_working_db(p)
    assert (p.audit_db.read_bytes(), p.audit_trail_json.read_bytes()) == before


CRASH_MID_TRANSACTION = r'''
import os, sqlite3, sys
c = sqlite3.connect(sys.argv[1], isolation_level=None)
c.execute("PRAGMA journal_mode=DELETE"); c.execute("PRAGMA synchronous=FULL")
c.execute("UPDATE cash_accounts SET balance = 1")            # committed change
c.execute("PRAGMA cache_size=1")                               # force pages to spill to disk
c.execute("BEGIN")
c.execute("UPDATE cash_accounts SET balance = 2")
c.execute("UPDATE tickets SET notes = randomblob(3000)")
os._exit(1)                                                    # crash: hot journal left behind
'''


def test_s3_crashed_transaction_journal_is_not_replayed_into_new_copy(sandbox):
    # Without protection, SQLite replays a leftover hot journal into the freshly copied file:
    # a probe on 2026-10-09 read balance 1.0 instead of 3400 with integrity_check still "ok".
    p = resolved(sandbox)
    maintenance.reset_working_db(p)
    subprocess.run([PYTHON, "-c", CRASH_MID_TRANSACTION, str(p.working_db)])
    journal = p.working_db.with_name(p.working_db.name + "-journal")
    assert journal.exists() and journal.stat().st_size > 0      # precondition: a real hot journal

    maintenance.reset_working_db(p)
    assert not journal.exists()
    with ro(p.original_db) as o, ro(p.working_db) as w:
        assert w.execute("SELECT balance FROM cash_accounts").fetchone()[0] == 3400.0
        names = {n for _, n, _ in maintenance.schema_objects(o)}
        assert maintenance.content_digest(w, only=names) == maintenance.content_digest(o)
        assert maintenance.integrity_ok(w)


def test_s3_tampered_original_is_refused_and_working_copy_kept(sandbox):
    p = resolved(sandbox)
    maintenance.reset_working_db(p)
    working_before = paths.sha256_file(p.working_db)
    make_writable(p.original_db)
    with open(p.original_db, "ab") as f:
        f.write(b"tamper")
    with pytest.raises(maintenance.ResetError, match="does not match"):
        maintenance.reset_working_db(p)
    assert paths.sha256_file(p.working_db) == working_before
    assert not p.working_db.with_name(p.working_db.name + ".resetting").exists()


def test_s3_reset_refuses_while_working_copy_is_locked(sandbox):
    p = resolved(sandbox)
    maintenance.reset_working_db(p)
    holder = maintenance.connect_rw(p.working_db)
    try:
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("UPDATE cash_accounts SET balance = 0")   # uncommitted transaction in progress
        with pytest.raises(maintenance.ResetError, match="in use"):
            maintenance.reset_working_db(p)
        assert p.working_db.with_name(p.working_db.name + "-journal").exists()  # live journal kept
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    with ro(p.working_db) as w:
        assert w.execute("SELECT balance FROM cash_accounts").fetchone()[0] == 3400.0


def test_s2_s3_runtime_reset_writes_only_the_runtime_folder(sandbox, runtime_dir):
    p = resolved(sandbox, runtime_dir)
    maintenance.reset_working_db(p)
    assert p.working_db.exists() and p.working_db.parent == runtime_dir.resolve()
    assert not (sandbox / "data" / "campus_customs_new.db").exists()
    assert paths.sha256_file(p.original_db) == paths.ORIGINAL_SHA256


def test_s4_snapshot_copies_committed_runtime_state(sandbox, runtime_dir):
    p = resolved(sandbox, runtime_dir)
    maintenance.reset_working_db(p)
    with closing(maintenance.connect_rw(p.working_db)) as con:
        con.execute("""INSERT INTO drafts (ticket_id, recipient, recipient_source, subject, body,
                       created_by, run_id, business_date, created_at)
                       VALUES (103, 'Yale AI Club', 'tickets.requester', 'Hoodies',
                               'Committed draft body text.', 'customer_service', 'r', '2026-08-31', 't')""")
    holder = maintenance.connect_rw(p.working_db)
    try:
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("""INSERT INTO drafts (ticket_id, recipient, recipient_source, subject, body,
                          created_by, run_id, business_date, created_at)
                          VALUES (101, 'Tauhid Zaman', 'tickets.requester', 'Tee',
                                  'UNCOMMITTED draft body.', 'customer_service', 'r', '2026-08-31', 't')""")
        result = maintenance.snapshot_runtime(p)      # taken while a write is still uncommitted
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    with ro(p.repo_working_db) as snap, ro(p.working_db) as live:
        assert maintenance.integrity_ok(snap)
        assert maintenance.content_digest(snap) == maintenance.content_digest(live)
        bodies = [b for (b,) in snap.execute("SELECT body FROM drafts")]
    assert bodies == ["Committed draft body text."]
    assert result["target_sha256"] == paths.sha256_file(p.repo_working_db)
    assert paths.sha256_file(p.original_db) == paths.ORIGINAL_SHA256


def test_s4_snapshot_refused_in_default_mode(sandbox):
    p = resolved(sandbox)
    maintenance.reset_working_db(p)
    before = paths.sha256_file(p.working_db)
    with pytest.raises(maintenance.SnapshotError, match="Not in runtime mode"):
        maintenance.snapshot_runtime(p)
    assert paths.sha256_file(p.working_db) == before


def test_cli_reset_requires_confirm_and_reports_json(sandbox):
    script = str(REAL_ROOT / "scripts" / "reset_db.py")
    env = {"SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""), "PATH": ""}
    dry = subprocess.run([PYTHON, script, "--root", str(sandbox)], capture_output=True, text=True, env=env)
    assert dry.returncode == 2 and not (sandbox / "data" / "campus_customs_new.db").exists()

    run = subprocess.run([PYTHON, script, "--root", str(sandbox), "--confirm"], capture_output=True, text=True, env=env)
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout)
    assert report["event"] == "db_reset" and report["mode"] == "default"
    assert report["working_db"] == str(sandbox.resolve() / "data" / "campus_customs_new.db")


def test_cli_snapshot_refuses_in_default_mode(sandbox):
    script = str(REAL_ROOT / "scripts" / "snapshot_runtime.py")
    env = {"SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""), "PATH": ""}
    run = subprocess.run([PYTHON, script, "--root", str(sandbox)], capture_output=True, text=True, env=env)
    assert run.returncode == 1 and "Not in runtime mode" in run.stderr
