"""Human-only decisions on agent proposals, and human ticket resolution (Problem 7).

Built to the approved Problem 5 design (architecture sections 4, 5, 13 and 14; spec section 7.7),
where it was deferred to Problem 7. This is the ONLY code that approves, rejects or executes:

  approve_action   payment  -> approve AND execute in one atomic transaction (R1, R4, D4, D6)
                   purchase -> status 'approved' = an AUTHORIZATION only; no cash moves (R3 shown as a warning)
  execute_purchase an approved purchase -> 'executed': cash is paid, with a confirmed amount and basis, the person's
                   supplier statement, R3 (refused while blocked: our choice) and the cash check (Problem 9, F3)
                   quote    -> status 'approved' only; nothing is sent, no revenue (R6, R7)
  reject_action    any proposal still 'proposed' (or an approved, unexecuted purchase) -> 'rejected'
  resolve_ticket   an open ticket -> 'resolved', ONLY with the person's explicit confirmation that the underlying
                   business issue is resolved and their own summary of how (agents have no ticket-status tool).
                   Nothing is inferred from approvals: the system does not record deliveries, shipments, customer
                   acceptance or messages sent, and says so in the record.
  controlled_reset the working database reset (R8) with intent and outcome records (Problem 7, A3)

Why no agent can reach this (architecture section 13):
  1. Not exposed: nothing here is an MCP tool, and agents have no HTTP, shell or code tools.
  2. Not imported by agent code: a test fails if server.py, ops.py or the agent modules import it.
  3. The MCP connection's authorizer denies every UPDATE and every write to money tables.
  4. A trigger refuses any pending_actions row inserted as anything but 'proposed'.

Every decision:
  * holds the run lock (no agent run, reset or other decision can overlap; refuses at once if busy),
  * appends 'human_decision_requested' to output/audit_trail.json BEFORE changing anything
    (if that fails, nothing changes),
  * changes the working database and appends hash-chained audit.db events in ONE transaction
    (journal_mode DELETE + synchronous FULL on both files, checked; commit both or neither),
  * appends 'human_decision_finished' with the outcome afterwards (retried briefly).

NOT ATOMIC ACROSS FILES: the JSON trail cannot be committed together with SQLite. The guarantee is instead:
  * the business change and its audit.db events are atomic (one SQLite transaction); audit.db carries the
    decision_id, so a committed decision is always discoverable there and attributed to the requesting name;
  * the JSON 'requested' event always exists BEFORE any change; if the 'finished' event is lost (error or crash),
    audit_reconcile reports the gap (GET /api/audit, scripts/verify_audit.py) and records what audit.db shows;
  * every new decision and every reset first reconciles any earlier gap while holding the run lock, so gaps
    cannot accumulate silently.

LOCAL PROTOTYPE: the name given is recorded for attribution only. It is not authenticated.
"""

from __future__ import annotations

import calendar
import re
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import date

import audit_db
import audit_reconcile
import audit_trail
import locks
import maintenance
import policies
from paths import Paths, assert_writable

NOTICE = ("LOCAL PROTOTYPE: not an authenticated approval system. The name entered is recorded for "
          "attribution only.")
DECISION_CODES = ("NOT_FOUND", "NOT_PENDING", "REFUSED_RULE", "BUSY", "INVALID_INPUT", "DB_UNAVAILABLE",
                  "AUDIT_FAILED", "RESET_FAILED")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'\-]{1,79}$")
RESERVED_NAMES = {"boss", "inventory", "accounting", "facilities", "customer_service", "customer service",
                  "system", "agent", "admin", "root"}
NOTE_MAX = 500


class DecisionError(Exception):
    def __init__(self, code: str, message: str):
        assert code in DECISION_CODES
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


# ---------------------------------------------------------------- inputs

def validate_decider(name) -> str:
    """The person making the decision, for ATTRIBUTION ONLY (no authentication exists)."""
    if not isinstance(name, str):
        raise DecisionError("INVALID_INPUT", "decided_by must be text")
    name = " ".join(name.split())
    if not NAME_RE.match(name):
        raise DecisionError("INVALID_INPUT", "decided_by must be 2-80 characters: letters, digits, spaces and . , ' -")
    if name.lower() in RESERVED_NAMES:
        raise DecisionError("INVALID_INPUT", f"{name!r} is an agent or system name; a human decision needs a person's name")
    return name


def _validate_note(note) -> str | None:
    if note is None:
        return None
    if not isinstance(note, str) or len(note.strip()) > NOTE_MAX:
        raise DecisionError("INVALID_INPUT", f"note must be text of at most {NOTE_MAX} characters")
    return note.strip() or None


def _require_id(name: str, value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DecisionError("INVALID_INPUT", f"{name} must be a positive whole number")
    return value


# ---------------------------------------------------------------- connection and helpers

def open_decision_connection(paths: Paths) -> sqlite3.Connection:
    """Unrestricted (human-path) connection to the working copy with audit.db attached.
    The settings that make the two-file commit atomic are set AND read back; otherwise we refuse."""
    assert_writable(paths.working_db, paths)
    if not paths.working_db.exists():
        raise DecisionError("DB_UNAVAILABLE", f"Working database not found at {paths.working_db}. Reset it first.")
    try:
        audit_db.ensure(paths.audit_db)
        con = maintenance.connect_rw(paths.working_db)
    except sqlite3.Error as exc:
        raise DecisionError("DB_UNAVAILABLE", f"Cannot open the databases: {exc}") from exc
    try:
        con.execute("ATTACH DATABASE ? AS audit", (str(paths.audit_db),))
        con.execute("PRAGMA audit.journal_mode = DELETE")
        con.execute("PRAGMA audit.synchronous = FULL")
        con.execute("PRAGMA recursive_triggers = ON")
        settings = {"main.journal_mode": con.execute("PRAGMA main.journal_mode").fetchone()[0],
                    "audit.journal_mode": con.execute("PRAGMA audit.journal_mode").fetchone()[0],
                    "main.synchronous": con.execute("PRAGMA main.synchronous").fetchone()[0],
                    "audit.synchronous": con.execute("PRAGMA audit.synchronous").fetchone()[0],
                    "foreign_keys": con.execute("PRAGMA foreign_keys").fetchone()[0]}
        if settings != {"main.journal_mode": "delete", "audit.journal_mode": "delete", "main.synchronous": 2,
                        "audit.synchronous": 2, "foreign_keys": 1}:
            raise DecisionError("DB_UNAVAILABLE", f"Required SQLite settings not in effect: {settings}")
        tables = {r[0] for r in con.execute("SELECT name FROM main.sqlite_master WHERE type = 'table'")}
        if not {"pending_actions", "drafts"} <= tables:
            raise DecisionError("DB_UNAVAILABLE", "The working database has no pending_actions/drafts tables; reset it first.")
    except DecisionError:
        con.close()
        raise
    except sqlite3.Error as exc:
        con.close()
        raise DecisionError("DB_UNAVAILABLE", f"Cannot prepare the decision connection: {exc}") from exc
    con.row_factory = sqlite3.Row
    return con


def _money(x) -> float:
    return round(float(x), 2)


def add_months(iso: str, months: int) -> str:
    """D6 (proposed policy): next_due moves forward by whole months, clamped to the month's last day."""
    d = date.fromisoformat(iso)
    month0 = d.month - 1 + months
    year, month = d.year + month0 // 12, month0 % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1])).isoformat()


def _one(cur: sqlite3.Cursor, what: str) -> None:
    """Every guarded UPDATE must change exactly one row; anything else aborts the transaction."""
    if cur.rowcount != 1:
        raise DecisionError("REFUSED_RULE", f"{what} changed concurrently; nothing was changed")


def _audit(con, *, actor: str, event_type: str, payload: dict, today: str, decision_id: str, ref_table: str,
           ref_id: int) -> int:
    # run_id stays NULL (a human is not a run); call_id carries the decision_id that also appears in the
    # JSON trail. The proposal's own run_id and created_at are in the payload, so after a reset (ids restart)
    # an old decision can never be mistaken for one about a new row.
    return audit_db.append_event(con, actor=actor, actor_kind="human", event_type=event_type, payload=payload,
                                 business_date=today, call_id=decision_id, ref_table=ref_table, ref_id=ref_id)


def _action_ref(row: sqlite3.Row) -> dict:
    return {"action_id": row["id"], "kind": row["kind"], "proposal_run_id": row["run_id"],
            "proposed_by": row["proposed_by"], "proposal_created_at": row["created_at"], "ticket_id": row["ticket_id"],
            "amount": _money(row["amount"]), "amount_basis": row["amount_basis"]}


# ---------------------------------------------------------------- the shared decision frame

APPEND_ATTEMPTS, APPEND_PAUSE = 3, 0.2
RECONCILE_HINT = ("It is reported by GET /api/audit and scripts/verify_audit.py, and is reconciled automatically before "
                  "the next decision or reset (or with: scripts/verify_audit.py reconcile --confirm).")


def append_with_retry(trail: audit_trail.AuditTrail, event: dict) -> bool:
    """Append ONE prepared event, retrying briefly. Safe to retry: the trail stores an event_id only once."""
    for attempt in range(APPEND_ATTEMPTS):
        try:
            trail.append([event])
            return True
        except audit_trail.AuditTrailError:
            if attempt < APPEND_ATTEMPTS - 1:
                time.sleep(APPEND_PAUSE)
    return False


def _hold_run_lock(paths: Paths):
    lock = locks.file_lock(locks.run_lock_path(paths.active_dir), timeout=0)
    try:
        lock.__enter__()
    except locks.LockTimeout:
        raise DecisionError("BUSY", "An agent run, a reset or another decision is in progress; nothing was changed. "
                                    "Try again when it finishes.") from None
    return lock


def _reconcile_first(paths: Paths, trail: audit_trail.AuditTrail) -> list[dict]:
    """Close any earlier gap (a decision, reset or tool call whose final JSON event is missing) before starting.
    The caller holds the run lock, so nothing is in progress. Fail-closed: if it cannot be recorded, refuse."""
    try:
        return audit_reconcile.reconcile_locked(paths, trail)
    except audit_trail.AuditTrailError as exc:
        raise DecisionError("AUDIT_FAILED", f"The audit trail could not be reconciled or written; nothing was changed ({exc}).") from exc


def _decide(paths: Paths, trail: audit_trail.AuditTrail, *, operation: str, target: dict, decided_by, note, apply,
            extra_details: dict | None = None) -> dict:
    name = validate_decider(decided_by)
    note = _validate_note(note)
    assert_writable(paths.working_db, paths)            # never the original database, checked before anything else
    actor = f"human:{name}"
    lock = _hold_run_lock(paths)
    try:
        reconciled = _reconcile_first(paths, trail)
        decision_id = str(uuid.uuid4())
        base = {"operation": operation, **target, "note": note, **(extra_details or {}), "notice": NOTICE}
        try:
            trail.append([audit_trail.make_event("human_decision_requested", actor=actor, actor_kind="human",
                                                 call_id=decision_id, ticket_id=target.get("ticket_id"), details=base)])
        except audit_trail.AuditTrailError as exc:
            raise DecisionError("AUDIT_FAILED", f"The audit trail could not record the request; nothing was changed ({exc}).") from exc

        def finish(outcome: dict, links: dict | None = None, details: dict | None = None) -> bool:
            return append_with_retry(trail, audit_trail.make_event(
                "human_decision_finished", actor=actor, actor_kind="human", call_id=decision_id,
                ticket_id=target.get("ticket_id"), outcome=outcome, links=links, details={**base, **(details or {})}))

        try:
            con = open_decision_connection(paths)
        except DecisionError as exc:
            finish({"status": "error", "code": exc.code, "message": exc.message})
            raise
        try:
            con.execute("BEGIN IMMEDIATE")
            today = con.execute("SELECT date_today FROM main.desk").fetchone()[0]
            result = apply(con, today, name, note, decision_id)
            con.execute("COMMIT")
        except DecisionError as exc:
            if con.in_transaction:
                con.execute("ROLLBACK")
            finish({"status": "refused" if exc.code in ("NOT_FOUND", "NOT_PENDING", "REFUSED_RULE", "INVALID_INPUT")
                    else "error", "code": exc.code, "message": exc.message})
            raise
        except BaseException as exc:
            if con.in_transaction:
                con.execute("ROLLBACK")
            message = f"{type(exc).__name__}: {exc}; nothing was changed"
            finish({"status": "error", "code": "DB_UNAVAILABLE", "message": audit_trail.summarize(message)})
            if isinstance(exc, sqlite3.Error):
                raise DecisionError("DB_UNAVAILABLE", message) from exc
            raise
        finally:
            con.close()

        result.update(decision_id=decision_id, decided_by=name, business_date=today, notice=NOTICE,
                      earlier_gaps_reconciled=len(reconciled))
        result["audit_trail_recorded"] = finish({"status": "ok"}, links=result.get("links"),
                                                details={"result": result.get("result")})
        result["reconciliation_pending"] = not result["audit_trail_recorded"]
        if not result["audit_trail_recorded"]:
            result.setdefault("warnings", []).append(
                f"The decision COMMITTED (business tables and audit.db, in one transaction), but its completion event "
                f"could not be written to output/audit_trail.json after {APPEND_ATTEMPTS} attempts. The JSON trail holds "
                f"the request (decision_id {decision_id}); audit.db holds the committed record under the same id. "
                + RECONCILE_HINT)
        return result
    finally:
        lock.__exit__(None, None, None)


# ---------------------------------------------------------------- approve

def _load_action(con, action_id: int) -> sqlite3.Row:
    row = con.execute("SELECT * FROM main.pending_actions WHERE id = ?", (action_id,)).fetchone()
    if row is None:
        raise DecisionError("NOT_FOUND", f"Unknown proposal {action_id}")
    if row["status"] != "proposed":
        extra = f" (payment {row['payment_id']} on {row['executed_at']})" if row["status"] == "executed" else ""
        raise DecisionError("NOT_PENDING", f"Proposal {action_id} is {row['status']!r}{extra}, not 'proposed'; "
                                           "a decision can be made only once")
    return row


def _execute_payment(con, row, today, name, note, decision_id) -> dict:
    """Approve and execute in ONE transaction (architecture section 5): all rows change, or none."""
    amount = _money(row["amount"])
    acct = con.execute("SELECT name, balance FROM main.cash_accounts WHERE name = ?", (row["account"],)).fetchone()
    if acct is None:
        raise DecisionError("NOT_FOUND", f"Unknown cash account {row['account']!r}")
    if row["invoice_id"] is not None:
        inv = con.execute("SELECT * FROM main.invoices WHERE id = ?", (row["invoice_id"],)).fetchone()
        if inv is None:
            raise DecisionError("NOT_FOUND", f"Invoice {row['invoice_id']} no longer exists")
        if inv["status"] != "open":
            raise DecisionError("REFUSED_RULE", f"R4: invoice {inv['id']} is {inv['status']!r}, not open; it cannot be paid again")
        if amount != _money(inv["amount"]):
            raise DecisionError("REFUSED_RULE", f"policy:full_amount_only: proposal {amount:.2f} differs from invoice {_money(inv['amount']):.2f}")
        pay_kind, ref_id = "invoice", inv["id"]
    else:
        lease = con.execute("SELECT * FROM main.leases WHERE id = ?", (row["lease_id"],)).fetchone()
        if lease is None:
            raise DecisionError("NOT_FOUND", f"Lease {row['lease_id']} no longer exists")
        if amount != _money(lease["monthly_rent"]):
            raise DecisionError("REFUSED_RULE", f"policy:full_amount_only: proposal {amount:.2f} differs from rent {_money(lease['monthly_rent']):.2f}")
        pay_kind, ref_id = "lease", lease["id"]
    if pay_kind not in policies.PAYMENT_KINDS:
        raise DecisionError("REFUSED_RULE", f"unknown payment kind {pay_kind!r}")

    before = _money(acct["balance"])
    # D4 (proposed policy, stricter than R1): approved-but-unexecuted commitments are reserved.
    reserved = _money(con.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM main.pending_actions WHERE kind = 'payment' AND status = 'approved' "
        "AND account = ? AND id <> ?", (row["account"], row["id"])).fetchone()[0])
    if policies.RESERVE_APPROVED_COMMITMENTS and _money(before - reserved - amount) < 0:
        raise DecisionError("REFUSED_RULE", f"R1: paying {amount:.2f} from {row['account']} (balance {before:.2f}, "
                                            f"reserved {reserved:.2f}) would make cash negative; nothing was paid")

    now = audit_db.utc_now()
    payment_id = con.execute(
        "INSERT INTO main.payments (kind, ref_id, amount, account, paid_at, approved_by) VALUES (?, ?, ?, ?, ?, ?)",
        (pay_kind, ref_id, amount, row["account"], today, name)).lastrowid
    _one(con.execute("UPDATE main.cash_accounts SET balance = ROUND(balance - ?, 2), date = ? "
                     "WHERE name = ? AND balance = ? AND balance - ? >= 0",
                     (amount, today, row["account"], acct["balance"], amount)), "cash account")
    if pay_kind == "invoice":
        _one(con.execute("UPDATE main.invoices SET status = ? WHERE id = ? AND status = 'open'",
                         (policies.INVOICE_PAID_STATUS, ref_id)), f"invoice {ref_id}")
        change = {"invoice_id": ref_id, "status_before": "open", "status_after": policies.INVOICE_PAID_STATUS}
    else:
        old_due = con.execute("SELECT next_due FROM main.leases WHERE id = ?", (ref_id,)).fetchone()[0]
        new_due = add_months(old_due, policies.LEASE_ADVANCE_MONTHS)
        _one(con.execute("UPDATE main.leases SET next_due = ? WHERE id = ? AND next_due = ?", (new_due, ref_id, old_due)),
             f"lease {ref_id}")
        change = {"lease_id": ref_id, "next_due_before": old_due, "next_due_after": new_due}
    _one(con.execute("UPDATE main.pending_actions SET status = 'executed', decided_by = ?, decided_at = ?, decision_note = ?, "
                     "executed_at = ?, payment_id = ? WHERE id = ? AND status = 'proposed'",
                     (name, now, note, now, payment_id, row["id"])), f"proposal {row['id']}")
    after = _money(con.execute("SELECT balance FROM main.cash_accounts WHERE name = ?", (row["account"],)).fetchone()[0])
    if after != _money(before - amount) or after < 0:
        raise DecisionError("REFUSED_RULE", f"balance check failed after payment ({before:.2f} - {amount:.2f} != {after:.2f})")

    actor = f"human:{name}"
    approved_ev = _audit(con, actor=actor, event_type="action_approved", today=today, decision_id=decision_id,
                         ref_table="pending_actions", ref_id=row["id"],
                         payload={**_action_ref(row), "decided_by": name, "decided_at": now, "note": note})
    payment = {"payment_id": payment_id, "kind": pay_kind, "ref_id": ref_id, "amount": amount, "account": row["account"],
               "paid_at": today, "approved_by": name}
    executed_ev = _audit(con, actor=actor, event_type="payment_executed", today=today, decision_id=decision_id,
                         ref_table="payments", ref_id=payment_id,
                         payload={**payment, "action": _action_ref(row), "balance_before": before, "balance_after": after,
                                  **change})
    return {"result": "payment_executed", "action_id": row["id"], "kind": "payment", "status": "executed",
            "payment": payment, "cash": {"account": row["account"], "balance_before": before, "balance_after": after},
            "changes": change, "warnings": [],
            "links": {"action_id": row["id"], "payment_id": payment_id,
                      "audit_db_event_ids": [approved_ev, executed_ev]}}


def _vendor_blocking(con, vendor_id) -> tuple[str, list[int]]:
    vendor = con.execute("SELECT name FROM main.vendors WHERE id = ?", (vendor_id,)).fetchone()
    blocking = [r[0] for r in con.execute("SELECT id FROM main.invoices WHERE vendor_id = ? AND status = 'open' ORDER BY id",
                                          (vendor_id,))]
    return (vendor["name"] if vendor else str(vendor_id)), blocking


def _approve_purchase(con, row, today, name, note, decision_id) -> dict:
    # Problem 9 F3 (Dr. Ryan's choice): approval is an AUTHORIZATION only (no cash, no order), so it is allowed while the
    # vendor is blocked; R3 is shown as a warning. EXECUTION (spending) is refused while blocked (see execute_purchase).
    vendor, blocking = _vendor_blocking(con, row["vendor_id"])
    warnings = []
    if blocking:
        warnings.append(f"R3: {vendor} has open unpaid invoice(s) {blocking} and ships nothing until they are paid. This approval "
                        "only authorizes the purchase; executing it (spending cash) will be refused until they are paid.")
    others = [r[0] for r in con.execute("SELECT id FROM main.pending_actions WHERE kind = 'purchase' AND status = 'approved' "
                                        "AND COALESCE(ticket_id, -1) = COALESCE(?, -1) AND id <> ?", (row["ticket_id"], row["id"]))]
    if others:
        warnings.append(f"Purchase(s) {others} for the same ticket are already approved; check this is not a duplicate order.")
    return _mark(con, row, today, name, note, decision_id, "approved", "action_approved", "purchase_approved", warnings,
                 "Approved as an authorization only. No money has moved and nothing is ordered or shipped. Spending cash "
                 "needs the separate 'Execute purchase' step with a confirmed amount and supplier. The amount shown is an "
                 "estimate, not a vendor quote.")


def _approve_quote(con, row, today, name, note, decision_id) -> dict:
    ticket = con.execute("SELECT status FROM main.tickets WHERE id = ?", (row["ticket_id"],)).fetchone()
    if ticket is None or ticket["status"] != "open":
        raise DecisionError("REFUSED_RULE", f"Ticket {row['ticket_id']} is not open; its quote cannot be approved")
    other = con.execute("SELECT id FROM main.pending_actions WHERE kind = 'quote' AND status = 'approved' AND ticket_id = ? "
                        "AND id <> ?", (row["ticket_id"], row["id"])).fetchone()
    if other:
        raise DecisionError("REFUSED_RULE", f"Quote {other[0]} is already approved for ticket {row['ticket_id']}; "
                                            "a customer can be offered only one price")
    return _mark(con, row, today, name, note, decision_id, "approved", "action_approved", "quote_approved", [],
                 "Approved internally. Nothing is sent (R7); customer acceptance is not recorded and no revenue or cash "
                 "is added (R6).")


def _mark(con, row, today, name, note, decision_id, status, event_type, result, warnings, meaning,
          extra_payload: dict | None = None) -> dict:
    now = audit_db.utc_now()
    _one(con.execute("UPDATE main.pending_actions SET status = ?, decided_by = ?, decided_at = ?, decision_note = ? "
                     "WHERE id = ? AND status = ?", (status, name, now, note, row["id"], row["status"])), f"proposal {row['id']}")
    ev = _audit(con, actor=f"human:{name}", event_type=event_type, today=today, decision_id=decision_id,
                ref_table="pending_actions", ref_id=row["id"],
                payload={**_action_ref(row), "status_before": row["status"], "status_after": status, "decided_by": name,
                         "decided_at": now, "note": note, **(extra_payload or {})})
    return {"result": result, "action_id": row["id"], "kind": row["kind"], "status": status, "meaning": meaning,
            "warnings": warnings, "links": {"action_id": row["id"], "audit_db_event_ids": [ev]}}


def approve_action(paths: Paths, trail: audit_trail.AuditTrail, action_id: int, *, decided_by, note=None) -> dict:
    action_id = _require_id("action_id", action_id)

    def apply(con, today, name, note_, decision_id):
        row = _load_action(con, action_id)
        handler = {"payment": _execute_payment, "purchase": _approve_purchase, "quote": _approve_quote}[row["kind"]]
        return handler(con, row, today, name, note_, decision_id)

    return _decide(paths, trail, operation="approve", target={"action_id": action_id}, decided_by=decided_by, note=note,
                   apply=apply)


def reject_action(paths: Paths, trail: audit_trail.AuditTrail, action_id: int, *, decided_by, note=None) -> dict:
    action_id = _require_id("action_id", action_id)

    def apply(con, today, name, note_, decision_id):
        row = con.execute("SELECT * FROM main.pending_actions WHERE id = ?", (action_id,)).fetchone()
        if row is not None and row["kind"] == "purchase" and row["status"] == "approved":
            # F3: an approved (authorized, unexecuted) purchase can still be cancelled before any money is spent.
            # The row's decision fields now show the withdrawal; the earlier authorization stays in its own audit event
            # AND is restated here, so the history reads: authorized -> withdrawn -> never executed or paid.
            return _mark(con, row, today, name, note_, decision_id, "rejected", "action_rejected", "authorization_withdrawn", [],
                         "The purchase authorization was withdrawn before execution. It was never executed and nothing was paid.",
                         extra_payload={"withdrawn_after_authorization": True, "authorized_by": row["decided_by"],
                                        "authorized_at": row["decided_at"], "authorization_note": row["decision_note"],
                                        "executed": False, "payment_id": None})
        row = _load_action(con, action_id)
        return _mark(con, row, today, name, note_, decision_id, "rejected", "action_rejected", "rejected", [],
                     "Rejected. Nothing is executed; cash and records are unchanged apart from this decision.")

    return _decide(paths, trail, operation="reject", target={"action_id": action_id}, decided_by=decided_by, note=note,
                   apply=apply)


# ---------------------------------------------------------------- purchase execution (Problem 9, F3)

PURCHASE_AMOUNT_BASES = {
    "recorded_unit_cost_estimate": "the database cost estimate (quantity × stored unit cost), confirmed by the person as the "
                                   "actual amount; not a supplier quote",
    "supplier_confirmed_price": "a price the person states the supplier confirmed outside the system (R7: the system contacts "
                                "no vendor); recorded as the person's statement",
}
CONFIRMATION_NOTE_LENGTH = (10, 500)


def commitments(con, account: str, exclude_action: int | None = None) -> dict:
    """Approved but unexecuted commitments. They are NEVER deducted from cash_accounts; they only limit what may be spent."""
    def total(kind):
        return _money(con.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM main.pending_actions WHERE kind = ? AND status = 'approved' "
            "AND (? IS NULL OR id <> ?) AND (kind <> 'payment' OR account = ?)",
            (kind, exclude_action, exclude_action, account)).fetchone()[0])
    return {"approved_unexecuted_payments": total("payment"), "approved_unexecuted_purchases": total("purchase")}


def execute_purchase(paths: Paths, trail: audit_trail.AuditTrail, action_id: int, *, decided_by, amount_basis, amount,
                     amount_confirmed=False, supplier_confirmed=False, confirmation_note=None, note=None,
                     account: str = "checking") -> dict:
    """Spend cash on an APPROVED purchase (step 2 of 2). Human-only, never reachable by agents.

    Requires: the proposal is an approved purchase; the vendor has no open invoice (refusing execution while blocked is OUR
    prudence choice: R3 itself forbids shipment, not payment); an explicitly confirmed actual amount with its basis; the
    person's statement that the supplier agreed to supply this item and quantity; and enough cash. Purchases yield to
    obligations: actual balance − approved unexecuted payments − other approved unexecuted purchases must cover the amount.
    Writes, in one transaction: a payments row (kind 'purchase', ref_id = the proposal), the cash decrease, status
    'executed' + payment_id, and a 'purchase_executed' audit.db event. Inventory is NOT changed: paying is not delivery."""
    action_id = _require_id("action_id", action_id)
    if amount_basis not in PURCHASE_AMOUNT_BASES:
        raise DecisionError("INVALID_INPUT", f"amount_basis must be one of {sorted(PURCHASE_AMOUNT_BASES)}")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not amount > 0 or round(float(amount), 2) != float(amount):
        raise DecisionError("INVALID_INPUT", "amount must be a positive number with at most two decimals")
    amount = _money(amount)
    if amount_confirmed is not True:
        raise DecisionError("INVALID_INPUT", "The actual amount must be explicitly confirmed (amount_confirmed = true)")
    if supplier_confirmed is not True:
        raise DecisionError("INVALID_INPUT", "Execution needs the person's statement that the supplier agreed to supply this item "
                                             "and quantity (supplier_confirmed = true); a vendor's recorded specialty is not enough")
    note_text = confirmation_note.strip() if isinstance(confirmation_note, str) else ""
    if amount_basis == "supplier_confirmed_price" and not CONFIRMATION_NOTE_LENGTH[0] <= len(note_text) <= CONFIRMATION_NOTE_LENGTH[1]:
        raise DecisionError("INVALID_INPUT", "A supplier-confirmed price needs a confirmation_note (10-500 characters): how and "
                                             "when the supplier confirmed it")
    if len(note_text) > CONFIRMATION_NOTE_LENGTH[1]:
        raise DecisionError("INVALID_INPUT", "confirmation_note must be at most 500 characters")

    def apply(con, today, name, note_, decision_id):
        row = con.execute("SELECT * FROM main.pending_actions WHERE id = ?", (action_id,)).fetchone()
        if row is None:
            raise DecisionError("NOT_FOUND", f"Unknown proposal {action_id}")
        if row["kind"] != "purchase":
            raise DecisionError("INVALID_INPUT", f"Proposal {action_id} is a {row['kind']}; only purchases are executed this way")
        if row["status"] != "approved":
            hint = {"proposed": "approve it first (authorization), then execute",
                    "executed": f"it was already executed (payment {row['payment_id']})",
                    "rejected": "it was rejected"}.get(row["status"], row["status"])
            raise DecisionError("NOT_PENDING", f"Purchase {action_id} is {row['status']!r}: {hint}")
        vendor, blocking = _vendor_blocking(con, row["vendor_id"])
        if blocking:
            raise DecisionError("REFUSED_RULE", f"R3: {vendor} has open unpaid invoice(s) {blocking} and ships nothing until they "
                                                "are paid. Execution is refused until then (our implementation choice: R3 forbids "
                                                "shipment; we also refuse to pay for goods that cannot ship).")
        unit = con.execute("SELECT unit_cost FROM main.pricing WHERE sku = ?", (row["sku"],)).fetchone()
        db_estimate = _money(row["qty"] * unit["unit_cost"]) if unit else None
        if amount_basis == "recorded_unit_cost_estimate" and amount != db_estimate:
            raise DecisionError("INVALID_INPUT", f"With basis recorded_unit_cost_estimate the amount must equal quantity × stored "
                                                 f"unit cost = {db_estimate:.2f}; got {amount:.2f}")
        acct = con.execute("SELECT name, balance FROM main.cash_accounts WHERE name = ?", (account,)).fetchone()
        if acct is None:
            raise DecisionError("NOT_FOUND", f"Unknown cash account {account!r}")
        before = _money(acct["balance"])
        held = commitments(con, account, exclude_action=row["id"])
        available = _money(before - held["approved_unexecuted_payments"] - held["approved_unexecuted_purchases"])
        if amount > available:
            raise DecisionError("REFUSED_RULE", f"Cash constraint: {amount:.2f} exceeds the cash available for purchases "
                                                f"({available:.2f} = actual {before:.2f} − approved unexecuted payments "
                                                f"{held['approved_unexecuted_payments']:.2f} − other approved unexecuted purchases "
                                                f"{held['approved_unexecuted_purchases']:.2f}). Nothing was paid (R1; purchases "
                                                "yield to obligations).")

        now = audit_db.utc_now()
        payment_id = con.execute(
            "INSERT INTO main.payments (kind, ref_id, amount, account, paid_at, approved_by) VALUES ('purchase', ?, ?, ?, ?, ?)",
            (row["id"], amount, account, today, name)).lastrowid
        _one(con.execute("UPDATE main.cash_accounts SET balance = ROUND(balance - ?, 2), date = ? "
                         "WHERE name = ? AND balance = ? AND balance - ? >= 0",
                         (amount, today, account, acct["balance"], amount)), "cash account")
        _one(con.execute("UPDATE main.pending_actions SET status = 'executed', executed_at = ?, payment_id = ? "
                         "WHERE id = ? AND status = 'approved'", (now, payment_id, row["id"])), f"purchase {row['id']}")
        after = _money(con.execute("SELECT balance FROM main.cash_accounts WHERE name = ?", (account,)).fetchone()[0])
        if after != _money(before - amount) or after < 0:
            raise DecisionError("REFUSED_RULE", f"balance check failed after the purchase ({before:.2f} - {amount:.2f} != {after:.2f})")
        payment = {"payment_id": payment_id, "kind": "purchase", "ref_id": row["id"], "amount": amount, "account": account,
                   "paid_at": today, "approved_by": name}
        execution = {"amount_basis": amount_basis, "basis_meaning": PURCHASE_AMOUNT_BASES[amount_basis],
                     "amount_confirmed_by_person": True, "supplier_confirmed_by_person": True,
                     "confirmation_note": note_text or None, "database_estimate": db_estimate,
                     "proposal_estimate": _money(row["amount"]),
                     "difference_from_database_estimate": None if db_estimate is None else _money(amount - db_estimate),
                     "vendor_id": row["vendor_id"], "vendor": vendor, "sku": row["sku"], "size": row["size"], "qty": row["qty"],
                     "authorized_by": row["decided_by"], "authorized_at": row["decided_at"], "executed_by": name, "executed_at": now,
                     "inventory_changed": False, "not_verified": ["delivery or receipt of the goods", "the supplier's agreement",
                                                                  "the supplier's price (unless the database estimate was used)"]}
        cash = {"account": account, "balance_before": before, "balance_after": after, "available_for_purchases_before": available,
                **held}
        ev = _audit(con, actor=f"human:{name}", event_type="purchase_executed", today=today, decision_id=decision_id,
                    ref_table="payments", ref_id=payment_id,
                    payload={**payment, "action": _action_ref(row), "execution": execution, "cash": cash, "note": note_})
        return {"result": "purchase_executed", "action_id": row["id"], "kind": "purchase", "status": "executed",
                "payment": payment, "cash": cash, "execution": execution, "warnings": [],
                "meaning": (f"{name} executed the purchase: {amount:.2f} was paid from {account} for the order. Paying is not "
                            "delivery: inventory is unchanged, and receipt of the goods is not recorded."),
                "links": {"action_id": row["id"], "payment_id": payment_id, "audit_db_event_ids": [ev]}}

    return _decide(paths, trail, operation="execute_purchase", target={"action_id": action_id}, decided_by=decided_by,
                   note=note, apply=apply,
                   extra_details={"amount_basis": amount_basis, "amount": amount, "amount_confirmed": True,
                                  "supplier_confirmed": True, "confirmation_note": note_text or None})


SUMMARY_LENGTH = (10, 1000)
NOT_VERIFIED_BY_SYSTEM = (
    "delivery, shipment or receipt of any goods",
    "customer acceptance of a quote, order or price",
    "that any message was sent (drafts are never sent by this system)",
    "any payment, refund or arrangement made outside this system",
)

# Problem 9, follow-up 36: what the person confirms when resolving.
#   business_outcome             the underlying business issue is actually resolved (the original meaning; default)
#   simulated_workflow_complete  the desk's workflow in this simulation is complete; real-world fulfilment is NOT
#                                verified. The person must name what stays unverified (at least one item).
RESOLUTION_BASES = ("business_outcome", "simulated_workflow_complete")
UNVERIFIED_OUTCOMES = {
    "delivery": "physical production, shipment, delivery or receipt of goods",
    "customer_acceptance": "acceptance by the customer or counterparty (of a quote, order, price or alternative)",
    "message_sent": "that any message was actually sent (drafts are never sent by this system, R7)",
    "outside_arrangement": "any payment, refund or arrangement made outside this system",
}


def resolve_ticket(paths: Paths, trail: audit_trail.AuditTrail, ticket_id: int, *, decided_by,
                   business_outcome_confirmed=False, resolution_summary=None, note=None,
                   resolution_basis="business_outcome", workflow_complete_confirmed=False,
                   unverified_outcomes=None) -> dict:
    """Spec section 7.7: ticket status changes only on a human action, audited. Agents have no such tool.

    Problem 7 (A1): deciding every proposal is NOT the same as solving the customer's or landlord's problem, so
    resolution needs the person's explicit confirmation that the underlying business issue is resolved, and their
    own summary of how. That is recorded as THEIR STATEMENT. Nothing is inferred from approvals: the record lists
    the system's own facts at that moment and states plainly what the system does not and cannot verify.

    Follow-up 36: a second basis, 'simulated_workflow_complete', lets the person confirm only that the desk's workflow
    in this simulation is complete, while naming what stays unverified. It is recorded with
    business_outcome_confirmed = false, and needs (in code) a completed agent run for the ticket and no purchase that
    is approved but not executed. The ticket status is 'resolved' either way; the basis says what that means."""
    ticket_id = _require_id("ticket_id", ticket_id)
    if resolution_basis not in RESOLUTION_BASES:
        raise DecisionError("INVALID_INPUT", f"resolution_basis must be one of {list(RESOLUTION_BASES)}")
    simulated = resolution_basis == "simulated_workflow_complete"
    if simulated:
        if business_outcome_confirmed is not False:
            raise DecisionError("INVALID_INPUT", "simulated_workflow_complete does not confirm the business outcome; "
                                                 "business_outcome_confirmed must be false")
        if workflow_complete_confirmed is not True:
            raise DecisionError("INVALID_INPUT", "simulated_workflow_complete needs the person's explicit confirmation that "
                                                 "the desk's workflow is complete (workflow_complete_confirmed = true)")
        if (not isinstance(unverified_outcomes, (list, tuple)) or not unverified_outcomes
                or any(u not in UNVERIFIED_OUTCOMES for u in unverified_outcomes)
                or len(set(unverified_outcomes)) != len(unverified_outcomes)):
            raise DecisionError("INVALID_INPUT", "simulated_workflow_complete needs at least one unverified outcome, each one of "
                                                 f"{list(UNVERIFIED_OUTCOMES)}, without repeats")
        unverified = [u for u in UNVERIFIED_OUTCOMES if u in unverified_outcomes]     # stable order
    else:
        if business_outcome_confirmed is not True:
            raise DecisionError("INVALID_INPUT", "Resolving a ticket needs the person's explicit confirmation that the underlying "
                                                 "business issue is resolved (business_outcome_confirmed = true). Approving or "
                                                 "rejecting proposals does not establish that. If only the simulated workflow "
                                                 "is complete, use resolution_basis = simulated_workflow_complete.")
        if workflow_complete_confirmed or unverified_outcomes:
            raise DecisionError("INVALID_INPUT", "workflow_complete_confirmed and unverified_outcomes belong to "
                                                 "resolution_basis = simulated_workflow_complete")
        unverified = []
    if not isinstance(resolution_summary, str) or not SUMMARY_LENGTH[0] <= len(resolution_summary.strip()) <= SUMMARY_LENGTH[1]:
        raise DecisionError("INVALID_INPUT", f"resolution_summary must be {SUMMARY_LENGTH[0]}-{SUMMARY_LENGTH[1]} characters: "
                                             "in the person's own words, how the business issue was resolved")
    summary = resolution_summary.strip()

    def apply(con, today, name, note_, decision_id):
        t = con.execute("SELECT id, status FROM main.tickets WHERE id = ?", (ticket_id,)).fetchone()
        if t is None:
            raise DecisionError("NOT_FOUND", f"Unknown ticket {ticket_id}")
        if t["status"] != "open":
            raise DecisionError("NOT_PENDING", f"Ticket {ticket_id} is {t['status']!r}, not open")
        undecided = [r[0] for r in con.execute("SELECT id FROM main.pending_actions WHERE ticket_id = ? AND status = 'proposed' "
                                               "ORDER BY id", (ticket_id,))]
        if undecided:
            raise DecisionError("REFUSED_RULE", f"Proposal(s) {undecided} on ticket {ticket_id} still await a decision; "
                                                "approve or reject them first")
        completed_runs = []
        if simulated:
            unpaid = [r[0] for r in con.execute("SELECT id FROM main.pending_actions WHERE ticket_id = ? AND kind = 'purchase' "
                                                "AND status = 'approved' ORDER BY id", (ticket_id,))]
            if unpaid:
                raise DecisionError("REFUSED_RULE", f"Purchase(s) {unpaid} on ticket {ticket_id} are authorized but not executed; "
                                                    "execute or withdraw them before declaring the workflow complete")
            completed_runs = _completed_runs(trail, ticket_id)
            if not completed_runs:
                raise DecisionError("REFUSED_RULE", f"No completed agent run is recorded for ticket {ticket_id}; the simulated "
                                                    "workflow cannot be complete without one")
        # The system's OWN records at this moment, copied as they are (no interpretation).
        records = {
            "proposals": [{"action_id": r["id"], "kind": r["kind"], "status": r["status"], "amount": _money(r["amount"]),
                           "payment_id": r["payment_id"]}
                          for r in con.execute("SELECT * FROM main.pending_actions WHERE ticket_id = ? ORDER BY id", (ticket_id,))],
            "unsent_drafts": con.execute("SELECT COUNT(*) FROM main.drafts WHERE ticket_id = ?", (ticket_id,)).fetchone()[0],
        }
        _one(con.execute("UPDATE main.tickets SET status = 'resolved' WHERE id = ? AND status = 'open'", (ticket_id,)),
             f"ticket {ticket_id}")
        ev = _audit(con, actor=f"human:{name}", event_type="ticket_resolved", today=today, decision_id=decision_id,
                    ref_table="tickets", ref_id=ticket_id,
                    payload={"ticket_id": ticket_id, "status_before": "open", "status_after": "resolved",
                             "decided_by": name, "note": note_, "resolution_basis": resolution_basis,
                             "business_outcome_confirmed": not simulated,
                             "workflow_complete_confirmed": True if simulated else None,
                             "unverified_outcomes": unverified, "completed_runs": completed_runs,
                             "resolution_summary": summary, "basis": "the person's own confirmation (attribution only)",
                             "system_records_at_resolution": records,
                             "not_verified_by_system": list(NOT_VERIFIED_BY_SYSTEM)})
        if simulated:
            meaning = (f"{name} confirmed that the desk's workflow for ticket {ticket_id} is complete in this simulation. "
                       "They did NOT confirm the business outcome; unverified: "
                       + "; ".join(UNVERIFIED_OUTCOMES[u] for u in unverified) + ". Agents cannot change ticket status.")
        else:
            meaning = (f"{name} confirmed that the underlying business issue is resolved. This is their statement, "
                       "recorded for attribution; the system has not verified the items listed in "
                       "not_verified_by_system. Agents cannot change ticket status.")
        out = {"result": "ticket_resolved", "ticket_id": ticket_id, "status": "resolved", "warnings": [],
               "resolution_basis": resolution_basis, "business_outcome_confirmed": not simulated,
               "unverified_outcomes": unverified, "resolution_summary": summary,
               "system_records_at_resolution": records, "not_verified_by_system": list(NOT_VERIFIED_BY_SYSTEM),
               "meaning": meaning, "links": {"ticket_id": ticket_id, "audit_db_event_ids": [ev]}}
        out["workflow_complete_confirmed_by" if simulated else "business_outcome_confirmed_by"] = name
        return out

    return _decide(paths, trail, operation="resolve_ticket", target={"ticket_id": ticket_id}, decided_by=decided_by,
                   note=note, apply=apply,
                   extra_details={"resolution_basis": resolution_basis, "business_outcome_confirmed": not simulated,
                                  "unverified_outcomes": unverified, "resolution_summary": summary})


def _completed_runs(trail: audit_trail.AuditTrail, ticket_id: int) -> list[str]:
    """Run IDs for this ticket whose run_finished event says 'completed' (read from the append-only trail)."""
    events = trail.read()
    mine = {e["run_id"] for e in events if e.get("event_type") == "run_started"
            and (e.get("details") or {}).get("ticket_id") == ticket_id}
    return [e["run_id"] for e in events if e.get("event_type") == "run_finished" and e["run_id"] in mine
            and (e.get("outcome") or {}).get("run_status") == "completed"]


# ---------------------------------------------------------------- controlled reset (R8; Problem 7, A3)

def _audit_db_system(paths: Paths, event_type: str, payload: dict) -> int:
    """One system event in audit.db (which a reset never erases), in its own transaction."""
    audit_db.ensure(paths.audit_db)
    with closing(maintenance.connect_rw(paths.audit_db)) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            ev = audit_db.append_event(con, actor="system", actor_kind="system", event_type=event_type, schema="main",
                                       payload=payload)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    return ev


def controlled_reset(paths: Paths, trail: audit_trail.AuditTrail, *, requested_by, via: str) -> dict:
    """Reset the working database (R8) so that its history cannot silently become inconsistent.

    Under ONE hold of the run lock:
      0. reconcile any earlier gap;
      1. JSON 'db_reset_requested' (reset_id)            - if it cannot be written, nothing happens;
      2. audit.db 'db_reset_started' (reset_id)          - if it cannot be written, nothing happens (outcome recorded);
         this is the boundary: audit.db events after it belong to the new working copy, if the swap succeeds;
      3. the existing verified reset, with the reset_id STAMPED into the new copy before the atomic swap;
      4. audit.db 'db_reset_finished' and JSON 'db_reset' (same reset_id), retried.
    If 4 fails (or the process dies after 3), the working copy's stamp still proves the reset took effect;
    reconciliation reports and records it. No step claims atomicity across the JSON file and SQLite."""
    name = validate_decider(requested_by)
    assert_writable(paths.working_db, paths)
    lock = _hold_run_lock(paths)
    try:
        reconciled = _reconcile_first(paths, trail)
        reset_id, requested_at = str(uuid.uuid4()), audit_db.utc_now()
        base = {"reset_id": reset_id, "requested_by": name, "via": via, "mode": paths.mode,
                "working_db": str(paths.working_db)}
        request = audit_trail.system_event("db_reset_requested", outcome=None, details=base)
        try:
            trail.append([request])
        except audit_trail.AuditTrailError as exc:
            raise DecisionError("AUDIT_FAILED", f"The audit trail could not record the reset request; nothing was changed ({exc}).") from exc

        def outcome(status: dict, details: dict) -> bool:
            return append_with_retry(trail, audit_trail.system_event(
                "db_reset", outcome=status, details={**base, "request_event_id": request["event_id"], **details}))

        try:
            started = _audit_db_system(paths, "db_reset_started", {**base, "audit_trail_request_event_id": request["event_id"]})
        except (sqlite3.Error, OSError) as exc:
            outcome({"status": "error", "code": "AUDIT_FAILED", "message": audit_trail.summarize(str(exc))},
                    {"stage": "audit.db boundary marker", "working_copy_replaced": False})
            raise DecisionError("AUDIT_FAILED", f"audit.db could not record the reset boundary; nothing was changed ({exc}).") from exc
        try:
            result = maintenance.reset_working_db_locked(paths, stamp={"reset_id": reset_id, "requested_by": name,
                                                                       "requested_at": requested_at})
        except (maintenance.ResetError, OSError) as exc:
            try:
                _audit_db_system(paths, "db_reset_finished", {**base, "status": "error", "message": str(exc)})
            except (sqlite3.Error, OSError):
                pass
            outcome({"status": "error", "code": "RESET_FAILED", "message": audit_trail.summarize(str(exc))},
                    {"stage": "reset", "audit_db_started_event_id": started})
            raise DecisionError("RESET_FAILED", str(exc)) from exc

        warnings = []
        try:
            finished = _audit_db_system(paths, "db_reset_finished", {**base, "status": "ok",
                                                                     "working_sha256": result["working_sha256_after_extension"]})
        except (sqlite3.Error, OSError) as exc:
            finished = None
            warnings.append(f"Reset done, but audit.db could not record its completion ({exc}); the boundary marker "
                            f"(event {started}) and the working copy's stamp still identify it.")
        recorded = outcome({"status": "ok"}, {**result, "audit_db_started_event_id": started,
                                              "audit_db_finished_event_id": finished})
        if not recorded:
            warnings.append(f"RESET COMPLETED (the working copy carries stamp {reset_id}), but its outcome event could not "
                            f"be written to output/audit_trail.json. " + RECONCILE_HINT)
        return {"reset": True, "reset_id": reset_id, "requested_by": name, "result": result,
                "audit_trail_recorded": recorded, "reconciliation_pending": not recorded,
                "audit_db_event_ids": {"started": started, "finished": finished}, "earlier_gaps_reconciled": len(reconciled),
                "warnings": warnings}
    finally:
        lock.__exit__(None, None, None)
