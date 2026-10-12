"""Problem 9 (follow-up 17): (1) a WITHDRAWN purchase authorization is distinguishable from a rejected proposal, with the
complete decision history kept in audit.db; (2) warnings stored when a proposal was made are labelled as historical, and
the CURRENT vendor restriction comes from current database facts. OFFLINE: temporary databases only.
"""

import json
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

import main
import maintenance
import ops

H = {"X-Human-Decision": "confirm"}
ME = {"decided_by": "Dr. Ryan", "confirm": True}
TEES = dict(vendor_id=1, sku="CC-TEE-WHITE", size="S", qty=10, ticket_id=101, rationale="Restock size S tees (estimate)")
PAY_501 = dict(kind="invoice", ref_id=501, amount=840, ticket_id=101, rationale="Pay overdue invoice 501")


@pytest.fixture
def c(work):
    with TestClient(main.create_app(p=work)) as client:
        yield client


def propose(work, op, agent, args):
    return getattr(ops, op)(work, ops.RunIdentity(str(uuid.uuid4()), agent, "simulated"), **args)["links"]["action_id"]


def actions(c, ticket=101):
    return {a["id"]: a for a in c.get(f"/api/tickets/{ticket}").json()["pending_actions"]}


def audit_events(work, ref_id):
    with closing(maintenance.connect_ro(work.audit_db)) as con:
        return [(r[0], json.loads(r[1])) for r in con.execute(
            "SELECT event_type, payload FROM events WHERE ref_table = 'pending_actions' AND ref_id = ? ORDER BY id", (ref_id,))]


# ---------------------------------------------------------------- (1) withdrawn authorization vs rejected proposal

def test_h1_a_withdrawn_authorization_is_distinct_from_a_rejected_proposal(work, c):
    withdrawn = propose(work, "propose_purchase", "inventory", TEES)
    rejected = propose(work, "propose_purchase", "inventory", {**TEES, "qty": 4})
    c.post(f"/api/actions/{withdrawn}/approve", json={**ME, "note": "Authorized pending supplier check"}, headers=H)
    r = c.post(f"/api/actions/{withdrawn}/reject", json={**ME, "decided_by": "Pat Lee", "note": "Supplier could not confirm"}, headers=H)
    assert r.json()["result"] == "authorization_withdrawn" and "never executed and nothing was paid" in r.json()["meaning"]
    assert c.post(f"/api/actions/{rejected}/reject", json=ME, headers=H).json()["result"] == "rejected"
    a = actions(c)
    assert a[withdrawn]["status"] == a[rejected]["status"] == "rejected"           # the stored status is the same...
    w = a[withdrawn]["withdrawal"]                                                  # ...but the history is not
    assert (w["authorized_by"], w["authorization_note"], w["withdrawn_by"], w["withdrawal_note"], w["executed"], w["paid"]) == \
           ("Dr. Ryan", "Authorized pending supplier check", "Pat Lee", "Supplier could not confirm", False, False)
    assert w["authorized_at"] < w["withdrawn_at"]
    assert a[rejected]["withdrawal"] is None
    assert a[withdrawn]["payment_id"] is None and a[withdrawn]["executed_at"] is None
    # The complete decision history is kept in audit.db: the authorization AND the withdrawal.
    hist = audit_events(work, withdrawn)
    assert [t for t, _ in hist] == ["proposal_created", "action_approved", "action_rejected"]
    approved, withdrawal = hist[1][1], hist[2][1]
    assert approved["decided_by"] == "Dr. Ryan" and approved["note"] == "Authorized pending supplier check"
    assert withdrawal["withdrawn_after_authorization"] is True and withdrawal["status_before"] == "approved"
    assert (withdrawal["authorized_by"], withdrawal["authorized_at"]) == (approved["decided_by"], approved["decided_at"])
    assert [t for t, _ in audit_events(work, rejected)] == ["proposal_created", "action_rejected"]
    assert "withdrawn_after_authorization" not in audit_events(work, rejected)[1][1]


def test_h2_after_a_reset_an_old_withdrawal_never_attaches_to_a_new_proposal_with_the_same_id(work, c):
    old = propose(work, "propose_purchase", "inventory", TEES)
    c.post(f"/api/actions/{old}/approve", json=ME, headers=H)
    c.post(f"/api/actions/{old}/reject", json=ME, headers=H)
    assert actions(c)[old]["withdrawal"] is not None
    maintenance.reset_working_db(work)                                              # ids restart; audit.db keeps history
    new = propose(work, "propose_purchase", "inventory", TEES)
    assert new == old
    c.post(f"/api/actions/{new}/reject", json=ME, headers=H)                       # a plain rejection of the NEW proposal
    assert actions(c)[new]["withdrawal"] is None


# ---------------------------------------------------------------- (2) historical warnings vs current conditions

def test_h3_stored_warnings_are_historical_and_the_current_vendor_status_comes_from_current_invoices(work, c):
    buy = propose(work, "propose_purchase", "inventory", TEES)                     # proposed while Bulldog is blocked
    a = actions(c)[buy]
    assert a["vendor_blocked"] is True and any(w.startswith("R3: Bulldog Print Co has open unpaid invoice(s) [501]") for w in a["warnings"])
    assert a["warnings_recorded_at"]["business_date"] == "2026-08-31" and a["warnings_recorded_at"]["created_at"] == a["created_at"]
    now = a["current_vendor_status"]
    assert (now["vendor"], now["open_invoice_ids"], now["blocked_now"], now["source"]) == \
           ("Bulldog Print Co", [501], True, "invoices table (current)")
    pay = propose(work, "propose_payment", "accounting", PAY_501)
    assert c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).status_code == 200     # #501 paid: Bulldog unblocked
    a = actions(c)[buy]
    assert a["vendor_blocked"] is True and any("R3" in w for w in a["warnings"])         # the record of the past is unchanged
    assert (a["current_vendor_status"]["open_invoice_ids"], a["current_vendor_status"]["blocked_now"]) == ([], False)   # today
    assert actions(c)[pay]["current_vendor_status"] is None                             # only purchases have a vendor


def test_h4_the_pending_list_and_run_views_carry_the_same_fields(work, c):
    buy = propose(work, "propose_purchase", "inventory", TEES)
    [p] = c.get("/api/actions/pending").json()["actions"]
    assert p["id"] == buy and p["current_vendor_status"]["blocked_now"] is True and p["withdrawal"] is None
    assert "warnings_recorded_at" in p
