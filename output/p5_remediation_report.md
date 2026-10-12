# Problem 5 remediation report (findings M1–M5 and necessary minor fixes)

**Authorized by:** Dr. Ryan, `AI_prompts.md`, Problem 5, follow-up 31.
**Source of findings:** `output/pre_p6_acceptance_audit.md`.
**Boundaries:** no real database reset or modification, no change to historical audit evidence, no live Portkey calls, no Problem 6.

Part 1 was written **before** any code was changed. Part 2 records what was done and the verification evidence.

---

## Part 1: Plan (written before implementation)

### M1. Agent MCP servers can use a different database than the runner
- **Root cause:**
  - In agent mode, each server subprocess resolves its own paths (`server.py:33`, `PATHS = paths.resolve()`). It uses the real hw5 root and `hw5/.env`.
  - The runner passes `CC_RUNTIME_DIR` only in runtime mode (`runner.py:53–56`).
  - `create_server()` also defaults to those real paths.
- **Files:** `mcp_server/server.py`, `backend/runner.py`.
- **Correction:**
  - The trusted launcher passes the runner's own selection explicitly: `CC_HW5_ROOT`, `CC_RUNTIME_DIR` (possibly empty), and the expected `CC_WORKING_DB`.
  - In agent mode the server resolves paths **only** from these (no `.env`, no defaults) and refuses to start if any is missing or the result does not equal `CC_WORKING_DB`.
  - `create_server()` with an agent identity requires explicit paths.
  - Development mode (Claude Code via `.mcp.json`) is unchanged.
- **Regression tests:**
  - A stdio run on an isolated **default-layout** root (the case that previously fell back to the real database) writes only there.
  - Missing or mismatched launch paths make the server refuse to start.
  - `create_server(identity)` without paths is refused.

### M2. Simulated mode could run a real model
- **Root cause:** a deny-list. Only `OpenAIChatModel` was refused (`runner.py:124–126`).
- **Files:** `backend/runner.py`.
- **Correction:**
  - An **allow-list**: simulated mode accepts only PydanticAI's offline `FunctionModel` and `TestModel`; model-name strings and every other class are refused.
  - In addition, **an offline network guard during simulated runs:** host-name resolution and socket connections to anything other than this computer raise an error. Even a test model that tried to call out cannot.
- **Regression tests:**
  - Model strings, `OpenAIChatModel`, and arbitrary model objects are refused in simulated mode.
  - A test model that attempts a network lookup during a simulated run is blocked.
  - The guard is removed after the run.

### M3. Reconciliation can act on an active run
- **Root cause:** `audit_reconcile.reconcile()` and `verify_audit.py` never consult the run lock.
- **Files:** `mcp_server/audit_reconcile.py`, `scripts/verify_audit.py`.
- **Correction:**
  - `reconcile()` **holds the run lock** for its whole duration, acquired without waiting. If a run is active it refuses (`ReconcileRefused`), and while it reconciles no run can start.
  - `check()` (read-only) reports `run_active` and labels incomplete calls "in progress" rather than as gaps.
- **Regression tests:**
  - Reconcile while a run holds the lock is refused and the trail is unchanged.
  - A run cannot start while reconcile holds the lock.
  - `check()` during a run reports in-progress calls, not gaps.

### M4. A row match by ID alone is wrong after a reset
- **Root cause:** `_row_exists` used `WHERE id = ?` (`audit_reconcile.py:50`). IDs restart after a reset; `audit.db` does not.
- **Files:** `mcp_server/audit_reconcile.py`.
- **Correction:**
  - Match on id **and** `run_id` **and** `call_id`.
  - If `audit.db` proves a commit but the row is absent or belongs to another run, report `committed_row_no_longer_present` (the change committed, and the working copy was since reset). Never report `committed` for someone else's row.
- **Regression test:**
  - Gap in run A, then a reset, then a new proposal with the same ID in run B; reconcile reports `committed_row_no_longer_present`.
  - With the original ID-only code, this test reports `committed` and fails.

### M5. No token, output, or time limits for live runs
- **Root cause:** `RunBudget` had only request and delegation limits (`total_tokens_limit=None`); there was no per-request output cap or wall-clock limit; the OpenAI client kept its default retries and timeout.
- **Files:** `backend/models.py`, `backend/runner.py`, `backend/delegation.py`, `backend/config.py`.
- **Correction:** enforceable defaults in `RunBudget`, all recorded in `run_started`:
  - `request_limit` 40
  - `total_tokens_limit` 60,000
  - `tool_calls_limit` 60
  - `max_output_tokens` 1,200 per request (passed to every agent run)
  - `max_delegations` 12
  - `wall_clock_seconds` 300
  - `request_timeout_seconds` 60
  - `max_http_retries` 1

  Live runs refuse a model whose client timeout or retries exceed the budget; `config.build_live_model()` builds a compliant one. **Every limit stops the run cleanly:** a `budget_exceeded` event with its code (`USAGE_LIMIT`, `DELEGATION_BUDGET`, `WALL_CLOCK`), then `run_finished` with status `stopped`, with nothing executed.
- **Regression tests:**
  - The token limit, tool-call limit, and wall clock each stop a run with the right code.
  - `max_tokens` reaches every agent's model call.
  - A live model with default retries or timeout is refused.

### Minor fixes judged necessary

| ID | Why necessary | Correction | Test |
|---|---|---|---|
| m1 | **Safety:** a `--root` script could follow the real `CC_RUNTIME_DIR` | With `--root`, resolve with no process or `.env` runtime setting | The CLI with a hostile `CC_RUNTIME_DIR` stays in the sandbox |
| m2 | **Security:** the credential guard was blind to keys loaded from `.env` | The configuration registers loaded secret values with the audit trail's guard | An event containing a `.env`-loaded key is refused |
| m3 | **Correctness:** `run_started` without `run_finished` | Team and toolset setup moved inside the run's `try` | A setup failure still records `run_finished` |
| m4 | **Correctness:** a `PathConfigError` escaped `reset_db.py` | Path resolution moved inside the handled block | A bad setting gives a clean error and exit code 1 |
| m6 | **Security:** the subprocess environment filter missed `TOKEN` and `PASSWORD` | One shared credential-name pattern | Unit test |
| m8 | **Audit correctness:** a cancelled tool call left no finish event | Record `tool_call_finished` with `incomplete/CANCELLED`, then re-raise | The wall-clock test checks it |
| m9 | **Correctness:** a reset could be left half-done, and the run lock was checked only once | Apply the schema to the staging copy **before** the swap, and hold the run lock for the whole reset or snapshot | A schema failure leaves the old working copy untouched; reset while a run is active is refused |
| m12 | **Reproducibility** | A README quick start: Python version (tested 3.14.7), venv, install, tests, verifier | Clean-clone run |

**Not changed (not clearly necessary now; documented instead):**
- m5: the cash basis. There is only one account, and approved commitments belong to Problem 7.
- m7: uncoded errors from the original tools, kept unchanged by design (D16e).
- m10: drafts on closed tickets. A business rule; no harm.
- m11: the `verify_tools.py` environment. Kept as the unchanged Problem 3/4 verifier.

---

## Part 2: Implementation and verification

**Tests:** `tests/test_remediation.py`, 30 tests.

**Defect reintroduction ("mutation"):** each original defect was put back in a scratch copy, and the regression tests were run against it. **Every reintroduced defect was caught.** Three of the first mutations were invalid: one was incomplete, and two broke the code's structure. They were redone so that each mutated file still compiles and faithfully restores the original defect. Results of the valid mutations:

| Finding | Correction made | Files | Regression tests | Reintroduced defect → result |
|---|---|---|---|---|
| **M1** path isolation | The launcher passes `CC_HW5_ROOT`, `CC_RUNTIME_DIR`, and `CC_WORKING_DB`. In agent mode the server resolves **only** from these (no `.env`, no defaults) and refuses to start if any is missing or mismatched. `create_server(identity)` requires explicit paths. Development mode is unchanged. | `mcp_server/server.py` (`launch_paths_from_env`, `create_server`, `__main__`), `backend/runner.py` (`stdio_launcher`) | `r1` (stdio run on an isolated **default-layout** root writes only there; the real working copy's hash is unchanged and no real `audit.db` is created), `r2` ×3 (refuses to start: no paths, partial paths, mismatch), `r3` (ignores `hw5/.env`), `r4` | Server ignoring the launch paths → **4 failed + 1 error** |
| **M2** simulated vs live | An **allow-list** (only `FunctionModel`/`TestModel`; strings and other classes refused), plus an **offline network guard** for the whole simulated run (non-local name lookups and socket connects raise `OfflineViolation`) | `backend/runner.py` (`_check_mode`, `offline_network_guard`) | `r5` ×4 refusals (`"openai:gpt-4o"`, `"gpt-6-luna"`, a non-model object, `OpenAIChatModel`), `r5` (TestModel accepted), `r6` (a model doing `getaddrinfo("api.portkey.ai")` is blocked; guard removed afterwards), `r6` (raw-IP connect blocked; loopback allowed) | Deny-list and no guard → **4 failed** |
| **M3** reconcile during a run | `reconcile()` holds the run lock for its whole duration and raises `ReconcileRefused` if a run is active. `check()` reports `run_active` and labels calls `in_progress`. `verify_audit.py` reports the refusal. | `mcp_server/audit_reconcile.py`, `scripts/verify_audit.py` | `r7` (refused during a run; trail byte-identical; `check` labels in-progress), `r8` (no run can start while reconcile holds the lock) | Reconcile without the lock → **2 failed** |
| **M4** matching rows after a reset | Rows match on id **and** `run_id` **and** `call_id`. A proven commit whose row is gone or reused is reported `committed_row_no_longer_present`, never `committed`. | `mcp_server/audit_reconcile.py` | `r9`: a lost completion event in run A, then a reset, then run B reuses id 1; the outcome is `committed_row_no_longer_present` | ID-only match → **1 failed** |
| **M5** limits | `RunBudget` defaults (all recorded in `run_started`): `request_limit` 40, `total_tokens_limit` 60,000, `tool_calls_limit` 60, `max_output_tokens` 1,200 (sent as `max_tokens` with every agent call), `max_delegations` 12, `wall_clock_seconds` 300 (`asyncio.wait_for`), `request_timeout_seconds` 60, `max_http_retries` 1. A live run refuses a model whose client exceeds the timeout or retry limit; `config.build_live_model()` builds a compliant one. Every limit gives `budget_exceeded` (with its code) and `run_finished` (`stopped`). | `backend/models.py`, `delegation.py`, `runner.py`, `config.py` | `r10` (token limit), `r11` (tool-call limit), `r12` (wall clock: stopped, `WALL_CLOCK`, run lock released), `r13` (`max_tokens` reaches every agent's call), `r14` (non-compliant live client refused before anything runs; compliant one accepted; no network), `r15` (all limits recorded) | No output cap → 1 failed. No wall clock → 1 failed. No tool-call limit → 1 failed. No live client check → 1 failed. |
| m1 | With `--root`, the scripts ignore process and `.env` runtime settings | `scripts/reset_db.py`, `snapshot_runtime.py`, `verify_audit.py` | `m1`: a hostile `CC_RUNTIME_DIR` is ignored; the sandbox is used | → 1 failed |
| m2 | The configuration registers `.env`-loaded keys with the audit trail's credential guard | `backend/config.py`, `mcp_server/audit_trail.py` | `m2` | → 1 failed |
| m3 | Toolset and team setup inside the run's `try` | `backend/runner.py` | `m3`: a setup failure still records `run_finished` | → 1 failed |
| m4 | Path errors give a clean `RESET FAILED` and exit code 1 | `scripts/reset_db.py` | `m4` (dry-run path only; nothing written) | Not mutated (clean-error path) |
| m6 | One shared credential-name pattern (KEY, SECRET, TOKEN, PASSWORD, CREDENTIAL) for the guard and the subprocess filter | `audit_trail.py`, `runner.py` | `m6` | Not mutated |
| m8 | A cancelled tool call records `tool_call_finished` with `incomplete/CANCELLED`; reconciliation still examines it | `audit_trail.py`, `audit_reconcile.py` | `m8` | Not mutated |
| m9 | The reset extends and verifies the **copy** before the swap (schema failure gives `ResetError`; working copy untouched); reset and snapshot **hold** the run lock throughout | `mcp_server/maintenance.py` | `m9`; existing `g10`, `a18` | Swap before schema → **1 failed** |
| m10 (new, found during remediation) | `run_active()` no longer creates the lock file, so read-only checks leave no files. The empty `data/.run.lock` this audit's own check created was removed. | `mcp_server/locks.py` | `m10` | — |

**Changes to existing tests (required by the corrections, not loosened):**
- `test_c4_stdio_agent_launch_end_to_end` now supplies the M1 launch paths (`CC_HW5_ROOT`, `CC_WORKING_DB`). All of its assertions are unchanged. Without them, the corrected server rightly refuses to start.

### Verification results
| Check | Result |
|---|---|
| Full suite, project, 3 consecutive runs (before m10) | 430 / 430 / 430 passed |
| Full suite after m10, plus the saved verbose run | **431 passed** (`output/p5_remediation_tests.txt`: 401 earlier + 30 remediation) |
| Clean-clone copy (fresh venv from `requirements.txt`), remediated code | **431 passed**; verifier **21/21** |
| Problem 3/4 verifier (project) | **21/21** |
| Original database | `23686a90…f360a`, unchanged |
| Working database | `dbaf24cb…8ede`, unchanged (never reset or modified) |
| `output/audit_trail.json` | `ec8e507a…`, unchanged (1 event, consistent) |
| `output/mcp_smoke.json` | `64a0c1b3…`, unchanged |
| Live Portkey calls / agent runs | None |

### Remaining limitations (honest)
- **The offline network guard** intercepts host-name resolution and Python-level socket connects. A raw-IP connection made through a low-level path that bypasses `socket.socket.connect` (for example, Windows proactor `ConnectEx`) is not intercepted. The model **allow-list** is the primary control; the guard is defense in depth.
- **The limit defaults** (60,000 tokens, 1,200 output tokens per request, 300 s) are engineering choices recorded in every run. They should be confirmed or adjusted by Dr. Ryan before the first live agent run.
- **Not changed, as planned:** m5 (cash basis; one account), m7 (uncoded original-tool errors; by design), m10-reviewer (drafts on closed tickets), m11 (`verify_tools.py` environment).
- **The official assignment text** is still not in `hw5/`, and `hw5/` is still not under version control (audit items 1 and 4). These are outside this remediation.
