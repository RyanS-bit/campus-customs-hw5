"""Stage 1, group X: schema extension constraints, exercised by real inserts on a reset sandbox copy.

These tests write directly with a privileged connection to prove the DATABASE-level backstops.
Agent-facing restrictions (authorizer, run identity) are Stage 2-3.
"""

import sqlite3
from contextlib import closing

import pytest

import maintenance
import paths

COMMON = dict(rationale="test rationale text", run_id="run-1", business_date="2026-08-31", created_at="t")


@pytest.fixture
def con(sandbox):
    p = paths.resolve(env={}, root=sandbox, use_dotenv=False)
    maintenance.reset_working_db(p)
    with closing(maintenance.connect_rw(p.working_db)) as c:
        yield c


def insert(con, table, **cols):
    keys = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    return con.execute(f"INSERT INTO {table} ({keys}) VALUES ({marks})", tuple(cols.values())).lastrowid


def payment(con, **over):
    cols = dict(kind="payment", invoice_id=501, amount=840.0, amount_basis="invoice_amount", account="checking",
                fundable_at_proposal=1, proposed_by="accounting", **COMMON)
    cols.update(over)
    return insert(con, "pending_actions", **cols)


def purchase(con, **over):
    cols = dict(kind="purchase", ticket_id=103, vendor_id=1, sku="CC-HOOD-NAVY", size="M", qty=12, amount=264.0,
                amount_basis="estimate_unit_cost", proposed_by="inventory", **COMMON)
    cols.update(over)
    return insert(con, "pending_actions", **cols)


def quote(con, **over):
    cols = dict(kind="quote", ticket_id=103, sku="CC-HOOD-NAVY", size="M", qty=20, unit_price=45.0, amount=900.0,
                amount_basis="quoted_unit_price", proposed_by="customer_service", **COMMON)
    cols.update(over)
    return insert(con, "pending_actions", **cols)


def test_x1_valid_proposals_default_to_proposed(con):
    ids = [payment(con), purchase(con), quote(con)]
    statuses = [s for (s,) in con.execute("SELECT status FROM pending_actions ORDER BY id")]
    assert len(ids) == 3 and statuses == ["proposed"] * 3


@pytest.mark.parametrize("field,value", [
    ("status", "approved"), ("status", "executed"), ("decided_by", "Dr. Ryan"),
    ("decided_at", "2026-08-31"), ("executed_at", "2026-08-31"),
])
def test_x1_insert_cannot_skip_human_approval(con, field, value):
    with pytest.raises(sqlite3.IntegrityError, match="must be status proposed"):
        payment(con, **{field: value})
    assert con.execute("SELECT COUNT(*) FROM pending_actions").fetchone()[0] == 0


def test_x3_duplicate_open_payment_rejected_until_first_is_closed(con):
    first = payment(con)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        payment(con)
    con.execute("UPDATE pending_actions SET status = 'rejected' WHERE id = ?", (first,))  # simulates a Problem 7 decision
    payment(con)
    lease_first = payment(con, invoice_id=None, lease_id=1, amount=2400.0, amount_basis="lease_rent")
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        payment(con, invoice_id=None, lease_id=1, amount=2400.0, amount_basis="lease_rent")
    assert lease_first


def test_x4_purchase_duplicates_vs_alternatives(con):
    purchase(con)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        purchase(con)                                   # exact duplicate (retry / repeated delegation)
    purchase(con, qty=20, amount=440.0)                 # genuine alternative: different quantity
    purchase(con, vendor_id=2)                          # genuine alternative: different vendor
    purchase(con, ticket_id=None)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        purchase(con, ticket_id=None)                   # NULL ticket still counts as the same key


def test_x5_quote_duplicates_vs_alternatives(con):
    quote(con)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        quote(con)
    quote(con, unit_price=50.0, amount=1000.0)


@pytest.mark.parametrize("make,over", [
    ("purchase", dict(vendor_id=None)),
    ("purchase", dict(unit_price=10.0)),
    ("payment", dict(lease_id=1)),                         # both invoice and lease
    ("payment", dict(amount_basis="lease_rent")),          # basis does not match invoice link
    ("payment", dict(fundable_at_proposal=None)),
    ("quote", dict(unit_price=None)),
    ("quote", dict(ticket_id=None)),
    ("payment", dict(amount=0)),
    ("payment", dict(proposed_by="ceo")),
    ("payment", dict(rationale="short")),
    ("payment", dict(warnings="not json")),
])
def test_x6_shape_checks(con, make, over):
    with pytest.raises(sqlite3.IntegrityError):
        {"payment": payment, "purchase": purchase, "quote": quote}[make](con, **over)


def test_x6_foreign_keys_enforced(con):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        payment(con, invoice_id=999)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        quote(con, ticket_id=999)


def draft(con, **over):
    cols = dict(ticket_id=102, recipient="Elm City Properties", recipient_source="leases.landlord",
                subject="Rent", body="Draft acknowledging the rent notice.", created_by="facilities",
                run_id="run-1", business_date="2026-08-31", created_at="t")
    cols.update(over)
    return insert(con, "drafts", **cols)


def test_x7_drafts_are_drafts_only(con):
    draft(con)
    for over in (dict(status="sent"), dict(recipient_source="guessed"), dict(created_by="accounting"),
                 dict(channel="sms"), dict(body="too short"), dict(recipient="")):
        with pytest.raises(sqlite3.IntegrityError):
            draft(con, **over)
    assert [s for (s,) in con.execute("SELECT status FROM drafts")] == ["draft"]
