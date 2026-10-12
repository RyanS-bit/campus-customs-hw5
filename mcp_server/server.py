"""Campus Customs MCP server (Problem 3; expanded in Problem 5, Stage 3).

Ten tools over the WORKING database (paths.resolve(): data/campus_customs_new.db by default).
The original data/campus_customs.db is never opened by this server.

Who the server acts for is fixed when the PROCESS IS LAUNCHED (output/p5_mcp_tools_spec.md 7.1):
  * no CC_RUN_ID / CC_AGENT / CC_RUN_MODE  -> development mode (e.g. Claude Code via .mcp.json):
    all ten tools are listed, read tools work, write tools always refuse with NO_RUN_CONTEXT;
  * all three set and valid                -> agent mode: ONLY that agent's tools are registered,
    and writes are attributed to that agent and run. The database is the one the launcher selected
    (CC_HW5_ROOT / CC_RUNTIME_DIR / CC_WORKING_DB, never hw5/.env or a default);
  * anything else                          -> the server refuses to start.
Nothing a caller sends (arguments, _meta, agent names, run IDs) can change this.

Run (stdio transport):  python mcp_server/server.py
"""

import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ops  # noqa: E402
import paths  # noqa: E402

PATHS = paths.resolve()
DB_PATH = PATHS.working_db

READ_ONLY = {"read_only_hint": True, "destructive_hint": False, "idempotent_hint": True, "open_world_hint": False}
PROPOSES = {"read_only_hint": False, "destructive_hint": False, "idempotent_hint": False, "open_world_hint": False}

READ_TOOLS = ("check_inventory", "get_financial_position", "get_open_tickets", "get_ticket", "list_vendors",
              "list_pending_actions")
WRITE_TOOLS = ("propose_payment", "propose_purchase", "propose_quote", "draft_message")

# Agent access matrix (spec section 4). Approve / reject / execute are not tools for anyone.
ACCESS = {
    "boss": {"check_inventory", "get_financial_position", "get_open_tickets", "get_ticket", "list_vendors",
             "list_pending_actions"},
    "inventory": {"check_inventory", "get_ticket", "list_vendors", "list_pending_actions", "propose_purchase"},
    "accounting": {"get_financial_position", "get_ticket", "list_vendors", "list_pending_actions", "propose_payment"},
    "facilities": {"get_financial_position", "get_ticket", "list_pending_actions", "draft_message"},
    "customer_service": {"check_inventory", "get_ticket", "list_pending_actions", "propose_quote", "draft_message"},
}

LAUNCH_VARS = ("CC_RUN_ID", "CC_AGENT", "CC_RUN_MODE")


class LaunchConfigError(RuntimeError):
    """The agent launch configuration is present but incomplete or invalid."""


def identity_from_env(env=None) -> ops.RunIdentity | None:
    """Trusted identity from the process environment set by whoever LAUNCHED the server."""
    env = os.environ if env is None else env
    present = {k: env[k] for k in LAUNCH_VARS if env.get(k)}
    if not present:
        return None
    if len(present) != len(LAUNCH_VARS):
        raise LaunchConfigError(f"Incomplete agent launch configuration: set all of {LAUNCH_VARS}, got {sorted(present)}")
    try:
        return ops.RunIdentity(present["CC_RUN_ID"], present["CC_AGENT"], present["CC_RUN_MODE"])
    except ValueError as exc:
        raise LaunchConfigError(f"Invalid agent launch configuration: {exc}") from None


def _connect(db_path: Path = None) -> sqlite3.Connection:
    """Open the working database in SQLite read-only mode (writes are impossible)."""
    db_path = DB_PATH if db_path is None else db_path
    if not db_path.exists():
        raise ToolError(f"Working database not found at {db_path}. Reset it from data/campus_customs.db.")
    con = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _business_date(con: sqlite3.Connection) -> date:
    return date.fromisoformat(con.execute("SELECT date_today FROM desk").fetchone()["date_today"])


def _money(x: float) -> float:
    return round(x, 2)


def _require_extension(con: sqlite3.Connection) -> None:
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not {"pending_actions", "drafts"} <= tables:
        raise ToolError("DB_UNAVAILABLE: The working database has not been prepared for Problem 5 "
                        "(pending_actions/drafts missing). A human must run scripts/reset_db.py --confirm.")


def _ticket_with_links(con: sqlite3.Connection, t: sqlite3.Row) -> dict:
    ticket = dict(t)
    ticket["linked_invoice"] = None
    ticket["linked_lease"] = None
    if t["invoice_id"] is not None:
        inv = con.execute("""
            SELECT inv.id, inv.amount, inv.due_date, inv.status, inv.description,
                   v.id AS vendor_id, v.name AS vendor_name, v.specialty, v.lead_days
            FROM invoices inv JOIN vendors v ON v.id = inv.vendor_id WHERE inv.id = ?
        """, (t["invoice_id"],)).fetchone()
        ticket["linked_invoice"] = dict(inv) if inv else None
    if t["lease_id"] is not None:
        lease = con.execute("SELECT * FROM leases WHERE id = ?", (t["lease_id"],)).fetchone()
        ticket["linked_lease"] = dict(lease) if lease else None
    return ticket


def _action_dict(r: sqlite3.Row) -> dict:
    d = {k: r[k] for k in ("id", "kind", "status", "ticket_id", "invoice_id", "lease_id", "vendor_id", "sku", "size",
                           "qty", "unit_price", "amount", "amount_basis", "account", "rationale", "proposed_by",
                           "run_id", "business_date", "created_at")}
    d["fundable_at_proposal"] = None if r["fundable_at_proposal"] is None else bool(r["fundable_at_proposal"])
    d["vendor_blocked"] = bool(r["vendor_blocked"])
    for k in ("blocking_invoice_ids", "alternative_to", "warnings"):
        d[k] = json.loads(r[k])
    return d


def _call_id(ctx: Context | None) -> str | None:
    """Correlation only, never authorization: an optional call_id from the request _meta."""
    try:
        meta = ctx.request_context.meta          # FastMCP 4.1.0: a plain dict
        value = meta.get("call_id") if isinstance(meta, dict) else getattr(meta, "call_id", None)
    except Exception:
        return None
    return value if isinstance(value, str) and 0 < len(value) <= 100 else None


LAUNCH_PATH_VARS = ("CC_HW5_ROOT", "CC_WORKING_DB")          # plus CC_RUNTIME_DIR (may be empty)


def launch_paths_from_env(env=None) -> paths.Paths:
    """Agent mode only (remediation M1): the database is the one the TRUSTED LAUNCHER selected.
    Paths are resolved from CC_HW5_ROOT and CC_RUNTIME_DIR alone, never from hw5/.env or defaults, and
    must reproduce CC_WORKING_DB exactly; otherwise the server refuses to start."""
    env = os.environ if env is None else env
    missing = [k for k in LAUNCH_PATH_VARS if not env.get(k)]
    if missing:
        raise LaunchConfigError(f"Agent mode requires the launcher to set {missing}; refusing to guess a database.")
    runtime = env.get("CC_RUNTIME_DIR", "")
    try:
        p = paths.resolve(env={"CC_RUNTIME_DIR": runtime} if runtime else {}, root=Path(env["CC_HW5_ROOT"]),
                          use_dotenv=False)
    except paths.PathConfigError as exc:
        raise LaunchConfigError(f"Invalid launch paths: {exc}") from None
    if os.path.normcase(str(p.working_db)) != os.path.normcase(str(Path(env["CC_WORKING_DB"]).resolve())):
        raise LaunchConfigError(f"Launch paths resolve to {p.working_db}, not the expected {env['CC_WORKING_DB']}.")
    return p


def create_server(identity: ops.RunIdentity | None, p: paths.Paths | None = None) -> FastMCP:
    """Build a server for one trusted launch identity (None = development mode).
    An agent-mode server must be given its paths explicitly (remediation M1): no silent default."""
    if p is None:
        if identity is not None:
            raise ValueError("An agent-mode server needs explicit paths from the trusted launcher.")
        p = PATHS
    allowed = set(READ_TOOLS + WRITE_TOOLS) if identity is None else ACCESS[identity.agent]
    mode_note = ("Development mode: read tools only; write tools refuse (no trusted run identity)."
                 if identity is None else f"Agent mode for {identity.agent}: only this agent's tools are available.")
    mcp = FastMCP(
        "campus-customs",
        instructions=(
            "Access to the Campus Customs shop database. Agents may read, propose and draft; nothing here "
            "approves, pays, orders or sends. "
            "The shop's 'today' is desk.date_today, not the real calendar date. "
            "Values labelled 'computed' are arithmetic on database values, not stored facts. " + mode_note
        ),
    )
    connect = lambda: _connect(p.working_db)  # noqa: E731

    def register(fn, annotations):
        if fn.__name__ in allowed:
            mcp.tool(fn, annotations=annotations)

    # ------------------------------------------------------------ original Problem 3 tools (unchanged)

    def check_inventory(sku: str | None = None) -> dict:
        """Stock on hand per size, with location, unit cost and list price.

        Pass a SKU (e.g. "CC-TEE-WHITE") for one product, or omit it for all products.
        Reads: inventory, pricing.
        """
        with closing(connect()) as con:
            query = """
                SELECT i.sku, i.name, i.size, i.qty, i.location, p.unit_cost, p.list_price
                FROM inventory i LEFT JOIN pricing p ON p.sku = i.sku
                {where} ORDER BY i.sku, i.rowid
            """
            rows = con.execute(query.format(where="WHERE i.sku = ?" if sku else ""), (sku,) if sku else ()).fetchall()
            if sku and not rows:
                known = [r["sku"] for r in con.execute("SELECT DISTINCT sku FROM inventory ORDER BY sku")]
                raise ToolError(f"Unknown SKU {sku!r}. Known SKUs: {', '.join(known)}")

            products: dict[str, dict] = {}
            for r in rows:
                prod = products.setdefault(r["sku"], {
                    "sku": r["sku"], "name": r["name"], "location": r["location"],
                    "unit_cost": r["unit_cost"], "list_price": r["list_price"],
                    "sizes": [], "total_qty": 0,
                })
                prod["sizes"].append({"size": r["size"], "qty": r["qty"]})
                prod["total_qty"] += r["qty"]
            return {"business_date": _business_date(con).isoformat(), "products": list(products.values())}

    def get_financial_position() -> dict:
        """Cash on hand and what the shop owes: open vendor invoices and upcoming lease rent.

        Overdue / days-until figures are measured from desk.date_today (the shop's date).
        Also lists vendors with an open invoice, who will not ship new products until paid.
        Reads: desk, cash_accounts, invoices, vendors, leases, payments.
        """
        with closing(connect()) as con:
            today = _business_date(con)

            accounts = [
                {"name": r["name"], "balance": _money(r["balance"]), "as_of": r["date"]}
                for r in con.execute("SELECT name, balance, date FROM cash_accounts ORDER BY name")
            ]
            invoices = []
            for r in con.execute("""
                SELECT inv.id, inv.vendor_id, v.name AS vendor_name, v.lead_days, inv.amount,
                       inv.due_date, inv.status, inv.description
                FROM invoices inv JOIN vendors v ON v.id = inv.vendor_id
                WHERE inv.status = 'open' ORDER BY inv.due_date
            """):
                days_past_due = (today - date.fromisoformat(r["due_date"])).days
                invoices.append({**dict(r), "amount": _money(r["amount"]),
                                 "days_overdue": max(days_past_due, 0), "overdue": days_past_due > 0})
            leases = []
            for r in con.execute("SELECT id, space_name, landlord, monthly_rent, next_due, notes FROM leases ORDER BY next_due"):
                leases.append({**dict(r), "monthly_rent": _money(r["monthly_rent"]),
                               "days_until_due": (date.fromisoformat(r["next_due"]) - today).days})
            paid = con.execute("SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM payments").fetchone()

        total_cash = _money(sum(a["balance"] for a in accounts))
        total_obligations = _money(sum(i["amount"] for i in invoices) + sum(l["monthly_rent"] for l in leases))
        return {
            "business_date": today.isoformat(),
            "cash_accounts": accounts,
            "open_invoices": invoices,
            "leases": leases,
            "vendors_with_open_invoices": sorted({i["vendor_name"] for i in invoices}),
            "payments_recorded": {"count": paid["n"], "total": _money(paid["total"])},
            "computed": {
                "total_cash": total_cash,
                "total_open_obligations": total_obligations,
                "cash_after_all_obligations": _money(total_cash - total_obligations),
                "note": (
                    "Hypothetical arithmetic, not stored facts. Obligations = all open invoices + next rent on each lease. "
                    "cash_after_all_obligations is what the balance WOULD be if all of them were paid; it is not the "
                    "current balance (see cash_accounts) and not a cash-flow forecast."
                ),
            },
        }

    def get_open_tickets() -> dict:
        """All open tickets, each with the invoice (and its vendor) or lease it links to.

        For product availability on a ticket's sku/size, use check_inventory.
        Reads: tickets, invoices, vendors, leases.
        """
        with closing(connect()) as con:
            tickets = [_ticket_with_links(con, t)
                       for t in con.execute("SELECT * FROM tickets WHERE status = 'open' ORDER BY id")]
            return {"business_date": _business_date(con).isoformat(), "count": len(tickets), "tickets": tickets}

    # ------------------------------------------------------------ Problem 5 read tools

    def get_ticket(ticket_id: int) -> dict:
        """One ticket (any status) with its linked invoice/vendor or lease, plus the proposals and
        unsent drafts already created for it, so work is not duplicated across delegations.
        Reads: tickets, invoices, vendors, leases, inventory, pending_actions, drafts.
        """
        with closing(connect()) as con:
            _require_extension(con)
            t = con.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            if t is None:
                known = ", ".join(str(r[0]) for r in con.execute("SELECT id FROM tickets ORDER BY id"))
                raise ToolError(f"NOT_FOUND: Unknown ticket {ticket_id}. Known tickets: {known}")
            ticket = _ticket_with_links(con, t)
            unknowns = []
            inv = ticket["linked_invoice"]
            if inv and inv["status"] == "open" and t["sku"]:
                stock = con.execute("SELECT qty FROM inventory WHERE sku = ? AND size = ?", (t["sku"], t["size"])).fetchone()
                if stock is not None and stock["qty"] == 0:
                    unknowns.append(f"Whether the goods billed on invoice #{inv['id']} were delivered is not recorded.")
            actions = [{k: r[k] for k in ("id", "kind", "status", "amount", "amount_basis", "proposed_by", "created_at")}
                       for r in con.execute("SELECT * FROM pending_actions WHERE ticket_id = ? ORDER BY id", (ticket_id,))]
            drafts = [{k: r[k] for k in ("id", "recipient", "recipient_source", "subject", "status", "created_by", "created_at")}
                      for r in con.execute("SELECT * FROM drafts WHERE ticket_id = ? ORDER BY id", (ticket_id,))]
            return {"business_date": _business_date(con).isoformat(), "ticket": ticket,
                    "pending_actions": actions, "drafts": drafts, "human_judgment": [], "unknowns": unknowns}

    def list_vendors() -> dict:
        """Every vendor with specialty, lead time, open invoices and whether rule R3 blocks new shipments.
        Arrival dates are hypothetical (business date + lead_days) and withheld while a vendor is blocked.
        Reads: desk, vendors, invoices.
        """
        with closing(connect()) as con:
            today = _business_date(con)
            vendors = []
            for v in con.execute("SELECT * FROM vendors ORDER BY id"):
                open_inv = [{"id": r["id"], "amount": _money(r["amount"]), "due_date": r["due_date"],
                             "days_overdue": max((today - date.fromisoformat(r["due_date"])).days, 0)}
                            for r in con.execute("SELECT * FROM invoices WHERE vendor_id = ? AND status = 'open' ORDER BY id",
                                                 (v["id"],))]
                blocked = bool(open_inv)
                ids = [i["id"] for i in open_inv]
                vendors.append({
                    "id": v["id"], "name": v["name"], "specialty": v["specialty"], "lead_days": v["lead_days"],
                    "open_invoices": open_inv, "blocked": blocked,
                    "blocked_reason": (f"R3: open unpaid invoice(s) {', '.join(map(str, ids))}; no new shipments until paid"
                                       if blocked else None),
                    "computed": {
                        "earliest_arrival_if_ordered_today": None if blocked else (today + timedelta(days=v["lead_days"])).isoformat(),
                        "note": ("Not estimable while blocked. If unblocked, arrival >= order date + lead_days. No order exists."
                                 if blocked else "Hypothetical: business date + lead_days. No order exists."),
                    },
                })
            return {"business_date": today.isoformat(), "vendors": vendors,
                    "human_judgment": ["Specialty is stored text; whether a vendor can supply a product is a human judgment."],
                    "unknowns": []}

    def list_pending_actions(ticket_id: int | None = None,
                             kind: Literal["payment", "purchase", "quote"] | None = None) -> dict:
        """Agent-prepared proposals with their status, and commitments compared with stored cash.
        Proposals reserve no cash and move no money. Optional filters: ticket_id, kind.
        Reads: pending_actions, cash_accounts, desk.
        """
        with closing(connect()) as con:
            _require_extension(con)
            if ticket_id is not None and con.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone() is None:
                raise ToolError(f"NOT_FOUND: Unknown ticket {ticket_id}")
            where, args = [], []
            if ticket_id is not None:
                where.append("ticket_id = ?"); args.append(ticket_id)  # noqa: E702
            if kind is not None:
                where.append("kind = ?"); args.append(kind)  # noqa: E702
            sql = "SELECT * FROM pending_actions" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
            actions = [_action_dict(r) for r in con.execute(sql, args)]

            def total(k, statuses):
                q = f"SELECT COALESCE(SUM(amount), 0) FROM pending_actions WHERE kind = ? AND status IN ({','.join('?' * len(statuses))})"
                return _money(con.execute(q, (k, *statuses)).fetchone()[0])

            accounts = [{"name": r["name"], "balance": _money(r["balance"])}
                        for r in con.execute("SELECT name, balance FROM cash_accounts ORDER BY name")]
            stored = _money(sum(a["balance"] for a in accounts))
            proposed, approved = total("payment", ("proposed",)), total("payment", ("approved",))
            return {
                "business_date": _business_date(con).isoformat(),
                "actions": actions,
                "cash": {"stored_balance": stored, "accounts": accounts},
                "computed": {
                    "proposed_payments_total": proposed,
                    "approved_commitments_total": approved,
                    "cash_if_all_proposed_payments_executed": _money(stored - proposed - approved),
                    "estimated_purchases_total": total("purchase", ("proposed", "approved")),
                    "note": ("Hypothetical. Proposals reserve no cash and move no money. Purchase totals are estimates, "
                             "not quotes, and are not payments (payment would follow a real invoice)."),
                },
            }

    # ------------------------------------------------------------ Problem 5 write tools (proposals and drafts only)

    def _write(op, ctx, **kwargs) -> dict:
        if identity is None:
            raise ToolError("NO_RUN_CONTEXT: This server was launched without a trusted run identity "
                            "(development mode). Write tools are unavailable.")
        try:
            return op(p, identity, call_id=_call_id(ctx), **kwargs)
        except ops.OpsError as exc:
            raise ToolError(str(exc)) from None

    def propose_payment(kind: Literal["invoice", "lease"], ref_id: int, amount: float, rationale: str,
                        ticket_id: int | None = None, account: str = "checking", ctx: Context = None) -> dict:
        """PROPOSE (never execute) full payment of an open invoice or the lease rent. A human approves later.
        Proposals above the stored balance are recorded but labelled NOT EXECUTABLE (R1). Accounting only.
        Writes: pending_actions (+ supplementary audit.db event).
        """
        return _write(ops.propose_payment, ctx, kind=kind, ref_id=ref_id, amount=amount, rationale=rationale,
                      ticket_id=ticket_id, account=account)

    def propose_purchase(vendor_id: int, sku: str, size: str, qty: int, rationale: str,
                         ticket_id: int | None = None, ctx: Context = None) -> dict:
        """PROPOSE (never place) a restock purchase. Allowed for a vendor blocked by an unpaid invoice, but
        flagged: nothing can ship until it is paid (R3). Cost is an estimate, not a quote. Inventory only.
        Writes: pending_actions (+ supplementary audit.db event).
        """
        return _write(ops.propose_purchase, ctx, vendor_id=vendor_id, sku=sku, size=size, qty=qty,
                      rationale=rationale, ticket_id=ticket_id)

    def propose_quote(ticket_id: int, sku: str, size: str, qty: int, unit_price: float, rationale: str,
                      ctx: Context = None) -> dict:
        """PROPOSE a customer price quote that exactly matches the ticket's sku/size/qty, between stored unit
        cost and list price (shop policy). Never sent; customer acceptance is never recorded. Customer Service only.
        Writes: pending_actions (+ supplementary audit.db event).
        """
        return _write(ops.propose_quote, ctx, ticket_id=ticket_id, sku=sku, size=size, qty=qty,
                      unit_price=unit_price, rationale=rationale)

    def draft_message(ticket_id: int, subject: str, body: str, related_action_id: int | None = None,
                      ctx: Context = None) -> dict:
        """Store an UNSENT email draft (R7: never sent). The recipient comes from shop records: the ticket's
        requester (Customer Service) or the lease landlord (Facilities). Commitment wording is flagged.
        Writes: drafts (+ supplementary audit.db event).
        """
        return _write(ops.draft_message, ctx, ticket_id=ticket_id, subject=subject, body=body,
                      related_action_id=related_action_id)

    for fn in (check_inventory, get_financial_position, get_open_tickets, get_ticket, list_vendors, list_pending_actions):
        register(fn, READ_ONLY)
    for fn in (propose_payment, propose_purchase, propose_quote, draft_message):
        register(fn, PROPOSES)
    return mcp


# Importers (verify_tools.py, tests) get a development-mode server.
mcp = create_server(None)


if __name__ == "__main__":
    try:
        launch_identity = identity_from_env()
        launch_paths = launch_paths_from_env() if launch_identity is not None else PATHS
    except LaunchConfigError as exc:
        print(f"REFUSING TO START: {exc}", file=sys.stderr)
        sys.exit(2)
    if ops._fault_hook is not None:
        print("REFUSING TO START: test fault injection is set in this process.", file=sys.stderr)
        sys.exit(2)
    create_server(launch_identity, launch_paths).run()
