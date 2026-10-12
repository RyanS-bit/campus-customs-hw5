# Pre–Problem 6 acceptance audit: Homework 5, Problems 1–5

**Date:** 2026-10-09/10 (local time). **Requested by:** Dr. Ryan (`AI_prompts.md`, Problem 5, follow-up 30). **Auditor:** Claude Code (Claude Opus 5.5), plus one independent read-only reviewer agent (a separate Claude instance, read-only tools, no execution).

**Scope:** an audit only. No source code, database, evidence file, or audit-trail file was modified. The only writes were this report and the prompt-log entry. No reset, no live Portkey call, no agent run.

**How to read the labels:**
- **[V]** directly verified during this audit, by running or reading something now.
- **[R]** reported by the independent reviewer and confirmed against the exact code during this audit.
- **[R?]** reported by the reviewer, not independently re-checked in this audit.
- **[P]** taken from earlier reports and not re-verified.
- **[A]** an assumption.

---

## 1. Executive summary

- **No critical defects.** [V]
  - Both real databases are intact. The working copy's nine business tables are identical to the original, with 0 proposals, 0 drafts, and 0 payments.
  - The required audit trail is valid. No secrets appear anywhere in `hw5/`.
  - The full test suite passes (401/401), and so does the Problem 3/4 verifier (21/21).
  - Both also pass in a **brand-new virtual environment built from `requirements.txt` alone**, on a copy containing only files a fresh clone would have.
- **No route to unauthorized power.** [V/R] No agent can approve, execute, pay, send, write protected tables, or reach the original database. The reviewer independently found no route either.
- **Five major findings** (§5): latent defects or missing controls that matter for **live agent runs (Problem 9)**. None affects the current data, and none blocks *planning* (Problem 6):
  - M1: the stdio launcher can point agent servers at the real database when the runner uses another location.
  - M2: "simulated" mode does not exclude every kind of real model.
  - M3: reconciliation can run during an active run.
  - M4: reconciliation matches rows by ID only, which is wrong after a reset.
  - M5: no token or output caps for live agent runs.
- **Assignment source unavailable.** The official assignment document is **not in `hw5/`**, and Claude did not search elsewhere. §2 is therefore **not** a word-for-word audit. It checks against the assignment wording Dr. Ryan supplied (logged verbatim), and cannot confirm "both panels" of each problem.
- **Not under version control.** `hw5/` has **0 tracked files** [V]. All work exists only in this OneDrive folder.
- **Readiness for Problem 6:** ready to begin, with conditions (§9).

## 2. Compliance matrix, Problems 1–5 (against supplied wording, not the official text)

| Problem | Requirement (as supplied) | Implementation | Evidence and verification |
|---|---|---|---|
| 1 | `AI_prompts.md`: one section per problem with official titles; verbatim prompts; a one-sentence reason per follow-up; Claude's words kept separate | `AI_prompts.md` (149 KB) | [V] All 11 sections present. Problems 1–5 each have a submitted prompt; every follow-up has a reason (P1: 1, P2: 3, P3: 1, P4: 2, P5: 26 headings); Claude's notes are separate; Problems 6–11 are correctly empty. *Verbatim fidelity cannot be checked mechanically.* |
| 1 | Minimal workspace: `requirements.txt`, `.env.example`, `.gitignore`, `.mcp.json`, `README.md`, `data/`, `mcp_server/`, `backend/`, `frontend/`, `output/` | All present | [V] Inventory (§3). `frontend/` is empty (Problem 8). |
| 2 | Study the database; original preserved; working copy `data/campus_customs_new.db` | `output/harness.md` (27.8 KB): tables, tickets, rules R1–R8 (§7a), question log; working copy | [V] Original hash `23686a90…f360a`; harness present and substantive |
| 3 | FastMCP server with tools for the database, connected to the working copy only | `mcp_server/server.py`; `verify_tools.py`; `mcp_server/README.md` | [V] Verifier 21/21 (the 3 tool-list checks were updated for 10 tools, as approved). The read connection refuses writes. |
| 4 | Server added to the vibe coder; each tool tested live; evidence in `output/mcp_smoke.json` | `.mcp.json`; `output/mcp_smoke.json` | [V] All 6 recorded Problem 4 results are identical to the Stage 3 live results, and **reproduced today** by fresh read-only calls. *The live calls themselves are evidenced by the session transcript, not re-runnable here.* |
| 5 | Five PydanticAI agents | `backend/agents.py` | [V] Tests `a1`/`a2`. Offline only. |
| 5 | Full connectivity (all-to-all delegation) | `backend/delegation.py` | [V] All 20 directed paths tested (`a6`); cycle, depth, and budget limits tested |
| 5 | One prompt per agent in `backend/prompts/` | 5 files, 2.8–3.1 KB each | [V] `a3`, `d5` |
| 5 | Data types in `backend/models.py` | `models.py` | [V] |
| 5 | Implementations under `backend/` | config, models, agents, delegation, runner | [V] |
| 5 | `gpt-6-luna` through Portkey with `PORTKEY_API_KEY` | `backend/config.py` (one configuration point) | [V] `a4`, `a22`, `d5`. [P] one live request accepted (`output/p5_live_connectivity.json`; not re-run, by instruction) |
| 5 | Expanded MCP tools | 10 tools; launch identity; per-agent registration | [V] Stage 3 tests; [V] 16/16 recorded live calls reproduced today |
| 5 | JSON audit logging, `output/audit_trail.json`, append-only, run history preserved | `mcp_server/audit_trail.py`, `audit_reconcile.py`, `locks.py`; runner hook | [V] Valid array, chain intact (1 `trail_initialized` event). Behavior verified **offline only**: no real agent-loop events exist yet. |
| 5 | Expanded `harness.md`; updated `mcp_server/README.md` | §9–10; README | [V] `d3` |
| R1–R8 | Rules respected by the Problem 5 tools | `ops.py`, `policies.py`, `authz.py`, schema | [V] by tests. **Execution-side R1/R4 belong to Problem 7.** |

## 3. File inventory [V]

| Area | Files | Assessment |
|---|---|---|
| Root | `.env.example`, `.gitignore`, `.mcp.json`, `AI_prompts.md`, `CLAUDE.md`, `README.md`, `requirements.txt`, `data.zip` | All substantive. `data.zip` is the supplied archive. |
| `data/` | `campus_customs.db` (original), `campus_customs_new.db` (working, 106 KB with the Problem 5 tables), `backups/…pre_stage3_reset….db` (git-ignored) | Correct. No `audit.db` or run lock present. |
| `mcp_server/` | `server.py`, `ops.py`, `authz.py`, `audit_db.py`, `audit_schema.sql`, `audit_trail.py`, `audit_reconcile.py`, `locks.py`, `maintenance.py`, `paths.py`, `policies.py`, `schema_ext.sql`, `verify_tools.py`, `README.md` | Substantive. |
| `backend/` | `config.py`, `models.py`, `agents.py`, `delegation.py`, `runner.py`, `prompts/` (5) | Substantive. FastAPI routes are Problem 7. |
| `scripts/` | `reset_db.py`, `snapshot_runtime.py`, `verify_audit.py`, `live_connectivity_check.py`, `stage2_evidence.py`, `stage3_evidence.py` | Substantive. The evidence scripts **overwrite** their output files if re-run (informational). |
| `tests/` | 13 files (Stages 1–7, helpers) | Substantive |
| `output/` | Harness; architecture; specification; acceptance matrix; roadmap; ticket map; smoke and regression evidence; 7 stage test logs; lock-stress and repeated-run logs; reset record; connectivity report; `audit_trail.json` | Substantive. The 7 per-stage test logs are history (the latest is Stage 7). |
| `frontend/` | Empty | Belongs to Problem 8 |
| Placeholders and leftovers | Five empty `.gitkeep` files (four in folders that now have content); `output/audit_trail.json.lock` (1 byte, written by the pre-fix lock code; git-ignored) | Informational; harmless |
| Duplicates or misplaced files | None found | — |

## 4. Test and evidence results [V]

| Check | Result |
|---|---|
| Full offline suite (this audit, in the project) | **401 passed** |
| Full offline suite in a **fresh venv** built only from `requirements.txt`, on a clean-clone copy (scratchpad) | **401 passed**; verifier **21/21** |
| Problem 3/4 verifier (project) | **21/21** |
| Saved test logs: pass lines vs summary line | Consistent in all 7 (43, 209, 282, 326, 383, 395, 401) |
| Problem 4 smoke vs Stage 3 live (6 calls) | Identical |
| Stage 3 live evidence (16 calls) reproduced by fresh read-only calls today | **16/16** |
| Stage 2 evidence | 16 attempts, 0 allowed; all rollbacks left state unchanged; labeled OFFLINE |
| Stage 3 regression evidence | Compatible; 6/6 authorized; 9/9 denied; labeled OFFLINE |

**How meaningful the tests are [V]:**
- Most tests assert observable behavior: database rows, hashes, file bytes, audit events, and refusal codes. Concurrency and crash tests use real subprocesses.
- More than 30 deliberate breakages ("mutation checks") were caught across Stages 1–7, each stage's results recorded in `AI_prompts.md`. That figure is [P]: the breakages were not re-run in this audit, and the earlier test was replaced where one initially went undetected (the Stage 1 journal test; the Stage 5 client filter).

**Untested or only simulated:**
- How `gpt-6-luna` decides as an agent: all agent tests use scripted models.
- Real agent-loop events in the real trail.
- The POSIX locking path (this machine runs Windows).
- `CancelledError` during tool calls (M-minor 8).
- Behavior on a slow or offline network.
- Everything in Problems 7–9: approval, execution, and ticket status.

## 5. Technical findings

### Critical
None. [V/R]

### Major (fix before any live agent run; none blocks Problem 6 planning)

| ID | Finding | Location | Scenario | Status |
|---|---|---|---|---|
| M1 | **The stdio launcher can point agent servers at a different database than the runner.** Each server subprocess resolves its own paths (`server.py:33` `PATHS = paths.resolve()`) and is launched by `create_server(launch_identity)` (`:414`). The runner passes a location only in runtime mode (`runner.py:53–56`). | `backend/runner.py`, `mcp_server/server.py` | `run_task(p=<non-default root>)` with the default launcher: the run lock and trail sit in one place, but proposals go to the real `data/campus_customs_new.db` and `data/audit.db`. | [R] confirmed in code. Not triggered so far: tests used in-memory servers or runtime mode. |
| M2 | **"Simulated" mode refuses only `OpenAIChatModel`.** Model strings (such as `"openai:…"`), other providers, or wrapper models pass the check and would run for real while labeled "simulated", with "synthetic" usage. | `runner.py:124–126` | A mislabeled live run | [R] confirmed |
| M3 | **Reconciliation does not check the run lock.** It can record `not_found` for a call that is still in progress; the real `tool_call_finished` then follows, so the trail holds contradictory outcomes. | `audit_reconcile.py`, `scripts/verify_audit.py` (no `run_active`) | An operator reconciles during a run | [R] confirmed |
| M4 | **Reconciliation's row check uses only the ID** (`WHERE id = ?`). After a reset, row IDs restart at 1 while `audit.db` keeps its history, so an old incomplete call can be reported `committed` against another run's row. | `audit_reconcile.py:50` | Reconcile after a reset | [R] confirmed |
| M5 | **No token or output caps for live agent runs.** `RunBudget.total_tokens_limit = None`; there is no `max_tokens` in any agent model setting; the OpenAI client keeps its default retries (2) and 600 s timeout, and those HTTP retries are not counted by `UsageLimits`; there is no wall-clock limit. Only `request_limit = 40` applies. | `backend/models.py:41`, `config.py`, `agents.py` | Cost and runaway risk in Problem 9 | [V] |

### Minor

| ID | Finding | Status |
|---|---|---|
| m1 | **`--root` scripts still read the real `CC_RUNTIME_DIR`** from the process environment (`reset_db.py:31`, `snapshot_runtime.py:30`, `verify_audit.py:29`). The reviewer rated this major; the auditor rates it minor, because `--root` is test-only and the CLI tests pass a sanitized environment. | [R] confirmed |
| m2 | **The audit-trail credential guard reads only `os.environ`.** The key, loaded from `.env` through `dotenv_values`, is invisible to it. Mitigation: the key is never placed into events, and the scan found no secrets. | [R] confirmed |
| m3 | **`run_started` can lack `run_finished`** if building the toolsets or team fails, because those steps sit before the `try` (`runner.py:148–151`). | [R] confirmed |
| m4 | **`reset_db.py` resolves paths outside its `try`,** so a `PathConfigError` escapes uncaught, without an audit event. | [R] confirmed |
| m5 | **The cash basis differs between places.** `fundable` uses one account's balance and ignores approved commitments; other calculations use total cash. No current effect (only 1 account). | [R] confirmed |
| m6 | **The subprocess environment filter** strips `KEY` and `SECRET` but not `TOKEN` or `PASSWORD`. | [R] confirmed |
| m7 | **Some errors arrive without a stable code** (`UnsafeWriteError`, `OSError` from `ensure`, the original tools' messages) and are recorded as `TOOL_ERROR`. The original tools are uncoded by design (D16e). | [R?] |
| m8 | **`CancelledError` during parallel tool calls** can skip `tool_call_finished` and `run_finished`. | [R?] |
| m9 | **The reset is not fully atomic:** a schema-extension failure after the swap leaves an unextended copy; the run lock is checked only once (a timing gap). | [R?] |
| m10 | **`draft_message`** accepts closed tickets and links to rejected or failed proposals. | [R?] |
| m11 | **`verify_tools.py`** hard-codes `data/` and its stdio subprocess inherits the full environment. | [R?] |
| m12 | **Reproducibility gaps:** the Python version is not documented (3.14.7 used); there is no quick-start setup section in the main README (the venv steps are in `mcp_server/README.md`); `.mcp.json` is Windows-specific (the README documents the macOS/Linux change). | [V] |

### Informational
- `audit.db` records only the acting agent; the delegation chain is in the JSON trail only.
- The trail re-reads and verifies the whole file on every append, so cost grows quadratically. There is no rotation.
- `server.py`'s check of the test-failure switch at startup is redundant in a fresh process.
- `create_server(p=PATHS)` defaults to the real paths, so a caller that forgets `p` targets the real working copy (same root cause as M1).
- The live evidence was transcribed by Claude. It is corroborated by today's 16/16 reproduction and the session transcript, but it is not machine-captured.
- **Complexity:** the supplementary `audit.db` plus the JSON trail, a two-layer permission model, and many per-stage evidence files are more than a minimal submission needs. Each is justified in the design record, but a grader may need a short guide (see the corrective actions).

## 6. Database and evidence integrity [V]

| Check | Result |
|---|---|
| Original database | SHA-256 `23686a90…f360a` = the expected value = the pre-reset backup; integrity ok |
| Working database | SHA-256 `dbaf24cb…8ede` (unchanged since the authorized reset; modified time 17:44); the 9 business tables are content-identical to the original; integrity ok |
| Unauthorized business activity | None: 0 payments, 0 proposals, 0 drafts; tickets 101–103 open; cash $3,400; invoice 501 open; inventory total 82 |
| `audit.db` / run lock | Absent: nothing has written agent work to the real database |
| `output/audit_trail.json` | Valid JSON array; 1 event (`trail_initialized`); hash chain valid; SHA `ec8e507a…` |
| `output/mcp_smoke.json` | SHA `64a0c1b3…`; content identical to the Stage 3 live results; reproducible today |
| Secrets | Neither the `PORTKEY_API_KEY` nor the `TIINGO_API_KEY` value, nor a 12-character fragment of either, appears in any of 87 files; no `hw5/.env`; no key-shaped strings |

## 7. Reproducibility assessment
- **Verified [V]:** a clean environment built from `requirements.txt` on a fresh-clone copy passes 401/401 and 21/21 **on this Windows machine with Python 3.14.7**.
- **Depends on:**
  1. The committed `data/campus_customs.db` and `data/campus_customs_new.db`.
  2. Python 3.14 (undocumented minimum).
  3. Windows paths in `.mcp.json` (a documented edit is needed for macOS/Linux).
  4. For live use only: `PORTKEY_API_KEY` in the environment, `hw5/.env`, or the course root `.env`. The course-root location is a local convention a grader would not share; `.env.example` documents the variables.
- **Not verified:** macOS/Linux; a machine without network access during `pip install`.
- **Manual actions a grader must know:** approve the MCP server in Claude Code; run `scripts/reset_db.py --confirm` before ticket runs (R8).

## 8. Academic quality
- **Strengths:**
  - Clear agent responsibilities, with Boss read-only.
  - Real collaboration through all-to-all delegation.
  - Prompts of about 3 KB each with role, tools, and ten shared rules.
  - A sensible ten-tool interface.
  - Rules enforced in code rather than by prompts.
  - Careful separation of facts, estimates, proposals, and unknowns.
  - Unusually strong evidence of testing.
- **Weaknesses:**
  - **Token-use controls are thin** (M5). This is the clearest gap against the assignment's "appropriate token-use controls".
  - The documentation is voluminous; a grader would benefit from a one-page guide.
- **Simpler alternatives, not recommended now:** dropping the supplementary `audit.db` would simplify the design, but it is approved (D11) and tested. The current recommendation is to *document* rather than remove it.

## 9. Outstanding issues, corrective actions, and readiness

| # | Action | When | Needs authorization |
|---|---|---|---|
| 1 | Provide the **official Problem 1–5 text** for a word-for-word compliance check (including "both panels") | Before final acceptance | Dr. Ryan |
| 2 | Fix **M1–M5**: pin the server root explicitly in the launcher and refuse mismatches; allow only offline test models in simulated mode; make reconciliation refuse while a run is active; match rows by `run_id`/`call_id`; add `max_tokens`, `total_tokens_limit`, a client timeout and retry limit, and a wall-clock limit | Before any live agent run (Problem 9); ideally before Problem 7 | Yes: a remediation step |
| 3 | Fix the quick minor items (m1–m6, m12): sanitize the environment for `--root`, extend the secret guard to configuration values, put the run setup inside the `try`, move path resolution inside the `try`, extend the environment filter, add a README quick-start with the Python version | Same remediation step | Yes |
| 4 | Decide on version control for `hw5/` (0 tracked files) | Soon; required by Problem 11 | Yes (Git actions) |
| 5 | Review the remaining [R?] items (m7–m11) | Remediation step | Yes |

**Readiness determination:**
- **Problem 6 ("Plan the three tickets") may begin.** It is planning, uses only verified facts, and none of the findings affects planning.
- **Conditions:**
  - Item 1 is recommended before Problem 5 is formally accepted.
  - Items 2–3 must be completed and re-verified **before any live agent run or the Problem 7 execution work**.
- Problem 5 is functionally complete for its offline scope. It is **not yet ready for live operation.**
