# Ticket 101, live attempt 1: failed (provider configuration error) and offline remediation

**Status:** a failed live attempt, preserved as evidence. It is not erased or replaced. A retry needs Dr. Ryan's separate authorization.

## The attempt
| | |
|---|---|
| Run ID | `53e4f001-d845-4c50-a3a6-1f09ebee50d6` |
| Started by | Dr. Ryan, from the dashboard (Gate G3, Problem 9 follow-up 7) |
| Started / finished (UTC) | 2026-10-10 20:57:26 / 20:57:39 |
| Phase / outcome | `failed` / `failed` |
| Events | trail seq 4–8: `run_started` (live, `gpt-6-luna`, ticket 101, G2 authorization recorded), `agent_started` boss, `agent_finished` boss (error), `error`, `run_finished` (error) |
| Error | HTTP 400 from the provider: "azure-openai error: Function tools with reasoning_effort are not supported for gpt-6-luna-global in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'." |
| Usage (provider-reported) | 0 requests, 0 tokens. Whether a rejected request is billed is not known. |
| Evidence files | `101_run.json`, `101_events.json` (5 events), `101_after_run_state.json` (saved by the read-only observer within 13 s of the end) |

## Business data after the attempt
- No tool call, proposal, draft or payment. Ticket 101 is still `open`; cash $3,400.00; `GET /api/audit` consistent, with no gaps.
- `data/campus_customs_new.db` is byte-identical to the post-reset copy (`d6b0e89a…`), so nothing in the business data changed.
- `data/audit.db` is unchanged (`67415c09…`). `output/audit_trail.json` gained only the attempt's 5 events (`4ae32e0b…`).

## Root cause
- **Our requests sent no reasoning setting.** PydanticAI sends `reasoning_effort` only when it is configured, and ours was not.
- **The model reasons by default.** The provider's `gpt-6-luna-global` deployment, reached through Portkey, reasons by default; PydanticAI's own profile for `gpt-6-luna` also says reasoning is on by default and can be turned off with `"none"`.
- **That default conflicts with tools.** The provider rejects function tools combined with reasoning on `/v1/chat/completions`. All five agents act only through function tools (their MCP tools, `delegate`, and the structured report), so every live run would have failed.
- **Why it was not caught earlier:** the single connectivity check in Problem 5 used no tools.

## Correction (minimum change)
- **`backend/config.py`:** `MODEL_SETTINGS = {"openai_reasoning_effort": "none"}`, attached to every `gpt-6-luna` model that `build_model` and `build_live_model` create. A new `has_required_settings()` check was added.
- **`backend/runner.py`:** the existing live-mode guard now also refuses a live model without that setting, before any paid call.
- **Unchanged:** the model (`gpt-6-luna` only), the gateway, tool access, agents, prompts, delegation, budgets (40 requests, 60,000 tokens, 60 tool calls, 12 delegations, 300 s), the audit and the approval paths.
- **Why not the Responses API:** the provider offered it as an alternative, but it would mean a different model class and an unverified Portkey route, which is a larger change. `"none"` is the provider's own stated remedy, and PydanticAI's profile for this model supports it.

## Offline verification (no model call, no network)
- `tests/test_problem9_live_config.py` (4 tests) uses a mock provider in the HTTP transport that applies the provider's rule:
  - l1: every model built by `config` carries the setting.
  - l2: the live guard refuses a model without it.
  - l3: the incident is reproduced with the old configuration (the same 400).
  - l4: a live-mode run through the real runner (in-memory MCP, sandbox database) sends `reasoning_effort: "none"` together with the Boss's approved tools, keeps the output limit, and completes.
- **Full suite:** 509 passed (`remediation_regression.txt`). Verifier 21/21 (`remediation_verify_tools.txt`). Frontend 15/15.
- **The dev backend** reloaded automatically when the two files changed. It is still live (`CC_LIVE_AUTHORIZATION` unchanged) and idle.

## Residual uncertainty
The offline tests prove what we now **send**. Whether the provider **accepts** it can only be shown by a live request, which was not authorized here. The next authorized ticket-101 run will show it, and will be recorded as attempt 2.
