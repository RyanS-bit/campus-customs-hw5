"""Stage 2, group V: business rules, policies, permissions and input validation of ops.py.

Expected figures come from the database (harness.md) and the approved spec, not from the code.
"""

import inspect
import json
import uuid

import pytest

import ops
from conftest import counts, privileged

PAY_501 = dict(kind="invoice", ref_id=501, amount=840, ticket_id=101, rationale="Pay overdue invoice 501")
PAY_RENT = dict(kind="lease", ref_id=1, amount=2400, ticket_id=102, rationale="Rent due 2026-09-02")
QUOTE_103 = dict(ticket_id=103, sku="CC-HOOD-NAVY", size="M", qty=20, unit_price=45, rationale="Bulk discount within policy")
BUY_103 = dict(vendor_id=1, sku="CC-HOOD-NAVY", size="M", qty=12, ticket_id=103, rationale="Cover the 12-unit shortfall")


def set_balance(work, value):
    with privileged(work) as con:
        con.execute("UPDATE cash_accounts SET balance = ?", (value,))


# ---- payments ---------------------------------------------------------------------

def test_v1_payment_proposals_report_hypothetical_cash(work, ident):
    first = ops.propose_payment(work, ident("accounting"), **PAY_501)
    second = ops.propose_payment(work, ident("accounting"), **PAY_RENT)
    assert first["executable"] is False and first["requires_human_approval"] is True
    assert first["proposal"]["fundable_at_proposal"] is True
    assert first["computed"]["cash_if_all_proposed_payments_executed"] == 2560.0
    assert second["computed"]["cash_if_all_proposed_payments_executed"] == 160.0
    assert "3 days overdue" in first["checks"][2]["detail"]
    assert "2 days from 2026-08-31" in second["checks"][2]["detail"]
    assert not first["warnings"] and not second["warnings"]
    with privileged(work) as con:   # money did not move
        assert con.execute("SELECT balance FROM cash_accounts").fetchone()[0] == 3400.0
        assert con.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0
        assert con.execute("SELECT status FROM invoices WHERE id = 501").fetchone()[0] == "open"


def test_v2_unfundable_payment_is_recorded_and_labelled_not_executable(work, ident):
    set_balance(work, 500)
    r = ops.propose_payment(work, ident("accounting"), **PAY_501)
    assert r["created"] and r["proposal"]["fundable_at_proposal"] is False
    assert r["checks"][0] == {"rule": "R1", "result": "fail", "detail": "840.00 > stored balance 500.00"}
    assert any(h.startswith("NOT EXECUTABLE") for h in r["human_judgment"])
    with privileged(work) as con:
        assert con.execute("SELECT fundable_at_proposal FROM pending_actions").fetchone()[0] == 0


def test_v3_competing_proposals_are_flagged(work, ident):
    set_balance(work, 3000)
    ops.propose_payment(work, ident("accounting"), **PAY_501)
    r = ops.propose_payment(work, ident("accounting"), **PAY_RENT)
    assert r["computed"]["cash_if_all_proposed_payments_executed"] == -240.0
    assert r["checks"][1]["result"] == "warn"
    assert any("Competing proposals" in w for w in r["warnings"])
    assert r["proposal"]["fundable_at_proposal"] is True       # each is individually fundable


@pytest.mark.parametrize("amount", [800, 840.01, 2400])
def test_v4_full_amount_policy(work, ident, amount):
    with pytest.raises(ops.OpsError, match="policy:full_amount_only"):
        ops.propose_payment(work, ident("accounting"), **{**PAY_501, "amount": amount})
    assert counts(work)["pending_actions"] == 0


def test_v5_closed_invoice_is_refused(work, ident):
    with privileged(work) as con:
        con.execute("UPDATE invoices SET status = 'paid' WHERE id = 501")
    with pytest.raises(ops.OpsError, match="R4"):
        ops.propose_payment(work, ident("accounting"), **PAY_501)


def test_v6_duplicate_payment_returns_existing_id(work, ident):
    first = ops.propose_payment(work, ident("accounting"), **PAY_501)["links"]["action_id"]
    with pytest.raises(ops.OpsError) as err:
        ops.propose_payment(work, ident("accounting"), **PAY_501)
    assert err.value.code == "DUPLICATE" and err.value.existing_id == first
    assert counts(work) == {"pending_actions": 1, "drafts": 0, "events": 1}


@pytest.mark.parametrize("override,code", [
    ({"amount": 840.001}, "INVALID_INPUT"), ({"amount": -840}, "INVALID_INPUT"), ({"amount": "840"}, "INVALID_INPUT"),
    ({"amount": True}, "INVALID_INPUT"), ({"amount": float("nan")}, "INVALID_INPUT"), ({"amount": float("inf")}, "INVALID_INPUT"),
    ({"ref_id": "501"}, "INVALID_INPUT"), ({"ref_id": 0}, "INVALID_INPUT"), ({"kind": "cash"}, "INVALID_INPUT"),
    ({"rationale": "short"}, "INVALID_INPUT"), ({"rationale": 12345678901}, "INVALID_INPUT"),
    ({"ref_id": 999}, "NOT_FOUND"), ({"ticket_id": 999}, "NOT_FOUND"), ({"account": "savings"}, "NOT_FOUND"),
    ({"call_id": "x" * 101}, "INVALID_INPUT"),
])
def test_v7_malformed_payment_inputs_write_nothing(work, ident, override, code):
    with pytest.raises(ops.OpsError) as err:
        ops.propose_payment(work, ident("accounting"), **{**PAY_501, **override})
    assert err.value.code == code
    assert counts(work) == {"pending_actions": 0, "drafts": 0, "events": 0}


# ---- permissions and identity ----------------------------------------------------

CALLS = {
    "propose_payment": (ops.propose_payment, PAY_501),
    "propose_purchase": (ops.propose_purchase, BUY_103),
    "propose_quote": (ops.propose_quote, QUOTE_103),
    "draft_message": (ops.draft_message, dict(ticket_id=101, subject="Tee", body="Thanks for your order request.")),
}
# Since Problem 5 follow-up 9, each drafting agent may only draft on its own kind of ticket, so the
# Facilities case uses the lease-linked rent ticket (102). Cross-responsibility drafting: test_v23.
FACILITIES_DRAFT = dict(ticket_id=102, subject="Rent", body="Your rent notice was received and is under review.")


@pytest.mark.parametrize("operation", CALLS)
@pytest.mark.parametrize("agent", ops.AGENTS)
def test_v8_only_permitted_agents_may_write(work, ident, operation, agent):
    fn, kwargs = CALLS[operation]
    if operation == "draft_message" and agent == "facilities":
        kwargs = FACILITIES_DRAFT
    if agent in ops.OPERATION_AGENTS[operation]:
        assert fn(work, ident(agent), **kwargs)["created"]
    else:
        with pytest.raises(ops.OpsError) as err:
            fn(work, ident(agent), **kwargs)
        assert err.value.code == "NOT_PERMITTED"
        assert counts(work) == {"pending_actions": 0, "drafts": 0, "events": 0}


@pytest.mark.parametrize("run_id,agent,mode", [
    ("not-a-uuid", "accounting", "simulated"), (str(uuid.uuid1()), "accounting", "simulated"),
    (str(uuid.uuid4()).upper(), "accounting", "simulated"), (str(uuid.uuid4()), "ceo", "simulated"),
    (str(uuid.uuid4()), "accounting", "production"),
])
def test_v9_run_identity_is_validated(run_id, agent, mode):
    with pytest.raises(ValueError):
        ops.RunIdentity(run_id, agent, mode)


def test_v9_missing_identity_is_refused(work):
    with pytest.raises(ops.OpsError) as err:
        ops.propose_payment(work, {"run_id": str(uuid.uuid4()), "agent": "accounting", "mode": "live"}, **PAY_501)
    assert err.value.code == "NO_RUN_CONTEXT"
    assert counts(work)["pending_actions"] == 0


# ---- purchases --------------------------------------------------------------------

def test_v10_purchase_from_blocked_vendor_is_flagged_not_refused(work, ident):
    ops.propose_payment(work, ident("accounting"), **PAY_501)
    ops.propose_payment(work, ident("accounting"), **PAY_RENT)
    r = ops.propose_purchase(work, ident("inventory"), **BUY_103)
    p = r["proposal"]
    assert (p["amount"], p["amount_basis"], p["vendor_blocked"], p["blocking_invoice_ids"]) == (264.0, "estimate_unit_cost", True, [501])
    assert r["computed"]["earliest_arrival_if_ordered_today"] is None
    assert any(w.startswith("R3:") for w in r["warnings"])
    assert any("exceeds cash remaining" in w and "160.00" in w for w in r["warnings"])
    assert any("No vendor quote exists" in u for u in r["unknowns"])
    with privileged(work) as con:   # nothing ordered or received
        assert con.execute("SELECT qty FROM inventory WHERE sku = 'CC-HOOD-NAVY' AND size = 'M'").fetchone()[0] == 8


def test_v11_unblocked_vendor_gets_lead_time_estimate(work, ident):
    r = ops.propose_purchase(work, ident("inventory"), vendor_id=2, sku="CC-MUG-CREST", size="OS", qty=10,
                             rationale="Restock mugs at zero")
    assert r["proposal"]["vendor_blocked"] is False
    assert r["computed"]["earliest_arrival_if_ordered_today"] == "2026-09-03"     # 2026-08-31 + 3 lead days
    assert not any(w.startswith("R3:") for w in r["warnings"])


def test_v12_purchase_duplicates_vs_alternatives(work, ident):
    first = ops.propose_purchase(work, ident("inventory"), **BUY_103)["links"]["action_id"]
    with pytest.raises(ops.OpsError) as err:
        ops.propose_purchase(work, ident("inventory"), **BUY_103)
    assert err.value.code == "DUPLICATE" and err.value.existing_id == first
    alt = ops.propose_purchase(work, ident("inventory"), **{**BUY_103, "qty": 20})
    assert alt["proposal"]["alternative_to"] == [first]
    assert any("Alternative to" in h for h in alt["human_judgment"])


@pytest.mark.parametrize("override,code", [
    ({"vendor_id": 9}, "NOT_FOUND"), ({"sku": "CC-NOPE"}, "NOT_FOUND"), ({"size": "XXL"}, "NOT_FOUND"),
    ({"qty": 0}, "INVALID_INPUT"), ({"qty": 501}, "INVALID_INPUT"), ({"qty": 2.5}, "INVALID_INPUT"),
    ({"sku": None}, "INVALID_INPUT"),
])
def test_v13_malformed_purchase_inputs_write_nothing(work, ident, override, code):
    with pytest.raises(ops.OpsError) as err:
        ops.propose_purchase(work, ident("inventory"), **{**BUY_103, **override})
    assert err.value.code == code
    assert counts(work)["pending_actions"] == 0


# ---- quotes -----------------------------------------------------------------------

def test_v14_quote_is_proposal_only_with_correct_arithmetic(work, ident):
    r = ops.propose_quote(work, ident("customer_service"), **QUOTE_103)
    assert (r["quote_state"], r["customer_acceptance"]) == ("proposed", "not recorded")
    c = r["computed"]
    assert (c["list_total"], c["discount_pct"], c["unit_margin"], c["total_margin"]) == (1160.0, 22.41, 23.0, 460.0)
    assert (c["fulfillable_from_stock"], c["on_hand"], c["shortfall"]) == (False, 8, 12)
    assert any("must not promise a delivery date" in h for h in r["human_judgment"])


@pytest.mark.parametrize("price,ok", [(21.99, False), (22, True), (58, True), (58.01, False)])
def test_v15_quote_floor_and_ceiling(work, ident, price, ok):
    call = lambda: ops.propose_quote(work, ident("customer_service"), **{**QUOTE_103, "unit_price": price})
    if ok:
        assert call()["created"]
    else:
        with pytest.raises(ops.OpsError, match="policy:quote_(floor|cap)"):
            call()


@pytest.mark.parametrize("override", [{"qty": 19}, {"size": "L"}, {"sku": "CC-TEE-WHITE"}, {"ticket_id": 102}])
def test_v16_quote_must_match_ticket(work, ident, override):
    with pytest.raises(ops.OpsError, match="policy:quote_matches_ticket"):
        ops.propose_quote(work, ident("customer_service"), **{**QUOTE_103, **override})
    assert counts(work)["pending_actions"] == 0


def test_v17_quote_duplicates_vs_alternatives_and_closed_ticket(work, ident):
    first = ops.propose_quote(work, ident("customer_service"), **QUOTE_103)["links"]["action_id"]
    with pytest.raises(ops.OpsError) as err:
        ops.propose_quote(work, ident("customer_service"), **QUOTE_103)
    assert err.value.code == "DUPLICATE" and err.value.existing_id == first
    assert ops.propose_quote(work, ident("customer_service"), **{**QUOTE_103, "unit_price": 50})["proposal"]["alternative_to"] == [first]
    with privileged(work) as con:
        con.execute("UPDATE tickets SET status = 'closed' WHERE id = 103")
    with pytest.raises(ops.OpsError, match="not open"):
        ops.propose_quote(work, ident("customer_service"), **{**QUOTE_103, "unit_price": 40})


# ---- drafts -----------------------------------------------------------------------

def test_v18_draft_recipient_comes_from_stored_records(work, ident):
    assert "recipient" not in inspect.signature(ops.draft_message).parameters
    rent = ops.draft_message(work, ident("facilities"), ticket_id=102, subject="Rent notice",
                             body="Your rent notice was received and is under review.")
    tee = ops.draft_message(work, ident("customer_service"), ticket_id=101, subject="Your tee",
                            body="Thank you for your request; we are checking availability.")
    assert (rent["draft"]["recipient"], rent["draft"]["recipient_source"]) == ("Elm City Properties", "leases.landlord")
    assert (tee["draft"]["recipient"], tee["draft"]["recipient_source"]) == ("Tauhid Zaman", "tickets.requester")
    for r in (rent, tee):
        assert r["sent"] is False and r["recipient_contact"] is None and r["status"] == "draft"


def test_v19_requester_differing_from_landlord_is_flagged(work, ident):
    with privileged(work) as con:
        con.execute("UPDATE tickets SET requester = 'Someone Else' WHERE id = 102")
    r = ops.draft_message(work, ident("facilities"), ticket_id=102, subject="Rent",
                          body="Your rent notice was received and is under review.")
    assert r["draft"]["recipient"] == "Elm City Properties"
    assert any("differs from the lease landlord" in w for w in r["warnings"])


def test_v20_commitment_warnings_are_never_cleared_by_a_linked_proposal(work, ident):
    quote = ops.propose_quote(work, ident("customer_service"), **QUOTE_103)["links"]["action_id"]
    r = ops.draft_message(work, ident("customer_service"), ticket_id=103, subject="Hoodie quote",
                          body="We can offer $45.00 per hoodie and they will ship tomorrow.", related_action_id=quote)
    text = " | ".join(r["warnings"])
    assert "Matches quote proposal" in text and "NOT approved" in text and "warning stands" in text
    assert "'will ship'" in text and "'tomorrow'" in text
    with privileged(work) as con:   # even an approved proposal only annotates the warning
        con.execute("UPDATE pending_actions SET status = 'approved', decided_by = 'test' WHERE id = ?", (quote,))
    r2 = ops.draft_message(work, ident("customer_service"), ticket_id=103, subject="Hoodie quote 2",
                           body="We can offer $45.00 per hoodie for your order.", related_action_id=quote)
    assert any("$45.00" in w and "(status: approved); warning stands" in w for w in r2["warnings"])
    with privileged(work) as con:
        stored = json.loads(con.execute("SELECT warnings FROM drafts WHERE id = 1").fetchone()[0])
    assert stored == r["warnings"]


@pytest.mark.parametrize("override,code", [
    ({"related_action_id": 999}, "NOT_FOUND"), ({"body": "too short"}, "INVALID_INPUT"),
    ({"subject": ""}, "INVALID_INPUT"), ({"ticket_id": 999}, "NOT_FOUND"), ({"subject": "x" * 201}, "INVALID_INPUT"),
])
def test_v21_malformed_drafts_write_nothing(work, ident, override, code):
    base = dict(ticket_id=101, subject="Tee", body="Thanks for your order request.")
    with pytest.raises(ops.OpsError) as err:
        ops.draft_message(work, ident("customer_service"), **{**base, **override})
    assert err.value.code == code
    assert counts(work)["drafts"] == 0


def test_v22_draft_cannot_link_another_tickets_proposal(work, ident):
    pay = ops.propose_payment(work, ident("accounting"), **PAY_501)["links"]["action_id"]
    with pytest.raises(ops.OpsError) as err:
        ops.draft_message(work, ident("facilities"), ticket_id=102, subject="Rent",
                          body="Your rent notice was received.", related_action_id=pay)
    assert err.value.code == "INVALID_INPUT"


# ---- drafting authority (Problem 5 follow-up 9) -----------------------------------

@pytest.mark.parametrize("agent,ticket_id,delegate_to", [
    ("facilities", 101, "customer_service"), ("facilities", 103, "customer_service"),
    ("customer_service", 102, "facilities"),
])
def test_v23_agents_cannot_draft_outside_their_responsibility(work, ident, agent, ticket_id, delegate_to):
    with pytest.raises(ops.OpsError) as err:
        ops.draft_message(work, ident(agent), ticket_id=ticket_id, subject="Out of scope",
                          body="This message is outside my responsibility.")
    assert err.value.code == "NOT_PERMITTED" and f"delegate this communication to {delegate_to}" in err.value.message
    assert counts(work) == {"pending_actions": 0, "drafts": 0, "events": 0}


@pytest.mark.parametrize("agent,ticket_id,recipient", [
    ("customer_service", 101, "Tauhid Zaman"), ("customer_service", 103, "Yale AI Club"),
    ("facilities", 102, "Elm City Properties"),
])
def test_v23_agents_draft_within_their_responsibility(work, ident, agent, ticket_id, recipient):
    r = ops.draft_message(work, ident(agent), ticket_id=ticket_id, subject="Update",
                          body="Your request was received and is under review.")
    assert r["draft"]["recipient"] == recipient and r["sent"] is False
