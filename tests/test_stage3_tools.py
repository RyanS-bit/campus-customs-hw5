"""Stage 3: the ten MCP tools through FastMCP's in-memory client, on temporary databases.

Includes backward compatibility (C5): the original three tools must reproduce the outputs and
error text recorded in output/mcp_smoke.json exactly.
"""

import json
import shutil

import pytest

import ops
import paths
import server
from conftest import REAL_ROOT, counts
from mcp_helpers import call, text

SMOKE = json.loads((REAL_ROOT / "output" / "mcp_smoke.json").read_text(encoding="utf-8"))


@pytest.fixture
def dev(work):
    return server.create_server(None, work)


@pytest.fixture
def agent(work, ident):
    return lambda name: server.create_server(ident(name), work)


# ---- C5: backward compatibility ----------------------------------------------------

@pytest.mark.parametrize("prepared", [False, True], ids=["plain-copy", "after-reset"])
def test_c5_original_tools_match_problem4_evidence_exactly(sandbox, prepared):
    p = paths.resolve(env={}, root=sandbox, use_dotenv=False)
    if prepared:
        import maintenance
        maintenance.reset_working_db(p)
    else:
        shutil.copyfile(p.original_db, p.working_db)      # the state Problem 4 was recorded against
    srv = server.create_server(None, p)
    recorded = SMOKE["calls"] + [SMOKE["additional_verification_calls"]["calls"][1]]
    for c in recorded:
        r = call(srv, c["tool"], c["input"])
        assert not r.is_error and r.structured_content == c["output"], f"call {c['n']} differs"
    err = SMOKE["additional_verification_calls"]["calls"][0]
    r = call(srv, err["tool"], err["input"])
    assert r.is_error and text(r) == err["error_text"]


def test_c5_original_tool_descriptions_unchanged(dev):
    import asyncio
    from fastmcp import Client

    async def go():
        async with Client(dev) as c:
            return {t.name: t.description for t in await c.list_tools()}
    desc = asyncio.run(go())
    assert desc["check_inventory"].startswith("Stock on hand per size, with location, unit cost and list price.")
    assert desc["get_financial_position"].startswith("Cash on hand and what the shop owes")
    assert desc["get_open_tickets"].startswith("All open tickets, each with the invoice")


def test_new_read_tools_explain_an_unprepared_database(sandbox):
    p = paths.resolve(env={}, root=sandbox, use_dotenv=False)
    shutil.copyfile(p.original_db, p.working_db)
    srv = server.create_server(None, p)
    for tool, args in [("get_ticket", {"ticket_id": 101}), ("list_pending_actions", {})]:
        r = call(srv, tool, args)
        assert r.is_error and text(r).startswith("DB_UNAVAILABLE:")
    assert not call(srv, "list_vendors").is_error          # needs only original tables


# ---- new read tools -----------------------------------------------------------------

def test_get_ticket_shows_links_unknowns_and_work_state(work, dev, agent):
    t = call(dev, "get_ticket", {"ticket_id": 101}).structured_content
    assert t["ticket"]["linked_invoice"]["id"] == 501 and t["ticket"]["linked_invoice"]["vendor_name"] == "Bulldog Print Co"
    assert t["unknowns"] == ["Whether the goods billed on invoice #501 were delivered is not recorded."]
    assert t["pending_actions"] == [] and t["drafts"] == []
    call(agent("accounting"), "propose_payment",
         dict(kind="invoice", ref_id=501, amount=840, ticket_id=101, rationale="Pay overdue invoice 501"))
    call(agent("customer_service"), "draft_message",
         dict(ticket_id=101, subject="Your tee", body="We are checking availability for your size S tee."))
    t = call(dev, "get_ticket", {"ticket_id": 101}).structured_content
    assert [(a["kind"], a["status"], a["amount"], a["proposed_by"]) for a in t["pending_actions"]] == [
        ("payment", "proposed", 840.0, "accounting")]
    assert [(d["recipient"], d["status"], d["created_by"]) for d in t["drafts"]] == [
        ("Tauhid Zaman", "draft", "customer_service")]
    rent = call(dev, "get_ticket", {"ticket_id": 102}).structured_content
    assert rent["ticket"]["linked_lease"]["landlord"] == "Elm City Properties" and rent["unknowns"] == []


@pytest.mark.parametrize("args,prefix", [({"ticket_id": 999}, "NOT_FOUND:"), ({"ticket_id": "abc"}, "")])
def test_get_ticket_errors(dev, args, prefix):
    r = call(dev, "get_ticket", args)
    assert r.is_error and text(r).startswith(prefix)


def test_list_vendors_reports_blocks_and_hypothetical_arrival(dev):
    v = {x["id"]: x for x in call(dev, "list_vendors").structured_content["vendors"]}
    assert v[1]["blocked"] and v[1]["blocked_reason"].startswith("R3: open unpaid invoice(s) 501")
    assert v[1]["open_invoices"] == [{"id": 501, "amount": 840.0, "due_date": "2026-08-28", "days_overdue": 3}]
    assert v[1]["computed"]["earliest_arrival_if_ordered_today"] is None
    assert (v[2]["blocked"], v[2]["computed"]["earliest_arrival_if_ordered_today"]) == (False, "2026-09-03")
    assert (v[3]["blocked"], v[3]["computed"]["earliest_arrival_if_ordered_today"]) == (False, "2026-09-01")


def test_list_pending_actions_tracks_commitments_without_moving_cash(work, dev, agent):
    empty = call(dev, "list_pending_actions").structured_content
    assert empty["actions"] == [] and empty["computed"]["cash_if_all_proposed_payments_executed"] == 3400.0
    acc = agent("accounting")
    call(acc, "propose_payment", dict(kind="invoice", ref_id=501, amount=840, ticket_id=101, rationale="Pay overdue invoice 501"))
    call(acc, "propose_payment", dict(kind="lease", ref_id=1, amount=2400, ticket_id=102, rationale="Rent due 2026-09-02"))
    call(agent("inventory"), "propose_purchase",
         dict(vendor_id=1, sku="CC-HOOD-NAVY", size="M", qty=12, ticket_id=103, rationale="Cover the 12-unit shortfall"))
    r = call(dev, "list_pending_actions").structured_content
    c = r["computed"]
    assert (c["proposed_payments_total"], c["cash_if_all_proposed_payments_executed"], c["estimated_purchases_total"]) == (3240.0, 160.0, 264.0)
    assert r["cash"]["stored_balance"] == 3400.0
    purchase = [a for a in r["actions"] if a["kind"] == "purchase"][0]
    assert purchase["vendor_blocked"] is True and purchase["blocking_invoice_ids"] == [501]
    assert len(call(dev, "list_pending_actions", {"kind": "payment"}).structured_content["actions"]) == 2
    assert len(call(dev, "list_pending_actions", {"ticket_id": 103}).structured_content["actions"]) == 1
    assert call(dev, "list_pending_actions", {"ticket_id": 999}).is_error
    assert call(dev, "list_pending_actions", {"kind": "refund"}).is_error


# ---- write tools in agent mode ------------------------------------------------------

def test_authorized_proposals_and_drafts_through_mcp(work, agent):
    pay = call(agent("accounting"), "propose_payment",
               dict(kind="invoice", ref_id=501, amount=840, ticket_id=101, rationale="Pay overdue invoice 501")).structured_content
    buy = call(agent("inventory"), "propose_purchase",
               dict(vendor_id=1, sku="CC-HOOD-NAVY", size="M", qty=12, ticket_id=103, rationale="Cover the 12-unit shortfall")).structured_content
    quote = call(agent("customer_service"), "propose_quote",
                 dict(ticket_id=103, sku="CC-HOOD-NAVY", size="M", qty=20, unit_price=45, rationale="Bulk discount within policy")).structured_content
    rent = call(agent("facilities"), "draft_message",
                dict(ticket_id=102, subject="Rent notice", body="Your rent notice was received and is under review.")).structured_content
    assert pay["created"] and pay["executable"] is False and pay["links"]["action_id"] == 1
    assert buy["proposal"]["vendor_blocked"] is True
    assert quote["customer_acceptance"] == "not recorded"
    assert rent["sent"] is False and rent["draft"]["recipient"] == "Elm City Properties"
    assert counts(work) == {"pending_actions": 3, "drafts": 1, "events": 4}


@pytest.mark.parametrize("tool,agent_name,args,prefix", [
    ("propose_payment", "accounting", dict(kind="invoice", ref_id=501, amount=840.001, rationale="bad amount here"), "INVALID_INPUT:"),
    ("propose_payment", "accounting", dict(kind="invoice", ref_id=501, amount=800, rationale="partial payment try"), "REFUSED_RULE: policy:full_amount_only"),
    ("propose_payment", "accounting", dict(kind="invoice", ref_id=999, amount=840, rationale="unknown invoice try"), "NOT_FOUND:"),
    ("propose_quote", "customer_service", dict(ticket_id=103, sku="CC-HOOD-NAVY", size="M", qty=20, unit_price=21, rationale="below cost quote"), "REFUSED_RULE: policy:quote_floor"),
    ("propose_quote", "customer_service", dict(ticket_id=103, sku="CC-HOOD-NAVY", size="L", qty=20, unit_price=45, rationale="mismatched size"), "REFUSED_RULE: policy:quote_matches_ticket"),
    ("draft_message", "customer_service", dict(ticket_id=102, subject="Rent", body="Landlord message from the wrong agent."), "NOT_PERMITTED:"),
    ("draft_message", "facilities", dict(ticket_id=101, subject="Tee", body="Customer message from the wrong agent."), "NOT_PERMITTED:"),
])
def test_refusals_carry_stable_codes_and_write_nothing(work, agent, tool, agent_name, args, prefix):
    r = call(agent(agent_name), tool, args)
    assert r.is_error and text(r).startswith(prefix), text(r)
    assert counts(work) == {"pending_actions": 0, "drafts": 0, "events": 0}


def test_duplicate_through_mcp_names_the_existing_proposal(work, agent):
    args = dict(kind="invoice", ref_id=501, amount=840, ticket_id=101, rationale="Pay overdue invoice 501")
    acc = agent("accounting")
    assert not call(acc, "propose_payment", args).is_error
    r = call(acc, "propose_payment", args)
    assert r.is_error and text(r).startswith("DUPLICATE: Open payment proposal 1 already exists")
    assert counts(work)["pending_actions"] == 1
