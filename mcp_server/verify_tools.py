"""Offline verification of the Campus Customs MCP server (no model calls).

Calls the tools through an in-memory MCP client and compares the results with independent SQL
queries. Also checks that neither real database file changes.

Two passes (Problem 9, follow-up 42):
  1. CURRENT  the real working copy, read-only, in whatever state the simulation has reached. The checks
              follow the stored records: an open invoice must appear with correct overdue arithmetic and
              block its vendor (R3); a paid invoice must have exactly one matching payment, be absent from
              the open invoices, and no longer block its vendor.
  2. BASELINE a fresh copy of the read-only original, prepared in a temporary folder by the existing reset
              routine (never the real working copy). The original Problem 3/4 facts are asserted exactly:
              $3,400 cash, invoice 501 open and 3 days overdue, Bulldog Print Co blocked, $160 after all
              obligations, tickets 101-103 open.

Run from hw5/:  .venv/Scripts/python mcp_server/verify_tools.py
"""

import asyncio
import hashlib
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

sys.path.insert(0, str(Path(__file__).resolve().parent))
import maintenance  # noqa: E402
import paths  # noqa: E402
import server  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
ORIGINAL, WORKING = DATA / "campus_customs.db", DATA / "campus_customs_new.db"
ORIGINAL_TOOLS = ("check_inventory", "get_financial_position", "get_open_tickets")
results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sql(db: Path, query: str, *args):
    con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    try:
        return con.execute(query, args).fetchall()
    finally:
        con.close()


async def verify(client: Client, db: Path, tag: str) -> dict:
    """Tool output versus independent SQL on the database the server reads. Valid for any simulation state."""
    q = lambda query, *a: sql(db, query, *a)  # noqa: E731

    # --- check_inventory ---
    inv = (await client.call_tool("check_inventory", {})).data
    db_rows = q("SELECT sku, size, qty FROM inventory")
    tool_rows = {(p["sku"], s["size"], s["qty"]) for p in inv["products"] for s in p["sizes"]}
    check(tool_rows == set(db_rows), f"[{tag}] check_inventory returns all {len(db_rows)} inventory rows exactly")
    prices = {p["sku"]: (p["unit_cost"], p["list_price"]) for p in inv["products"]}
    check(prices == {s: (c, l) for s, c, l in q("SELECT sku, unit_cost, list_price FROM pricing")},
          f"[{tag}] check_inventory prices match pricing table")
    tee = (await client.call_tool("check_inventory", {"sku": "CC-TEE-WHITE"})).data["products"]
    check(len(tee) == 1 and {s["size"]: s["qty"] for s in tee[0]["sizes"]}["S"] == q(
        "SELECT qty FROM inventory WHERE sku='CC-TEE-WHITE' AND size='S'")[0][0],
        f"[{tag}] check_inventory(sku) filters to one product; tee size S matches DB (ticket 101)")
    bad = await client.call_tool("check_inventory", {"sku": "NOPE"}, raise_on_error=False)
    check(bad.is_error, f"[{tag}] check_inventory rejects an unknown SKU with an error")

    # --- get_financial_position ---
    fin = (await client.call_tool("get_financial_position", {})).data
    cash = q("SELECT SUM(balance) FROM cash_accounts")[0][0]
    open_inv = q("SELECT id, amount FROM invoices WHERE status='open'")
    rent = q("SELECT SUM(monthly_rent) FROM leases")[0][0]
    check(fin["business_date"] == q("SELECT date_today FROM desk")[0][0], f"[{tag}] business date comes from desk")
    check(fin["computed"]["total_cash"] == round(cash, 2), f"[{tag}] total cash matches DB ({cash})")
    check({(i["id"], i["amount"]) for i in fin["open_invoices"]} == set(open_inv), f"[{tag}] open invoices match DB")
    check(fin["computed"]["cash_after_all_obligations"] == round(cash - sum(a for _, a in open_inv) - rent, 2),
          f"[{tag}] cash after obligations = cash - open invoices - rent")
    overdue = {r[0]: int(r[1]) for r in q("SELECT id, julianday((SELECT date_today FROM desk)) - julianday(due_date) "
                                          "FROM invoices WHERE status='open'")}
    check(all(i["days_overdue"] == max(overdue[i["id"]], 0) for i in fin["open_invoices"]),
          f"[{tag}] days overdue for every open invoice = SQLite date math {overdue}")
    check(fin["vendors_with_open_invoices"] == [r[0] for r in q(
        "SELECT DISTINCT v.name FROM invoices i JOIN vendors v ON v.id=i.vendor_id WHERE i.status='open' ORDER BY v.name")],
        f"[{tag}] vendors with open invoices match DB (rule R3)")

    # --- invoice 501 (ticket 101): stored facts, and consistency with its status ---
    (amount, due_date, status, vendor), = q("SELECT i.amount, i.due_date, i.status, v.name FROM invoices i "
                                            "JOIN vendors v ON v.id = i.vendor_id WHERE i.id = 501")
    check(amount == 840.0 and due_date == "2026-08-28" and vendor == "Bulldog Print Co",
          f"[{tag}] invoice 501 stored facts: $840.00, due 2026-08-28, Bulldog Print Co")
    tool_501 = [i for i in fin["open_invoices"] if i["id"] == 501]
    pays_501 = q("SELECT amount FROM payments WHERE kind = 'invoice' AND ref_id = 501")
    if status == "open":
        check(len(tool_501) == 1 and tool_501[0]["days_overdue"] == overdue[501] and not pays_501
              and vendor in fin["vendors_with_open_invoices"],
              f"[{tag}] invoice 501 is OPEN: listed, {overdue[501]} days overdue, no payment, vendor blocked (R3)")
    elif status == "paid":
        check(not tool_501 and pays_501 == [(840.0,)] and vendor not in fin["vendors_with_open_invoices"],
              f"[{tag}] invoice 501 is PAID: exactly one $840.00 payment, not listed as open, vendor no longer blocked (R3)")
    else:
        check(False, f"[{tag}] invoice 501 has an unexpected status {status!r}")

    # --- get_open_tickets (only open ones) and get_ticket (any status: links never change) ---
    tix = (await client.call_tool("get_open_tickets", {})).data
    check([t["id"] for t in tix["tickets"]] == [r[0] for r in q("SELECT id FROM tickets WHERE status='open' ORDER BY id")],
          f"[{tag}] get_open_tickets returns every open ticket and only those")
    t101, t102, t103 = [(await client.call_tool("get_ticket", {"ticket_id": i})).data["ticket"] for i in (101, 102, 103)]
    check(t101["linked_invoice"]["id"] == 501 and t101["linked_invoice"]["vendor_name"] == vendor,
          f"[{tag}] ticket 101 carries invoice 501 and its vendor")
    check(t102["linked_lease"]["id"] == 1, f"[{tag}] ticket 102 carries lease 1")
    check(t103["linked_invoice"] is None and t103["linked_lease"] is None, f"[{tag}] ticket 103 has no linked invoice or lease")
    check([t101["status"], t102["status"], t103["status"]] == [r[0] for r in q("SELECT status FROM tickets ORDER BY id")],
          f"[{tag}] get_ticket reports each ticket's stored status")
    return {"fin": fin, "tickets": [t["id"] for t in tix["tickets"]], "cash": cash}


async def main() -> None:
    before = {p: sha(p) for p in (ORIGINAL, WORKING)}
    check(server.DB_PATH == WORKING, f"server points at working copy ({server.DB_PATH.name})")

    # ---------------------------------------------------------------- pass 1: the real working copy, as it is now
    async with Client(server.mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        # Problem 5 (D16) expanded the server from three to ten tools; these two checks were updated
        # from "exactly three tools" / "every tool read-only" accordingly (logged in AI_prompts.md).
        check(set(tools) == set(server.READ_TOOLS + server.WRITE_TOOLS) and set(ORIGINAL_TOOLS) <= set(tools),
              f"the original three tools plus the approved Problem 5 tools are registered: {sorted(tools)}")
        check(all(tools[n].annotations and tools[n].annotations.read_only_hint for n in server.READ_TOOLS)
              and not any(tools[n].annotations.read_only_hint for n in server.WRITE_TOOLS),
              "the original three (and all read tools) are annotated read-only; proposal tools are not")
        current = await verify(client, WORKING, "current")

    # ---------------------------------------------------------------- pass 2: a fresh copy of the original
    with tempfile.TemporaryDirectory(prefix="cc_verify_") as td:
        root = Path(td) / "hw5"
        (root / "data").mkdir(parents=True)
        (root / "output").mkdir()
        shutil.copyfile(ORIGINAL, root / "data" / ORIGINAL.name)
        base_paths = paths.resolve(env={}, root=root, use_dotenv=False)
        maintenance.reset_working_db(base_paths)              # the existing R8 reset, applied to the temporary copy
        check(sha(root / "data" / ORIGINAL.name) == paths.ORIGINAL_SHA256,
              "baseline copy of the original matches the recorded SHA-256")
        async with Client(server.create_server(None, base_paths)) as client:
            base = await verify(client, base_paths.working_db, "baseline")
        fin = base["fin"]
        check(base["cash"] == 3400.0 and base["tickets"] == [101, 102, 103],
              "[baseline] original state: $3,400.00 checking; tickets 101, 102, 103 open")
        check([(i["id"], i["days_overdue"]) for i in fin["open_invoices"]] == [(501, 3)]
              and fin["vendors_with_open_invoices"] == ["Bulldog Print Co"],
              "[baseline] invoice 501 open and 3 days overdue on 2026-08-31; Bulldog Print Co blocked (R3)")
        check(fin["computed"]["cash_after_all_obligations"] == 160.0,
              "[baseline] cash after all obligations = 3,400 - 840 - 2,400 = $160.00 (hypothetical, as in Problem 3)")

    # --- real subprocess over stdio (how an MCP host such as Claude Code connects) ---
    stdio = StdioTransport(command=sys.executable, args=[str(Path(server.__file__).resolve())])
    async with Client(stdio) as client:
        names = sorted(t.name for t in await client.list_tools())
        fin = (await client.call_tool("get_financial_position", {})).data
        check(names == sorted(server.READ_TOOLS + server.WRITE_TOOLS)
              and fin["computed"]["total_cash"] == round(current["cash"], 2),
              "server starts as a stdio subprocess and answers tool calls")

    # --- read-only guarantee ---
    con = server._connect()
    try:
        con.execute("UPDATE cash_accounts SET balance = balance")
        check(False, "server connection refuses writes")
    except sqlite3.OperationalError as e:
        check("readonly" in str(e).lower(), f"server connection refuses writes ({e})")
    finally:
        con.close()

    after = {p: sha(p) for p in (ORIGINAL, WORKING)}
    check(before[ORIGINAL] == after[ORIGINAL], f"original DB unchanged (sha256 {after[ORIGINAL][:12]}...)")
    check(before[WORKING] == after[WORKING], f"working DB unchanged by read-only tools (sha256 {after[WORKING][:12]}...)")

    failed = sum(not ok for ok, _ in results)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":          # importable by tests/test_problem9_verify_tools.py (follow-up 42)
    asyncio.run(main())
