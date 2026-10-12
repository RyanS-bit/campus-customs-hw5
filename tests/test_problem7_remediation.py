"""Problem 7 remediation (Dr. Ryan's review, follow-up 4): A1 resolution confirmation, A2 decision audit
completion, A3 reset audit consistency. All offline, on temporary copies (conftest `work`); crashes are real
process exits (os._exit) in a subprocess. No model, network or real database is touched.
"""

import json
import subprocess
import sys
import uuid
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

import approval
import audit_db
import audit_reconcile
import audit_trail
import main
import maintenance
import ops
from conftest import PROTECTED_TABLES, REAL_ROOT

H = {"X-Human-Decision": "confirm"}
ME = {"decided_by": "Dr. Ryan", "confirm": True}
SUMMARY = "Landlord confirmed by phone that the September rent was received"
RESOLVE = {**ME, "business_outcome_confirmed": True, "resolution_summary": SUMMARY}
RESET = {"requested_by": "Dr. Ryan", "confirm": "RESET"}
PAY_501 = dict(kind="invoice", ref_id=501, amount=840, ticket_id=101, rationale="Pay overdue invoice 501")
PAY_RENT = dict(kind="lease", ref_id=1, amount=2400, ticket_id=102, rationale="Pay rent due 2026-09-02")


class FlakyTrail(audit_trail.AuditTrail):
    """The real trail, except that append() fails for chosen events (simulating a disk/lock failure).
    Reconciliation uses append_with(), which is not affected."""

    def __init__(self, path, fail_when, times=None):
        super().__init__(path)
        self.fail_when, self.times, self.failures, self.active = fail_when, times, 0, True

    def append(self, events):
        if self.active and any(self.fail_when(e) for e in events) and (self.times is None or self.failures < self.times):
            self.failures += 1
            raise audit_trail.AuditTrailError("simulated append failure")
        return super().append(events)


def finished_decision(e):
    return e["event_type"] == "human_decision_finished"


def reset_outcome(e):
    return e["event_type"] == "db_reset"


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(approval, "APPEND_PAUSE", 0)


@pytest.fixture
def client_for(work):
    opened = []

    def make(trail=None):
        c = TestClient(main.create_app(p=work, trail=trail))
        c.__enter__()
        opened.append(c)
        return c
    yield make
    for c in opened:
        c.__exit__(None, None, None)


def propose(work, args, agent="accounting"):
    op = ops.propose_payment if "kind" in args else None
    return op(work, ops.RunIdentity(str(uuid.uuid4()), agent, "simulated"), **args)["links"]["action_id"]


def q(work, sql, args=()):
    with closing(maintenance.connect_ro(work.working_db)) as con:
        return con.execute(sql, args).fetchall()


def cash(work):
    return q(work, "SELECT balance FROM cash_accounts")[0][0]


def trail_events(work):
    return audit_trail.AuditTrail(work.audit_trail_json).read()


def audit_rows(work):
    with closing(maintenance.connect_ro(work.audit_db)) as con:
        return con.execute("SELECT id, actor, actor_kind, event_type, call_id, ref_table, ref_id, payload FROM events ORDER BY id").fetchall()


def digest_except_tickets(work):
    with closing(maintenance.connect_ro(work.working_db)) as con:
        return maintenance.content_digest(con, only=PROTECTED_TABLES - {"tickets"})


CRASH = r"""
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import approval, audit_trail, paths
p = paths.resolve(env={}, root=Path(sys.argv[2]), use_dotenv=False)
real = audit_trail.AuditTrail.append
def crash(self, events):
    if any(e["event_type"] == sys.argv[3] and (e.get("outcome") or {}).get("status") == "ok" for e in events):
        os._exit(1)                                   # the process dies right after the commit / swap
    return real(self, events)
audit_trail.AuditTrail.append = crash
tr = audit_trail.AuditTrail(p.audit_trail_json)
if sys.argv[4] == "approve":
    approval.approve_action(p, tr, int(sys.argv[5]), decided_by="Dr. Ryan")
else:
    approval.controlled_reset(p, tr, requested_by="Dr. Ryan", via="crash test")
print("NOT REACHED")
"""


def crash_in_subprocess(work, event_type, *args):
    out = subprocess.run([sys.executable, "-c", CRASH, str(REAL_ROOT / "mcp_server"), str(work.root), event_type, *args],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 1 and "NOT REACHED" not in out.stdout, out.stderr[-1500:]


# ================================================================ A1: resolution needs an explicit business confirmation

@pytest.mark.parametrize("body", [
    ME,                                                                           # old body: no confirmation at all
    {**ME, "resolution_summary": SUMMARY},                                        # summary but no confirmation
    {**ME, "business_outcome_confirmed": False, "resolution_summary": SUMMARY},   # explicitly not confirmed
    {**ME, "business_outcome_confirmed": "yes", "resolution_summary": SUMMARY},
    {**ME, "business_outcome_confirmed": True},                                   # no summary
    {**ME, "business_outcome_confirmed": True, "resolution_summary": "done"},     # too short to say how
])
def test_a1_1_resolution_without_an_explicit_business_confirmation_is_refused(work, client_for, body):
    c = client_for()
    r = c.post("/api/tickets/103/resolve", json=body, headers=H)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_INPUT"
    assert q(work, "SELECT status FROM tickets WHERE id = 103")[0][0] == "open"
    assert not work.audit_trail_json.exists() or not trail_events(work)


def test_a1_2_the_function_itself_refuses_without_confirmation(work):
    tr = audit_trail.AuditTrail(work.audit_trail_json)
    for confirmed in (False, None, "true", 1):
        with pytest.raises(approval.DecisionError) as e:
            approval.resolve_ticket(work, tr, 103, decided_by="Dr. Ryan", business_outcome_confirmed=confirmed,
                                    resolution_summary=SUMMARY)
        assert e.value.code == "INVALID_INPUT"
    assert q(work, "SELECT status FROM tickets WHERE id = 103")[0][0] == "open"


def test_a1_3_deciding_every_proposal_never_resolves_a_ticket(work, client_for):
    c = client_for()
    pay = propose(work, PAY_RENT)
    assert c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).status_code == 200
    t = {x["id"]: x for x in c.get("/api/tickets").json()["tickets"]}
    assert t[102]["status"] == "open" and t[102]["awaiting_human_decision"] == 0 and not t[102]["is_resolved"]


def test_a1_4_resolution_records_the_statement_and_the_facts_without_inferring_anything(work, client_for):
    c = client_for()
    pay = propose(work, PAY_RENT)
    assert c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).status_code == 200
    before = digest_except_tickets(work)
    r = c.post("/api/tickets/102/resolve", json=RESOLVE, headers=H)
    assert r.status_code == 200, r.json()
    b = r.json()
    assert b["status"] == "resolved" and b["business_outcome_confirmed_by"] == "Dr. Ryan" and b["resolution_summary"] == SUMMARY
    assert "their statement" in b["meaning"] and "not verified" in b["meaning"]
    assert any("customer acceptance" in x for x in b["not_verified_by_system"])
    assert any("delivery" in x for x in b["not_verified_by_system"])
    assert b["system_records_at_resolution"] == {"proposals": [{"action_id": pay, "kind": "payment", "status": "executed",
                                                                "amount": 2400.0, "payment_id": 1}], "unsent_drafts": 0}
    # Nothing else changed: no payment, delivery, stock or cash movement is invented by resolving.
    assert digest_except_tickets(work) == before
    ev = [json.loads(r_[7]) for r_ in audit_rows(work) if r_[3] == "ticket_resolved"][0]
    assert ev["business_outcome_confirmed"] is True and ev["resolution_summary"] == SUMMARY
    assert ev["basis"].startswith("the person's own confirmation") and ev["not_verified_by_system"]
    req = [e for e in trail_events(work) if e["event_type"] == "human_decision_requested"][-1]
    assert req["details"]["business_outcome_confirmed"] is True and req["details"]["resolution_summary"] == SUMMARY


# ================================================================ A2: a committed decision stays discoverable and attributable

def test_a2_1_a_lost_completion_event_is_reported_then_reconciled_before_the_next_decision(work, client_for):
    tr = FlakyTrail(work.audit_trail_json, finished_decision)
    c = client_for(tr)
    pay = propose(work, PAY_501)
    r = c.post(f"/api/actions/{pay}/approve", json=ME, headers=H)
    assert r.status_code == 200
    b = r.json()
    assert not b["audit_trail_recorded"] and b["reconciliation_pending"] and tr.failures == approval.APPEND_ATTEMPTS
    assert b["decision_id"] in b["warnings"][0] and "The decision COMMITTED" in b["warnings"][0]
    assert cash(work) == 2560                                                 # the payment really happened
    # Discoverable: the audit status names the decision, the person, the payment and the audit.db events.
    st = c.get("/api/audit").json()
    assert not st["consistent"]
    [gap] = st["incomplete"]
    assert (gap["kind"], gap["db_outcome"], gap["decision_id"], gap["decided_by"], gap["operation"], gap["action_id"]) == \
           ("human_decision", "committed", b["decision_id"], "human:Dr. Ryan", "approve", pay)
    assert gap["payment_ids"] == [1] and gap["audit_db_event_types"] == ["action_approved", "payment_executed"]
    # Reconciled automatically, under the lock, before the next decision; recorded once.
    tr.active = False
    other = propose(work, PAY_RENT)
    r2 = c.post(f"/api/actions/{other}/reject", json=ME, headers=H).json()
    assert r2["earlier_gaps_reconciled"] == 1
    evs = trail_events(work)
    rec = [e for e in evs if e["event_type"] == "incomplete_operation_reconciled"]
    assert len(rec) == 1 and rec[0]["details"]["kind"] == "human_decision"
    assert rec[0]["outcome"] == {"status": "incomplete", "db_outcome": "committed"}
    assert rec[0]["details"]["decision_id"] == b["decision_id"] and rec[0]["details"]["payment_ids"] == [1]
    assert "cannot be committed atomically" in rec[0]["details"]["note"]
    second_request = next(e for e in evs if e["event_type"] == "human_decision_requested" and e["call_id"] == r2["decision_id"])
    assert rec[0]["seq"] < second_request["seq"]
    assert c.get("/api/audit").json()["consistent"]
    assert audit_reconcile.reconcile(work) == []                              # never recorded twice


def test_a2_2_a_transient_append_failure_is_retried_without_a_gap(work, client_for):
    tr = FlakyTrail(work.audit_trail_json, finished_decision, times=1)
    c = client_for(tr)
    pay = propose(work, PAY_501)
    b = c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).json()
    assert b["audit_trail_recorded"] and not b["reconciliation_pending"] and tr.failures == 1
    assert len([e for e in trail_events(work) if e["event_type"] == "human_decision_finished"]) == 1
    assert audit_reconcile.check(work)["consistent"]


def test_a2_3_a_crash_after_the_commit_leaves_a_discoverable_attributed_payment(work):
    pay = propose(work, PAY_501)
    crash_in_subprocess(work, "human_decision_finished", "approve", str(pay))
    assert cash(work) == 2560 and q(work, "SELECT approved_by FROM payments") == [("Dr. Ryan",)]
    report = audit_reconcile.check(work)
    [gap] = report["incomplete"]
    assert (gap["kind"], gap["db_outcome"], gap["decided_by"], gap["payment_ids"]) == \
           ("human_decision", "committed", "human:Dr. Ryan", [1])
    assert not report["consistent"]
    added = audit_reconcile.reconcile(work)
    assert len(added) == 1 and added[0]["details"]["payment_ids"] == [1]
    assert audit_reconcile.check(work)["consistent"]


def test_a2_4_a_refused_decision_with_a_lost_event_is_shown_as_not_committed(work, client_for):
    with closing(maintenance.connect_rw(work.working_db)) as con:
        con.execute("UPDATE cash_accounts SET balance = 500")                # sandbox setup: payment impossible (R1)
    tr = FlakyTrail(work.audit_trail_json, finished_decision)
    c = client_for(tr)
    pay = propose(work, PAY_501)
    assert c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).status_code == 409
    [gap] = audit_reconcile.check(work)["incomplete"]
    assert gap["db_outcome"] == "not_found" and "nothing from this decision was committed" in gap["reason"]
    assert cash(work) == 500 and q(work, "SELECT COUNT(*) FROM payments")[0][0] == 0


def test_a2_5_after_a_later_reset_the_committed_payment_is_still_attributed(work, client_for):
    tr = FlakyTrail(work.audit_trail_json, finished_decision)
    c = client_for(tr)
    pay = propose(work, PAY_501)
    decision = c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).json()["decision_id"]
    maintenance.reset_working_db(work)                                         # a reset outside the API (e.g. the CLI)
    [gap] = audit_reconcile.check(work)["incomplete"]
    assert (gap["db_outcome"], gap["decision_id"], gap["decided_by"], gap["payment_ids"]) == \
           ("committed_row_no_longer_present", decision, "human:Dr. Ryan", [1])


def test_a2_6_a_human_audit_db_event_without_a_json_request_is_flagged(work):
    audit_db.ensure(work.audit_db)
    with closing(maintenance.connect_rw(work.audit_db)) as con:              # e.g. tampering, or another tool
        con.execute("BEGIN IMMEDIATE")
        audit_db.append_event(con, actor="human:Someone", actor_kind="human", event_type="payment_executed", schema="main",
                              payload={}, call_id=str(uuid.uuid4()), ref_table="payments", ref_id=1)
        con.execute("COMMIT")
    report = audit_reconcile.check(work)
    assert not report["consistent"] and any("has no JSON human_decision_requested" in x for x in report["cross_reference_problems"])


# ================================================================ A3: reset history cannot silently become inconsistent

def test_a3_1_a_reset_is_recorded_before_and_after_and_stamped_into_the_new_copy(work, client_for):
    c = client_for()
    b = c.post("/api/reset", json=RESET, headers=H).json()
    assert b["reset"] and b["audit_trail_recorded"] and not b["reconciliation_pending"] and b["warnings"] == []
    assert maintenance.read_reset_stamp(work.working_db) == b["reset_id"]
    types = [(e["event_type"], (e["details"] or {}).get("reset_id")) for e in trail_events(work)]
    assert types == [("db_reset_requested", b["reset_id"]), ("db_reset", b["reset_id"])]
    rows = [r for r in audit_rows(work) if r[3].startswith("db_reset")]
    assert [r[3] for r in rows] == ["db_reset_started", "db_reset_finished"]
    assert [r[0] for r in rows] == [b["audit_db_event_ids"]["started"], b["audit_db_event_ids"]["finished"]]
    assert c.get("/api/audit").json()["consistent"]


def test_a3_2_a_lost_reset_outcome_is_proven_by_the_stamp_and_reconciled(work, client_for):
    tr = FlakyTrail(work.audit_trail_json, reset_outcome)
    c = client_for(tr)
    b = c.post("/api/reset", json=RESET, headers=H).json()
    assert b["reset"] and not b["audit_trail_recorded"] and b["reconciliation_pending"]
    assert b["reset_id"] in b["warnings"][-1] and "RESET COMPLETED" in b["warnings"][-1]
    [gap] = c.get("/api/audit").json()["incomplete"]
    assert (gap["kind"], gap["db_outcome"], gap["reset_id"]) == ("db_reset", "committed", b["reset_id"])
    tr.active = False
    pay = propose(work, PAY_501)
    assert c.post(f"/api/actions/{pay}/reject", json=ME, headers=H).json()["earlier_gaps_reconciled"] == 1
    assert c.get("/api/audit").json()["consistent"]


def test_a3_3_a_crash_right_after_the_swap_is_recovered_from_the_stamp(work):
    pay = propose(work, PAY_501)
    approval.approve_action(work, audit_trail.AuditTrail(work.audit_trail_json), pay, decided_by="Dr. Ryan")
    assert cash(work) == 2560
    crash_in_subprocess(work, "db_reset", "reset")
    assert cash(work) == 3400                                                  # the reset did take effect
    request = [e for e in trail_events(work) if e["event_type"] == "db_reset_requested"][-1]
    assert maintenance.read_reset_stamp(work.working_db) == request["details"]["reset_id"]
    [gap] = audit_reconcile.check(work)["incomplete"]
    assert (gap["kind"], gap["db_outcome"]) == ("db_reset", "committed")
    audit_reconcile.reconcile(work)
    assert audit_reconcile.check(work)["consistent"]


def test_a3_4_a_failed_swap_changes_nothing_and_its_outcome_is_recorded(work, client_for, monkeypatch):
    c = client_for()
    pay = propose(work, PAY_501)
    assert c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).status_code == 200
    def fail(*_a, **_k):
        raise maintenance.ResetError("simulated: working copy in use")
    monkeypatch.setattr(maintenance, "reset_working_db_locked", fail)
    r = c.post("/api/reset", json=RESET, headers=H)
    assert r.status_code == 500 and r.json()["error"]["code"] == "RESET_FAILED"
    assert cash(work) == 2560 and maintenance.read_reset_stamp(work.working_db) is None
    outcome = [e for e in trail_events(work) if e["event_type"] == "db_reset"][-1]
    assert outcome["outcome"]["status"] == "error" and outcome["details"]["stage"] == "reset"
    assert [r_[3] for r_ in audit_rows(work)][-2:] == ["db_reset_started", "db_reset_finished"]
    assert json.loads(audit_rows(work)[-1][7])["status"] == "error"
    assert c.get("/api/audit").json()["consistent"]


def test_a3_5_a_failed_swap_with_a_lost_outcome_is_shown_as_not_replacing_the_copy(work, client_for, monkeypatch):
    tr = FlakyTrail(work.audit_trail_json, reset_outcome)
    c = client_for(tr)
    monkeypatch.setattr(maintenance, "reset_working_db_locked",
                        lambda *_a, **_k: (_ for _ in ()).throw(maintenance.ResetError("simulated")))
    assert c.post("/api/reset", json=RESET, headers=H).status_code == 500
    [gap] = audit_reconcile.check(work)["incomplete"]
    assert gap["db_outcome"] == "not_found" and "did not replace the working copy" in gap["reason"]


def test_a3_6_if_audit_db_cannot_mark_the_boundary_nothing_is_reset(work, client_for, monkeypatch):
    c = client_for()
    pay = propose(work, PAY_501)
    assert c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).status_code == 200
    real = approval._audit_db_system
    def failing(p, event_type, payload):
        if event_type == "db_reset_started":
            raise OSError("simulated: audit.db not writable")
        return real(p, event_type, payload)
    monkeypatch.setattr(approval, "_audit_db_system", failing)
    r = c.post("/api/reset", json=RESET, headers=H)
    assert r.status_code == 503 and r.json()["error"]["code"] == "AUDIT_FAILED"
    assert cash(work) == 2560
    outcome = [e for e in trail_events(work) if e["event_type"] == "db_reset"][-1]
    assert outcome["outcome"]["status"] == "error" and outcome["details"]["working_copy_replaced"] is False


def test_a3_7_if_the_request_cannot_be_recorded_nothing_is_reset(work, client_for):
    c = client_for()
    pay = propose(work, PAY_501)
    assert c.post(f"/api/actions/{pay}/approve", json=ME, headers=H).status_code == 200
    data = json.loads(work.audit_trail_json.read_text(encoding="utf-8"))
    data[0]["actor"] = "human:Someone Else"                                    # damage the trail
    work.audit_trail_json.write_text(json.dumps(data), encoding="utf-8")
    r = c.post("/api/reset", json=RESET, headers=H)
    assert r.status_code == 503 and r.json()["error"]["code"] == "AUDIT_FAILED"
    assert cash(work) == 2560 and maintenance.read_reset_stamp(work.working_db) is None


def test_a3_8_a_later_reset_makes_an_unfinished_one_indeterminate_not_guessed(work, client_for, monkeypatch):
    tr = FlakyTrail(work.audit_trail_json, reset_outcome)
    c = client_for(tr)
    assert c.post("/api/reset", json=RESET, headers=H).status_code == 200      # outcome lost
    sys.path.insert(0, str(REAL_ROOT / "scripts"))
    import reset_db
    assert reset_db.main(["--confirm", "--root", str(work.root)]) == 0         # a CLI reset replaces the copy (no stamp)
    gaps = audit_reconcile.check(work)["incomplete"]
    assert len(gaps) == 1 and gaps[0]["db_outcome"] == "indeterminate" and "later reset" in gaps[0]["reason"]
    assert maintenance.read_reset_stamp(work.working_db) is None


def test_a3_9_the_stamp_never_alters_the_original_tables(work, client_for):
    c = client_for()
    c.post("/api/reset", json=RESET, headers=H)
    with closing(maintenance.connect_ro(work.original_db)) as o, closing(maintenance.connect_ro(work.working_db)) as w:
        names = {n for _, n, _ in maintenance.schema_objects(o)}
        assert maintenance.content_digest(w, only=names) == maintenance.content_digest(o)
    assert c.get("/api/cash").json()["checking_balance"] == 3400
