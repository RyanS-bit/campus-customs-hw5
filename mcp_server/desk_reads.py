"""Read-only queries for the backend API (Problem 7).

Every connection is opened with SQLite mode=ro, so nothing here can write. Ticket and proposal rows are
shaped by the same helpers the MCP tools use (server._ticket_with_links, server._action_dict), so the
dashboard and the agents see the same facts.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import server
from paths import Paths

DECISION_FIELDS = ("decided_by", "decided_at", "decision_note", "executed_at", "payment_id")


class ReadError(RuntimeError):
    """The working database is missing or cannot be read."""


def _connect(p: Paths) -> sqlite3.Connection:
    if not p.working_db.exists():
        raise ReadError(f"Working database not found at {p.working_db}. Reset it first.")
    try:
        con = sqlite3.connect(p.working_db.resolve().as_uri() + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    except sqlite3.Error as exc:
        raise ReadError(f"Cannot read the working database: {exc}") from exc
    if not {"pending_actions", "drafts"} <= tables:
        con.close()
        raise ReadError("The working database has not been prepared (pending_actions/drafts missing). Reset it first.")
    return con


def _withdrawals(p: Paths) -> dict[tuple[int, str], dict]:
    """Withdrawn purchase authorizations, from audit.db (read-only). Keyed by (proposal id, the proposal's run_id) so that
    after a reset, when ids restart, an old withdrawal can never be attached to a new proposal."""
    if not p.audit_db.exists():
        return {}
    try:
        with closing(sqlite3.connect(p.audit_db.resolve().as_uri() + "?mode=ro", uri=True)) as con:
            rows = con.execute("SELECT ref_id, payload, ts_utc FROM events WHERE event_type = 'action_rejected' "
                               "AND ref_table = 'pending_actions' ORDER BY id").fetchall()
    except sqlite3.Error:
        return {}
    out = {}
    for ref_id, payload, ts in rows:
        d = json.loads(payload)
        if d.get("withdrawn_after_authorization"):
            out[(ref_id, d.get("proposal_run_id"))] = {
                "authorized_by": d.get("authorized_by"), "authorized_at": d.get("authorized_at"),
                "authorization_note": d.get("authorization_note"), "withdrawn_by": d.get("decided_by"),
                "withdrawn_at": d.get("decided_at"), "withdrawal_note": d.get("note"), "executed": False, "paid": False,
                "source": "audit.db action_rejected event (the authorization's own action_approved event is also kept)"}
    return out


def _resolutions(p: Paths) -> dict[int, dict]:
    """How each ticket was resolved (Problem 9, follow-up 36), from audit.db ticket_resolved events (read-only).
    Only events after the latest completed reset count, so an old resolution never attaches to a reopened ticket.
    Events written before follow-up 36 have no resolution_basis; they confirmed the business outcome."""
    if not p.audit_db.exists():
        return {}
    try:
        with closing(sqlite3.connect(p.audit_db.resolve().as_uri() + "?mode=ro", uri=True)) as con:
            last_reset = con.execute("SELECT COALESCE(MAX(id), 0) FROM events WHERE event_type = 'db_reset_finished'").fetchone()[0]
            rows = con.execute("SELECT ref_id, payload, ts_utc FROM events WHERE event_type = 'ticket_resolved' "
                               "AND ref_table = 'tickets' AND id > ? ORDER BY id", (last_reset,)).fetchall()
    except sqlite3.Error:
        return {}
    out = {}
    for ref_id, payload, ts in rows:
        d = json.loads(payload)
        basis = d.get("resolution_basis") or ("business_outcome" if d.get("business_outcome_confirmed") else None)
        out[ref_id] = {"basis": basis, "business_outcome_confirmed": bool(d.get("business_outcome_confirmed")),
                       "unverified_outcomes": d.get("unverified_outcomes") or [],
                       "resolved_by": d.get("decided_by"), "resolved_at": ts, "summary": d.get("resolution_summary"),
                       "source": "audit.db ticket_resolved event"}
    return out


def _current_vendor_status(con, vendor_id) -> dict:
    """CURRENT vendor restriction from today's invoices table (not from warnings stored when the proposal was made)."""
    v = con.execute("SELECT name FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
    open_ids = [r[0] for r in con.execute("SELECT id FROM invoices WHERE vendor_id = ? AND status = 'open' ORDER BY id", (vendor_id,))]
    return {"vendor_id": vendor_id, "vendor": v["name"] if v else None, "open_invoice_ids": open_ids, "blocked_now": bool(open_ids),
            "as_of": con.execute("SELECT date_today FROM desk").fetchone()[0], "source": "invoices table (current)"}


def _action(r: sqlite3.Row, con=None, withdrawals: dict | None = None) -> dict:
    d = server._action_dict(r)
    d.update({k: r[k] for k in DECISION_FIELDS})
    # Problem 9: stored warnings and flags describe conditions WHEN THE PROPOSAL WAS MADE; say so explicitly.
    d["warnings_recorded_at"] = {"business_date": r["business_date"], "created_at": r["created_at"]}
    d["withdrawal"] = (withdrawals or {}).get((r["id"], r["run_id"])) if r["status"] == "rejected" else None
    d["current_vendor_status"] = _current_vendor_status(con, r["vendor_id"]) if con is not None and r["kind"] == "purchase" else None
    return d


def business_date(p: Paths) -> str:
    with closing(_connect(p)) as con:
        return con.execute("SELECT date_today FROM desk").fetchone()[0]


def list_tickets(p: Paths) -> dict:
    resolutions = _resolutions(p)
    with closing(_connect(p)) as con:
        tickets = []
        for t in con.execute("SELECT * FROM tickets ORDER BY id"):
            counts = {s: 0 for s in ("proposed", "approved", "executed", "rejected", "failed")}
            for status, n in con.execute("SELECT status, COUNT(*) FROM pending_actions WHERE ticket_id = ? GROUP BY status",
                                         (t["id"],)):
                counts[status] = n
            drafts = con.execute("SELECT COUNT(*) FROM drafts WHERE ticket_id = ?", (t["id"],)).fetchone()[0]
            tickets.append({**{k: t[k] for k in ("id", "type", "requester", "subject", "status", "sku", "size", "qty",
                                                  "invoice_id", "lease_id", "notes", "created_at")},
                            "is_open": t["status"] == "open", "is_resolved": t["status"] == "resolved",
                            "proposals": counts, "awaiting_human_decision": counts["proposed"], "drafts": drafts,
                            "resolution": resolutions.get(t["id"]) if t["status"] == "resolved" else None})
        return {"business_date": con.execute("SELECT date_today FROM desk").fetchone()[0], "tickets": tickets}


def ticket_ids(p: Paths) -> dict[int, str]:
    with closing(_connect(p)) as con:
        return {r["id"]: r["status"] for r in con.execute("SELECT id, status FROM tickets")}


def get_ticket(p: Paths, ticket_id: int) -> dict | None:
    with closing(_connect(p)) as con:
        t = con.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if t is None:
            return None
        ticket = server._ticket_with_links(con, t)
        ticket.update(is_open=t["status"] == "open", is_resolved=t["status"] == "resolved",
                      resolution=_resolutions(p).get(ticket_id) if t["status"] == "resolved" else None)
        withdrawals = _withdrawals(p)
        actions = [_action(r, con, withdrawals)
                   for r in con.execute("SELECT * FROM pending_actions WHERE ticket_id = ? ORDER BY id", (ticket_id,)).fetchall()]
        drafts = [{**dict(r), "warnings": json.loads(r["warnings"]), "sent": False}
                  for r in con.execute("SELECT * FROM drafts WHERE ticket_id = ? ORDER BY id", (ticket_id,))]
        return {"business_date": con.execute("SELECT date_today FROM desk").fetchone()[0], "ticket": ticket,
                "pending_actions": actions, "drafts": drafts, "inventory": _inventory_for(con, t)}


def _inventory_for(con, t) -> dict | None:
    """Problem 8: stored stock and pricing for the ticket's product (read-only), so the dashboard can show the
    inventory facts without computing them. None for tickets without a product (e.g. the rent notice)."""
    if not t["sku"]:
        return None
    rows = con.execute("""SELECT i.name, i.size, i.qty, i.location, p.unit_cost, p.list_price
                          FROM inventory i LEFT JOIN pricing p ON p.sku = i.sku WHERE i.sku = ? ORDER BY i.rowid""",
                       (t["sku"],)).fetchall()
    if not rows:
        return None
    on_hand = next((r["qty"] for r in rows if r["size"] == t["size"]), None)
    return {"sku": t["sku"], "name": rows[0]["name"], "location": rows[0]["location"],
            "unit_cost": rows[0]["unit_cost"], "list_price": rows[0]["list_price"],
            "sizes": [{"size": r["size"], "qty": r["qty"]} for r in rows],
            "requested_size": t["size"], "requested_qty": t["qty"], "on_hand_requested_size": on_hand,
            "computed": {"shortfall": None if on_hand is None or t["qty"] is None else max(t["qty"] - on_hand, 0),
                         "note": "Arithmetic on stored values (requested quantity minus stock on hand). Not a forecast."},
            "source": "inventory and pricing tables (stored values)"}


def list_actions(p: Paths, *, status: str | None = None, ticket_id: int | None = None, run_id: str | None = None) -> list[dict]:
    where, args = [], []
    for col, val in (("status", status), ("ticket_id", ticket_id), ("run_id", run_id)):
        if val is not None:
            where.append(f"{col} = ?")
            args.append(val)
    sql = "SELECT * FROM pending_actions" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
    with closing(_connect(p)) as con:
        withdrawals = _withdrawals(p)
        return [_action(r, con, withdrawals) for r in con.execute(sql, args).fetchall()]


def drafts_for_run(p: Paths, run_id: str) -> list[dict]:
    with closing(_connect(p)) as con:
        return [{k: r[k] for k in ("id", "ticket_id", "recipient", "subject", "created_by", "created_at")} | {"sent": False}
                for r in con.execute("SELECT * FROM drafts WHERE run_id = ? ORDER BY id", (run_id,))]


def cash(p: Paths) -> dict:
    """Stored balances only. Proposals, quotes and estimates are reported separately and never counted as cash."""
    with closing(_connect(p)) as con:
        accounts = [{"name": r["name"], "balance": round(r["balance"], 2), "as_of": r["date"]}
                    for r in con.execute("SELECT name, balance, date FROM cash_accounts ORDER BY name")]
        payments = [dict(r) for r in con.execute("SELECT * FROM payments ORDER BY id")]

        def total(kind, status):
            return round(con.execute("SELECT COALESCE(SUM(amount), 0) FROM pending_actions WHERE kind = ? AND status = ?",
                                     (kind, status)).fetchone()[0], 2)

        checking = next((a for a in accounts if a["name"] == "checking"), None)
        actual = checking["balance"] if checking else None
        approved_payments, approved_purchases = total("payment", "approved"), total("purchase", "approved")
        return {"business_date": con.execute("SELECT date_today FROM desk").fetchone()[0],
                "checking_balance": actual,
                "checking_as_of": checking["as_of"] if checking else None,
                "source": "cash_accounts table (stored balance after executed payments and executed purchases only)",
                "accounts": accounts, "payments_executed": payments,
                "not_counted_as_cash": {
                    "proposed_payments_total": total("payment", "proposed"),
                    "approved_unexecuted_payments_total": approved_payments,
                    "proposed_or_approved_purchase_estimates": round(total("purchase", "proposed") + approved_purchases, 2),
                    "proposed_purchase_estimates": total("purchase", "proposed"),
                    "approved_unexecuted_purchases_total": approved_purchases,
                    "proposed_or_approved_quotes_total": round(total("quote", "proposed") + total("quote", "approved"), 2),
                    "note": "Information only. Proposals and approved-but-unexecuted purchases move no money; purchase figures "
                            "are estimates; quotes add no revenue (R6). The balance changes only when a person executes a "
                            "payment (by approving it) or executes an approved purchase."},
                # Problem 9 F3: computed limits, never stored and never deducted from cash_accounts.
                "available": None if actual is None else {
                    "for_obligations": round(actual - approved_payments, 2),
                    "for_purchases": round(actual - approved_payments - approved_purchases, 2),
                    "note": "Computed, not stored. Invoice and rent payments may use actual cash minus approved unexecuted "
                            "payments; purchases may use that minus approved unexecuted purchases (purchases yield to "
                            "obligations). Commitments are never deducted from the stored balance."}}
