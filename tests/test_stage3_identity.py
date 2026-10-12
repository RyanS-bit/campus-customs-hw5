"""Stage 3, group C: identity comes only from the trusted server launch (spec section 7.1).

C1 no context -> writes refused      C2 per-agent tool registration      C3 forged context ignored
C4 invalid launch configuration -> server refuses to start             C5 dev connections read-only
(C5 backward compatibility of outputs is in test_stage3_tools.py.)
"""

import asyncio
import os
import sqlite3
import subprocess
import sys
import uuid

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

import maintenance
import ops
import paths
import server
from conftest import REAL_ROOT, counts, privileged
from mcp_helpers import call, text, tool_names

SERVER_PY = str(REAL_ROOT / "mcp_server" / "server.py")
WRITE_ARGS = {
    "propose_payment": dict(kind="invoice", ref_id=501, amount=840, ticket_id=101, rationale="Pay overdue invoice 501"),
    "propose_purchase": dict(vendor_id=1, sku="CC-HOOD-NAVY", size="M", qty=12, ticket_id=103, rationale="Cover the 12-unit shortfall"),
    "propose_quote": dict(ticket_id=103, sku="CC-HOOD-NAVY", size="M", qty=20, unit_price=45, rationale="Bulk discount within policy"),
    "draft_message": dict(ticket_id=101, subject="Tee", body="Thanks for your order request."),
}


# ---- C1 -----------------------------------------------------------------------------

@pytest.mark.parametrize("tool", server.WRITE_TOOLS)
@pytest.mark.parametrize("forged_meta", [None, {"agent": "accounting", "run_id": str(uuid.uuid4()), "mode": "live"}])
def test_c1_development_mode_refuses_every_write(work, tool, forged_meta):
    dev = server.create_server(None, work)
    r = call(dev, tool, WRITE_ARGS[tool], meta=forged_meta)
    assert r.is_error and text(r).startswith("NO_RUN_CONTEXT:")
    assert counts(work) == {"pending_actions": 0, "drafts": 0, "events": 0}


def test_c1_development_mode_lists_all_ten_tools_and_no_approval_tools(work):
    names = tool_names(server.create_server(None, work))
    assert names == set(server.READ_TOOLS + server.WRITE_TOOLS) and len(names) == 10
    assert not any(w in n for n in names for w in ("approve", "reject", "execute", "pay_", "send", "status"))


# ---- C2 -----------------------------------------------------------------------------

@pytest.mark.parametrize("agent", ops.AGENTS)
def test_c2_each_agent_server_registers_exactly_its_tools(work, ident, agent):
    srv = server.create_server(ident(agent), work)
    assert tool_names(srv) == server.ACCESS[agent]
    for tool in set(server.WRITE_TOOLS) - server.ACCESS[agent]:
        r = call(srv, tool, WRITE_ARGS[tool])
        assert r.is_error and "Unknown tool" in text(r)
    assert counts(work) == {"pending_actions": 0, "drafts": 0, "events": 0}


def test_c2_matrix_matches_the_approved_specification():
    assert server.ACCESS == {
        "boss": {"check_inventory", "get_financial_position", "get_open_tickets", "get_ticket", "list_vendors", "list_pending_actions"},
        "inventory": {"check_inventory", "get_ticket", "list_vendors", "list_pending_actions", "propose_purchase"},
        "accounting": {"get_financial_position", "get_ticket", "list_vendors", "list_pending_actions", "propose_payment"},
        "facilities": {"get_financial_position", "get_ticket", "list_pending_actions", "draft_message"},
        "customer_service": {"check_inventory", "get_ticket", "list_pending_actions", "propose_quote", "draft_message"},
    }
    for agent, tools in server.ACCESS.items():   # server matrix and ops permissions agree
        for op in server.WRITE_TOOLS:
            assert (op in tools) == (agent in ops.OPERATION_AGENTS[op])


# ---- C3 -----------------------------------------------------------------------------

def test_c3_forged_meta_cannot_change_attribution(work, ident):
    launch = ident("customer_service")
    forged_run = str(uuid.uuid4())
    srv = server.create_server(launch, work)
    r = call(srv, "propose_quote", WRITE_ARGS["propose_quote"],
             meta={"agent": "accounting", "run_id": forged_run, "mode": "live", "call_id": "c-77"})
    assert not r.is_error
    with privileged(work) as con:
        row = con.execute("SELECT proposed_by, run_id, call_id FROM pending_actions").fetchone()
        ev = con.execute("SELECT actor, run_id, run_mode, call_id FROM audit.events").fetchone()
    assert row == ("customer_service", launch.run_id, "c-77")            # call_id kept for correlation only
    assert ev == ("customer_service", launch.run_id, "simulated", "c-77")


@pytest.mark.parametrize("extra", [{"agent": "accounting"}, {"run_id": "forged"}, {"identity": {"agent": "boss"}},
                                   {"proposed_by": "accounting"}, {"status": "approved"}, {"mode": "live"}])
def test_c3_forged_arguments_are_rejected_and_write_nothing(work, ident, extra):
    srv = server.create_server(ident("customer_service"), work)
    r = call(srv, "propose_quote", {**WRITE_ARGS["propose_quote"], **extra})
    assert r.is_error and "Unexpected keyword argument" in text(r)
    assert counts(work) == {"pending_actions": 0, "drafts": 0, "events": 0}


def test_c3_agent_cannot_escalate_by_choosing_another_agents_ticket(work, ident):
    srv = server.create_server(ident("customer_service"), work)
    r = call(srv, "draft_message", dict(ticket_id=102, subject="Rent", body="Trying to message the landlord."))
    assert r.is_error and text(r).startswith("NOT_PERMITTED:") and "delegate this communication to facilities" in text(r)


# ---- C4 -----------------------------------------------------------------------------

GOOD = {"CC_RUN_ID": str(uuid.uuid4()), "CC_AGENT": "accounting", "CC_RUN_MODE": "simulated"}


def test_c4_identity_from_environment():
    assert server.identity_from_env({}) is None
    who = server.identity_from_env(GOOD)
    assert (who.agent, who.mode, who.run_id) == ("accounting", "simulated", GOOD["CC_RUN_ID"])


@pytest.mark.parametrize("env", [
    {"CC_AGENT": "accounting"},                                           # partial
    {"CC_RUN_ID": GOOD["CC_RUN_ID"], "CC_AGENT": "accounting"},           # partial
    {**GOOD, "CC_AGENT": "ceo"},
    {**GOOD, "CC_AGENT": "Accounting"},
    {**GOOD, "CC_RUN_ID": "run-1"},
    {**GOOD, "CC_RUN_ID": str(uuid.uuid1())},
    {**GOOD, "CC_RUN_MODE": "production"},
])
def test_c4_invalid_launch_configuration_is_refused(env):
    with pytest.raises(server.LaunchConfigError):
        server.identity_from_env(env)


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CC_")}
    env.update(extra)
    return env


@pytest.mark.parametrize("bad", [{"CC_AGENT": "ceo"}, {"CC_RUN_ID": "not-a-uuid"}, {"CC_RUN_MODE": None}])
def test_c4_server_process_refuses_to_start(tmp_path, bad):
    env = _clean_env(CC_RUNTIME_DIR=str(tmp_path / "rt"), **GOOD)
    for k, v in bad.items():
        env.pop(k) if v is None else env.__setitem__(k, v)
    proc = subprocess.run([sys.executable, SERVER_PY], env=env, capture_output=True, text=True, timeout=60,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 2 and "REFUSING TO START" in proc.stderr


# ---- stdio end to end: identity really comes from the launch environment --------------

def _stdio(env):
    return Client(StdioTransport(command=sys.executable, args=[SERVER_PY], env=env))


def test_c4_stdio_agent_launch_end_to_end(sandbox, tmp_path):
    runtime = tmp_path / "rt"
    p = paths.resolve(env={"CC_RUNTIME_DIR": str(runtime)}, root=REAL_ROOT, use_dotenv=False)
    maintenance.reset_working_db(p)                    # copies the real ORIGINAL into the temp runtime folder
    run_id = str(uuid.uuid4())
    # Since remediation M1 an agent-mode server must be TOLD its database by the launcher.
    env = _clean_env(CC_RUNTIME_DIR=str(runtime), CC_RUN_ID=run_id, CC_AGENT="accounting", CC_RUN_MODE="simulated",
                     CC_HW5_ROOT=str(REAL_ROOT), CC_WORKING_DB=str(p.working_db))

    async def go():
        async with _stdio(env) as c:
            names = {t.name for t in await c.list_tools()}
            ok = await c.call_tool("propose_payment", WRITE_ARGS["propose_payment"], raise_on_error=False,
                                   meta={"agent": "boss", "run_id": str(uuid.uuid4())})
            other = await c.call_tool("propose_quote", WRITE_ARGS["propose_quote"], raise_on_error=False)
            return names, ok, other
    names, ok, other = asyncio.run(go())
    assert names == server.ACCESS["accounting"]
    assert not ok.is_error and other.is_error
    con = sqlite3.connect(p.working_db)
    try:
        assert con.execute("SELECT proposed_by, run_id FROM pending_actions").fetchall() == [("accounting", run_id)]
    finally:
        con.close()


def test_c1_stdio_development_launch_cannot_write(tmp_path):
    runtime = tmp_path / "rt"
    p = paths.resolve(env={"CC_RUNTIME_DIR": str(runtime)}, root=REAL_ROOT, use_dotenv=False)
    maintenance.reset_working_db(p)
    env = _clean_env(CC_RUNTIME_DIR=str(runtime))      # like Claude Code's .mcp.json launch: no run identity

    async def go():
        async with _stdio(env) as c:
            return (len(await c.list_tools()),
                    await c.call_tool("propose_payment", WRITE_ARGS["propose_payment"], raise_on_error=False,
                                      meta={"agent": "accounting", "run_id": str(uuid.uuid4())}))
    n, r = asyncio.run(go())
    assert n == 10 and r.is_error and text(r).startswith("NO_RUN_CONTEXT:")
    assert counts(p)["pending_actions"] == 0


# ---- C5: ordinary development connections are read-only ------------------------------

def test_c5_read_connection_cannot_write(work):
    con = server._connect(work.working_db)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            con.execute("UPDATE cash_accounts SET balance = 0")
    finally:
        con.close()


def test_c5_development_mode_reads_leave_the_file_byte_identical(work):
    dev = server.create_server(None, work)
    before = paths.sha256_file(work.working_db)
    for tool, args in [("check_inventory", {}), ("get_financial_position", {}), ("get_open_tickets", {}),
                       ("get_ticket", {"ticket_id": 101}), ("list_vendors", {}), ("list_pending_actions", {})]:
        assert not call(dev, tool, args).is_error
    for tool in server.WRITE_TOOLS:
        call(dev, tool, WRITE_ARGS[tool])
    assert paths.sha256_file(work.working_db) == before
