"""Proposal and draft operations (Problem 5, Stage 2).

The only code that writes agent work to the working database. Each operation:
  1. checks that the trusted RunIdentity's agent may perform it,
  2. opens the RESTRICTED write connection (authorizer + audit.db attached),
  3. validates inputs and applies rules R1-R8 / proposed policies inside BEGIN IMMEDIATE,
  4. inserts ONE pending_actions or drafts row and ONE audit.db event, then commits both or neither.

Nothing here approves, executes, pays, orders, or sends. Specification:
output/p5_mcp_tools_spec.md (revision 2). Errors are OpsError with a stable code.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import audit_db
import authz
import policies
from paths import Paths, assert_writable

AGENTS = ("boss", "inventory", "accounting", "facilities", "customer_service")
RUN_MODES = ("simulated", "live")
OPEN_STATUSES = ("proposed", "approved")

# Which agent may perform which write operation (spec section 4). Enforced here as defense in
# depth; the primary boundary is per-agent tool registration at server launch (Stage 3).
OPERATION_AGENTS = {
    "propose_payment": frozenset({"accounting"}),
    "propose_purchase": frozenset({"inventory"}),
    "propose_quote": frozenset({"customer_service"}),
    "draft_message": frozenset({"customer_service", "facilities"}),
}

ERROR_CODES = ("INVALID_INPUT", "NOT_FOUND", "REFUSED_RULE", "DUPLICATE", "NOT_PERMITTED",
               "NO_RUN_CONTEXT", "DB_UNAVAILABLE", "DB_DENIED", "AUDIT_FAILED")

# Test-only fault injection. Always None in normal operation. It is a module attribute, so it can
# only be set by Python code running inside the same process (offline tests and the offline evidence
# script). No MCP tool, argument, or request field reaches it, server.py never sets it, and a LIVE
# run refuses to write while it is set (see _run). Protections are tested in test_stage3_safeguards.py.
_fault_hook = None


class OpsError(Exception):
    def __init__(self, code: str, message: str, existing_id: int | None = None):
        assert code in ERROR_CODES
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.existing_id = code, message, existing_id


@dataclass(frozen=True)
class RunIdentity:
    """Who is acting. Built ONLY by trusted code from the server's launch environment (Stage 3)."""
    run_id: str
    agent: str
    mode: str

    def __post_init__(self):
        if self.agent not in AGENTS:
            raise ValueError(f"unknown agent {self.agent!r}")
        if self.mode not in RUN_MODES:
            raise ValueError(f"unknown run mode {self.mode!r}")
        try:
            parsed = uuid.UUID(self.run_id)
        except (ValueError, TypeError, AttributeError):
            raise ValueError(f"run_id is not a UUID: {self.run_id!r}") from None
        if parsed.version != 4 or str(parsed) != self.run_id:
            raise ValueError(f"run_id must be a canonical UUID4: {self.run_id!r}")


# ---------------------------------------------------------------- connection

def open_write_connection(paths: Paths) -> authz.RestrictedConnection:
    """The restricted connection every write operation uses."""
    assert_writable(paths.working_db, paths)
    if not paths.working_db.exists():
        raise OpsError("DB_UNAVAILABLE", f"Working database not found at {paths.working_db}. Run the reset first.")
    try:
        audit_db.ensure(paths.audit_db)
        con = sqlite3.connect(paths.working_db, isolation_level=None, timeout=5.0, factory=authz.RestrictedConnection)
    except sqlite3.Error as exc:
        raise OpsError("DB_UNAVAILABLE", f"Cannot open databases: {exc}") from exc
    try:
        con.execute("ATTACH DATABASE ? AS audit", (str(paths.audit_db),))
        for schema in ("main", "audit"):
            con.execute(f"PRAGMA {schema}.journal_mode = DELETE")
            con.execute(f"PRAGMA {schema}.synchronous = FULL")
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA recursive_triggers = ON")
        con.execute("PRAGMA busy_timeout = 5000")
        settings = {
            "main.journal_mode": con.execute("PRAGMA main.journal_mode").fetchone()[0],
            "audit.journal_mode": con.execute("PRAGMA audit.journal_mode").fetchone()[0],
            "main.synchronous": con.execute("PRAGMA main.synchronous").fetchone()[0],
            "audit.synchronous": con.execute("PRAGMA audit.synchronous").fetchone()[0],
            "foreign_keys": con.execute("PRAGMA foreign_keys").fetchone()[0],
            "recursive_triggers": con.execute("PRAGMA recursive_triggers").fetchone()[0],
        }
        required = {"main.journal_mode": "delete", "audit.journal_mode": "delete", "main.synchronous": 2,
                    "audit.synchronous": 2, "foreign_keys": 1, "recursive_triggers": 1}
        if settings != required:
            raise OpsError("DB_UNAVAILABLE", f"Required SQLite settings not in effect: {settings}")
        if hasattr(con, "enable_load_extension"):
            con.enable_load_extension(False)
        authz.install(con)
    except OpsError:
        con.close()
        raise
    except sqlite3.Error as exc:
        con.close()
        raise OpsError("DB_UNAVAILABLE", f"Cannot prepare the write connection: {exc}") from exc
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------- helpers

def _fault(stage: str) -> None:
    if _fault_hook is not None:
        _fault_hook(stage)


def _money(x: float) -> float:
    return round(float(x), 2)


def _require_text(name: str, value, bounds: tuple[int, int]) -> str:
    if not isinstance(value, str):
        raise OpsError("INVALID_INPUT", f"{name} must be text")
    text = value.strip()
    if not bounds[0] <= len(text) <= bounds[1]:
        raise OpsError("INVALID_INPUT", f"{name} must be {bounds[0]}-{bounds[1]} characters (got {len(text)})")
    return text


def _require_int(name: str, value, low: int = 1, high: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OpsError("INVALID_INPUT", f"{name} must be a whole number")
    if value < low or (high is not None and value > high):
        raise OpsError("INVALID_INPUT", f"{name} must be between {low} and {high}" if high else f"{name} must be >= {low}")
    return value


def _require_money(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise OpsError("INVALID_INPUT", f"{name} must be a positive number")
    if Decimal(str(value)).as_tuple().exponent < -2:
        raise OpsError("INVALID_INPUT", f"{name} must have at most 2 decimal places")
    return _money(value)


def _business_date(con) -> date:
    return date.fromisoformat(con.execute("SELECT date_today FROM desk").fetchone()[0])


def _ticket(con, ticket_id: int) -> sqlite3.Row:
    row = con.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        known = ", ".join(str(r[0]) for r in con.execute("SELECT id FROM tickets ORDER BY id"))
        raise OpsError("NOT_FOUND", f"Unknown ticket {ticket_id}. Known tickets: {known}")
    return row


def _cash(con) -> float:
    return _money(con.execute("SELECT COALESCE(SUM(balance), 0) FROM cash_accounts").fetchone()[0])


def _open_payment_total(con) -> float:
    return _money(con.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM pending_actions WHERE kind = 'payment' AND status IN ('proposed', 'approved')"
    ).fetchone()[0])


def _open_invoices_for_vendor(con, vendor_id: int) -> list[int]:
    return [r[0] for r in con.execute(
        "SELECT id FROM invoices WHERE vendor_id = ? AND status = 'open' ORDER BY id", (vendor_id,))]


def _sku_size(con, sku, size) -> sqlite3.Row:
    if not isinstance(sku, str) or not isinstance(size, str):
        raise OpsError("INVALID_INPUT", "sku and size must be text")
    row = con.execute(
        """SELECT i.sku, i.size, i.qty, i.name, p.unit_cost, p.list_price
           FROM inventory i LEFT JOIN pricing p ON p.sku = i.sku WHERE i.sku = ? AND i.size = ?""",
        (sku, size)).fetchone()
    if row is None:
        known = ", ".join(f"{r[0]}/{r[1]}" for r in con.execute("SELECT sku, size FROM inventory ORDER BY sku, size"))
        raise OpsError("NOT_FOUND", f"Unknown sku/size {sku}/{size}. Known: {known}")
    if row["unit_cost"] is None or row["list_price"] is None:
        raise OpsError("NOT_FOUND", f"No pricing recorded for {sku}")
    return row


def _insert(con, table: str, row: dict) -> int:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    return con.execute(f"INSERT INTO main.{table} ({cols}) VALUES ({marks})", tuple(row.values())).lastrowid


def _is_denial(exc: sqlite3.Error) -> bool:
    return "not authorized" in str(exc) or "authorization denied" in str(exc)


def _run(paths: Paths, identity: RunIdentity, operation: str, call_id: str | None, plan) -> dict:
    """Shared transaction: plan() validates and builds the row; row + audit event commit together."""
    if not isinstance(identity, RunIdentity):
        raise OpsError("NO_RUN_CONTEXT", "Write operations require a trusted run identity.")
    if identity.agent not in OPERATION_AGENTS[operation]:
        raise OpsError("NOT_PERMITTED", f"Agent {identity.agent!r} may not perform {operation}.")
    if call_id is not None and (not isinstance(call_id, str) or len(call_id) > 100):
        raise OpsError("INVALID_INPUT", "call_id must be text of at most 100 characters")
    if _fault_hook is not None and identity.mode == "live":
        raise OpsError("DB_UNAVAILABLE", "Test fault injection is active; refusing to write during a live run.")

    con = open_write_connection(paths)
    try:
        con.execute("BEGIN IMMEDIATE")
        today = _business_date(con)
        table, row, result, event_type = plan(con, today)
        row.update(run_id=identity.run_id, call_id=call_id, business_date=today.isoformat(),
                   created_at=audit_db.utc_now())
        try:
            new_id = _insert(con, table, row)
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise OpsError("DUPLICATE", f"An equivalent open record already exists ({exc}).") from exc
            raise OpsError("INVALID_INPUT", f"Rejected by database constraint: {exc}") from exc
        _fault("after_row")
        try:
            event_id = audit_db.append_event(
                con, actor=identity.agent, actor_kind="agent", event_type=event_type,
                payload={"table": table, "id": new_id, **row}, business_date=today.isoformat(),
                run_id=identity.run_id, run_mode=identity.mode, call_id=call_id, ref_table=table, ref_id=new_id)
        except sqlite3.Error as exc:
            raise OpsError("AUDIT_FAILED", f"Audit event could not be recorded; nothing was saved ({exc}).") from exc
        _fault("after_audit")
        con.execute("COMMIT")
    except BaseException as exc:
        if con.in_transaction:
            con.execute("ROLLBACK")
        if isinstance(exc, sqlite3.Error):
            if _is_denial(exc):
                raise OpsError("DB_DENIED", f"Database authorizer refused the operation; nothing was saved ({exc}).") from exc
            raise OpsError("DB_UNAVAILABLE", f"Database error; nothing was saved ({exc}).") from exc
        raise
    finally:
        con.close()

    key = "action_id" if table == "pending_actions" else "draft_id"
    result["links"] = {key: new_id, "audit_db_event_id": event_id}
    result["business_date"] = today.isoformat()
    return result


def _result(status: str, **extra) -> dict:
    base = {"created": True, "status": status, "requires_human_approval": True,
            "checks": [], "warnings": [], "human_judgment": [], "unknowns": []}
    base.update(extra)
    return base


# ---------------------------------------------------------------- operations

def propose_payment(paths: Paths, identity: RunIdentity, *, kind, ref_id, amount, rationale,
                    ticket_id=None, account="checking", call_id=None) -> dict:
    """Prepare (never execute) a full payment of an open invoice or the lease rent. Accounting only."""
    if kind not in policies.PAYMENT_KINDS:
        raise OpsError("INVALID_INPUT", f"kind must be one of {policies.PAYMENT_KINDS}")
    ref_id = _require_int("ref_id", ref_id)
    amount = _require_money("amount", amount)
    rationale = _require_text("rationale", rationale, policies.RATIONALE_LENGTH)
    if ticket_id is not None:
        ticket_id = _require_int("ticket_id", ticket_id)
    if not isinstance(account, str):
        raise OpsError("INVALID_INPUT", "account must be text")

    def plan(con, today):
        acct = con.execute("SELECT name, balance FROM cash_accounts WHERE name = ?", (account,)).fetchone()
        if acct is None:
            known = ", ".join(r[0] for r in con.execute("SELECT name FROM cash_accounts"))
            raise OpsError("NOT_FOUND", f"Unknown account {account!r}. Known accounts: {known}")
        ticket = _ticket(con, ticket_id) if ticket_id is not None else None
        result = _result("proposed", executable=False)

        if kind == "invoice":
            inv = con.execute("SELECT * FROM invoices WHERE id = ?", (ref_id,)).fetchone()
            if inv is None:
                known = ", ".join(str(r[0]) for r in con.execute("SELECT id FROM invoices ORDER BY id"))
                raise OpsError("NOT_FOUND", f"Unknown invoice {ref_id}. Known invoices: {known}")
            if inv["status"] != "open":
                raise OpsError("REFUSED_RULE", f"R4: invoice {ref_id} is {inv['status']!r}, not open")
            stored, basis, link = _money(inv["amount"]), "invoice_amount", {"invoice_id": ref_id}
            days = (today - date.fromisoformat(inv["due_date"])).days
            timing = f"invoice {ref_id} " + (f"{days} days overdue" if days > 0 else f"due in {-days} days") + f" as of {today}"
            if ticket is not None and ticket["invoice_id"] not in (None, ref_id):
                result["warnings"].append(f"Ticket {ticket_id} is linked to invoice {ticket['invoice_id']}, not {ref_id}.")
        else:
            lease = con.execute("SELECT * FROM leases WHERE id = ?", (ref_id,)).fetchone()
            if lease is None:
                known = ", ".join(str(r[0]) for r in con.execute("SELECT id FROM leases ORDER BY id"))
                raise OpsError("NOT_FOUND", f"Unknown lease {ref_id}. Known leases: {known}")
            stored, basis, link = _money(lease["monthly_rent"]), "lease_rent", {"lease_id": ref_id}
            days = (date.fromisoformat(lease["next_due"]) - today).days
            timing = f"lease {ref_id} rent due {lease['next_due']} ({days} days from {today})"
            if ticket is not None and ticket["lease_id"] not in (None, ref_id):
                result["warnings"].append(f"Ticket {ticket_id} is linked to lease {ticket['lease_id']}, not {ref_id}.")

        if amount != stored:
            raise OpsError("REFUSED_RULE", f"policy:full_amount_only: amount {amount:.2f} differs from the stored {stored:.2f}")
        column = next(iter(link))
        dup = con.execute(
            f"SELECT id FROM pending_actions WHERE kind = 'payment' AND {column} = ? AND status IN ('proposed', 'approved')",
            (ref_id,)).fetchone()
        if dup:
            raise OpsError("DUPLICATE", f"Open payment proposal {dup[0]} already exists for {kind} {ref_id}.", existing_id=dup[0])

        balance = _money(acct["balance"])
        other_open = _open_payment_total(con)
        fundable = stored <= balance
        cash_if_all = _money(balance - other_open - stored)
        result["checks"] = [
            {"rule": "R1", "result": "pass" if fundable else "fail",
             "detail": f"{stored:.2f} {'<=' if fundable else '>'} stored balance {balance:.2f}"},
            {"rule": "R1-competing", "result": "pass" if cash_if_all >= 0 else "warn",
             "detail": f"cash if all open payment proposals were executed: {cash_if_all:.2f}"},
            {"rule": "R5", "result": "info", "detail": timing},
        ]
        result["human_judgment"].append("Approval and execution are human-only (Problem 7). This proposal moves no money.")
        if not fundable:
            result["human_judgment"].append(
                "NOT EXECUTABLE: amount exceeds the stored balance; execution would be refused (R1). Recorded for human review only.")
        if cash_if_all < 0:
            result["warnings"].append(
                f"Competing proposals: open payment proposals would exceed cash by {-cash_if_all:.2f}. A human must choose.")
        result["proposal"] = {"kind": "payment", **link, "amount": stored, "amount_basis": basis,
                              "account": account, "fundable_at_proposal": fundable}
        result["computed"] = {"stored_balance": balance, "other_open_payment_proposals": other_open,
                              "cash_if_all_proposed_payments_executed": cash_if_all,
                              "note": "Hypothetical arithmetic. Proposals reserve no cash and move no money."}
        row = {"kind": "payment", "ticket_id": ticket_id, **link, "amount": stored, "amount_basis": basis,
               "account": account, "fundable_at_proposal": int(fundable), "rationale": rationale,
               "warnings": json.dumps(result["warnings"]), "proposed_by": identity.agent}
        return "pending_actions", row, result, "proposal_created"

    return _run(paths, identity, "propose_payment", call_id, plan)


def propose_purchase(paths: Paths, identity: RunIdentity, *, vendor_id, sku, size, qty, rationale,
                     ticket_id=None, call_id=None) -> dict:
    """Prepare (never place) a restock purchase. Allowed for a blocked vendor, but flagged (R3). Inventory only."""
    vendor_id = _require_int("vendor_id", vendor_id)
    qty = _require_int("qty", qty, 1, policies.PURCHASE_MAX_QTY)
    rationale = _require_text("rationale", rationale, policies.RATIONALE_LENGTH)
    if ticket_id is not None:
        ticket_id = _require_int("ticket_id", ticket_id)

    def plan(con, today):
        vendor = con.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
        if vendor is None:
            known = ", ".join(f"{r[0]} {r[1]}" for r in con.execute("SELECT id, name FROM vendors ORDER BY id"))
            raise OpsError("NOT_FOUND", f"Unknown vendor {vendor_id}. Known vendors: {known}")
        item = _sku_size(con, sku, size)
        ticket = _ticket(con, ticket_id) if ticket_id is not None else None

        dup = con.execute(
            """SELECT id FROM pending_actions WHERE kind = 'purchase' AND status IN ('proposed', 'approved')
               AND COALESCE(ticket_id, -1) = COALESCE(?, -1) AND vendor_id = ? AND sku = ? AND size = ? AND qty = ?""",
            (ticket_id, vendor_id, sku, size, qty)).fetchone()
        if dup:
            raise OpsError("DUPLICATE", f"Open purchase proposal {dup[0]} is identical (ticket, vendor, sku, size, qty).",
                           existing_id=dup[0])
        if ticket_id is not None:
            alt_rows = con.execute("SELECT id FROM pending_actions WHERE kind = 'purchase' AND status IN ('proposed', 'approved') "
                                   "AND ticket_id = ? ORDER BY id", (ticket_id,))
        else:
            alt_rows = con.execute("SELECT id FROM pending_actions WHERE kind = 'purchase' AND status IN ('proposed', 'approved') "
                                   "AND ticket_id IS NULL AND sku = ? AND size = ? ORDER BY id", (sku, size))
        alternatives = [r[0] for r in alt_rows]

        blocking = _open_invoices_for_vendor(con, vendor_id)
        estimate = _money(qty * item["unit_cost"])
        cash_if_all = _money(_cash(con) - _open_payment_total(con))
        arrival = None if blocking else (today + timedelta(days=vendor["lead_days"])).isoformat()

        result = _result("proposed", executable=False)
        if blocking:
            result["warnings"].append(
                f"R3: {vendor['name']} has open unpaid invoice(s) {blocking}. No shipment, order placement, or receipt "
                "is possible until they are paid.")
        if estimate > cash_if_all:
            result["warnings"].append(
                f"Estimated cost {estimate:.2f} exceeds cash remaining if all open payment proposals were executed ({cash_if_all:.2f}).")
        if ticket is not None and (ticket["sku"], ticket["size"]) != (sku, size):
            result["warnings"].append(f"Ticket {ticket_id} concerns {ticket['sku']}/{ticket['size']}, not {sku}/{size}.")
        if alternatives:
            result["human_judgment"].append(f"Alternative to open purchase proposal(s) {alternatives}; a human must choose.")
        result["human_judgment"].append(
            f"Confirm that {vendor['name']} (specialty: {vendor['specialty']}) supplies this product.")
        result["unknowns"].append(
            "No vendor quote exists; the actual cost is unknown and may differ substantially from this estimate.")
        result["checks"] = [
            {"rule": "R3", "result": "warn" if blocking else "pass",
             "detail": f"vendor blocked by invoice(s) {blocking}" if blocking else "vendor has no open invoices"},
            {"rule": "R1-informational", "result": "warn" if estimate > cash_if_all else "pass",
             "detail": f"estimate {estimate:.2f} vs cash if all open payment proposals executed {cash_if_all:.2f}"},
        ]
        result["proposal"] = {"kind": "purchase", "vendor_id": vendor_id, "sku": sku, "size": size, "qty": qty,
                              "amount": estimate, "amount_basis": "estimate_unit_cost",
                              "vendor_blocked": bool(blocking), "blocking_invoice_ids": blocking,
                              "alternative_to": alternatives}
        result["computed"] = {"estimate": estimate, "earliest_arrival_if_ordered_today": arrival,
                              "note": "Estimate = qty x pricing.unit_cost; not a vendor quote. "
                                      + ("Arrival not estimable while the vendor is blocked." if blocking
                                         else "Arrival = business date + lead_days if ordered today; no order exists.")}
        row = {"kind": "purchase", "ticket_id": ticket_id, "vendor_id": vendor_id, "sku": sku, "size": size, "qty": qty,
               "amount": estimate, "amount_basis": "estimate_unit_cost", "vendor_blocked": int(bool(blocking)),
               "blocking_invoice_ids": json.dumps(blocking), "alternative_to": json.dumps(alternatives),
               "rationale": rationale, "warnings": json.dumps(result["warnings"]), "proposed_by": identity.agent}
        return "pending_actions", row, result, "proposal_created"

    return _run(paths, identity, "propose_purchase", call_id, plan)


def propose_quote(paths: Paths, identity: RunIdentity, *, ticket_id, sku, size, qty, unit_price, rationale,
                  call_id=None) -> dict:
    """Prepare (never send or accept) a customer price quote. Customer Service only."""
    ticket_id = _require_int("ticket_id", ticket_id)
    qty = _require_int("qty", qty)
    unit_price = _require_money("unit_price", unit_price)
    rationale = _require_text("rationale", rationale, policies.RATIONALE_LENGTH)

    def plan(con, today):
        ticket = _ticket(con, ticket_id)
        if ticket["status"] != "open":
            raise OpsError("REFUSED_RULE", f"Ticket {ticket_id} is {ticket['status']!r}, not open")
        if (ticket["sku"], ticket["size"], ticket["qty"]) != (sku, size, qty):
            raise OpsError("REFUSED_RULE",
                           f"policy:quote_matches_ticket: ticket {ticket_id} requests "
                           f"{ticket['sku']}/{ticket['size']} x {ticket['qty']}, not {sku}/{size} x {qty}")
        item = _sku_size(con, sku, size)
        cost, listed = _money(item["unit_cost"]), _money(item["list_price"])
        if unit_price < cost:
            raise OpsError("REFUSED_RULE", f"policy:quote_floor: {unit_price:.2f} is below unit cost {cost:.2f}")
        if unit_price > listed:
            raise OpsError("REFUSED_RULE", f"policy:quote_cap: {unit_price:.2f} is above list price {listed:.2f}")
        dup = con.execute("SELECT id FROM pending_actions WHERE kind = 'quote' AND status IN ('proposed', 'approved') "
                          "AND ticket_id = ? AND unit_price = ?", (ticket_id, unit_price)).fetchone()
        if dup:
            raise OpsError("DUPLICATE", f"Open quote proposal {dup[0]} already offers {unit_price:.2f} on ticket {ticket_id}.",
                           existing_id=dup[0])
        alternatives = [r[0] for r in con.execute(
            "SELECT id FROM pending_actions WHERE kind = 'quote' AND status IN ('proposed', 'approved') AND ticket_id = ? ORDER BY id",
            (ticket_id,))]

        on_hand = item["qty"]
        shortfall = max(qty - on_hand, 0)
        total = _money(qty * unit_price)
        result = _result("proposed", quote_state="proposed", customer_acceptance="not recorded")
        if shortfall:
            result["human_judgment"].append(
                f"Only {on_hand} on hand ({shortfall} short). The quote must not promise a delivery date.")
        if alternatives:
            result["human_judgment"].append(f"Alternative to open quote proposal(s) {alternatives}; a human must choose.")
        result["human_judgment"].append("A quote is communicated only after human approval (Problem 7); "
                                        "customer acceptance is never recorded by the system.")
        result["checks"] = [
            {"rule": "policy:quote_floor", "result": "pass", "detail": f"{unit_price:.2f} >= unit cost {cost:.2f}"},
            {"rule": "policy:quote_cap", "result": "pass", "detail": f"{unit_price:.2f} <= list price {listed:.2f}"},
            {"rule": "policy:quote_matches_ticket", "result": "pass", "detail": f"{sku}/{size} x {qty}"},
        ]
        result["proposal"] = {"kind": "quote", "ticket_id": ticket_id, "sku": sku, "size": size, "qty": qty,
                              "unit_price": unit_price, "amount": total, "amount_basis": "quoted_unit_price",
                              "alternative_to": alternatives}
        result["computed"] = {
            "list_total": _money(qty * listed), "discount_pct": round(100 * (1 - unit_price / listed), 2),
            "unit_margin": _money(unit_price - cost), "total_margin": _money(qty * (unit_price - cost)),
            "fulfillable_from_stock": shortfall == 0, "on_hand": on_hand, "shortfall": shortfall,
            "note": "R6: sales revenue is not modeled; this quote adds no cash."}
        row = {"kind": "quote", "ticket_id": ticket_id, "sku": sku, "size": size, "qty": qty, "unit_price": unit_price,
               "amount": total, "amount_basis": "quoted_unit_price", "alternative_to": json.dumps(alternatives),
               "rationale": rationale, "warnings": json.dumps(result["warnings"]), "proposed_by": identity.agent}
        return "pending_actions", row, result, "proposal_created"

    return _run(paths, identity, "propose_quote", call_id, plan)


_MONEY_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")
_DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}"
    r"|today|tomorrow|tonight|next week|this week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE)


def commitment_warnings(body: str, related: sqlite3.Row | None) -> list[str]:
    """Flag wording that may assert a commitment. Warnings only; a linked proposal never clears one."""
    warnings = []
    lowered = body.lower()
    for phrase in policies.COMMITMENT_PHRASES:
        if re.search(rf"\b{re.escape(phrase)}\b", lowered):
            warnings.append(f"Body contains {phrase!r}, which may assert a commitment. Human review required.")
    for match in _MONEY_RE.finditer(body):
        figure = _money(float(match.group(1).replace(",", "")))
        note = f"Body asserts a price or amount (${figure:.2f})."
        if related is not None and figure in {_money(related["amount"]), _money(related["unit_price"] or -1)}:
            flag = "" if related["status"] == "approved" else ", NOT approved"
            note += f" Matches {related['kind']} proposal {related['id']} (status: {related['status']}{flag}); warning stands."
        warnings.append(note)
    for match in _DATE_RE.finditer(body):
        warnings.append(f"Body mentions a date or time ({match.group(0)!r}), which may imply a delivery or payment commitment.")
    return warnings


def draft_message(paths: Paths, identity: RunIdentity, *, ticket_id, subject, body, related_action_id=None,
                  call_id=None) -> dict:
    """Store an unsent email draft to the ticket's verified counterparty (R7: never sent).
    Customer Service and Facilities only."""
    ticket_id = _require_int("ticket_id", ticket_id)
    subject = _require_text("subject", subject, policies.SUBJECT_LENGTH)
    body = _require_text("body", body, policies.BODY_LENGTH)
    if related_action_id is not None:
        related_action_id = _require_int("related_action_id", related_action_id)

    def plan(con, today):
        ticket = _ticket(con, ticket_id)
        result = _result("draft", sent=False, recipient_contact=None)
        if ticket["lease_id"] is not None:
            lease = con.execute("SELECT landlord FROM leases WHERE id = ?", (ticket["lease_id"],)).fetchone()
            if lease is None:
                raise OpsError("NOT_FOUND", f"Ticket {ticket_id} links to lease {ticket['lease_id']}, which does not exist")
            recipient, source = lease["landlord"], "leases.landlord"
            if ticket["requester"] != recipient:
                result["warnings"].append(
                    f"Ticket requester {ticket['requester']!r} differs from the lease landlord {recipient!r}; addressed to the landlord.")
        else:
            recipient, source = ticket["requester"], "tickets.requester"
        if policies.DRAFT_AUTHORITY[identity.agent] != source:
            responsible = next(a for a, s in policies.DRAFT_AUTHORITY.items() if s == source)
            raise OpsError("NOT_PERMITTED",
                           f"{identity.agent} may not draft to {recipient} ({source}) on ticket {ticket_id}; "
                           f"delegate this communication to {responsible}.")

        related = None
        if related_action_id is not None:
            related = con.execute("SELECT * FROM pending_actions WHERE id = ?", (related_action_id,)).fetchone()
            if related is None:
                raise OpsError("NOT_FOUND", f"Unknown proposal {related_action_id}")
            if related["ticket_id"] != ticket_id:
                raise OpsError("INVALID_INPUT", f"Proposal {related_action_id} belongs to ticket {related['ticket_id']}, not {ticket_id}")

        result["warnings"] += commitment_warnings(body, related)
        result["human_judgment"].append("R7: this message will never be sent by the system. A human must review it.")
        result["unknowns"].append("No contact address is stored; a human must address the message.")
        result["draft"] = {"ticket_id": ticket_id, "recipient": recipient, "recipient_source": source,
                           "channel": "email", "subject": subject, "related_action_id": related_action_id}
        row = {"ticket_id": ticket_id, "recipient": recipient, "recipient_source": source, "channel": "email",
               "subject": subject, "body": body, "related_action_id": related_action_id,
               "warnings": json.dumps(result["warnings"]), "created_by": identity.agent}
        return "drafts", row, result, "draft_created"

    return _run(paths, identity, "draft_message", call_id, plan)
