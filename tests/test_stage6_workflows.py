"""Stage 6: integrated multi-agent workflows for tickets 101, 102 and 103 (OFFLINE).

Scripted offline models stand in for gpt-6-luna; the MCP servers, rules, database writes, delegation
and audit trail are the real code, on temporary databases and temporary trails. These tests show the
PLUMBING behaves correctly for plausible agent decisions. They are not evidence of how gpt-6-luna
will actually decide.
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid

import pytest

import audit_reconcile
import audit_trail
import models
import runner
import server
from agent_script import Scripted
from conftest import REAL_ROOT, counts, privileged

PAY_501 = {"kind": "invoice", "ref_id": 501, "amount": 840, "ticket_id": 101, "rationale": "Pay overdue invoice 501 to unblock Bulldog Print Co"}
PAY_RENT = {"kind": "lease", "ref_id": 1, "amount": 2400, "ticket_id": 102, "rationale": "Rent due 2026-09-02 per lease 1"}
BUY_103 = {"vendor_id": 1, "sku": "CC-HOOD-NAVY", "size": "M", "qty": 12, "ticket_id": 103, "rationale": "Cover the 12-unit shortfall"}
QUOTE_103 = {"ticket_id": 103, "sku": "CC-HOOD-NAVY", "size": "M", "qty": 20, "unit_price": 45, "rationale": "Bulk discount within policy"}


def run(work, script, task, entry="boss", budget=None):
    return asyncio.run(runner.run_task(task, p=work, model=script.models(), mode="simulated", entry=entry,
                                       budget=budget, launcher=runner.in_memory_launcher))


def events(work):
    return audit_trail.AuditTrail(work.audit_trail_json).read()


def returned(script, agent, tool):
    return [c for n, c in script.returns[agent] if n == tool]


def db(work, sql, *args):
    with privileged(work) as con:
        return con.execute(sql, args).fetchall()


def assert_no_business_change(work):
    """Nothing executed: cash, invoices, leases, inventory, payments and tickets as in the original."""
    assert db(work, "SELECT balance FROM cash_accounts") == [(3400.0,)]
    assert db(work, "SELECT status FROM invoices WHERE id = 501") == [("open",)]
    assert db(work, "SELECT next_due FROM leases WHERE id = 1") == [("2026-09-02",)]
    assert db(work, "SELECT COUNT(*) FROM payments") == [(0,)]
    assert db(work, "SELECT SUM(qty) FROM inventory") == [(82,)]
    assert db(work, "SELECT id, status FROM tickets ORDER BY id") == [(101, "open"), (102, "open"), (103, "open")]


# ---- ticket 101: Tauhid Zaman, one tee size S (stock 0; reprint invoice 501 unpaid) ----------

def test_w1_ticket_101(work):
    s = Scripted({
        "boss": [[("tool", "get_ticket", {"ticket_id": 101}),
                  ("delegate", "inventory", "Is CC-TEE-WHITE size S available, and can it be restocked?"),
                  ("delegate", "accounting", "Prepare payment of invoice 501, which blocks the reprint vendor"),
                  ("delegate", "customer_service", "Draft a reply to Tauhid Zaman without promising a date"),
                  ("final", {"summary": "Tee S out of stock; payment of #501 proposed; customer draft prepared",
                             "needs_human": ["Approve or reject payment proposal 1"],
                             "unknowns": ["Whether the goods on invoice 501 were delivered"]})]],
        "inventory": [[("tool", "check_inventory", {"sku": "CC-TEE-WHITE"}), ("tool", "list_vendors", {})]],
        "accounting": [[("tool", "get_financial_position", {}), ("tool", "propose_payment", PAY_501)]],
        "customer_service": [[("tool", "draft_message", {"ticket_id": 101, "subject": "Your size S tee",
                                                          "body": "Size S is currently out of stock. We are working on a restock and will update you."})]],
    })
    out = run(work, s, "Handle ticket 101")
    assert out.status == "completed"
    # genuine facts reached the agents through MCP
    tee = returned(s, "inventory", "check_inventory")[0]["products"][0]
    assert {z["size"]: z["qty"] for z in tee["sizes"]}["S"] == 0
    bulldog = [v for v in returned(s, "inventory", "list_vendors")[0]["vendors"] if v["id"] == 1][0]
    assert bulldog["blocked"] and bulldog["computed"]["earliest_arrival_if_ordered_today"] is None
    assert "Whether the goods billed on invoice #501 were delivered is not recorded." in returned(s, "boss", "get_ticket")[0]["unknowns"]
    # only authorized proposals and drafts exist; nothing executed
    assert db(work, "SELECT kind, invoice_id, amount, status, proposed_by FROM pending_actions") == [("payment", 501, 840.0, "proposed", "accounting")]
    assert db(work, "SELECT recipient, recipient_source, status, created_by FROM drafts") == [("Tauhid Zaman", "tickets.requester", "draft", "customer_service")]
    assert_no_business_change(work)
    assert out.report.needs_human == ["Approve or reject payment proposal 1"]


# ---- ticket 102: Elm City Properties rent notice ------------------------------------------

def test_w2_ticket_102_facilities_delegates_payment_then_drafts(work):
    s = Scripted({
        "boss": [[("delegate", "facilities", "Handle the rent notice on ticket 102")]],
        "facilities": [[("tool", "get_ticket", {"ticket_id": 102}),
                        ("delegate", "accounting", "Propose paying the lease 1 rent"),
                        ("tool", "draft_message", {"ticket_id": 102, "subject": "Rent notice received",
                                                   "body": "We received your notice. A payment of $2,400.00 is awaiting internal approval.",
                                                   "related_action_id": 1})]],
        "accounting": [[("tool", "propose_payment", PAY_RENT)]],
    })
    out = run(work, s, "Handle ticket 102")
    assert out.status == "completed"
    pay = returned(s, "accounting", "propose_payment")[0]
    assert pay["proposal"]["fundable_at_proposal"] is True and pay["executable"] is False
    started = [e for e in events(work) if e["event_type"] == "tool_call_started" and e["tool"] == "propose_payment"][0]
    assert started["actor"] == "accounting" and started["chain"] == ["boss", "facilities", "accounting"]
    draft = returned(s, "facilities", "draft_message")[0]
    assert draft["draft"]["recipient"] == "Elm City Properties" and draft["sent"] is False
    assert any("NOT approved" in w and "warning stands" in w for w in draft["warnings"])
    assert_no_business_change(work)


# ---- ticket 103: Yale AI Club, 20 hoodies M at a discount (8 on hand) ----------------------

def test_w3_ticket_103_quote_and_blocked_restock(work):
    s = Scripted({
        "boss": [[("delegate", "customer_service", "Respond to Yale AI Club's bulk discount request")]],
        "customer_service": [[("tool", "check_inventory", {"sku": "CC-HOOD-NAVY"}),
                              ("delegate", "inventory", "Can we restock 12 hoodies size M?"),
                              ("tool", "propose_quote", QUOTE_103),
                              ("tool", "draft_message", {"ticket_id": 103, "subject": "Your hoodie order",
                                                         "body": "We can offer $45.00 per hoodie, subject to approval. We have 8 in stock today.",
                                                         "related_action_id": 2})]],
        "inventory": [[("tool", "list_vendors", {}), ("tool", "propose_purchase", BUY_103)]],
    })
    out = run(work, s, "Handle ticket 103")
    assert out.status == "completed"
    buy = returned(s, "inventory", "propose_purchase")[0]
    assert buy["proposal"]["vendor_blocked"] is True and buy["proposal"]["amount_basis"] == "estimate_unit_cost"
    assert buy["proposal"]["amount"] == 264.0 and any(w.startswith("R3:") for w in buy["warnings"])
    quote = returned(s, "customer_service", "propose_quote")[0]
    assert quote["customer_acceptance"] == "not recorded" and quote["computed"]["shortfall"] == 12
    assert db(work, "SELECT qty FROM inventory WHERE sku = 'CC-HOOD-NAVY' AND size = 'M'") == [(8,)]
    assert_no_business_change(work)


# ---- all three in one run: cross-ticket cash competition --------------------------------

def test_w4_all_three_tickets_compete_for_cash(work):
    s = Scripted({
        "boss": [[("tool", "get_open_tickets", {}),
                  ("delegate", "accounting", "Propose the payments for tickets 101 and 102"),
                  ("delegate", "inventory", "Propose a restock for ticket 103"),
                  ("tool", "list_pending_actions", {})]],
        "accounting": [[("tool", "propose_payment", PAY_501), ("tool", "propose_payment", PAY_RENT)]],
        "inventory": [[("tool", "propose_purchase", BUY_103)]],
    })
    out = run(work, s, "Handle all open tickets")
    assert out.status == "completed"
    summary = returned(s, "boss", "list_pending_actions")[0]["computed"]
    assert (summary["proposed_payments_total"], summary["cash_if_all_proposed_payments_executed"], summary["estimated_purchases_total"]) == (3240.0, 160.0, 264.0)
    buy = returned(s, "inventory", "propose_purchase")[0]
    assert any("exceeds cash remaining" in w and "160.00" in w for w in buy["warnings"])
    assert [t["id"] for t in returned(s, "boss", "get_open_tickets")[0]["tickets"]] == [101, 102, 103]
    assert_no_business_change(work)


# ---- stopping at human approval and unavailable information -------------------------------

def test_w5_agents_stop_at_approval_boundaries_and_refusals_change_nothing(work):
    s = Scripted({
        "boss": [[("delegate", "accounting", "Pay invoice 501 now"),
                  ("tool", "propose_payment", PAY_501),                                     # Boss has no such tool
                  ("final", {"summary": "Payment proposed; approval required", "needs_human": ["Approve payment 1"]})]],
        "accounting": [[("tool", "propose_payment", PAY_501),
                        ("tool", "propose_payment", PAY_501),                               # retry: duplicate
                        ("tool", "propose_payment", {**PAY_501, "amount": 900}),            # not the stored amount
                        ("tool", "approve_payment", {"action_id": 1})]],                    # no approval tool exists
    })
    out = run(work, s, "Pay invoice 501")
    assert out.status == "completed"
    results = returned(s, "accounting", "propose_payment")
    assert results[0]["created"] is True
    assert results[1]["ok"] is False and results[1]["error"].startswith("DUPLICATE")
    assert results[2]["ok"] is False and "policy:full_amount_only" in results[2]["error"]
    assert any("Unknown tool" in str(c) for c in returned(s, "accounting", "approve_payment"))
    assert any("Unknown tool" in str(c) for c in returned(s, "boss", "propose_payment"))
    assert db(work, "SELECT status FROM pending_actions") == [("proposed",)]
    assert_no_business_change(work)
    finished = [e for e in events(work) if e["event_type"] == "tool_call_finished" and e["tool"] == "propose_payment"]
    assert [e["outcome"]["status"] for e in finished] == ["ok", "refused", "refused"]


def test_w5_unavailable_information_is_reported_not_invented(work):
    s = Scripted({"boss": [[("delegate", "inventory", "When will the tee reprint arrive?")]],
                  "inventory": [[("tool", "list_vendors", {}),
                                 ("final", {"summary": "Arrival cannot be estimated while the vendor is blocked",
                                            "unknowns": ["Reprint arrival date: vendor blocked by invoice 501; no order exists"]})]]})
    out = run(work, s, "Reprint arrival?")
    bulldog = [v for v in returned(s, "inventory", "list_vendors")[0]["vendors"] if v["id"] == 1][0]
    assert bulldog["computed"]["earliest_arrival_if_ordered_today"] is None
    assert out.status == "completed" and counts(work) == {"pending_actions": 0, "drafts": 0, "events": 0}


# ---- failure handling, shared usage, reconciliation -------------------------------------

def test_w6_failure_mid_workflow_keeps_committed_work_visible_and_reconcilable(work):
    s = Scripted({"boss": [[("delegate", "accounting", "Propose payment of invoice 501"),
                            ("delegate", "customer_service", "Draft to Tauhid Zaman")]],
                  "accounting": [[("tool", "propose_payment", PAY_501)]],
                  "customer_service": [[("raise", "model failure while drafting")]]})
    out = run(work, s, "Handle ticket 101")
    assert out.status == "completed"
    assert counts(work) == {"pending_actions": 1, "drafts": 0, "events": 1}
    failed = [e for e in events(work) if e["event_type"] == "delegation_completed" and e["details"]["to"] == "customer_service"][0]
    assert failed["outcome"]["status"] == "error"
    assert audit_reconcile.check(work)["consistent"]


def test_w7_shared_usage_limit_stops_a_multi_ticket_run_without_partial_corruption(work):
    s = Scripted({"boss": [[("delegate", "accounting", "Propose both payments"), ("delegate", "inventory", "Restock 103")]],
                  "accounting": [[("tool", "propose_payment", PAY_501), ("tool", "propose_payment", PAY_RENT)]],
                  "inventory": [[("tool", "propose_purchase", BUY_103)]]})
    out = run(work, s, "Handle all open tickets", budget=models.RunBudget(request_limit=4))
    assert out.status == "stopped" and "request_limit" in out.error
    created = counts(work)
    assert created["pending_actions"] == created["events"]                     # every committed row has its audit.db event
    assert "inventory" not in s.runs
    assert audit_reconcile.check(work)["consistent"]
    assert_no_business_change(work)


def test_w8_run_history_is_preserved_across_successive_runs(work):
    first = run(work, Scripted({"boss": [[("tool", "get_open_tickets", {})]]}), "Survey tickets")
    history = events(work)
    second = run(work, Scripted({"boss": [[("delegate", "accounting", "Propose 501")]],
                                 "accounting": [[("tool", "propose_payment", PAY_501)]]}), "Ticket 101 payment")
    evs = events(work)
    assert evs[: len(history)] == history
    assert {e["run_id"] for e in evs} == {first.run_id, second.run_id}
    assert audit_trail.AuditTrail(work.audit_trail_json).verify()["valid"]
    finished = [e for e in evs if e["event_type"] == "tool_call_finished" and e["tool"] == "propose_payment"][0]
    assert db(work, "SELECT call_id, run_id FROM pending_actions") == [(finished["call_id"], second.run_id)]


# ---- architecture consistency (Stage 5 still matches the approved design) ----------------

def test_w9_permission_sources_agree():
    import ops
    import policies
    for op, agents_allowed in ops.OPERATION_AGENTS.items():
        assert agents_allowed == {a for a, tools in server.ACCESS.items() if op in tools}
    assert set(policies.DRAFT_AUTHORITY) == {a for a, tools in server.ACCESS.items() if "draft_message" in tools}
    assert server.ACCESS["boss"].isdisjoint(server.WRITE_TOOLS)
    assert set(models.AGENT_NAMES) == set(server.ACCESS) == set(ops.AGENTS)


# ---- the connectivity script's dry run sends and writes nothing ---------------------------

def test_w10_connectivity_dry_run_exits_2_and_sends_nothing():
    report = REAL_ROOT / "output" / "p5_live_connectivity.json"
    before = report.read_bytes() if report.exists() else None
    env = {k: v for k, v in os.environ.items() if "KEY" not in k}
    env.update(PORTKEY_BASE_URL="http://127.0.0.1:9/v1", PORTKEY_API_KEY="dummy-offline-key")
    proc = subprocess.run([sys.executable, str(REAL_ROOT / "scripts" / "live_connectivity_check.py")],
                          capture_output=True, text=True, timeout=60, env=env)
    assert proc.returncode == 2 and "Nothing sent" in proc.stderr
    assert (report.read_bytes() if report.exists() else None) == before
