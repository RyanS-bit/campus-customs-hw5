"""Stage 2, group B: the restricted write connection actually used by ops.py.

Every attempt runs on ops.open_write_connection (the real factory), and the business tables are
fingerprinted before and after. Hostile triggers are planted with a privileged TEST connection to
show that indirect routes are denied when a legitimate operation fires them.
"""

import sqlite3
from contextlib import closing

import pytest

import ops
from conftest import counts, privileged, protected_digest

PROTECTED = ["desk", "cash_accounts", "inventory", "pricing", "vendors", "invoices", "leases", "payments", "tickets"]


@pytest.fixture
def rc(work):
    with closing(ops.open_write_connection(work)) as con:
        yield con


def seed_proposal(work, ident):
    return ops.propose_payment(work, ident("accounting"), kind="invoice", ref_id=501, amount=840,
                               ticket_id=101, rationale="Pay overdue invoice 501")["links"]["action_id"]


DIRECT_WRITES = (
    [f"UPDATE {t} SET rowid = rowid" for t in PROTECTED]
    + [f"DELETE FROM {t}" for t in PROTECTED]
    + ["UPDATE cash_accounts SET balance = 0",
       "UPDATE invoices SET status = 'paid'",
       "UPDATE tickets SET status = 'closed'",
       "UPDATE inventory SET qty = qty + 100",
       "UPDATE leases SET next_due = '2026-10-02'",
       "INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by) VALUES ('invoice', 501, 840, 'checking', '2026-08-31', 'x')",
       "INSERT INTO cash_accounts (name, balance, date) VALUES ('fake', 1000000, '2026-08-31')",
       "INSERT INTO tickets (type, requester, subject, status, created_at) VALUES ('x', 'x', 'x', 'closed', 'x')",
       "INSERT INTO invoices (vendor_id, amount, due_date, status, description) VALUES (1, 1, '2026-08-31', 'paid', 'x')",
       "UPDATE pending_actions SET status = 'approved'",
       "UPDATE pending_actions SET decided_by = 'agent'",
       "DELETE FROM pending_actions",
       "DELETE FROM drafts",
       "UPDATE drafts SET status = 'draft'",
       "UPDATE audit.events SET payload = '{}'",
       "DELETE FROM audit.events",
       "UPDATE schema_ext_meta SET version = 99",
       "INSERT INTO schema_ext_meta VALUES (99, 'x')"]
)


@pytest.mark.parametrize("sql", DIRECT_WRITES)
def test_b1_direct_writes_to_protected_records_are_denied(work, ident, rc, sql):
    seed_proposal(work, ident)
    before = (protected_digest(work), counts(work))
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        rc.execute(sql)
    assert rc.denials, "the denial must be recorded"
    assert (protected_digest(work), counts(work)) == before


HOSTILE_TRIGGERS = [
    ("main", "CREATE TRIGGER hostile AFTER INSERT ON pending_actions BEGIN UPDATE cash_accounts SET balance = 0; END"),
    ("main", "CREATE TRIGGER hostile AFTER INSERT ON pending_actions BEGIN "
             "INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by) VALUES ('invoice', 501, 840, 'checking', 'x', 'x'); END"),
    ("main", "CREATE TRIGGER hostile BEFORE INSERT ON pending_actions BEGIN UPDATE invoices SET status = 'paid'; END"),
    ("main", "CREATE TRIGGER hostile AFTER INSERT ON drafts BEGIN UPDATE tickets SET status = 'closed'; END"),
    ("audit", "CREATE TRIGGER audit.hostile AFTER INSERT ON events BEGIN DELETE FROM events WHERE id < NEW.id; END"),
]


@pytest.mark.parametrize("where,ddl", HOSTILE_TRIGGERS)
def test_b2_hostile_trigger_fired_by_a_real_operation_is_denied(work, ident, where, ddl):
    with privileged(work) as con:
        con.execute(ddl)                                # planted by the TEST, outside the restricted path
    before = (protected_digest(work), counts(work))
    with pytest.raises(ops.OpsError) as err:
        if "drafts" in ddl:
            ops.draft_message(work, ident("customer_service"), ticket_id=101, subject="Tee",
                              body="Thanks for your order request.")
        else:
            ops.propose_payment(work, ident("accounting"), kind="invoice", ref_id=501, amount=840,
                                rationale="Pay overdue invoice 501")
    assert err.value.code in ("DB_DENIED", "AUDIT_FAILED")
    assert (protected_digest(work), counts(work)) == before


SCHEMA_CHANGES = [
    "CREATE TABLE x (a)", "CREATE TEMP TABLE x (a)", "CREATE VIEW v AS SELECT 1", "CREATE INDEX ix ON tickets (subject)",
    "CREATE TRIGGER t AFTER INSERT ON pending_actions BEGIN UPDATE cash_accounts SET balance = 0; END",
    "CREATE TEMP TRIGGER t AFTER INSERT ON pending_actions BEGIN UPDATE cash_accounts SET balance = 0; END",
    "DROP TABLE tickets", "DROP TRIGGER pending_actions_insert_as_proposed", "DROP TRIGGER pending_actions_no_delete",
    "DROP INDEX ux_pending_purchase", "DROP TRIGGER audit.events_no_update",
    "ALTER TABLE tickets ADD COLUMN x", "ALTER TABLE tickets RENAME TO t2", "REINDEX", "ANALYZE",
]


@pytest.mark.parametrize("sql", SCHEMA_CHANGES)
def test_b3_schema_changes_are_denied(work, rc, sql):
    before = protected_digest(work)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized|authorization denied"):
        rc.execute(sql)
    assert protected_digest(work) == before


def test_b4_attach_and_detach_are_denied(work, rc):
    for sql, args in [("ATTACH DATABASE ? AS orig", (str(work.original_db),)),
                      ("ATTACH DATABASE ':memory:' AS m", ()), ("DETACH DATABASE audit", ())]:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized|authorization denied"):
            rc.execute(sql, args)
    assert [d["action"] for d in rc.denials] == ["ATTACH", "ATTACH", "DETACH"]


@pytest.mark.parametrize("sql", [
    "PRAGMA foreign_keys = OFF", "PRAGMA recursive_triggers = OFF", "PRAGMA writable_schema = ON",
    "PRAGMA journal_mode = WAL", "PRAGMA main.synchronous = OFF", "PRAGMA audit.journal_mode = OFF",
    "PRAGMA ignore_check_constraints = ON", "PRAGMA query_only = 0", "PRAGMA foreign_keys",
])
def test_b5_pragmas_are_denied(rc, sql):
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        rc.execute(sql)


def test_b5_settings_still_in_force_after_attempts(work):
    # Read back through a privileged connection: the restricted one cannot even read pragmas.
    with closing(ops.open_write_connection(work)) as con:
        for sql in ("PRAGMA foreign_keys = OFF", "PRAGMA recursive_triggers = OFF"):
            with pytest.raises(sqlite3.DatabaseError):
                con.execute(sql)
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            con.execute("""INSERT INTO drafts (ticket_id, recipient, recipient_source, subject, body, created_by,
                           run_id, business_date, created_at) VALUES (999, 'x', 'tickets.requester', 's',
                           'body long enough here', 'facilities', 'r', 'd', 't')""")


def test_b6_replace_and_upsert_cannot_overwrite_existing_rows(work, ident, rc):
    action_id = seed_proposal(work, ident)
    with privileged(work) as con:
        before_row = con.execute("SELECT * FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
        before_event = con.execute("SELECT * FROM audit.events WHERE id = 1").fetchone()
    payment_cols = ("kind, invoice_id, amount, amount_basis, account, fundable_at_proposal, rationale, proposed_by, "
                    "run_id, business_date, created_at")
    payment_vals = "'payment', 501, 840, 'invoice_amount', 'checking', 1, 'forged replacement', 'accounting', 'r', 'd', 't'"
    attempts = [
        f"INSERT OR REPLACE INTO pending_actions (id, {payment_cols}) VALUES ({action_id}, {payment_vals})",
        f"REPLACE INTO pending_actions ({payment_cols}) VALUES ({payment_vals})",            # hits the unique index
        f"INSERT INTO pending_actions (id, {payment_cols}) VALUES ({action_id}, {payment_vals}) "
        "ON CONFLICT(id) DO UPDATE SET rationale = 'forged upsert'",
        "INSERT OR REPLACE INTO audit.events (id, ts_utc, actor, actor_kind, event_type, payload, prev_hash, hash) "
        f"VALUES (1, 't', 'x', 'agent', 'forged', '{{}}', '{'1' * 64}', '{'2' * 64}')",
    ]
    for sql in attempts:
        with pytest.raises(sqlite3.DatabaseError):
            rc.execute(sql)
        if rc.in_transaction:
            rc.execute("ROLLBACK")
    with privileged(work) as con:
        assert con.execute("SELECT * FROM pending_actions WHERE id = ?", (action_id,)).fetchone() == before_row
        assert con.execute("SELECT * FROM audit.events WHERE id = 1").fetchone() == before_event
        assert con.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0] == 1


def test_b7_vacuum_and_vacuum_into_are_denied(work, rc, tmp_path):
    target = tmp_path / "exfiltrated.db"
    for sql in ("VACUUM", f"VACUUM INTO '{target}'"):
        with pytest.raises(sqlite3.DatabaseError, match="not authorized|authorization denied"):
            rc.execute(sql)
    assert not target.exists()


@pytest.mark.parametrize("sql", ["SELECT load_extension('x')", "SELECT randomblob(10)",
                                 "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 3) SELECT * FROM r"])
def test_b8_unlisted_functions_and_recursion_are_denied(rc, sql):
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        rc.execute(sql).fetchall()


def test_b9_audit_events_are_append_only_even_for_privileged_code(work, ident):
    seed_proposal(work, ident)
    with privileged(work) as con:
        for sql in ("UPDATE audit.events SET payload = '{}'", "DELETE FROM audit.events"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                con.execute(sql)


def test_b10_restricted_insert_cannot_skip_approval(work, rc):
    with pytest.raises(sqlite3.IntegrityError, match="must be status proposed"):
        rc.execute("""INSERT INTO pending_actions (kind, invoice_id, amount, amount_basis, account, fundable_at_proposal,
                      rationale, proposed_by, run_id, business_date, created_at, status)
                      VALUES ('payment', 501, 840, 'invoice_amount', 'checking', 1, 'self approved now',
                              'accounting', 'r', 'd', 't', 'approved')""")
    assert counts(work)["pending_actions"] == 0


def test_b11_legitimate_operation_changes_only_permitted_tables(work, ident):
    before = protected_digest(work)
    seed_proposal(work, ident)
    assert protected_digest(work) == before
    assert counts(work) == {"pending_actions": 1, "drafts": 0, "events": 1}


def test_b12_every_operation_uses_a_restricted_connection(work, ident, monkeypatch):
    """Spy on the real factory: each operation's connection must deny a write to cash_accounts."""
    seen = []
    real = ops.open_write_connection

    def spy(p):
        con = real(p)
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            con.execute("UPDATE cash_accounts SET balance = 0")
        seen.append(type(con).__name__)
        return con

    monkeypatch.setattr(ops, "open_write_connection", spy)
    pay = ops.propose_payment(work, ident("accounting"), kind="lease", ref_id=1, amount=2400, ticket_id=102,
                              rationale="Rent due 2026-09-02")
    ops.propose_purchase(work, ident("inventory"), vendor_id=2, sku="CC-MUG-CREST", size="OS", qty=10,
                         rationale="Restock mugs at zero")
    ops.propose_quote(work, ident("customer_service"), ticket_id=103, sku="CC-HOOD-NAVY", size="M", qty=20,
                      unit_price=45, rationale="Bulk discount within policy")
    ops.draft_message(work, ident("facilities"), ticket_id=102, subject="Rent",
                      body="Your notice was received and is under review.", related_action_id=pay["links"]["action_id"])
    assert seen == ["RestrictedConnection"] * 4
    with privileged(work) as con:
        assert con.execute("SELECT balance FROM cash_accounts").fetchone()[0] == 3400.0
