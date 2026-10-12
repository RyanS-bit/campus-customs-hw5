"""Problem 9 remediation: the failed live run 53e4f001-d845-4c50-a3a6-1f09ebee50d6 (provider HTTP 400:
"Function tools with reasoning_effort are not supported for gpt-6-luna-global in /v1/chat/completions ...
set reasoning_effort to 'none'").

OFFLINE ONLY. The live model is built by config.build_live_model as in production, but its HTTP client is given a
mock transport (httpx2.MockTransport) that plays the provider and applies the provider's actual rule. No request
leaves the process; the runner's offline network guard is also active as a backstop.
"""

import asyncio
import json

import httpx2
import openai
import pytest
from pydantic_ai.exceptions import ModelHTTPError

import config
import runner
from models import RunBudget

PROVIDER_400 = ("azure-openai error: Function tools with reasoning_effort are not supported for gpt-6-luna-global in "
                "/v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'.")
CFG = config.ModelConfig("gpt-6-luna", "https://api.portkey.ai/v1", "dummy-offline-key-for-tests")


class MockProvider:
    """Plays Portkey/Azure for gpt-6-luna-global: tools + reasoning (anything but 'none') -> the recorded 400;
    otherwise answers with a call to the structured-output tool, so an agent finishes in one request."""

    def __init__(self):
        self.requests: list[dict] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.requests.append({"url": str(request.url), "body": body})
        if body.get("tools") and body.get("reasoning_effort") != "none":
            return httpx2.Response(400, json={"error": {"message": PROVIDER_400, "type": "invalid_request_error",
                                                        "param": None, "code": None}})
        output_tool = next(t["function"]["name"] for t in body["tools"] if t["function"]["name"].startswith("final_result"))
        args = {"summary": "offline mock reply", "facts": [], "proposals_created": [], "drafts_created": [], "estimates": [],
                "needs_human": [], "unknowns": [], "delegations": []}
        return httpx2.Response(200, json={
            "id": "chatcmpl-offline", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
                         "tool_calls": [{"id": "call_1", "type": "function",
                                         "function": {"name": output_tool, "arguments": json.dumps(args)}}]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})


@pytest.fixture
def mock_provider(monkeypatch):
    """Route every AsyncOpenAI client built by config.build_model through the mock provider."""
    provider = MockProvider()
    real = openai.AsyncOpenAI

    class OfflineAsyncOpenAI(real):
        def __init__(self, *args, **kwargs):
            kwargs["http_client"] = httpx2.AsyncClient(transport=httpx2.MockTransport(provider))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(openai, "AsyncOpenAI", OfflineAsyncOpenAI)
    return provider


def test_l1_every_model_built_by_config_turns_reasoning_off():
    budget = RunBudget()
    for model in (config.build_live_model(CFG, budget), config.build_model(CFG)):
        assert model.settings == {"openai_reasoning_effort": "none"}
        assert config.is_required_model(model) and config.has_required_settings(model)
        assert model.model_name == "gpt-6-luna"


def test_l2_the_live_guard_refuses_a_model_without_the_setting(monkeypatch):
    budget = RunBudget()
    runner._check_mode(config.build_live_model(CFG, budget), "live", "test authorization", budget)   # passes
    monkeypatch.setattr(config, "MODEL_SETTINGS", {"openai_reasoning_effort": "none"})
    bare = config.build_live_model(CFG, budget)
    bare._settings = None                                                    # as before the fix: no settings
    with pytest.raises(ValueError, match="required settings"):
        runner._check_mode(bare, "live", "test authorization", budget)


def test_l3_the_mock_reproduces_the_incident_when_reasoning_is_not_turned_off(mock_provider, monkeypatch):
    from pydantic_ai import Agent
    monkeypatch.setattr(config, "MODEL_SETTINGS", {})                         # the configuration of the failed run
    model = config.build_live_model(CFG, RunBudget())
    assert not model.settings
    agent = Agent(model, output_type=str)

    @agent.tool_plain
    def get_ticket(ticket_id: int) -> str:                                   # any function tool triggers the provider rule
        return "unused"

    with pytest.raises(ModelHTTPError) as exc:
        asyncio.run(agent.run("ping"))
    assert exc.value.status_code == 400 and "set reasoning_effort to 'none'" in str(exc.value.body)
    assert "reasoning_effort" not in mock_provider.requests[0]["body"]       # what the failed run sent: no setting


def test_l4_a_live_run_through_the_real_runner_sends_reasoning_none_with_tools_and_completes(work, mock_provider):
    budget = RunBudget()
    model = config.build_live_model(CFG, budget)
    with runner.offline_network_guard():                                     # backstop: no real host is reachable
        out = asyncio.run(runner.run_task("Handle desk ticket 101 only.", p=work, model=model, mode="live",
                                          live_authorization="offline regression test", launcher=runner.in_memory_launcher,
                                          budget=budget, ticket_id=101))
    assert out.status == "completed" and out.mode == "live" and out.report.summary == "offline mock reply"
    assert len(mock_provider.requests) == 1
    req = mock_provider.requests[0]
    assert req["url"] == "https://api.portkey.ai/v1/chat/completions"
    body = req["body"]
    assert body["model"] == "gpt-6-luna" and body["reasoning_effort"] == "none"
    names = {t["function"]["name"] for t in body["tools"]}
    assert {"get_open_tickets", "get_ticket", "check_inventory", "delegate"} <= names     # Boss's approved tools + delegate
    assert not names & {"propose_payment", "propose_purchase", "propose_quote", "draft_message"}   # Boss stays read-only
    assert body.get("max_completion_tokens", body.get("max_tokens")) == budget.max_output_tokens   # run limit still applied
