"""The five Campus Customs agents (Problem 5, Stage 5).

Each agent gets: its own prompt file (backend/prompts/<name>.md), the shared AgentReport output,
the shared `delegate` tool, and ONLY its own MCP toolset. The MCP server for each agent is launched
with that agent's identity, so it registers only that agent's tools; the filter below is a second,
independent layer using the same approved matrix (server.ACCESS).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from pydantic_ai import Agent, Tool  # noqa: E402
from pydantic_ai.models import Model  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp_server"))
from server import ACCESS  # noqa: E402

from delegation import delegate  # noqa: E402
from models import AGENT_NAMES, AgentReport, RunDeps  # noqa: E402

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def load_prompt(name: str) -> str:
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"prompt file for {name} is empty")
    return text


def build_team(model: Model | Mapping[str, Model], toolsets: Mapping) -> dict[str, Agent]:
    """Build all five agents. `model` is the single configured gpt-6-luna model; tests may pass a
    mapping of offline test models per agent instead."""
    team = {}
    for name in AGENT_NAMES:
        allowed = frozenset(ACCESS[name])
        toolset = toolsets[name].filtered(lambda ctx, tool_def, allowed=allowed: tool_def.name in allowed)
        team[name] = Agent(
            model[name] if isinstance(model, Mapping) else model,
            name=name,
            instructions=load_prompt(name),
            deps_type=RunDeps,
            output_type=AgentReport,
            toolsets=[toolset],
            tools=[Tool(delegate, takes_ctx=True)],
            retries=2,
        )
    return team
