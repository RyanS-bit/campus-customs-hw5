"""Problem 9, follow-up 42: the offline MCP checker (mcp_server/verify_tools.py) is valid for every legitimate
simulation state and still catches inconsistent records.

Each test builds a development-mode MCP server over a TEMPORARY working copy (conftest `work`) and runs the
checker's `verify` pass against it. The real databases are never opened for writing.
"""
import asyncio
from contextlib import closing

import pytest
from fastmcp import Client

import approval
import audit_trail
import maintenance
import ops
import server
import verify_tools
import uuid


def run_verify(work, tag="t"):
    verify_tools.results.clear()

    async def go():
        async with Client(server.create_server(None, work)) as client:
            return await verify_tools.verify(client, work.working_db, tag)
    out = asyncio.run(go())
    return out, {label: ok for ok, label in verify_tools.results}


def failed(labels):
    return [label for label, ok in labels.items() if not ok]


def pay_501(work):
    ident = ops.RunIdentity(str(uuid.uuid4()), "accounting", "simulated")
    action = ops.propose_payment(work, ident, kind="invoice", ref_id=501, amount=840, ticket_id=101,
                                 rationale="Pay overdue invoice 501")["links"]["action_id"]
    approval.approve_action(work, audit_trail.AuditTrail(work.audit_trail_json), action, decided_by="Dr. Ryan")


def test_v1_original_state_passes_with_invoice_open(work):
    out, labels = run_verify(work)
    assert failed(labels) == []
    assert any("invoice 501 is OPEN" in label for label in labels)
    assert out["cash"] == 3400.0 and out["tickets"] == [101, 102, 103]


def test_v2_after_the_human_payment_it_passes_with_invoice_paid(work):
    pay_501(work)
    out, labels = run_verify(work)
    assert failed(labels) == []
    assert any("invoice 501 is PAID" in label for label in labels) and out["cash"] == 2560.0


def test_v3_after_tickets_are_resolved_the_ticket_link_checks_still_run(work):
    pay_501(work)
    with closing(maintenance.connect_rw(work.working_db)) as con:          # temporary copy only
        con.execute("UPDATE tickets SET status = 'resolved'")
        con.commit()
    out, labels = run_verify(work)
    assert failed(labels) == [] and out["tickets"] == []
    assert sum("carries" in label or "no linked" in label for label in labels) == 3


@pytest.mark.parametrize("corrupt, expected", [
    ("INSERT INTO payments (kind, ref_id, amount, account, paid_at, approved_by) "
     "VALUES ('invoice', 501, 840, 'checking', '2026-08-31', 'x')", "invoice 501 is PAID"),       # paid twice
    ("UPDATE invoices SET amount = 800 WHERE id = 501", "invoice 501 stored facts"),
    ("UPDATE invoices SET status = 'void' WHERE id = 501", "unexpected status"),
])
def test_v4_inconsistent_records_are_caught(work, corrupt, expected):
    pay_501(work)
    with closing(maintenance.connect_rw(work.working_db)) as con:          # temporary copy only
        con.execute(corrupt)
        con.commit()
    _, labels = run_verify(work)
    assert any(expected in label for label in failed(labels)), failed(labels)


def test_v5_an_invoice_marked_paid_without_a_payment_is_caught(work):
    with closing(maintenance.connect_rw(work.working_db)) as con:          # temporary copy only; no payment exists
        con.execute("UPDATE invoices SET status = 'paid' WHERE id = 501")
        con.commit()
    _, labels = run_verify(work)
    assert any("invoice 501 is PAID" in label for label in failed(labels)), failed(labels)


def test_v6_overdue_days_follow_the_stored_dates_not_a_constant(work):
    with closing(maintenance.connect_rw(work.working_db)) as con:          # temporary copy only
        con.execute("UPDATE desk SET date_today = '2026-09-04'")
        con.commit()
    out, labels = run_verify(work)
    assert [i["days_overdue"] for i in out["fin"]["open_invoices"]] == [7]
    assert not any("days overdue" in label or "is OPEN" in label for label in failed(labels))
