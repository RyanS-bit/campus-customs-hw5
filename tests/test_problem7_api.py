"""Problem 7: the FastAPI backend (backend/main.py) and the human decision path (mcp_server/approval.py).

Every test runs against an isolated temporary copy of the hw5 layout (conftest `work`). Agent runs use the
Problem 5 runner with SCRIPTED OFFLINE models (PydanticAI FunctionModel) and the in-memory MCP launcher:
no network, no model API, no Portkey. The session guard in conftest fails the run if any real database
file or the real output/audit_trail.json changes.
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

import approval
import audit_db
import audit_reconcile
import audit_trail
import locks
import main
import maintenance
import ops
import paths
import runner
from agent_script import Scripted
from conftest import REAL_ROOT, privileged, protected_digest
from models import RunBudget

H = {"X-Human-Decision": "confirm"}
ME = {"decided_by": "Dr. Ryan", "confirm": True}
RESOLVE = {**ME, "business_outcome_confirmed": True, "resolution_summary": "Landlord confirmed receipt of the rent payment"}
PAY_501 = dict(kind="invoice", ref_id=501, amount=840, ticket_id=101, rationale="Pay overdue invoice 501")
PAY_RENT = dict(kind="lease", ref_id=1, amount=2400, ticket_id=102, rationale="Pay rent due 2026-09-02")
BUY_TEES = dict(vendor_id=1, sku="CC-TEE-WHITE", size="S", qty=10, ticket_id=101, rationale="Restock size S tees")
QUOTE_45 = dict(ticket_id=103, sku="CC-HOOD-NAVY", size="M", qty=20, unit_price=45, rationale="Bulk discount within policy")


# ---------------------------------------------------------------- helpers

def scripted_factory(work, script, budget=None):
    async def start(ticket_id, run_id):
        return await runner.run_task(main.ticket_task(ticket_id), p=work, model=script.models(), mode="simulated",
                                     launcher=runner.in_memory_launcher, budget=budget, run_id=run_id, ticket_id=ticket_id)
    return start


@pytest.fixture
def make_client(work):
    opened = []

    def make(factory=None, mode="simulated"):
        c = TestClient(main.create_app(p=work, run_factory=factory, run_mode=mode if factory else None))
        c.__enter__()
        opened.append(c)
        return c
    yield make
    for c in opened:
        c.__exit__(None, None, None)


def agent(work, name):
    return ops.RunIdentity(str(uuid.uuid4()), name, "simulated")


def propose(work, op, name, args):
    return getattr(ops, op)(work, agent(work, name), **args)["links"]["action_id"]


def wait_run(c, run_id, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = c.get(f"/api/runs/{run_id}").json()
        if view["phase"] not in ("queued", "active"):
            return view
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish")


def db(work, sql, args=()):
    with closing(maintenance.connect_ro(work.working_db)) as con:
        return con.execute(sql, args).fetchall()


def cash(work):
    return db(work, "SELECT balance FROM cash_accounts WHERE name = 'checking'")[0][0]


def trail(work):
    return audit_trail.AuditTrail(work.audit_trail_json).read()


def audit_events(work):
    with closing(maintenance.connect_ro(work.audit_db)) as con:
        return con.execute("SELECT id, actor, actor_kind, event_type, call_id, ref_table, ref_id, payload FROM events ORDER BY id").fetchall()


def ticket101_script():
    return Scripted({
        "boss": [[("tool", "get_ticket", {"ticket_id": 101}),
                  ("delegate", "inventory", "Check size S tee stock and the restock path"),
                  ("delegate", "accounting", "Prepare payment of invoice 501"),
                  ("final", {"summary": "Size S is out; payment of #501 proposed; a human must approve it",
                             "needs_human": ["Approve or reject the payment proposal"]})]],
        "inventory": [[("tool", "check_inventory", {"sku": "CC-TEE-WHITE"}), ("tool", "list_vendors", {}),
                       ("final", {"summary": "Size S on hand is 0; Bulldog is blocked by #501"})]],
        "accounting": [[("tool", "get_financial_position", {}), ("tool", "propose_payment", PAY_501),
                        ("final", {"summary": "Payment proposal created for #501"})]],
    })


# ---------------------------------------------------------------- 1-2: tickets

def test_p7_01_lists_the_three_tickets_with_status_from_the_database(work, make_client):
    c = make_client()
    r = c.get("/api/tickets")
    assert r.status_code == 200
    body = r.json()
    rows = db(work, "SELECT id, requester, subject, status FROM tickets ORDER BY id")
    assert [(t["id"], t["requester"], t["subject"], t["status"]) for t in body["tickets"]] == [tuple(x) for x in rows]
    assert [t["id"] for t in body["tickets"]] == [101, 102, 103]
    assert all(t["is_open"] and not t["is_resolved"] for t in body["tickets"])
    assert (body["open_count"], body["resolved_count"], body["business_date"]) == (3, 0, "2026-08-31")


def test_p7_02_ticket_status_is_read_live_not_hardcoded(work, make_client):
    c = make_client()
    with privileged(work) as con:
        con.execute("UPDATE tickets SET status = 'resolved' WHERE id = 102")       # test setup on the sandbox only
    t = {x["id"]: x for x in c.get("/api/tickets").json()["tickets"]}
    assert t[102]["status"] == "resolved" and t[102]["is_resolved"] and not t[102]["is_open"]
    assert t[101]["is_open"] and c.get("/api/tickets").json()["resolved_count"] == 1


def test_p7_03_ticket_detail_and_invalid_ids(work, make_client):
    c = make_client()
    d = c.get("/api/tickets/101").json()
    assert d["ticket"]["linked_invoice"]["id"] == 501 and d["ticket"]["linked_invoice"]["description"] == "Rush reprint CC-TEE-WHITE S"
    assert d["pending_actions"] == [] and d["drafts"] == [] and d["runs"] == []
    assert c.get("/api/tickets/102").json()["ticket"]["linked_lease"]["monthly_rent"] == 2400
    # Problem 8: read-only inventory facts for the ticket's product (stored values; none for the rent ticket).
    inv101 = d["inventory"]
    assert (inv101["sku"], inv101["requested_size"], inv101["on_hand_requested_size"], inv101["computed"]["shortfall"]) == \
           ("CC-TEE-WHITE", "S", 0, 1)
    assert {s["size"]: s["qty"] for s in inv101["sizes"]} == {r[0]: r[1] for r in db(work, "SELECT size, qty FROM inventory WHERE sku = 'CC-TEE-WHITE'")}
    inv103 = c.get("/api/tickets/103").json()["inventory"]
    assert (inv103["on_hand_requested_size"], inv103["requested_qty"], inv103["computed"]["shortfall"],
            inv103["unit_cost"], inv103["list_price"]) == (8, 20, 12, 22, 58)
    assert c.get("/api/tickets/102").json()["inventory"] is None
    r = c.get("/api/tickets/9999")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    r = c.get("/api/tickets/abc")
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_INPUT"


# ---------------------------------------------------------------- 3-5: runs and events

def test_p7_04_runs_refuse_without_authorization_and_without_the_human_header(work, make_client, monkeypatch):
    monkeypatch.delenv(main.LIVE_AUTH_VAR, raising=False)
    c = make_client()                                                  # the default: no run factory
    r = c.post("/api/tickets/101/run", headers=H)
    assert r.status_code == 403 and r.json()["error"]["code"] == "RUNS_NOT_AUTHORIZED"
    c2 = make_client(scripted_factory(work, ticket101_script()))
    r = c2.post("/api/tickets/101/run")                                # no X-Human-Decision header
    assert r.status_code == 403 and r.json()["error"]["code"] == "HUMAN_CONFIRMATION_REQUIRED"
    assert not work.audit_trail_json.exists() or not [e for e in trail(work) if e["event_type"] == "run_started"]


def test_p7_05_a_run_goes_through_the_existing_runner_and_waits_for_a_human(work, make_client):
    c = make_client(scripted_factory(work, ticket101_script()))
    r = c.post("/api/tickets/101/run", headers=H)
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    assert str(uuid.UUID(run_id)) == run_id and r.json()["phase"] == "queued"
    view = wait_run(c, run_id)
    assert view["phase"] == "completed" and view["ticket_id"] == 101
    assert view["outcome"] == "awaiting_human_approval" and view["awaiting_human_action"]
    [prop] = view["proposals_awaiting_decision"]
    assert (prop["kind"], prop["invoice_id"], prop["amount"], prop["proposed_by"], prop["run_id"]) == \
           ("payment", 501, 840, "accounting", run_id)
    # A finished run never approves, pays or resolves anything.
    assert view["ticket_status"] == "open" and not view["ticket_resolved"]
    assert cash(work) == 3400 and db(work, "SELECT COUNT(*) FROM payments")[0][0] == 0
    assert c.get("/api/cash").json()["checking_balance"] == 3400
    assert view["agent_report"]["summary"].startswith("Size S is out")
    assert "claim" in view["agent_report_source"]
    assert view["usage"]["source"].startswith("offline test model")
    t101 = {t["id"]: t for t in c.get("/api/tickets").json()["tickets"]}[101]
    assert (t101["latest_run_id"], t101["latest_run_phase"], t101["awaiting_human_decision"]) == (run_id, "completed", 1)
    assert audit_reconcile.check(work)["consistent"]


def test_p7_06_events_are_ordered_and_attributed(work, make_client):
    c = make_client(scripted_factory(work, ticket101_script()))
    run_id = c.post("/api/tickets/101/run", headers=H).json()["run_id"]
    wait_run(c, run_id)
    evs = c.get(f"/api/events?run_id={run_id}").json()["events"]
    raw = [e for e in trail(work) if e.get("run_id") == run_id]
    assert [e["seq"] for e in evs] == [e["seq"] for e in raw] == sorted(e["seq"] for e in raw)
    assert [e["run_seq"] for e in evs] == list(range(1, len(evs) + 1))
    assert evs[0]["event_type"] == "run_started" and evs[-1]["event_type"] == "run_finished"
    assert all(e["ticket_id"] == 101 for e in evs)
    calls = [(e["actor"], e["tool"]) for e in evs if e["event_type"] == "tool_call_started"]
    assert calls == [("boss", "get_ticket"), ("inventory", "check_inventory"), ("inventory", "list_vendors"),
                     ("accounting", "get_financial_position"), ("accounting", "propose_payment")]
    delegations = [(e["delegated_by"], e["delegated_to"]) for e in evs if e["event_type"] == "delegation_requested"]
    assert delegations == [("boss", "inventory"), ("boss", "accounting")]
    said = {e["actor"]: e["said_summary"] for e in evs if e["event_type"] == "agent_finished"}
    assert said["inventory"] == "Size S on hand is 0; Bulldog is blocked by #501"
    assert said["accounting"] == "Payment proposal created for #501"
    pay_finish = next(e for e in evs if e["event_type"] == "tool_call_finished" and e["tool"] == "propose_payment")
    assert pay_finish["outcome"]["status"] == "ok" and pay_finish["links"]["action_id"] == 1
    assert next(e for e in evs if e["event_type"] == "agent_started" and e["actor"] == "accounting")["chain"] == ["boss", "accounting"]
    assert all(e["description"] for e in evs)
    # Ticket filter gives the same run events; other tickets have none.
    assert [e["seq"] for e in c.get("/api/events?ticket_id=101").json()["events"]] == [e["seq"] for e in evs]
    assert c.get("/api/events?ticket_id=102").json()["events"] == []


def test_p7_07_event_polling_cursor_and_limits(work, make_client):
    c = make_client(scripted_factory(work, ticket101_script()))
    run_id = c.post("/api/tickets/101/run", headers=H).json()["run_id"]
    wait_run(c, run_id)
    allp = c.get("/api/events").json()
    n = allp["trail_events"]
    assert allp["next_after_seq"] == n and not allp["has_more"] and allp["trail_valid"]
    page = c.get("/api/events?after_seq=0&limit=5").json()
    assert [e["seq"] for e in page["events"]] == [1, 2, 3, 4, 5] and page["has_more"] and page["next_after_seq"] == 5
    rest = c.get(f"/api/events?after_seq={page['next_after_seq']}&limit=1000").json()
    assert [e["seq"] for e in rest["events"]] == list(range(6, n + 1)) and not rest["has_more"]
    assert c.get(f"/api/events?after_seq={n}").json()["events"] == []                   # nothing new
    assert [e["seq"] for e in c.get("/api/events?limit=3").json()["events"]] == [n - 2, n - 1, n]   # most recent
    for bad in ("limit=0", "limit=1001", "after_seq=-1"):
        assert c.get(f"/api/events?{bad}").status_code == 422


def test_p7_08_run_rejects_unknown_and_resolved_tickets(work, make_client):
    c = make_client(scripted_factory(work, ticket101_script()))
    r = c.post("/api/tickets/9999/run", headers=H)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    with privileged(work) as con:
        con.execute("UPDATE tickets SET status = 'resolved' WHERE id = 101")
    r = c.post("/api/tickets/101/run", headers=H)
    assert r.status_code == 409 and r.json()["error"]["code"] == "TICKET_NOT_OPEN"
    assert c.post("/api/tickets/abc/run", headers=H).status_code == 422
    assert not work.audit_trail_json.exists() or not trail(work)


def test_p7_09_one_run_at_a_time_and_nothing_else_mutates_during_it(work, make_client):
    release = threading.Event()
    started = threading.Event()

    async def slow(ticket_id, run_id):
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.02)
        return await scripted_factory(work, ticket101_script())(ticket_id, run_id)

    c = make_client(slow)
    pay = propose(work, "propose_payment", "accounting", PAY_RENT)
    run_id = c.post("/api/tickets/101/run", headers=H).json()["run_id"]
    assert started.wait(10)
    for path, body in ((f"/api/actions/{pay}/approve", ME), (f"/api/actions/{pay}/reject", ME),
                       ("/api/tickets/103/resolve", RESOLVE), ("/api/reset", {"requested_by": "Dr. Ryan", "confirm": "RESET"})):
        r = c.post(path, json=body, headers=H)
        assert r.status_code == 409 and r.json()["error"]["code"] == "RUN_ACTIVE", path
    r = c.post("/api/tickets/102/run", headers=H)
    assert r.status_code == 409 and r.json()["error"]["code"] == "RUN_ACTIVE"
    assert c.get(f"/api/runs/{run_id}").json()["phase"] == "queued"
    release.set()
    assert wait_run(c, run_id)["phase"] == "completed"
    assert cash(work) == 3400                                     # the refused approval changed nothing
    assert c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).status_code == 200    # allowed afterwards


def test_p7_10_another_process_holding_the_run_lock_blocks_every_change(work, make_client):
    c = make_client(scripted_factory(work, ticket101_script()))
    pay = propose(work, "propose_payment", "accounting", PAY_501)
    before = protected_digest(work)
    with locks.file_lock(locks.run_lock_path(work.active_dir), timeout=0):           # e.g. a CLI run elsewhere
        r = c.post(f"/api/actions/{pay}/approve", json=ME, headers=H)
        assert r.status_code == 409 and r.json()["error"]["code"] == "BUSY"
        r = c.post("/api/reset", json={"requested_by": "Dr. Ryan", "confirm": "RESET"}, headers=H)
        assert r.status_code == 409 and r.json()["error"]["code"] == "BUSY"
        r = c.post("/api/tickets/101/run", headers=H)
        assert r.status_code == 409 and r.json()["error"]["code"] == "RUN_ACTIVE"
    assert protected_digest(work) == before
    assert db(work, "SELECT status FROM pending_actions WHERE id = ?", (pay,))[0][0] == "proposed"


def test_p7_11_failed_and_stopped_runs_are_reported_honestly(work, make_client):
    c = make_client(scripted_factory(work, Scripted({"boss": [[("raise", "model unavailable")]]})))
    view = wait_run(c, c.post("/api/tickets/103/run", headers=H).json()["run_id"])
    assert (view["phase"], view["outcome"]) == ("failed", "failed") and "model unavailable" in view["error"]
    script = Scripted({"boss": [[("delegate", "inventory", "check hoodies")]]})
    c2 = make_client(scripted_factory(work, script, budget=RunBudget(max_delegations=0)))
    view = wait_run(c2, c2.post("/api/tickets/103/run", headers=H).json()["run_id"])
    assert (view["phase"], view["outcome"]) == ("stopped", "incomplete_limit_reached")
    assert view["ticket_status"] == "open"


def test_p7_12_a_run_that_never_finished_is_reported_as_interrupted(work, make_client):
    rid = str(uuid.uuid4())
    audit_trail.AuditTrail(work.audit_trail_json).append([audit_trail.make_event(
        "run_started", actor="system", actor_kind="system", mode="simulated", run_id=rid,
        details={"ticket_id": 102, "entry_agent": "boss"})])
    c = make_client()
    view = c.get(f"/api/runs/{rid}").json()
    assert (view["phase"], view["outcome"], view["ticket_id"]) == ("interrupted", "interrupted", 102)
    assert c.get(f"/api/runs/{uuid.uuid4()}").status_code == 404


# ---------------------------------------------------------------- 6-9: approvals and cash

def test_p7_13_proposals_alone_never_change_cash(work, make_client):
    c = make_client()
    for op, who, args in (("propose_payment", "accounting", PAY_501), ("propose_payment", "accounting", PAY_RENT),
                          ("propose_purchase", "inventory", BUY_TEES), ("propose_quote", "customer_service", QUOTE_45)):
        propose(work, op, who, args)
    body = c.get("/api/cash").json()
    assert body["checking_balance"] == 3400 and body["payments_executed"] == []
    assert body["not_counted_as_cash"]["proposed_payments_total"] == 3240
    assert body["not_counted_as_cash"]["proposed_or_approved_quotes_total"] == 900
    assert c.get("/api/actions/pending").json()["count"] == 4
    assert c.get("/api/actions/pending?ticket_id=103").json()["count"] == 1


def test_p7_14_approving_a_payment_executes_it_atomically_and_is_audited(work, make_client):
    c = make_client()
    pay = propose(work, "propose_payment", "accounting", PAY_501)
    r = c.post(f"/api/actions/{pay}/approve", json={**ME, "note": "Unblock Bulldog"}, headers=H)
    assert r.status_code == 200, r.json()
    b = r.json()
    assert (b["result"], b["status"], b["cash"]["balance_before"], b["cash"]["balance_after"]) == ("payment_executed", "executed", 3400, 2560)
    assert b["audit_trail_recorded"] and "LOCAL PROTOTYPE" in b["notice"]
    assert c.get("/api/cash").json()["checking_balance"] == 2560 == cash(work)
    assert db(work, "SELECT kind, ref_id, amount, account, paid_at, approved_by FROM payments") == [
        ("invoice", 501, 840.0, "checking", "2026-08-31", "Dr. Ryan")]
    assert db(work, "SELECT status FROM invoices WHERE id = 501")[0][0] == "paid"
    assert db(work, "SELECT status, decided_by, decision_note, payment_id FROM pending_actions WHERE id = ?", (pay,))[0] == \
           ("executed", "Dr. Ryan", "Unblock Bulldog", 1)
    # audit.db: approval + execution in the same transaction, attributed to the human, chain valid.
    human = [e for e in audit_events(work) if e[2] == "human"]
    assert [(e[1], e[3], e[4]) for e in human] == [("human:Dr. Ryan", "action_approved", b["decision_id"]),
                                                   ("human:Dr. Ryan", "payment_executed", b["decision_id"])]
    assert json.loads(human[1][7])["balance_after"] == 2560
    assert audit_db.verify_chain(work.audit_db)["valid"]
    # JSON trail: requested before, finished after, as a human actor.
    js = [e for e in trail(work) if e["actor_kind"] == "human"]
    assert [(e["event_type"], e["call_id"], (e["outcome"] or {}).get("status")) for e in js] == [
        ("human_decision_requested", b["decision_id"], None), ("human_decision_finished", b["decision_id"], "ok")]
    assert js[1]["links"]["payment_id"] == 1
    assert audit_trail.AuditTrail(work.audit_trail_json).verify()["valid"]
    assert audit_reconcile.check(work)["consistent"]


def test_p7_15_rent_payment_moves_the_due_date_one_month(work, make_client):
    c = make_client()
    pay = propose(work, "propose_payment", "accounting", PAY_RENT)
    b = c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).json()
    assert b["changes"] == {"lease_id": 1, "next_due_before": "2026-09-02", "next_due_after": "2026-10-02"}
    assert cash(work) == 1000
    assert approval.add_months("2026-01-31", 1) == "2026-02-28" and approval.add_months("2026-12-15", 1) == "2027-01-15"


def test_p7_16_duplicate_approval_and_decided_proposals_are_refused(work, make_client):
    c = make_client()
    pay = propose(work, "propose_payment", "accounting", PAY_501)
    assert c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).status_code == 200
    for verb in ("approve", "reject"):
        r = c.post(f"/api/actions/{pay}/{verb}", json=ME, headers=H)
        assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_PENDING"
    assert cash(work) == 2560 and db(work, "SELECT COUNT(*) FROM payments")[0][0] == 1


def test_p7_17_concurrent_approvals_of_one_payment_execute_once(work):
    pay = propose(work, "propose_payment", "accounting", PAY_501)
    tr = audit_trail.AuditTrail(work.audit_trail_json)
    results = []

    def go():
        try:
            results.append(approval.approve_action(work, tr, pay, decided_by="Dr. Ryan")["result"])
        except approval.DecisionError as exc:
            results.append(exc.code)
    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("payment_executed") == 1 and set(results) <= {"payment_executed", "BUSY", "NOT_PENDING"}
    assert cash(work) == 2560 and db(work, "SELECT COUNT(*) FROM payments")[0][0] == 1


def test_p7_18_a_payment_that_would_make_cash_negative_is_refused_and_changes_nothing(work, make_client):
    c = make_client()
    with privileged(work) as con:
        con.execute("UPDATE cash_accounts SET balance = 500 WHERE name = 'checking'")        # sandbox setup only
    pay = propose(work, "propose_payment", "accounting", PAY_501)                           # recorded as NOT fundable
    before = protected_digest(work)
    r = c.post(f"/api/actions/{pay}/approve", json=ME, headers=H)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REFUSED_RULE" and "R1" in r.json()["error"]["message"]
    assert protected_digest(work) == before and cash(work) == 500
    assert db(work, "SELECT status FROM pending_actions WHERE id = ?", (pay,))[0][0] == "proposed"
    finished = [e for e in trail(work) if e["event_type"] == "human_decision_finished"]
    assert finished[-1]["outcome"]["status"] == "refused" and finished[-1]["outcome"]["code"] == "REFUSED_RULE"


def test_p7_19_two_payments_that_together_exceed_cash_cannot_both_execute(work, make_client):
    c = make_client()
    with privileged(work) as con:
        con.execute("UPDATE cash_accounts SET balance = 3000 WHERE name = 'checking'")
    rent = propose(work, "propose_payment", "accounting", PAY_RENT)
    inv = propose(work, "propose_payment", "accounting", PAY_501)
    assert c.post(f"/api/actions/{rent}/approve", json=ME, headers=H).status_code == 200            # 3000 -> 600
    r = c.post(f"/api/actions/{inv}/approve", json=ME, headers=H)                                    # 840 > 600
    assert r.status_code == 409 and "R1" in r.json()["error"]["message"]
    assert cash(work) == 600 and db(work, "SELECT status FROM invoices WHERE id = 501")[0][0] == "open"


def test_p7_20_an_invoice_already_paid_cannot_be_paid_again(work, make_client):
    c = make_client()
    pay = propose(work, "propose_payment", "accounting", PAY_501)
    with privileged(work) as con:
        con.execute("UPDATE invoices SET status = 'paid' WHERE id = 501")
    r = c.post(f"/api/actions/{pay}/approve", json=ME, headers=H)
    assert r.status_code == 409 and "R4" in r.json()["error"]["message"] and cash(work) == 3400


def test_p7_21_rejection_changes_only_the_decision(work, make_client):
    c = make_client()
    pay = propose(work, "propose_payment", "accounting", PAY_501)
    before = protected_digest(work)
    r = c.post(f"/api/actions/{pay}/reject", json={**ME, "note": "Wait for vendor confirmation"}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert protected_digest(work) == before and cash(work) == 3400
    assert db(work, "SELECT status, decided_by FROM pending_actions WHERE id = ?", (pay,))[0] == ("rejected", "Dr. Ryan")
    assert c.get("/api/actions/pending").json()["count"] == 0
    assert [e[3] for e in audit_events(work) if e[2] == "human"] == ["action_rejected"]


def test_p7_22_purchase_approval_respects_the_vendor_block_and_moves_no_cash(work, make_client):
    # Problem 9 F3 (Dr. Ryan's decision): approval is an authorization only, so it is allowed while the vendor is blocked,
    # with an R3 warning; EXECUTION (spending) is what is refused while blocked (tests/test_problem9_purchase_execution.py).
    c = make_client()
    buy = propose(work, "propose_purchase", "inventory", BUY_TEES)
    r = c.post(f"/api/actions/{buy}/approve", json=ME, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert any(w.startswith("R3: Bulldog Print Co has open unpaid invoice(s) [501]") for w in r.json()["warnings"])
    assert "No money has moved" in r.json()["meaning"]
    pay = propose(work, "propose_payment", "accounting", PAY_501)
    assert c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).status_code == 200    # unblocks the vendor
    assert cash(work) == 2560                                   # only the payment moved money; approval moved none
    assert db(work, "SELECT status FROM pending_actions WHERE id = ?", (buy,))[0][0] == "approved"
    assert db(work, "SELECT qty FROM inventory WHERE sku = 'CC-TEE-WHITE' AND size = 'S'")[0][0] == 0


def test_p7_23_quote_approval_sends_nothing_and_allows_one_price(work, make_client):
    c = make_client()
    q1 = propose(work, "propose_quote", "customer_service", QUOTE_45)
    q2 = propose(work, "propose_quote", "customer_service", {**QUOTE_45, "unit_price": 50})
    r = c.post(f"/api/actions/{q1}/approve", json=ME, headers=H)
    assert r.status_code == 200 and "Nothing is sent" in r.json()["meaning"] and cash(work) == 3400
    r = c.post(f"/api/actions/{q2}/approve", json=ME, headers=H)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REFUSED_RULE"


@pytest.mark.parametrize("headers,body,status,code", [
    ({}, ME, 403, "HUMAN_CONFIRMATION_REQUIRED"),
    ({"X-Human-Decision": "yes"}, ME, 403, "HUMAN_CONFIRMATION_REQUIRED"),
    (H, None, 422, "INVALID_INPUT"),
    (H, {"decided_by": "Dr. Ryan", "confirm": False}, 422, "INVALID_INPUT"),
    (H, {"decided_by": "Dr. Ryan"}, 422, "INVALID_INPUT"),
    (H, {**ME, "status": "approved"}, 422, "INVALID_INPUT"),           # no extra fields (e.g. a forged status)
    (H, {**ME, "approved_by": "boss"}, 422, "INVALID_INPUT"),
    (H, {**ME, "decided_by": "accounting"}, 422, "INVALID_INPUT"),     # an agent name is not a person
    (H, {**ME, "decided_by": "System"}, 422, "INVALID_INPUT"),
    (H, {**ME, "decided_by": "<script>"}, 422, "INVALID_INPUT"),
    (H, {**ME, "note": "x" * 501}, 422, "INVALID_INPUT"),
])
def test_p7_24_invalid_or_unconfirmed_decisions_are_refused_and_change_nothing(work, make_client, headers, body, status, code):
    c = make_client()
    pay = propose(work, "propose_payment", "accounting", PAY_501)
    before = protected_digest(work)
    r = c.post(f"/api/actions/{pay}/approve", json=body, headers=headers)
    assert (r.status_code, r.json()["error"]["code"]) == (status, code)
    assert protected_digest(work) == before
    assert db(work, "SELECT status FROM pending_actions WHERE id = ?", (pay,))[0][0] == "proposed"


def test_p7_25_unknown_actions(work, make_client):
    c = make_client()
    r = c.post("/api/actions/999/approve", json=ME, headers=H)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert c.post("/api/actions/0/approve", json=ME, headers=H).status_code == 422
    assert c.post("/api/actions/abc/approve", json=ME, headers=H).status_code == 422


def test_p7_26_ticket_resolution_is_human_only_and_needs_decided_proposals(work, make_client):
    c = make_client()
    pay = propose(work, "propose_payment", "accounting", PAY_RENT)
    r = c.post("/api/tickets/102/resolve", json=RESOLVE, headers=H)
    assert r.status_code == 409 and "await a decision" in r.json()["error"]["message"]
    assert c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).status_code == 200
    r = c.post("/api/tickets/102/resolve", json={**RESOLVE, "note": "Rent paid"}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    t = {x["id"]: x for x in c.get("/api/tickets").json()["tickets"]}
    assert t[102]["is_resolved"] and t[101]["is_open"] and t[103]["is_open"]
    assert c.post("/api/tickets/102/resolve", json=RESOLVE, headers=H).json()["error"]["code"] == "NOT_PENDING"
    assert c.post("/api/tickets/9999/resolve", json=RESOLVE, headers=H).status_code == 404
    assert c.post("/api/tickets/101/resolve", json=RESOLVE).status_code == 403
    assert "ticket_resolved" in [e[3] for e in audit_events(work)]


# ---------------------------------------------------------------- 10-13: reset, original database, audit integrity

def test_p7_27_reset_restores_starting_values_and_keeps_all_audit_history(work, make_client):
    c = make_client()
    pay = propose(work, "propose_payment", "accounting", PAY_501)
    first = c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).json()
    trail_before = trail(work)
    audit_before = audit_events(work)
    original_sha = paths.sha256_file(work.original_db)
    r = c.post("/api/reset", json={"requested_by": "Dr. Ryan", "confirm": "RESET"}, headers=H)
    assert r.status_code == 200, r.json()
    b = r.json()
    assert b["reset"] and b["audit_trail_recorded"] and b["warnings"] == [] and b["result"]["original_tables_unchanged"]
    # Starting values are back.
    assert c.get("/api/cash").json()["checking_balance"] == 3400
    assert db(work, "SELECT status FROM invoices WHERE id = 501")[0][0] == "open"
    assert db(work, "SELECT COUNT(*) FROM payments")[0][0] == 0 and c.get("/api/actions/pending").json()["count"] == 0
    assert all(t["is_open"] for t in c.get("/api/tickets").json()["tickets"])
    with closing(maintenance.connect_ro(work.original_db)) as o, closing(maintenance.connect_ro(work.working_db)) as w:
        names = {n for _, n, _ in maintenance.schema_objects(o)}
        assert maintenance.content_digest(w, only=names) == maintenance.content_digest(o)
    # The original is untouched; earlier audit records are all still there, unchanged, plus the reset markers.
    assert paths.sha256_file(work.original_db) == original_sha == paths.ORIGINAL_SHA256
    after = trail(work)
    assert after[:len(trail_before)] == trail_before
    request_ev, reset_ev = after[-2], after[-1]
    assert request_ev["event_type"] == "db_reset_requested" and reset_ev["event_type"] == "db_reset"
    assert reset_ev["details"]["requested_by"] == "Dr. Ryan" and reset_ev["outcome"]["status"] == "ok"
    assert reset_ev["details"]["reset_id"] == request_ev["details"]["reset_id"] == b["reset_id"]
    assert maintenance.read_reset_stamp(work.working_db) == b["reset_id"]
    audit_after = audit_events(work)
    assert audit_after[:len(audit_before)] == audit_before
    boundary = audit_after[len(audit_before)]
    assert boundary[3] == "db_reset_started" and json.loads(boundary[7])["audit_trail_request_event_id"] == request_ev["event_id"]
    assert audit_after[len(audit_before) + 1][3] == "db_reset_finished"
    assert audit_db.verify_chain(work.audit_db)["valid"]
    # Ids restart after a reset, but a new decision on the new action #1 cannot be confused with the old one.
    new_pay = propose(work, "propose_payment", "accounting", PAY_501)
    assert new_pay == pay == 1
    second = c.post(f"/api/actions/{new_pay}/approve", json=ME, headers=H).json()
    old, new = ([json.loads(e[7]) for e in audit_events(work) if e[3] == "action_approved"])
    assert old["proposal_run_id"] != new["proposal_run_id"] and first["decision_id"] != second["decision_id"]
    ids = [e[0] for e in audit_events(work)]
    assert ids.index(boundary[0]) < ids.index(next(e[0] for e in audit_events(work)
                                                   if e[3] == "action_approved" and e[4] == second["decision_id"]))
    assert audit_reconcile.check(work)["consistent"]


@pytest.mark.parametrize("headers,body,status,code", [
    ({}, {"requested_by": "Dr. Ryan", "confirm": "RESET"}, 403, "HUMAN_CONFIRMATION_REQUIRED"),
    (H, {"requested_by": "Dr. Ryan", "confirm": "yes"}, 422, "INVALID_INPUT"),
    (H, {"requested_by": "Dr. Ryan"}, 422, "INVALID_INPUT"),
    (H, {"requested_by": "boss", "confirm": "RESET"}, 422, "INVALID_INPUT"),
])
def test_p7_28_reset_needs_an_explicit_human_request(work, make_client, headers, body, status, code):
    c = make_client()
    pay = propose(work, "propose_payment", "accounting", PAY_501)
    assert c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).status_code == 200
    r = c.post("/api/reset", json=body, headers=headers)
    assert (r.status_code, r.json()["error"]["code"]) == (status, code)
    assert cash(work) == 2560                                      # not reset


def test_p7_29_the_real_original_database_is_never_the_write_target(work):
    bad = paths.Paths(root=work.root, mode="default", original_db=work.original_db, working_db=work.original_db,
                      audit_db=work.audit_db, audit_trail_json=work.audit_trail_json, repo_working_db=work.repo_working_db)
    before = paths.sha256_file(work.original_db)
    with pytest.raises(paths.UnsafeWriteError):
        approval.approve_action(bad, audit_trail.AuditTrail(work.audit_trail_json), 1, decided_by="Dr. Ryan")
    with pytest.raises(paths.UnsafeWriteError):
        maintenance.reset_working_db(bad)
    assert paths.sha256_file(work.original_db) == before


def test_p7_30_a_damaged_audit_trail_is_not_served_and_blocks_decisions(work, make_client):
    c = make_client()
    pay = propose(work, "propose_payment", "accounting", PAY_501)
    c.post(f"/api/actions/{pay}/reject", json=ME, headers=H)
    pay2 = propose(work, "propose_payment", "accounting", PAY_501)
    data = json.loads(work.audit_trail_json.read_text(encoding="utf-8"))
    data[0]["actor"] = "human:Someone Else"                              # tamper
    work.audit_trail_json.write_text(json.dumps(data), encoding="utf-8")
    r = c.get("/api/events")
    assert r.status_code == 503 and r.json()["error"]["code"] == "AUDIT_TRAIL_UNREADABLE"
    r = c.post(f"/api/actions/{pay2}/approve", json=ME, headers=H)           # fail-closed: cannot record -> no change
    assert r.status_code == 503 and r.json()["error"]["code"] == "AUDIT_FAILED"
    assert cash(work) == 3400 and db(work, "SELECT status FROM pending_actions WHERE id = ?", (pay2,))[0][0] == "proposed"


def test_p7_31_a_missing_working_database_is_reported_not_invented(work, make_client):
    c = make_client()
    work.working_db.unlink()
    for path in ("/api/tickets", "/api/cash", "/api/actions/pending", "/api/tickets/101"):
        r = c.get(path)
        assert r.status_code == 503 and r.json()["error"]["code"] == "DB_UNAVAILABLE", path


# ---------------------------------------------------------------- 14: startup command, CORS, boundaries

def test_p7_32_cors_allows_only_the_local_dashboard(work, make_client):
    c = make_client()
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-human-decision"}
    ok = c.options("/api/reset", headers={"Origin": "http://localhost:5173", **pre})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "http://localhost:5173"
    bad = c.options("/api/reset", headers={"Origin": "http://evil.example", **pre})
    assert "access-control-allow-origin" not in bad.headers
    assert "access-control-allow-origin" not in c.get("/api/cash", headers={"Origin": "http://evil.example"}).headers


def test_p7_33_service_index_lists_the_routes(work, make_client, monkeypatch):
    monkeypatch.delenv(main.LIVE_AUTH_VAR, raising=False)
    body = make_client().get("/").json()
    assert body["docs"] == "/docs" and body["live_runs_enabled"] is False
    assert set(body["routes"]) == {
        "GET /api/tickets", "GET /api/tickets/{ticket_id}", "POST /api/tickets/{ticket_id}/run", "GET /api/runs/{run_id}",
        "GET /api/events", "GET /api/actions/pending", "POST /api/actions/{action_id}/approve",
        "POST /api/actions/{action_id}/reject", "POST /api/tickets/{ticket_id}/resolve", "GET /api/cash", "POST /api/reset", "GET /api/audit",
        "POST /api/actions/{action_id}/execute",                           # Problem 9 F3: purchase execution (human only)
        "GET /api/runs/{run_id}/responses"}                                # Problem 9 follow-up 20: full agent replies (read-only)
    assert make_client().get("/openapi.json").status_code == 200


def test_p7_34_live_runs_are_enabled_only_by_an_explicit_setting(work, monkeypatch):
    monkeypatch.delenv(main.LIVE_AUTH_VAR, raising=False)
    with TestClient(main.create_app(p=work)) as c:
        assert c.get("/").json()["run_mode"] is None
    monkeypatch.setenv(main.LIVE_AUTH_VAR, "test only: never started")
    with TestClient(main.create_app(p=work)) as c:                      # built, NOT started (no network in tests)
        assert c.get("/").json()["run_mode"] == "live" and c.get("/").json()["live_runs_enabled"] is True
        assert c.post("/api/tickets/101/run").status_code == 403          # still needs the human header


def test_p7_35_the_professors_startup_command_loads_the_app_from_backend(tmp_path):
    """`uvicorn main:app --reload --port 8000`, run from hw5/backend/: the real uvicorn command line parses those exact
    arguments; only its final run() is intercepted, and the app is then loaded the way run() loads it (app_dir on
    sys.path, uvicorn.Config.load). No server is started (no socket is opened)."""
    code = (
        "import socket, sys\n"
        "def _no_server(*a, **k):\n"
        "    raise RuntimeError('test guard: no socket may be bound or listened on')\n"
        "socket.socket.bind = socket.socket.listen = _no_server   # hard guarantee: this check can never start a server\n"
        "import uvicorn.main\n"
        "cli = sys.modules['uvicorn.main']   # the MODULE; the name uvicorn.main is re-bound to the click command\n"
        "seen = {}\n"
        "cli.run = lambda app, **kw: seen.update(app=app, **kw)\n"
        "cli.main.main(['main:app', '--reload', '--port', '8000'], standalone_mode=False)\n"
        "assert seen, 'uvicorn did not reach run()'\n"
        "assert (seen['app'], seen['reload'], seen['port'], seen['host']) == ('main:app', True, 8000, '127.0.0.1'), seen\n"
        "sys.path.insert(0, seen['app_dir'])\n"
        "cfg = uvicorn.Config(seen['app'], reload=seen['reload'], port=seen['port'], host=seen['host'])\n"
        "cfg.load()\n"
        "import main\n"
        "assert cfg.loaded_app is not None and main.app.title == 'Campus Customs backend'\n"
        "print('LOADED', cfg.host, cfg.port, main.app.state.paths.working_db.name)\n")
    env = {k: v for k, v in os.environ.items() if k not in (main.LIVE_AUTH_VAR,) and not k.startswith("CC_")}
    env["CC_RUNTIME_DIR"] = str(tmp_path)                               # resolve paths away from the real data/
    out = subprocess.run([sys.executable, "-c", code], cwd=REAL_ROOT / "backend", env=env, capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "LOADED 127.0.0.1 8000 campus_customs_new.db" in out.stdout


def test_p7_36_backend_requirements_are_pinned():
    req = (REAL_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "fastapi==" in req and "uvicorn==" in req
