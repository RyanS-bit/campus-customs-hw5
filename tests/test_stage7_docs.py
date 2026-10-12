"""Stage 7: the documentation agrees with the implementation (checked by machine, not by assertion)."""

import asyncio
import itertools
import re

from fastmcp import Client

import agents
import server
from agent_script import AGENTS
from conftest import REAL_ROOT

DOCS = REAL_ROOT / "output"
LABELS = {"Boss": "boss", "Inventory": "inventory", "Accounting": "accounting", "Facilities": "facilities",
          "Customer Service": "customer_service"}


def test_d1_spec_access_matrix_equals_the_servers_matrix():
    text = (DOCS / "p5_mcp_tools_spec.md").read_text(encoding="utf-8")
    section = text[text.index("## 4. Agent access matrix"):text.index("## 5.")]
    lines = [l for l in section.splitlines() if l.startswith("|")]
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    cols = [LABELS[h] for h in header[1:]]
    documented = {a: set() for a in cols}
    for row in lines[2:]:
        cells = [c.strip() for c in row.strip("|").split("|")]
        tool = cells[0].strip("`").split(" ")[0]
        if tool not in server.READ_TOOLS + server.WRITE_TOOLS:
            continue                                          # e.g. the agent-side `delegate` row
        for agent, cell in zip(cols, cells[1:]):
            if cell == "✓":
                documented[agent].add(tool)
    assert documented == server.ACCESS


def test_d2_tool_descriptions_name_the_authorised_agents():
    async def go():
        async with Client(server.create_server(None)) as c:
            return {t.name: t.description for t in await c.list_tools()}
    desc = asyncio.run(go())
    assert "Accounting only" in desc["propose_payment"]
    assert "Inventory only" in desc["propose_purchase"]
    assert "Customer Service only" in desc["propose_quote"]
    assert "Customer Service" in desc["draft_message"] and "Facilities" in desc["draft_message"]
    for name in server.WRITE_TOOLS:
        assert re.search(r"PROPOSE|UNSENT", desc[name]), name               # never "execute"/"send"
        assert "never" in desc[name].lower() or "unsent" in desc[name].lower()


def test_d3_readmes_and_harness_mention_every_tool_and_backend_module():
    mcp_readme = (REAL_ROOT / "mcp_server" / "README.md").read_text(encoding="utf-8")
    for tool in server.READ_TOOLS + server.WRITE_TOOLS:
        assert f"`{tool}" in mcp_readme, tool
    readme = (REAL_ROOT / "README.md").read_text(encoding="utf-8")
    for f in ("backend/config.py", "backend/models.py", "backend/agents.py", "backend/delegation.py",
              "backend/runner.py", "backend/prompts/*.md", "output/audit_trail.json"):
        assert f"`{f}`" in readme, f
    harness = (DOCS / "harness.md").read_text(encoding="utf-8")
    assert "10 tools" in harness and "5. Agent team" in harness


def test_d4_all_twenty_directed_delegation_paths_are_tested():
    pairs = list(itertools.permutations(AGENTS, 2))
    assert len(pairs) == 20
    src = (REAL_ROOT / "tests" / "test_stage5_agents.py").read_text(encoding="utf-8")
    assert "itertools.permutations(AGENTS, 2)" in src and "test_a6_every_agent_can_delegate_to_every_other_agent" in src


def test_d5_every_agent_has_a_prompt_file_and_the_model_is_documented_once():
    for a in AGENTS:
        assert (agents.PROMPTS_DIR / f"{a}.md").is_file()
    config_src = (REAL_ROOT / "backend" / "config.py").read_text(encoding="utf-8")
    assert config_src.count('REQUIRED_MODEL = "gpt-6-luna"') == 1
    # One configuration point: no other module DEFINES the model (a quoted model-name value) or builds a
    # model client. Prose mentions in docstrings and error messages are allowed.
    for f in (REAL_ROOT / "backend").glob("*.py"):
        if f.name == "config.py":
            continue
        src = f.read_text(encoding="utf-8")
        assert not re.search(r"""["']@?[\w.-]*/?gpt-6-luna["']""", src), f.name
        assert not re.search(r"OpenAIChatModel\(|OpenAIProvider\(|AsyncOpenAI\(", src), f.name


def test_d6_ticket_map_does_not_assume_bulldog_can_supply_hoodies():
    text = (DOCS / "p5_ticket_workflow_map.md").read_text(encoding="utf-8")
    assert "hoodie M reprint possible" not in text
    assert "not** a sufficient condition" in text and "UNKNOWN" in text
