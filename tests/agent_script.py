"""Scripted OFFLINE models for the agent tests (PydanticAI FunctionModel; no network, no real model).

A plan is a list of actions an agent takes in ONE of its runs, one action per model turn:
  ("tool", name, args)       call an MCP tool
  ("delegate", to, task)     call the delegate tool
  ("raise", message)         the model itself fails
  ("final", fields)          return the final AgentReport (default: {"summary": "<agent> done"})
Each agent consumes its plans in order, one plan per time it is run.
"""

from __future__ import annotations

from collections import defaultdict

from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

AGENTS = ("boss", "inventory", "accounting", "facilities", "customer_service")


class Scripted:
    def __init__(self, plans: dict[str, list[list[tuple]]] | None = None):
        self.plans = {a: list(v) for a, v in (plans or {}).items()}
        self.current: dict[str, list[tuple]] = {}
        self.tools_seen: dict[str, list[list[str]]] = defaultdict(list)
        self.returns: dict[str, list[tuple[str, object]]] = defaultdict(list)
        self.runs: list[str] = []

    def _final(self, agent: str, info: AgentInfo, fields: dict | None = None) -> ModelResponse:
        payload = {"summary": f"{agent} done", **(fields or {})}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])

    def model_for(self, agent: str) -> FunctionModel:
        def fn(messages, info: AgentInfo):
            turn = sum(isinstance(m, ModelResponse) for m in messages)
            if turn == 0:
                queue = self.plans.get(agent) or []
                self.current[agent] = queue.pop(0) if queue else []
                self.tools_seen[agent].append(sorted(t.name for t in info.function_tools))
                self.runs.append(agent)
            for part in getattr(messages[-1], "parts", []):
                if isinstance(part, (ToolReturnPart, RetryPromptPart)):
                    self.returns[agent].append((part.tool_name, part.content))
            plan = self.current[agent]
            if turn < len(plan):
                kind, *rest = plan[turn]
                if kind == "tool":
                    return ModelResponse(parts=[ToolCallPart(rest[0], rest[1])])
                if kind == "delegate":
                    return ModelResponse(parts=[ToolCallPart("delegate", {"to": rest[0], "task": rest[1]})])
                if kind == "raise":
                    raise RuntimeError(rest[0])
                if kind == "final":
                    return self._final(agent, info, rest[0])
            return self._final(agent, info)
        return FunctionModel(fn, model_name=f"scripted-{agent}")

    def models(self) -> dict[str, FunctionModel]:
        return {a: self.model_for(a) for a in AGENTS}
