"""Shared data types for the agent team (Problem 5, Stage 5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai.usage import UsageLimits

if TYPE_CHECKING:
    from audit_trail import RunRecorder

AgentName = Literal["boss", "inventory", "accounting", "facilities", "customer_service"]
AGENT_NAMES: tuple[str, ...] = ("boss", "inventory", "accounting", "facilities", "customer_service")
RunMode = Literal["simulated", "live"]


class AgentReport(BaseModel):
    """Every agent's final answer. Keeps facts, estimates, proposals and unknowns apart."""
    summary: str = Field(description="What you found and did, in plain language.")
    facts: list[str] = Field(default_factory=list, description="Facts read in this run, each naming the tool it came from.")
    proposals_created: list[int] = Field(default_factory=list, description="pending_actions IDs returned by your tools.")
    drafts_created: list[int] = Field(default_factory=list, description="Draft IDs returned by your tools (unsent).")
    estimates: list[str] = Field(default_factory=list, description="Estimates, labelled as estimates (not quotes).")
    needs_human: list[str] = Field(default_factory=list, description="Decisions a person must make.")
    unknowns: list[str] = Field(default_factory=list, description="What the records do not show.")
    delegations: list[str] = Field(default_factory=list, description="Who you delegated to and why.")


class BudgetExceeded(RuntimeError):
    """The run's delegation budget is spent. Stops the whole run."""


@dataclass
class RunBudget:
    """ONE mutable object shared by every nested run in a delegation tree.
    Every limit is enforced in code (remediation M5); reaching any limit stops the run as 'stopped'."""
    max_depth: int = 4                  # hops; with five agents, cycle prevention already caps chains at 4
    max_delegations: int = 12           # delegate() calls per run (BudgetExceeded)
    request_limit: int = 40             # model requests across ALL agents in the run (UsageLimits)
    total_tokens_limit: int = 60_000    # input + output tokens across ALL agents (UsageLimits)
    tool_calls_limit: int = 60          # tool calls across ALL agents (UsageLimits)
    max_output_tokens: int = 1_200      # per model request (model setting max_tokens)
    wall_clock_seconds: float = 300.0   # whole run (asyncio timeout)
    request_timeout_seconds: float = 60.0   # live HTTP client timeout (checked before a live run)
    max_http_retries: int = 1           # live HTTP client retries (checked before a live run)
    delegations_used: int = 0

    @property
    def usage_limits(self) -> UsageLimits:
        return UsageLimits(request_limit=self.request_limit, total_tokens_limit=self.total_tokens_limit,
                           tool_calls_limit=self.tool_calls_limit)

    @property
    def model_settings(self) -> dict:
        return {"max_tokens": self.max_output_tokens}

    def count_delegation(self) -> None:
        if self.delegations_used >= self.max_delegations:
            raise BudgetExceeded(f"delegation budget of {self.max_delegations} is spent")
        self.delegations_used += 1

    def describe(self) -> dict:
        return {"max_depth": self.max_depth, "max_delegations": self.max_delegations,
                "request_limit": self.request_limit, "total_tokens_limit": self.total_tokens_limit,
                "tool_calls_limit": self.tool_calls_limit, "max_output_tokens": self.max_output_tokens,
                "wall_clock_seconds": self.wall_clock_seconds,
                "request_timeout_seconds": self.request_timeout_seconds, "max_http_retries": self.max_http_retries}


@dataclass(frozen=True)
class RunDeps:
    """Run context passed to every agent. Built only by the runner (trusted code), never by a model.
    `chain` is immutable: each delegation gets chain + (target,), so sibling calls cannot interfere."""
    run_id: str
    mode: RunMode
    chain: tuple[str, ...]
    budget: RunBudget
    recorder: "RunRecorder"
    team: dict[str, Any] = field(repr=False, compare=False)
    # Follow-up 20: the delegation that started the current agent (None for the entry agent), and the
    # per-run full-response log (run_responses.ResponseLog; None when a caller does not keep one).
    delegation_id: str | None = None
    responses: Any = field(default=None, repr=False, compare=False)

    @property
    def current(self) -> str:
        return self.chain[-1]
