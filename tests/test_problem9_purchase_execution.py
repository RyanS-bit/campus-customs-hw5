"""Problem 9, F3: purchase authorization (approve) versus expenditure (execute).

Dr. Ryan's decisions: two human steps; an executed purchase is a payments row of kind 'purchase' linked to the proposal;
the amount needs an explicit confirmation and a recorded basis (database estimate or supplier-confirmed price); inventory
never changes on execution; approval is allowed while the vendor is blocked (R3 warning) but EXECUTION is refused (our
prudence choice); purchases yield to obligations. OFFLINE: temporary databases only.
"""

import json
import sqlite3
import subprocess
import sys
import threading
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

import approval
import audit_reconcile
import audit_trail
import main
import maintenance
import ops
import runner
import server
from agent_script import Scripted
from conftest import REAL_ROOT, privileged, protected_digest

H = {"X-Human-Decision": "confirm"}
ME = {"decided_by": "Dr. Ryan", "confirm": True}
PAY_501 = dict(kind="invoice", ref_id=501, amount=840, ticket_id=101, rationale="Pay overdue invoice 501")
PAY_RENT = dict(kind="lease", ref_id=1, amount=2400, ticket_id=102, rationale="Pay rent due 2026-09-02")
TEES = dict(vendor_id=1, sku="CC-TEE-WHITE", size="S", qty=10, ticket_id=101, rationale="Restock size S tees (estimate)")
HOODIES = dict(vendor_id=1, sku="CC-HOOD-NAVY", size="M", qty=12, ticket_id=103, rationale="Cover the 12 hoodie shortfall (estimate)")
EST = {"amount_basis": "recorded_unit_cost_estimate", "amount_confirmed": True, "supplier_confirmed": True}


def execute_body(amount, **over):
    return {**ME, **EST, "amount": amount, **over}


@pytest.fixture
def c(work):
    with TestClient(main.create_app(p=work)) as client:
        yield client


def propose(work, op, agent, args):
    return getattr(ops, op)(work, ops.RunIdentity(str(uuid.uuid4()), agent, "simulated"), **args)["links"]["action_id"]


def q(work, sql, args=()):
    with closing(maintenance.connect_ro(work.working_db)) as con:
        return con.execute(sql, args).fetchall()


def cash(work):
    return q(work, "SELECT balance FROM cash_accounts")[0][0]


def status(work, action):
    return q(work, "SELECT status FROM pending_actions WHERE id = ?", (action,))[0][0]


def stock(work, sku, size):
    return q(work, "SELECT qty FROM inventory WHERE sku = ? AND size = ?", (sku, size))[0][0]


def audit_rows(work, event_type):
    with closing(maintenance.connect_ro(work.audit_db)) as con:
        return [json.loads(r[0]) for r in con.execute("SELECT payload FROM events WHERE event_type = ? ORDER BY id", (event_type,))]


def approved_tee_purchase(work, c, pay_501=True):
    buy = propose(work, "propose_purchase", "inventory", TEES)
    assert c.post(f"/api/actions/{buy}/approve", json=ME, headers=H).status_code == 200
    if pay_501:
        pay = propose(work, "propose_payment", "accounting", PAY_501)
        assert c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).status_code == 200      # 3400 -> 2560, unblocks Bulldog
    return buy


# ---------------------------------------------------------------- approval is authorization only

def test_p1_approval_while_blocked_is_an_authorization_with_an_r3_warning_and_moves_no_cash(work, c):
    buy = propose(work, "propose_purchase", "inventory", HOODIES)
    r = c.post(f"/api/actions/{buy}/approve", json=ME, headers=H).json()
    assert r["status"] == "approved" and r["warnings"][0].startswith("R3: Bulldog Print Co has open unpaid invoice(s) [501]")
    assert "executing it (spending cash) will be refused" in r["warnings"][0] and "No money has moved" in r["meaning"]
    assert cash(work) == 3400 and q(work, "SELECT COUNT(*) FROM payments") == [(0,)]
    body = c.get("/api/cash").json()
    assert body["checking_balance"] == 3400                                    # actual cash unchanged
    assert body["not_counted_as_cash"]["approved_unexecuted_purchases_total"] == 264
    assert body["available"] == {**body["available"], "for_obligations": 3400, "for_purchases": 3136}   # computed, not stored


def test_p2_execution_is_refused_while_the_vendor_is_blocked_and_changes_nothing(work, c):
    buy = approved_tee_purchase(work, c, pay_501=False)
    before = protected_digest(work)
    r = c.post(f"/api/actions/{buy}/execute", json=execute_body(80), headers=H)
    assert r.status_code == 409 and r.json()["error"]["code"] == "REFUSED_RULE"
    msg = r.json()["error"]["message"]
    assert msg.startswith("R3: Bulldog Print Co") and "our implementation choice" in msg
    assert protected_digest(work) == before and status(work, buy) == "approved"
    # Nothing was committed, so a later execution (after #501 is paid) is a normal, legitimate request.
    pay = propose(work, "propose_payment", "accounting", PAY_501)
    assert c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).status_code == 200
    assert c.post(f"/api/actions/{buy}/execute", json=execute_body(80), headers=H).status_code == 200


# ---------------------------------------------------------------- a successful execution

def test_p3_execution_pays_atomically_links_everything_and_leaves_inventory_unchanged(work, c):
    buy = approved_tee_purchase(work, c)
    r = c.post(f"/api/actions/{buy}/execute", json=execute_body(80, note="Bulldog agreed by phone"), headers=H)
    assert r.status_code == 200, r.json()
    b = r.json()
    assert (b["result"], b["status"], b["cash"]["balance_before"], b["cash"]["balance_after"]) == ("purchase_executed", "executed", 2560, 2480)
    assert b["execution"]["amount_basis"] == "recorded_unit_cost_estimate" and b["execution"]["database_estimate"] == 80
    assert b["execution"]["inventory_changed"] is False and "Paying is not delivery" in b["meaning"]
    # Ledger, cash, proposal, inventory
    pay_id = b["payment"]["payment_id"]
    assert q(work, "SELECT kind, ref_id, amount, account, paid_at, approved_by FROM payments WHERE id = ?", (pay_id,)) == \
           [("purchase", buy, 80.0, "checking", "2026-08-31", "Dr. Ryan")]
    assert cash(work) == 2480
    assert q(work, "SELECT status, payment_id, decided_by FROM pending_actions WHERE id = ?", (buy,)) == [("executed", pay_id, "Dr. Ryan")]
    assert stock(work, "CC-TEE-WHITE", "S") == 0                              # a purchase is not a delivery
    # audit.db and the JSON trail
    [ev] = audit_rows(work, "purchase_executed")
    assert (ev["kind"], ev["ref_id"], ev["amount"], ev["execution"]["executed_by"]) == ("purchase", buy, 80, "Dr. Ryan")
    js = [e for e in audit_trail.AuditTrail(work.audit_trail_json).read() if e["call_id"] == b["decision_id"]]
    assert [(e["event_type"], (e["outcome"] or {}).get("status")) for e in js] == \
           [("human_decision_requested", None), ("human_decision_finished", "ok")]
    assert js[0]["details"]["operation"] == "execute_purchase" and js[0]["details"]["amount_basis"] == "recorded_unit_cost_estimate"
    assert audit_reconcile.check(work)["consistent"]
    # Only executed transactions change the balance; reconciliation holds.
    body = c.get("/api/cash").json()
    assert [p["kind"] for p in body["payments_executed"]] == ["invoice", "purchase"]
    assert body["not_counted_as_cash"]["approved_unexecuted_purchases_total"] == 0
    assert 3400 - sum(p["amount"] for p in body["payments_executed"]) == body["checking_balance"] == 2480


def test_p4_a_supplier_confirmed_price_is_recorded_as_the_persons_statement_with_its_difference(work, c):
    buy = approved_tee_purchase(work, c)
    r = c.post(f"/api/actions/{buy}/execute", headers=H, json=execute_body(
        95.5, amount_basis="supplier_confirmed_price", confirmation_note="Bulldog emailed a price of $9.55 per tee on 2026-08-31"))
    assert r.status_code == 200
    ex = r.json()["execution"]
    assert (ex["amount_basis"], ex["database_estimate"], ex["difference_from_database_estimate"]) == ("supplier_confirmed_price", 80, 15.5)
    assert ex["confirmation_note"].startswith("Bulldog emailed") and "person states" in ex["basis_meaning"]
    assert cash(work) == 2560 - 95.5


# ---------------------------------------------------------------- refusals: duplicates, state, confirmations

def test_p5_duplicate_and_out_of_order_execution_are_refused(work, c):
    buy = approved_tee_purchase(work, c)
    assert c.post(f"/api/actions/{buy}/execute", json=execute_body(80), headers=H).status_code == 200
    r = c.post(f"/api/actions/{buy}/execute", json=execute_body(80), headers=H)
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_PENDING" and "already executed" in r.json()["error"]["message"]
    assert q(work, "SELECT COUNT(*) FROM payments WHERE kind = 'purchase'") == [(1,)] and cash(work) == 2480
    proposed = propose(work, "propose_purchase", "inventory", {**TEES, "qty": 5})                     # never approved
    r = c.post(f"/api/actions/{proposed}/execute", json=execute_body(40), headers=H)
    assert r.status_code == 409 and "approve it first" in r.json()["error"]["message"]
    withdrawn = propose(work, "propose_purchase", "inventory", {**TEES, "qty": 3})
    assert c.post(f"/api/actions/{withdrawn}/approve", json=ME, headers=H).status_code == 200
    assert c.post(f"/api/actions/{withdrawn}/reject", json=ME, headers=H).json()["status"] == "rejected"   # authorization withdrawn
    assert c.post(f"/api/actions/{withdrawn}/execute", json=execute_body(24), headers=H).json()["error"]["code"] == "NOT_PENDING"
    assert cash(work) == 2480


def test_p6_concurrent_execution_requests_pay_exactly_once(work, c):
    buy = approved_tee_purchase(work, c)
    tr = audit_trail.AuditTrail(work.audit_trail_json)
    results = []

    def go():
        try:
            results.append(approval.execute_purchase(work, tr, buy, decided_by="Dr. Ryan", amount=80, **EST)["result"])
        except approval.DecisionError as exc:
            results.append(exc.code)
    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("purchase_executed") == 1 and set(results) <= {"purchase_executed", "BUSY", "NOT_PENDING"}
    assert q(work, "SELECT COUNT(*) FROM payments WHERE kind = 'purchase'") == [(1,)] and cash(work) == 2480


@pytest.mark.parametrize("body,code", [
    (execute_body(80, amount_confirmed=False), "INVALID_INPUT"),                     # amount not explicitly confirmed
    ({k: v for k, v in execute_body(80).items() if k != "amount_confirmed"}, "INVALID_INPUT"),
    (execute_body(80, supplier_confirmed=False), "INVALID_INPUT"),                   # supplier agreement not stated
    ({k: v for k, v in execute_body(80).items() if k != "supplier_confirmed"}, "INVALID_INPUT"),
    (execute_body(95, amount_basis="supplier_confirmed_price"), "INVALID_INPUT"),    # supplier price without confirmation note
    (execute_body(95, amount_basis="supplier_confirmed_price", confirmation_note="ok"), "INVALID_INPUT"),
    (execute_body(90), "INVALID_INPUT"),                                             # "estimate" basis but not the estimate
    (execute_body(0), "INVALID_INPUT"),
    (execute_body(-80), "INVALID_INPUT"),
    (execute_body(80.001), "INVALID_INPUT"),
    (execute_body(80, amount_basis="guess"), "INVALID_INPUT"),
    ({k: v for k, v in execute_body(80).items() if k != "amount"}, "INVALID_INPUT"),  # amount always required
    (execute_body(80, status="executed"), "INVALID_INPUT"),                          # no extra fields
    (execute_body(80, decided_by="inventory"), "INVALID_INPUT"),                     # an agent name is not a person
])
def test_p7_missing_or_unsupported_confirmations_are_refused_and_change_nothing(work, c, body, code):
    buy = approved_tee_purchase(work, c)
    before = protected_digest(work)
    r = c.post(f"/api/actions/{buy}/execute", json=body, headers=H)
    assert (r.status_code, r.json()["error"]["code"]) == (422, code)
    assert protected_digest(work) == before and status(work, buy) == "approved"


def test_p8_only_purchases_execute_this_way_and_the_human_header_is_required(work, c):
    rent = propose(work, "propose_payment", "accounting", PAY_RENT)
    assert c.post(f"/api/actions/{rent}/execute", json=execute_body(2400), headers=H).json()["error"]["code"] == "INVALID_INPUT"
    assert status(work, rent) == "proposed"
    assert c.post("/api/actions/999/execute", json=execute_body(80), headers=H).status_code == 404
    buy = approved_tee_purchase(work, c)
    assert c.post(f"/api/actions/{buy}/execute", json=execute_body(80)).status_code == 403
    assert status(work, buy) == "approved"


# ---------------------------------------------------------------- cash constraints: actual, commitments, obligations first

def test_p9_insufficient_actual_cash_is_refused_and_changes_nothing(work, c):
    buy = approved_tee_purchase(work, c)
    with privileged(work) as con:
        con.execute("UPDATE cash_accounts SET balance = 50")                          # sandbox setup only
    before = protected_digest(work)
    r = c.post(f"/api/actions/{buy}/execute", json=execute_body(80), headers=H)
    assert r.status_code == 409 and r.json()["error"]["message"].startswith("Cash constraint: 80.00 exceeds")
    assert protected_digest(work) == before and status(work, buy) == "approved"


def test_p10_other_approved_purchases_are_counted_before_a_purchase_is_executed(work, c):
    buy = approved_tee_purchase(work, c)                                             # $80, cash 2560 after #501
    hoodies = propose(work, "propose_purchase", "inventory", HOODIES)               # $264 estimate
    assert c.post(f"/api/actions/{hoodies}/approve", json=ME, headers=H).status_code == 200
    with privileged(work) as con:
        con.execute("UPDATE cash_accounts SET balance = 300")                       # 300 >= 80 actual, but 300 - 264 < 80
    r = c.post(f"/api/actions/{buy}/execute", json=execute_body(80), headers=H)
    assert r.status_code == 409 and "other approved unexecuted purchases 264.00" in r.json()["error"]["message"]
    assert c.post(f"/api/actions/{hoodies}/reject", json=ME, headers=H).status_code == 200   # withdraw the other commitment
    assert c.post(f"/api/actions/{buy}/execute", json=execute_body(80), headers=H).status_code == 200
    assert cash(work) == 220


def test_p11_approved_unexecuted_payments_are_counted_before_a_purchase_is_executed(work, c):
    buy = approved_tee_purchase(work, c)                                             # cash 2560
    rent = propose(work, "propose_payment", "accounting", PAY_RENT)
    with privileged(work) as con:                                                    # an approved, unexecuted payment (D4)
        con.execute("UPDATE pending_actions SET status = 'approved', decided_by = 'Dr. Ryan' WHERE id = ?", (rent,))
        con.execute("UPDATE cash_accounts SET balance = 2450")                      # 2450 - 2400 = 50 < 80
    r = c.post(f"/api/actions/{buy}/execute", json=execute_body(80), headers=H)
    assert r.status_code == 409 and "approved unexecuted payments 2400.00" in r.json()["error"]["message"]
    assert cash(work) == 2450


def test_p12_purchase_commitments_never_block_paying_an_obligation(work, c):
    pay = propose(work, "propose_payment", "accounting", PAY_501)
    assert c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).status_code == 200      # 2560
    hoodies = propose(work, "propose_purchase", "inventory", HOODIES)
    assert c.post(f"/api/actions/{hoodies}/approve", json=ME, headers=H).status_code == 200  # $264 committed, not spent
    rent = propose(work, "propose_payment", "accounting", PAY_RENT)
    r = c.post(f"/api/actions/{rent}/approve", json=ME, headers=H)                            # 2560 - 2400 = 160: allowed
    assert r.status_code == 200 and cash(work) == 160
    r = c.post(f"/api/actions/{hoodies}/execute", json=execute_body(264), headers=H)          # 264 > 160: refused (R1)
    assert r.status_code == 409 and "Cash constraint" in r.json()["error"]["message"] and cash(work) == 160
    assert c.get("/api/cash").json()["available"]["for_purchases"] == 160 - 264              # shown, never stored


# ---------------------------------------------------------------- failures, lost events, crashes

def test_p13_a_failure_inside_the_transaction_rolls_everything_back(work, c, monkeypatch):
    buy = approved_tee_purchase(work, c)
    before = protected_digest(work)
    real = approval._audit

    def failing(con, **kw):
        if kw["event_type"] == "purchase_executed":
            raise sqlite3.OperationalError("simulated audit.db failure")
        return real(con, **kw)
    monkeypatch.setattr(approval, "_audit", failing)
    r = c.post(f"/api/actions/{buy}/execute", json=execute_body(80), headers=H)
    assert r.status_code == 503 and r.json()["error"]["code"] == "DB_UNAVAILABLE"
    assert protected_digest(work) == before and status(work, buy) == "approved"
    assert q(work, "SELECT COUNT(*) FROM payments WHERE kind = 'purchase'") == [(0,)]
    finished = [e for e in audit_trail.AuditTrail(work.audit_trail_json).read() if e["event_type"] == "human_decision_finished"]
    assert finished[-1]["outcome"]["status"] == "error"
    assert audit_reconcile.check(work)["consistent"]


class _LoseFinish(audit_trail.AuditTrail):
    def append(self, events):
        if any(e["event_type"] == "human_decision_finished" and (e.get("details") or {}).get("operation") == "execute_purchase"
               for e in events):
            raise audit_trail.AuditTrailError("simulated append failure")
        return super().append(events)


def test_p14_a_lost_completion_event_is_reported_and_reconciled_with_the_payment(work, monkeypatch):
    monkeypatch.setattr(approval, "APPEND_PAUSE", 0)
    with TestClient(main.create_app(p=work, trail=_LoseFinish(work.audit_trail_json))) as c:
        buy = approved_tee_purchase(work, c)
        b = c.post(f"/api/actions/{buy}/execute", json=execute_body(80), headers=H).json()
        assert b["status"] == "executed" and not b["audit_trail_recorded"] and b["reconciliation_pending"]
        [gap] = c.get("/api/audit").json()["incomplete"]
        assert (gap["kind"], gap["db_outcome"], gap["operation"], gap["decided_by"]) == \
               ("human_decision", "committed", "execute_purchase", "human:Dr. Ryan")
        assert gap["payment_ids"] == [b["payment"]["payment_id"]] and gap["audit_db_event_types"] == ["purchase_executed"]
    audit_reconcile.reconcile(work)
    assert audit_reconcile.check(work)["consistent"]


CRASH = r"""
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import approval, audit_trail, paths
p = paths.resolve(env={}, root=Path(sys.argv[2]), use_dotenv=False)
real = audit_trail.AuditTrail.append
def crash(self, events):
    if any(e["event_type"] == "human_decision_finished" and (e.get("outcome") or {}).get("status") == "ok" for e in events):
        os._exit(1)                                   # the process dies right after the commit
    return real(self, events)
audit_trail.AuditTrail.append = crash
approval.execute_purchase(p, audit_trail.AuditTrail(p.audit_trail_json), int(sys.argv[3]), decided_by="Dr. Ryan", amount=80,
                          amount_basis="recorded_unit_cost_estimate", amount_confirmed=True, supplier_confirmed=True)
print("NOT REACHED")
"""


def test_p15_an_interrupted_execution_is_discoverable_and_attributable(work, c):
    buy = approved_tee_purchase(work, c)
    out = subprocess.run([sys.executable, "-c", CRASH, str(REAL_ROOT / "mcp_server"), str(work.root), str(buy)],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 1 and "NOT REACHED" not in out.stdout, out.stderr[-1500:]
    assert cash(work) == 2480 and status(work, buy) == "executed"                    # the commit happened
    [gap] = audit_reconcile.check(work)["incomplete"]
    assert (gap["db_outcome"], gap["operation"], gap["decided_by"]) == ("committed", "execute_purchase", "human:Dr. Ryan")
    audit_reconcile.reconcile(work)
    assert audit_reconcile.check(work)["consistent"]


# ---------------------------------------------------------------- agents cannot reach the execution pathway

def test_p16_no_agent_can_execute_a_purchase(work):
    assert all(not ({"execute_purchase", "approve", "execute"} & tools) for tools in server.ACCESS.values())
    for name in ("server.py", "ops.py"):
        src = (REAL_ROOT / "mcp_server" / name).read_text(encoding="utf-8")
        assert "execute_purchase" not in src and "import approval" not in src
    buy = propose(work, "propose_purchase", "inventory", TEES)
    import asyncio
    script = Scripted({"boss": [[("delegate", "inventory", "x")]],
                       "inventory": [[("tool", "execute_purchase", {"action_id": buy, "amount": 80})]]})
    asyncio.run(runner.run_task("try", p=work, model=script.models(), mode="simulated", launcher=runner.in_memory_launcher))
    assert status(work, buy) == "proposed" and q(work, "SELECT COUNT(*) FROM payments") == [(0,)]
