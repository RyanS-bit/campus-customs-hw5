"""Problem 9, follow-up 36: two resolution bases.

  A  business_outcome             the person confirms the underlying business issue is resolved (unchanged; default)
  B  simulated_workflow_complete  the person confirms only that the desk's simulated workflow is complete, names what
                                  stays unverified, and the record says business_outcome_confirmed = false

Offline: temporary copies of the hw5 layout (conftest `work`), scripted models and the in-memory MCP launcher.
Test b14 only READS the real audit.db (the conftest session guard fails the run if it changes).
"""
import asyncio
import json
import sqlite3
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

import approval
import audit_db
import audit_trail
import desk_reads
import main
import maintenance
import ops
import paths
import runner
from agent_script import Scripted
from conftest import REAL_ROOT

H = {"X-Human-Decision": "confirm"}
SUMMARY = "Desk workflow complete in the simulation; delivery and customer acceptance are not recorded or verified."
B = {"decided_by": "Dr. Ryan", "confirm": True, "resolution_basis": "simulated_workflow_complete",
     "business_outcome_confirmed": False, "workflow_complete_confirmed": True,
     "unverified_outcomes": ["delivery", "customer_acceptance"], "resolution_summary": SUMMARY}
A = {"decided_by": "Dr. Ryan", "confirm": True, "business_outcome_confirmed": True,
     "resolution_summary": "The landlord confirmed receipt of the rent payment."}


def trail(work):
    return audit_trail.AuditTrail(work.audit_trail_json)


def completed_run(work, ticket_id):
    s = Scripted({"boss": [[("final", {"summary": f"ticket {ticket_id} reviewed"})]]})
    out = asyncio.run(runner.run_task(main.ticket_task(ticket_id), p=work, model=s.models(), mode="simulated",
                                      launcher=runner.in_memory_launcher, ticket_id=ticket_id))
    assert out.status == "completed"
    return out.run_id


def status(work, ticket_id):
    with closing(maintenance.connect_ro(work.working_db)) as con:
        return con.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()[0]


def resolved_events(work):
    if not work.audit_db.exists():                 # created on the first audited write
        return []
    with closing(maintenance.connect_ro(work.audit_db)) as con:
        return [json.loads(r[0]) for r in con.execute("SELECT payload FROM events WHERE event_type = 'ticket_resolved' ORDER BY id")]


@pytest.fixture
def client(work):
    with TestClient(main.create_app(p=work)) as c:
        yield c


# ---------------------------------------------------------------- B: allowed, and recorded as NOT a business outcome

def test_b1_simulated_workflow_resolution_records_what_was_and_was_not_confirmed(work, client):
    run_id = completed_run(work, 103)
    r = client.post("/api/tickets/103/resolve", json=B, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolution_basis"] == "simulated_workflow_complete" and body["business_outcome_confirmed"] is False
    assert body["unverified_outcomes"] == ["delivery", "customer_acceptance"]
    assert "did NOT confirm the business outcome" in body["meaning"] and "business_outcome_confirmed_by" not in body
    assert status(work, 103) == "resolved"
    ev = resolved_events(work)[-1]
    assert ev["resolution_basis"] == "simulated_workflow_complete" and ev["business_outcome_confirmed"] is False
    assert ev["workflow_complete_confirmed"] is True and ev["completed_runs"] == [run_id]
    assert ev["unverified_outcomes"] == ["delivery", "customer_acceptance"] and ev["resolution_summary"] == SUMMARY
    finished = [e for e in trail(work).read() if e["event_type"] == "human_decision_finished"][-1]
    assert finished["details"]["resolution_basis"] == "simulated_workflow_complete"
    assert finished["details"]["business_outcome_confirmed"] is False


def test_b2_ticket_reads_show_the_basis_and_never_claim_fulfilment(work, client):
    completed_run(work, 101)
    client.post("/api/tickets/101/resolve", json={**B, "unverified_outcomes": ["delivery"]}, headers=H)
    listed = {t["id"]: t for t in client.get("/api/tickets").json()["tickets"]}
    res = listed[101]["resolution"]
    assert res["basis"] == "simulated_workflow_complete" and res["business_outcome_confirmed"] is False
    assert res["unverified_outcomes"] == ["delivery"] and res["resolved_by"] == "Dr. Ryan"
    assert listed[102]["resolution"] is None and listed[103]["resolution"] is None          # open tickets: none
    detail = client.get("/api/tickets/101").json()["ticket"]
    assert detail["resolution"]["basis"] == "simulated_workflow_complete"


# ---------------------------------------------------------------- B: refused unless every requirement is met

@pytest.mark.parametrize("change, code", [
    ({"workflow_complete_confirmed": False}, "INVALID_INPUT"),
    ({"business_outcome_confirmed": True}, "INVALID_INPUT"),
    ({"unverified_outcomes": []}, "INVALID_INPUT"),
    ({"unverified_outcomes": ["delivery", "delivery"]}, "INVALID_INPUT"),
    ({"unverified_outcomes": ["teleported"]}, "INVALID_INPUT"),               # not in the fixed list (request model)
    ({"workflow_complete_confirmed": "yes"}, "INVALID_INPUT"),                # strict booleans
    ({"resolution_summary": "done"}, "INVALID_INPUT"),
    ({"resolution_basis": "auto"}, "INVALID_INPUT"),
])
def test_b3_simulated_basis_needs_explicit_confirmation_and_named_unverified_outcomes(work, client, change, code):
    completed_run(work, 103)
    r = client.post("/api/tickets/103/resolve", json={**B, **change}, headers=H)
    assert r.status_code == 422 and r.json()["error"]["code"] == code
    assert status(work, 103) == "open" and resolved_events(work) == []


def test_b4_simulated_basis_needs_a_completed_agent_run_for_that_ticket(work, client):
    completed_run(work, 102)                                                  # a run, but for another ticket
    r = client.post("/api/tickets/103/resolve", json=B, headers=H)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REFUSED_RULE" and "No completed agent run" in r.text
    assert status(work, 103) == "open" and resolved_events(work) == []


def test_b5_a_failed_run_does_not_count(work, client):
    s = Scripted({"boss": [[("raise", "simulated model failure")]]})
    out = asyncio.run(runner.run_task(main.ticket_task(103), p=work, model=s.models(), mode="simulated",
                                      launcher=runner.in_memory_launcher, ticket_id=103))
    assert out.status == "error"
    assert client.post("/api/tickets/103/resolve", json=B, headers=H).status_code == 409
    assert status(work, 103) == "open"


def test_b6_undecided_proposals_and_unexecuted_purchases_block_the_simulated_basis(work, client):
    completed_run(work, 101)
    ident = ops.RunIdentity(str(uuid.uuid4()), "inventory", "simulated")
    action = ops.propose_purchase(work, ident, vendor_id=1, sku="CC-TEE-WHITE", size="S", qty=1, ticket_id=101,
                                  rationale="One replacement tee")["links"]["action_id"]
    r = client.post("/api/tickets/101/resolve", json=B, headers=H)
    assert r.status_code == 409 and "still await a decision" in r.text              # existing rule, unchanged
    approval.approve_action(work, trail(work), action, decided_by="Dr. Ryan")         # authorized, not executed
    r = client.post("/api/tickets/101/resolve", json=B, headers=H)
    assert r.status_code == 409 and "authorized but not executed" in r.text
    approval.reject_action(work, trail(work), action, decided_by="Dr. Ryan", note="withdrawn")
    assert client.post("/api/tickets/101/resolve", json=B, headers=H).status_code == 200


# ---------------------------------------------------------------- A: unchanged, and kept apart from B

def test_b7_business_outcome_basis_is_unchanged_and_needs_no_run(work, client):
    r = client.post("/api/tickets/103/resolve", json=A, headers=H)
    assert r.status_code == 200 and r.json()["resolution_basis"] == "business_outcome"
    assert r.json()["business_outcome_confirmed_by"] == "Dr. Ryan"
    ev = resolved_events(work)[-1]
    assert ev["business_outcome_confirmed"] is True and ev["unverified_outcomes"] == [] and ev["completed_runs"] == []
    assert client.get("/api/tickets").json()["tickets"][2]["resolution"]["basis"] == "business_outcome"


@pytest.mark.parametrize("extra", [{"workflow_complete_confirmed": True}, {"unverified_outcomes": ["delivery"]}])
def test_b8_business_outcome_basis_refuses_simulated_fields(work, client, extra):
    r = client.post("/api/tickets/103/resolve", json={**A, **extra}, headers=H)
    assert r.status_code == 422 and status(work, 103) == "open"


def test_b9_the_function_refuses_bad_combinations_too(work):
    for kw in ({"resolution_basis": "simulated_workflow_complete", "workflow_complete_confirmed": True,
                "unverified_outcomes": ("delivery",), "business_outcome_confirmed": None},
               {"resolution_basis": "simulated_workflow_complete", "workflow_complete_confirmed": 1,
                "unverified_outcomes": ["delivery"]}):
        with pytest.raises(approval.DecisionError) as e:
            approval.resolve_ticket(work, trail(work), 103, decided_by="Dr. Ryan", resolution_summary=SUMMARY, **kw)
        assert e.value.code == "INVALID_INPUT"
    assert status(work, 103) == "open"


def test_b10_a_resolved_ticket_cannot_be_resolved_again_on_the_other_basis(work, client):
    completed_run(work, 103)
    assert client.post("/api/tickets/103/resolve", json=B, headers=H).status_code == 200
    r = client.post("/api/tickets/103/resolve", json=A, headers=H)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_PENDING"
    assert len(resolved_events(work)) == 1


# ---------------------------------------------------------------- reading earlier records

def test_b11_an_earlier_event_without_a_basis_reads_as_business_outcome(work):
    approval.resolve_ticket(work, trail(work), 102, decided_by="Dr. Ryan", business_outcome_confirmed=True,
                            resolution_summary="Rent paid; landlord informed.")
    with closing(maintenance.connect_rw(work.audit_db)) as con:                 # rewrite-free: add a legacy-shaped event
        con.execute("BEGIN IMMEDIATE")
        audit_db.append_event(con, actor="human:Earlier", actor_kind="human", event_type="ticket_resolved",
                              ref_table="tickets", ref_id=101, schema="main",
                              payload={"ticket_id": 101, "business_outcome_confirmed": True, "resolution_summary": "old form"})
        con.execute("COMMIT")
    res = desk_reads._resolutions(work)
    assert res[101]["basis"] == "business_outcome" and res[102]["basis"] == "business_outcome"


def test_b12_after_a_reset_old_resolutions_do_not_attach(work, client):
    completed_run(work, 103)
    client.post("/api/tickets/103/resolve", json=B, headers=H)
    approval.controlled_reset(work, trail(work), requested_by="Dr. Ryan", via="test")
    assert desk_reads._resolutions(work) == {}
    assert client.get("/api/tickets").json()["tickets"][2]["resolution"] is None


def test_b13_no_route_or_agent_tool_resolves_automatically(work):
    import server
    assert not any("resolve" in t for tools in server.ACCESS.values() for t in tools)
    assert [r for r in main.create_app(p=work).routes if "resolve" in getattr(r, "path", "")][0].methods == {"POST"}


def test_b14_the_real_ticket_102_resolution_reads_as_business_outcome():
    real = paths.resolve(env={}, root=REAL_ROOT, use_dotenv=False)            # read-only
    res = desk_reads._resolutions(real)
    assert res[102]["basis"] == "business_outcome" and res[102]["business_outcome_confirmed"] is True
    if 101 in res:                                   # resolved by Dr. Ryan on 2026-10-11 (follow-up 38): basis B only
        assert res[101]["basis"] == "simulated_workflow_complete" and res[101]["business_outcome_confirmed"] is False
        assert "delivery" in res[101]["unverified_outcomes"]
    for r in res.values():                           # the flag and the basis never disagree in the real records
        assert r["business_outcome_confirmed"] == (r["basis"] == "business_outcome")


# ---------------------------------------------------------------- follow-up 39: awaiting_human_action after resolution

def test_b15_a_resolved_ticket_no_longer_awaits_action_but_its_unsent_draft_is_kept_unchanged(work, client):
    body = "Size S is out of stock; we will write again when we have a firm update."
    s = Scripted({"boss": [[("delegate", "customer_service", "Draft an update")]],
                  "customer_service": [[("tool", "draft_message", {"ticket_id": 101, "subject": "Your tee", "body": body})]]})
    out = asyncio.run(runner.run_task(main.ticket_task(101), p=work, model=s.models(), mode="simulated",
                                      launcher=runner.in_memory_launcher, ticket_id=101))
    assert out.status == "completed"
    before = client.get(f"/api/runs/{out.run_id}").json()
    assert before["awaiting_human_action"] is True and len(before["drafts_unsent"]) == 1      # open: the draft awaits review
    r = client.post("/api/tickets/101/resolve", json={**B, "unverified_outcomes": ["delivery", "message_sent"]}, headers=H)
    assert r.status_code == 200
    after = client.get(f"/api/runs/{out.run_id}").json()
    assert after["awaiting_human_action"] is False and after["outcome"] == "ticket_resolved_by_human"
    assert after["drafts_unsent"] == before["drafts_unsent"]                                  # the draft is kept, unchanged
    with closing(maintenance.connect_ro(work.working_db)) as con:
        assert con.execute("SELECT body, status FROM drafts WHERE ticket_id = 101").fetchall() == [(body, "draft")]


def test_b16_an_open_ticket_with_only_an_unsent_draft_still_awaits_review(work, client):
    s = Scripted({"boss": [[("delegate", "customer_service", "Draft a reply")]],
                  "customer_service": [[("tool", "draft_message", {"ticket_id": 103, "subject": "Hoodies",
                                                                    "body": "We are reviewing your bulk request."})]]})
    out = asyncio.run(runner.run_task(main.ticket_task(103), p=work, model=s.models(), mode="simulated",
                                      launcher=runner.in_memory_launcher, ticket_id=103))
    view = client.get(f"/api/runs/{out.run_id}").json()
    assert view["ticket_status"] == "open" and view["awaiting_human_action"] is True
    assert view["outcome"] == "awaiting_human_review_of_drafts"
