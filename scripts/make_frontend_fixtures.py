"""Record real backend responses for the dashboard's offline tests (Problem 8).

Run from hw5/:  .venv/Scripts/python scripts/make_frontend_fixtures.py

The FastAPI app (backend/main.py) runs IN-PROCESS on a TEMPORARY copy of the original database, with
SCRIPTED OFFLINE agents (PydanticAI FunctionModel, the same helper the backend tests use). No server is
started, no model or network is called, and the real data/ and output/ files are never opened for writing.

Output: frontend/src/test/fixtures/backend_samples.json. These are genuine API responses from a sandbox
storyline, so the dashboard's TypeScript types and tests follow the real contract. They are NOT evidence
of real ticket work (Problem 9 does that with live, authorized runs).
"""

import asyncio
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

HW5 = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HW5 / "mcp_server"), str(HW5 / "backend"), str(HW5 / "tests")]
os.environ.pop("CC_LIVE_AUTHORIZATION", None)

from fastapi.testclient import TestClient  # noqa: E402

import audit_trail  # noqa: E402
import main  # noqa: E402
import maintenance  # noqa: E402
import paths  # noqa: E402
import runner  # noqa: E402
from agent_script import Scripted  # noqa: E402

OUT = HW5 / "frontend" / "src" / "test" / "fixtures" / "backend_samples.json"
H = {"X-Human-Decision": "confirm"}
ME = {"decided_by": "Dr. Ryan", "confirm": True}

SCRIPTS = {
    101: {
        "boss": [[("tool", "get_ticket", {"ticket_id": 101}),
                  ("delegate", "inventory", "Check size S tee stock and the restock path for ticket 101"),
                  ("delegate", "accounting", "Review invoice 501 and prepare its payment if appropriate"),
                  ("final", {"summary": "Size S tees are out of stock. Bulldog Print Co is blocked by unpaid invoice "
                                        "#501; a payment proposal for $840 awaits human approval.",
                             "needs_human": ["Approve or reject payment proposal for invoice #501"],
                             "unknowns": ["Whether the tees billed on invoice #501 were delivered is not recorded"],
                             "delegations": ["inventory: stock and vendor status", "accounting: invoice #501"]})]],
        "inventory": [[("tool", "check_inventory", {"sku": "CC-TEE-WHITE"}), ("tool", "list_vendors", {}),
                       ("final", {"summary": "Size S on hand is 0. Bulldog Print Co is blocked by open invoice #501; "
                                             "no second order proposed until fulfillment is clarified."})]],
        "accounting": [[("tool", "get_financial_position", {}),
                        ("tool", "propose_payment", {"kind": "invoice", "ref_id": 501, "amount": 840, "ticket_id": 101,
                                                     "rationale": "Invoice 501 is 3 days overdue and blocks the vendor"}),
                        ("final", {"summary": "Payment proposal created for invoice #501 ($840)."})]],
    },
    102: {
        "boss": [[("tool", "get_ticket", {"ticket_id": 102}),
                  ("delegate", "facilities", "Draft an acknowledgement to the landlord for ticket 102"),
                  ("delegate", "accounting", "Prepare the September rent payment for lease 1"),
                  ("final", {"summary": "Rent of $2,400 is due 2026-09-02. A payment proposal and an unsent draft to "
                                        "Elm City Properties await human review.",
                             "needs_human": ["Approve or reject the rent payment", "Review the unsent landlord draft"]})]],
        "facilities": [[("tool", "draft_message", {"ticket_id": 102, "subject": "Re: Rent due",
                                                   "body": "Thank you for the reminder. The rent payment is being reviewed internally."}),
                        ("final", {"summary": "Unsent draft to the landlord stored for review."})]],
        "accounting": [[("tool", "propose_payment", {"kind": "lease", "ref_id": 1, "amount": 2400, "ticket_id": 102,
                                                     "rationale": "Monthly rent for the Chapel Street shop"}),
                        ("final", {"summary": "Rent payment proposal created ($2,400)."})]],
    },
    103: {
        "boss": [[("tool", "get_ticket", {"ticket_id": 103}),
                  ("delegate", "customer_service", "Prepare a quote for 20 navy hoodies, size M, for ticket 103"),
                  ("final", {"summary": "8 of 20 hoodies are in stock (12 short). A $45 quote and a restock proposal "
                                        "await human decisions; no vendor is confirmed to supply navy hoodies.",
                             "estimates": ["Restock of 12 at unit cost $22 = $264 (estimate, not a vendor quote)"],
                             "needs_human": ["Decide on the $45 quote", "Decide on the restock proposal"]})]],
        "customer_service": [[("tool", "check_inventory", {"sku": "CC-HOOD-NAVY"}),
                              ("tool", "propose_quote", {"ticket_id": 103, "sku": "CC-HOOD-NAVY", "size": "M", "qty": 20,
                                                         "unit_price": 45, "rationale": "Bulk discount within the price policy"}),
                              ("delegate", "inventory", "Propose a restock of 12 navy hoodies, size M"),
                              ("final", {"summary": "Quote proposal at $45 per hoodie created."})]],
        "inventory": [[("tool", "propose_purchase", {"vendor_id": 1, "sku": "CC-HOOD-NAVY", "size": "M", "qty": 12,
                                                     "ticket_id": 103, "rationale": "Cover the 12-unit shortfall"}),
                       ("final", {"summary": "Restock proposal created; the vendor is blocked by invoice #501."})]],
    },
}


def sandbox() -> paths.Paths:
    root = Path(tempfile.mkdtemp(prefix="cc_fixtures_")) / "hw5"
    (root / "data").mkdir(parents=True)
    (root / "output").mkdir()
    shutil.copyfile(HW5 / "data" / paths.ORIGINAL_NAME, root / "data" / paths.ORIGINAL_NAME)
    os.chmod(root / "data" / paths.ORIGINAL_NAME, stat.S_IREAD)
    p = paths.resolve(env={}, root=root, use_dotenv=False)
    assert HW5 not in p.working_db.parents
    maintenance.reset_working_db(p)
    return p


def main_() -> None:
    p = sandbox()
    current = {}

    async def factory(ticket_id, run_id):
        return await runner.run_task(main.ticket_task(ticket_id), p=p, model=current["script"].models(), mode="simulated",
                                     launcher=runner.in_memory_launcher, run_id=run_id, ticket_id=ticket_id)

    s: dict = {}

    def rec(name, resp):
        s[name] = {"status": resp.status_code, "body": resp.json()}
        return resp.json()

    def wait(c, run_id):
        while True:
            v = c.get(f"/api/runs/{run_id}").json()
            if v["phase"] not in ("queued", "active"):
                return
            time.sleep(0.05)

    with TestClient(main.create_app(p=p)) as off:
        rec("index_runs_disabled", off.get("/"))
        rec("run_refused_not_authorized", off.post("/api/tickets/101/run", headers=H))

    with TestClient(main.create_app(p=p, run_factory=factory, run_mode="simulated")) as c:
        rec("index", c.get("/"))
        rec("tickets_initial", c.get("/api/tickets"))
        for t in (101, 102, 103):
            rec(f"ticket_{t}_initial", c.get(f"/api/tickets/{t}"))
        rec("cash_initial", c.get("/api/cash"))
        rec("pending_initial", c.get("/api/actions/pending"))
        rec("audit_initial", c.get("/api/audit"))
        rec("run_missing_header", c.post("/api/tickets/101/run"))
        rec("run_unknown_ticket", c.post("/api/tickets/9999/run", headers=H))

        for t in (101, 102, 103):
            current["script"] = Scripted(SCRIPTS[t])
            acc = rec(f"run_{t}_accepted", c.post(f"/api/tickets/{t}/run", headers=H))
            wait(c, acc["run_id"])
            rec(f"run_{t}_completed", c.get(f"/api/runs/{acc['run_id']}"))
            rec(f"events_{t}", c.get(f"/api/events?run_id={acc['run_id']}"))
            rec(f"ticket_{t}_after_run", c.get(f"/api/tickets/{t}"))
        rec("events_page_first", c.get("/api/events?after_seq=0&limit=5"))
        rec("tickets_after_runs", c.get("/api/tickets"))
        pend = rec("pending_after_runs", c.get("/api/actions/pending"))
        rec("cash_after_runs", c.get("/api/cash"))
        ids = {(a["kind"], a["ticket_id"]): a["id"] for a in pend["actions"]}
        pay501, rent = ids[("payment", 101)], ids[("payment", 102)]
        quote, buy = ids[("quote", 103)], ids[("purchase", 103)]

        rec("resolve_103_refused_undecided", c.post("/api/tickets/103/resolve", headers=H, json={
            **ME, "business_outcome_confirmed": True, "resolution_summary": "Customer accepted the quote by email"}))
        # Problem 9 F3: approval is an authorization only (allowed while Bulldog is blocked, with an R3 warning);
        # execution spends cash and is refused while the vendor is blocked or cash is insufficient.
        rec("approve_purchase_blocked_vendor", c.post(f"/api/actions/{buy}/approve", json=ME, headers=H))
        rec("ticket_103_purchase_approved", c.get("/api/tickets/103"))
        rec("cash_with_approved_purchase", c.get("/api/cash"))
        execute_264 = {**ME, "amount_basis": "recorded_unit_cost_estimate", "amount": 264, "amount_confirmed": True,
                       "supplier_confirmed": True}
        rec("execute_purchase_refused_r3", c.post(f"/api/actions/{buy}/execute", json=execute_264, headers=H))
        rec("execute_purchase_missing_confirmation", c.post(f"/api/actions/{buy}/execute", headers=H,
                                                            json={**execute_264, "supplier_confirmed": False}))
        rec("approve_invalid_input", c.post(f"/api/actions/{pay501}/approve", json={"decided_by": "Dr. Ryan"}, headers=H))
        rec("approve_payment_501", c.post(f"/api/actions/{pay501}/approve", json=ME, headers=H))
        rec("approve_payment_501_again", c.post(f"/api/actions/{pay501}/approve", json=ME, headers=H))
        rec("cash_after_501", c.get("/api/cash"))
        rec("ticket_103_after_501_paid", c.get("/api/tickets/103"))      # purchase proposed while blocked; vendor not blocked NOW
        rec("approve_quote", c.post(f"/api/actions/{quote}/approve", json=ME, headers=H))
        rec("approve_rent", c.post(f"/api/actions/{rent}/approve", json=ME, headers=H))   # obligations first: allowed
        rec("cash_after_rent", c.get("/api/cash"))
        rec("execute_purchase_refused_cash", c.post(f"/api/actions/{buy}/execute", json=execute_264, headers=H))
        rec("reject_purchase", c.post(f"/api/actions/{buy}/reject", json={**ME, "note": "No confirmed hoodie supplier"}, headers=H))
        rec("ticket_103_purchase_withdrawn", c.get("/api/tickets/103"))  # authorized earlier, then withdrawn; never paid
        rec("resolve_102", c.post("/api/tickets/102/resolve", headers=H, json={
            **ME, "business_outcome_confirmed": True, "resolution_summary": "Landlord confirmed receipt of the September rent"}))
        rec("tickets_after_decisions", c.get("/api/tickets"))
        rec("ticket_102_resolved", c.get("/api/tickets/102"))
        rec("pending_after_decisions", c.get("/api/actions/pending"))
        rec("run_resolved_ticket", c.post("/api/tickets/102/run", headers=H))
        rec("audit_after_decisions", c.get("/api/audit"))

        current["script"] = Scripted({"boss": [[("raise", "model unavailable")]]})
        acc = rec("run_failed_accepted", c.post("/api/tickets/101/run", headers=H))
        wait(c, acc["run_id"])
        rec("run_failed", c.get(f"/api/runs/{acc['run_id']}"))

        # Follow-up 36: resolution basis B (simulated workflow complete; fulfilment NOT verified) on ticket 101,
        # whose proposal was executed above and which has a completed run.
        rec("resolve_101_simulated", c.post("/api/tickets/101/resolve", headers=H, json={
            **ME, "resolution_basis": "simulated_workflow_complete", "business_outcome_confirmed": False,
            "workflow_complete_confirmed": True, "unverified_outcomes": ["delivery", "customer_acceptance"],
            "resolution_summary": "Invoice #501 paid in the simulation; delivery of the size S tee and customer acceptance "
                                  "are not recorded or verified"}))
        rec("tickets_after_simulated_resolution", c.get("/api/tickets"))
        rec("ticket_101_resolved_simulated", c.get("/api/tickets/101"))

    rid = str(uuid.uuid4())
    audit_trail.AuditTrail(p.audit_trail_json).append([audit_trail.make_event(
        "run_started", actor="system", actor_kind="system", mode="simulated", run_id=rid,
        details={"ticket_id": 101, "entry_agent": "boss", "note": "fixture: a run whose process stopped"})])
    with TestClient(main.create_app(p=p)) as c:
        rec("run_interrupted", c.get(f"/api/runs/{rid}"))

    # A second sandbox for a SUCCESSFUL purchase execution (proposals created through the agents' own ops functions).
    p2 = sandbox()
    import ops
    agent = lambda name: ops.RunIdentity(str(uuid.uuid4()), name, "simulated")  # noqa: E731
    buy2 = ops.propose_purchase(p2, agent("inventory"), vendor_id=1, sku="CC-TEE-WHITE", size="S", qty=10, ticket_id=101,
                                rationale="Restock size S tees (estimate, supplier unconfirmed)")["links"]["action_id"]
    pay2 = ops.propose_payment(p2, agent("accounting"), kind="invoice", ref_id=501, amount=840, ticket_id=101,
                               rationale="Invoice 501 is overdue and blocks the vendor")["links"]["action_id"]
    with TestClient(main.create_app(p=p2)) as c:
        rec("approve_tee_purchase", c.post(f"/api/actions/{buy2}/approve", json=ME, headers=H))
        rec("approve_payment_501_sandbox2", c.post(f"/api/actions/{pay2}/approve", json=ME, headers=H))
        execute_80 = {**ME, "amount_basis": "recorded_unit_cost_estimate", "amount": 80, "amount_confirmed": True,
                      "supplier_confirmed": True, "note": "Bulldog confirmed it can reprint size S"}
        rec("execute_purchase_ok", c.post(f"/api/actions/{buy2}/execute", json=execute_80, headers=H))
        rec("execute_purchase_again", c.post(f"/api/actions/{buy2}/execute", json=execute_80, headers=H))
        plain = ops.propose_purchase(p2, agent("inventory"), vendor_id=1, sku="CC-TEE-WHITE", size="S", qty=4, ticket_id=101,
                                     rationale="A smaller alternative restock (estimate)")["links"]["action_id"]
        rec("reject_plain_purchase", c.post(f"/api/actions/{plain}/reject", json={**ME, "note": "Not needed"}, headers=H))
        rec("cash_after_purchase", c.get("/api/cash"))
        rec("ticket_101_purchase_executed", c.get("/api/tickets/101"))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = {"_about": {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "generated_by": "scripts/make_frontend_fixtures.py",
                      "note": "Real responses from backend/main.py run in-process on a TEMPORARY database with SCRIPTED "
                              "OFFLINE agents. Test fixtures only; not evidence of real ticket work."},
           **s}
    OUT.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(s)} samples to {OUT.relative_to(HW5)}")


if __name__ == "__main__":
    main_()
