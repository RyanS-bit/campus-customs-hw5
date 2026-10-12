"""Problem 9 F1 and F2: agent coordination instructions, proposal preparation, refusal of unsupported transactions,
and ticket resolution. OFFLINE: scripted models (PydanticAI FunctionModel), in-memory MCP, temporary databases.

What these tests can and cannot show: they verify the instructions the agents receive and that the system fully supports
(and polices) the intended delegation and proposal flow. They cannot predict what the live model will choose; that is
shown only by the live runs, which are recorded as observed.
"""

import asyncio
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

import agents
import approval
import audit_trail
import main
import maintenance
import runner
from agent_script import AGENTS, Scripted

RULE_11 = "Make human decisions actionable, but only when the records support it"
PAY_501 = {"kind": "invoice", "ref_id": 501, "amount": 840, "ticket_id": 101,
           "rationale": "Invoice 501 is overdue and blocks Bulldog Print Co; delivery of the invoiced tees is not recorded."}
H = {"X-Human-Decision": "confirm"}
ME = {"decided_by": "Dr. Ryan", "confirm": True}


def run(work, script, task="Handle desk ticket 101 only.", ticket_id=101):
    return asyncio.run(runner.run_task(task, p=work, model=script.models(), mode="simulated",
                                       launcher=runner.in_memory_launcher, run_id=str(uuid.uuid4()), ticket_id=ticket_id))


def rows(work, sql):
    with closing(maintenance.connect_ro(work.working_db)) as con:
        return con.execute(sql).fetchall()


def finished_calls(work, run_id):
    return [e for e in audit_trail.AuditTrail(work.audit_trail_json).read()
            if e["run_id"] == run_id and e["event_type"] == "tool_call_finished"]


# ---------------------------------------------------------------- the instructions (F1)

def test_c1_the_actionability_rule_is_shared_by_all_five_agents():
    shared = {a: agents.load_prompt(a).split("## Rules", 1)[1] for a in AGENTS}
    assert len(set(shared.values())) == 1
    rule = shared["boss"]
    assert RULE_11 in rule
    for phrase in ("prepares that proposal or draft", "is not a\nrecommendation to approve".replace("\n", " "),
                   "Never manufacture", "no invented amounts, prices, suppliers, quantities, or facts",
                   "nothing created only to show activity", "say so under `needs_human` and `unknowns`"):
        assert phrase in rule, phrase


def test_c2_the_boss_routes_by_the_business_problem():
    boss = agents.load_prompt("boss").split("## Rules")[0]
    assert "route by the actual business problem" in boss
    assert "open invoice linked to the ticket, even when the ticket is a customer order" in boss
    for specialist in ("**Accounting:**", "**Inventory:**", "**Customer Service:**", "**Facilities:**"):
        assert specialist in boss
    assert "to none only for activity" in boss
    assert "You are read-only" in boss                                   # the Boss still creates nothing itself


def test_c3_each_specialist_knows_what_to_prepare_and_what_not_to():
    acc = agents.load_prompt("accounting").split("## Rules")[0]
    assert "prepare the full-amount payment proposal" in acc and "Deciding whether to pay belongs to the person" in acc
    assert "Do not propose amounts that are not stored" in acc
    inv = agents.load_prompt("inventory").split("## Rules")[0]
    assert "Propose a purchase only when the records support it" in inv
    assert "do not propose a second order" in inv and "unconfirmed" in inv
    cs = agents.load_prompt("customer_service").split("## Rules")[0]
    assert "prepare an unsent draft to the requester that states only recorded facts" in cs
    assert "Propose a quote only when the ticket asks for pricing" in cs
    fac = agents.load_prompt("facilities").split("## Rules")[0]
    assert "prepare that landlord draft" in fac and "delegate to Accounting" in fac


# ---------------------------------------------------------------- delegation and proposal preparation are supported

def test_c4_a_coordinated_101_run_prepares_actionable_items_attributed_to_the_right_agents(work):
    script = Scripted({
        "boss": [[("tool", "get_ticket", {"ticket_id": 101}),
                  ("delegate", "accounting", "Open invoice 501 is linked to ticket 101; prepare its payment for a person to decide"),
                  ("delegate", "inventory", "Check size S stock and suppliers for ticket 101"),
                  ("delegate", "customer_service", "Prepare an unsent draft telling the customer where ticket 101 stands"),
                  ("final", {"summary": "Payment proposal and customer draft prepared; nothing executed",
                             "needs_human": ["Decide the #501 payment", "Review the customer draft"]})]],
        "accounting": [[("tool", "propose_payment", PAY_501), ("final", {"summary": "Payment of #501 proposed"})]],
        "inventory": [[("tool", "check_inventory", {"sku": "CC-TEE-WHITE"}),
                       ("final", {"summary": "Size S is 0; #501 may already cover the reprint (fulfilment not recorded); no second order"})]],
        "customer_service": [[("tool", "draft_message", {"ticket_id": 101, "subject": "Your size S tee",
                                                         "body": "Size S is not available right now. We will update you on the restock."}),
                              ("final", {"summary": "Customer draft prepared"})]],
    })
    out = run(work, script)
    assert out.status == "completed"
    evs = [e for e in audit_trail.AuditTrail(work.audit_trail_json).read() if e["run_id"] == out.run_id]
    assert [(e["actor"], e["details"]["to"]) for e in evs if e["event_type"] == "delegation_requested"] == \
           [("boss", "accounting"), ("boss", "inventory"), ("boss", "customer_service")]
    assert rows(work, "SELECT kind, invoice_id, amount, proposed_by, status, ticket_id FROM pending_actions") == \
           [("payment", 501, 840.0, "accounting", "proposed", 101)]
    assert rows(work, "SELECT recipient, created_by, ticket_id FROM drafts") == [("Tauhid Zaman", "customer_service", 101)]
    assert rows(work, "SELECT COUNT(*) FROM pending_actions WHERE kind = 'purchase'") == [(0,)]   # no second order
    # Prepared, not executed: no money moved and the ticket is not resolved.
    assert rows(work, "SELECT balance FROM cash_accounts") == [(3400.0,)]
    assert rows(work, "SELECT COUNT(*) FROM payments") == [(0,)]
    assert rows(work, "SELECT status FROM tickets WHERE id = 101") == [("open",)]


# ---------------------------------------------------------------- unsupported transactions are refused, and nothing is created

@pytest.mark.parametrize("agent,tool,args,code", [
    ("accounting", "propose_payment", {**PAY_501, "amount": 500}, "REFUSED_RULE"),                  # not the stored amount
    ("accounting", "propose_payment", {**PAY_501, "ref_id": 999}, "NOT_FOUND"),                     # no such obligation
    ("inventory", "propose_purchase", {"vendor_id": 99, "sku": "CC-TEE-WHITE", "size": "S", "qty": 1, "ticket_id": 101,
                                       "rationale": "Invented supplier for testing"}, "NOT_FOUND"),  # supplier not recorded
    ("inventory", "propose_purchase", {"vendor_id": 1, "sku": "CC-MUG", "size": "S", "qty": 1, "ticket_id": 101,
                                       "rationale": "Invented item for testing"}, "NOT_FOUND"),      # item not recorded
    ("customer_service", "propose_quote", {"ticket_id": 103, "sku": "CC-HOOD-NAVY", "size": "M", "qty": 20, "unit_price": 10,
                                           "rationale": "Below cost for testing"}, "REFUSED_RULE"), # below unit cost
    ("customer_service", "propose_quote", {"ticket_id": 103, "sku": "CC-HOOD-NAVY", "size": "M", "qty": 25, "unit_price": 45,
                                           "rationale": "Quantity not requested"}, "REFUSED_RULE"), # not what the ticket asks
    ("customer_service", "draft_message", {"ticket_id": 102, "subject": "Rent", "body": "A message to the landlord for testing."},
     "NOT_PERMITTED"),                                                                                  # not its counterparty
])
def test_c5_unsupported_transactions_are_refused_by_the_tools_and_create_nothing(work, agent, tool, args, code):
    script = Scripted({"boss": [[("delegate", agent, "prepare it")]], agent: [[("tool", tool, args)]]})
    out = run(work, script)
    assert out.status == "completed"
    [call] = [c for c in finished_calls(work, out.run_id) if c["tool"] == tool]
    assert (call["actor"], call["outcome"]["status"], call["outcome"]["code"]) == (agent, "refused", code)
    assert rows(work, "SELECT COUNT(*) FROM pending_actions") == [(0,)] and rows(work, "SELECT COUNT(*) FROM drafts") == [(0,)]
    assert rows(work, "SELECT balance FROM cash_accounts") == [(3400.0,)]


def test_c6_the_boss_cannot_prepare_anything_itself(work):
    script = Scripted({"boss": [[("tool", "propose_payment", PAY_501)]]})
    out = run(work, script)
    assert rows(work, "SELECT COUNT(*) FROM pending_actions") == [(0,)]
    assert out.status in ("completed", "error")                        # the tool simply does not exist for the Boss


# ---------------------------------------------------------------- ticket resolution after a finished run (F2)

def test_c7_a_finished_run_never_resolves_the_ticket_and_resolution_stays_a_human_step(work):
    def factory():
        async def start(ticket_id, run_id):
            script = Scripted({"boss": [[("delegate", "accounting", "prepare #501")]],
                               "accounting": [[("tool", "propose_payment", PAY_501)]]})
            return await runner.run_task(main.ticket_task(ticket_id), p=work, model=script.models(), mode="simulated",
                                         launcher=runner.in_memory_launcher, run_id=run_id, ticket_id=ticket_id)
        return start
    with TestClient(main.create_app(p=work, run_factory=factory(), run_mode="simulated")) as c:
        rid = c.post("/api/tickets/101/run", headers=H).json()["run_id"]
        import time
        for _ in range(300):
            view = c.get(f"/api/runs/{rid}").json()
            if view["phase"] not in ("queued", "active"):
                break
            time.sleep(0.05)
        assert view["phase"] == "completed" and view["outcome"] == "awaiting_human_approval"
        assert view["ticket_status"] == "open" and not view["ticket_resolved"]
        # Resolution needs every proposal decided first, then an explicit business confirmation.
        resolve = {**ME, "business_outcome_confirmed": True, "resolution_summary": "Invoice settled; customer told size S is back-ordered."}
        assert c.post("/api/tickets/101/resolve", json=resolve, headers=H).json()["error"]["code"] == "REFUSED_RULE"
        action = view["proposals_awaiting_decision"][0]["id"]
        assert c.post(f"/api/actions/{action}/reject", json=ME, headers=H).status_code == 200
        assert c.get("/api/tickets/101").json()["ticket"]["status"] == "open"           # deciding is not resolving
        assert c.post("/api/tickets/101/resolve", json={**ME, "resolution_summary": "x" * 20}, headers=H).status_code == 422
        r = c.post("/api/tickets/101/resolve", json=resolve, headers=H)
        assert r.status_code == 200 and r.json()["status"] == "resolved"
        assert c.get(f"/api/runs/{rid}").json()["outcome"] == "ticket_resolved_by_human"


def test_c8_resolution_is_still_impossible_for_agents(work):
    # No MCP tool for any agent can change ticket status; the human route requires a person's confirmation.
    import server
    assert all("status" not in t and "resolve" not in t for tools in server.ACCESS.values() for t in tools)
    with pytest.raises(approval.DecisionError):
        approval.resolve_ticket(work, audit_trail.AuditTrail(work.audit_trail_json), 101, decided_by="accounting",
                                business_outcome_confirmed=True, resolution_summary="An agent name is not a person")
