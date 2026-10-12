# Problem 5 acceptance matrix (Stage 7)

**Problem 5:** Build the agent team and grow the MCP tools (15 points).

**Source of the requirements:** the official Homework 5 text is not in `hw5/`, and Claude did not search elsewhere. Requirements are taken from the assignment wording Dr. Ryan supplied, which `AI_prompts.md` preserves verbatim:
- Problem 1, follow-up 1 §5: the required architecture.
- Problem 2, follow-up 2: rules R1–R8.
- Problem 5, follow-up 4: the deliverables list and the answers to Q1–Q5.

A final word-for-word check against the official Problem 5 text is recommended (see §4).

**Verification levels:**
- **Implemented:** the code exists.
- **Offline-verified:** automated tests on temporary databases, with scripted models.
- **Live-verified:** demonstrated against a real external system.
- **Not yet verified:** needs a real agent run or a later problem.

## 1. Required deliverables

| # | Requirement (as supplied) | Implementation | Files | Evidence | Level | Limits / not yet verified |
|---|---|---|---|---|---|---|
| 1 | Five PydanticAI agents: Boss, Inventory, Accounting, Facilities, Customer Service | One `Agent` each, with a structured `AgentReport` output | `backend/agents.py` | `test_a1` (builds all five on one model), `test_a2`, `test_a15` | Offline-verified | No live **agent** run yet: how `gpt-6-luna` reasons with tools and delegation is untested |
| 2 | Full connectivity: every agent can delegate to every other | A shared `delegate(to, task)` tool, with cycle, depth, delegation-budget, and shared-usage safeguards | `backend/delegation.py`, `backend/models.py` | `test_a6` (all **20** directed paths), `a7`–`a11`, `d4` | Offline-verified | Delegation choices by the real model are untested |
| 3 | One prompt file per agent under `backend/prompts/` | Five files: role, tools, and identical shared rules | `backend/prompts/*.md` | `test_a3`, `d5` | Verified (static) | Prompt effectiveness with `gpt-6-luna` is untested |
| 4 | Data types in `backend/models.py` | `AgentReport`, `RunDeps`, `RunBudget`, `BudgetExceeded`, `AgentName` | `backend/models.py` | Used by every Stage 5–6 test | Offline-verified | — |
| 5 | Implementations under `backend/` | config, models, agents, delegation, runner | `backend/*.py` | Stage 5–6 tests; `a21` (no SQLite or approval code in agent code) | Offline-verified | FastAPI routes are Problem 7 |
| 6 | Required model configuration: `gpt-6-luna` through Portkey, `PORTKEY_API_KEY` | One configuration point; any other model rejected; key never logged | `backend/config.py`, `.env.example` | `a4`, `a22`, `d5` offline; **one live request** accepted (`output/p5_live_connectivity.json`) | **Live-verified (connectivity)** | Only one trivial request; no agent workload has run |
| 7 | Expanded MCP tools (no names or count mandated) | 10 tools: 6 read, 4 propose/draft; identity fixed at launch; per-agent registration | `mcp_server/server.py`, `ops.py`, `authz.py` | 67 Stage 3 tests; `output/mcp_regression_p5.json`; **16 live calls** (`output/mcp_live_regression_p5.json`, 38/38 cross-checks) | **Live-verified** (development mode: reads and write refusals); agent-mode writes offline-verified | Agent-mode writes have not been made against the real database, by instruction |
| 8 | JSON audit logging: append-only `output/audit_trail.json` for agent-loop events, with activity, delegation, MCP tool use, outcomes, and run IDs, preserving previous runs | Hash-chained JSON array; OS lock plus atomic replace; fail-closed; `RunRecorder` plus a tool-call hook; reconciliation with `audit.db` | `mcp_server/audit_trail.py`, `audit_reconcile.py`, `locks.py`, `backend/runner.py`, `scripts/verify_audit.py` | Stage 4 J/G tests (concurrency, crashes, damage, retries); `a14`–`a19`; `w6`–`w8`; lock stress 100/100 | Offline-verified; real file exists (`trail_initialized`, verified consistent) | **No real agent-loop events yet.** Those appear only with the first real run. |
| 9 | Expanded `output/harness.md` | §9 (MCP tools) and §10 (Problem 5 status, Stages 1–6) | `output/harness.md` | `d3` | Done | — |
| 10 | Updated `mcp_server/README.md` | All 10 tools, launch identity, write layer, locks | `mcp_server/README.md` | `d3` | Done | — |

## 2. Rules and principles that Problem 5 must respect

| Rule | How Problem 5 enforces it | Evidence | Level / limit |
|---|---|---|---|
| Agents use MCP for shop facts | Agent code never opens SQLite; facts reach agents only through tool returns | `a21`, `w1` (agents receive genuine values) | Offline-verified |
| R1: no negative cash | Payment proposals above the balance are labeled NOT EXECUTABLE; competing proposals flagged | `v2`, `v3`, `w4` | Proposal side verified. **The refusal at execution is Problem 7.** |
| R2: human approval | Agents can only insert `proposed` rows; no approve or execute tool exists; the authorizer blocks UPDATE/DELETE | `B1`–`B11`, `x1`, `w5`, `c1`–`c5` | Offline-verified (and live: development-mode write refusals) |
| R3: an unpaid invoice blocks the vendor | Purchase flagged `vendor_blocked`; no arrival estimate while blocked | `v10`, `w3`, live `list_vendors` | Verified. Ordering and shipping are not built. |
| R4: records updated on payment | Non-open invoices refused | `v5` | The update itself is Problem 7 |
| R5: business date = `desk.date_today` | Every computed date | Stage 3 cross-checks | Live-verified |
| R6: no revenue | Quotes state that they add no cash | `v14` | Offline-verified |
| R7: nothing sent | Drafts only; no send path; recipient from records | `x7`, `v18`–`v23` | Offline-verified |
| R8: reset before runs | `scripts/reset_db.py` (verified, recorded) | Stage 1 S tests; the authorized real reset | **Live-verified (real reset, Stage 3)** |
| Delegation cannot raise permissions | Server registration plus an independent client filter; identity from launch | `a2` (both layers, each tested alone), `a12`, `a13`, `c3` | Offline-verified |

## 3. Architecture and permissions review (Stage 7)
- **All five agents exist,** and all 20 directed delegation paths are tested.
- **Access matrix agrees in every source:** `server.ACCESS`, the specification table (parsed by `d1`), `ops.OPERATION_AGENTS`, and `policies.DRAFT_AUTHORITY` (`w9`). Boss has no write tool.
- **Tool descriptions** name their authorized agents and never claim execution or sending (`d2`).
- **Documentation corrected:** 7 out-of-date statements in the README, harness, architecture, and ticket map (see `AI_prompts.md`, follow-up 29 notes).
- **No agent can bypass approval:** no approval, execution, or ticket-status code exists for anyone (`s2`, `a21`). Delegation never changes an agent's tool set; tools come from the launch identity.

## 4. Outstanding items

| Item | Type | Blocks Problem 5 acceptance? |
|---|---|---|
| Word-for-word check of this matrix against the official Problem 5 text | Information needed from Dr. Ryan | Recommended before final acceptance |
| Live agent behavior of `gpt-6-luna` (reasoning, tool use, delegation) | Not yet verified | No, unless the assignment requires a live agent demonstration in Problem 5. Dr. Ryan's earlier answer to Q3: live operation is needed for Problem 9. |
| Real agent-loop events in `output/audit_trail.json` | Not yet verified | Same as above |
| Approval and execution, ticket status, draft review | Problem 7 | No (explicitly Problem 7) |
