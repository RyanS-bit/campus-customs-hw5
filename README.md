# Homework 5: Campus Customs Multi-Agent Operations

AI Foundations for Managers. Dr. Ryan Suspanic.

**Status (2026-10-12): Problems 1–11 are complete.** Problem 11: this project is published at https://github.com/RyanS-bit/campus-customs-hw5 (also recorded in `output/github_url.txt`).

The repository root is the project's `hw5/` folder. Wherever this README says `hw5/`, use the root of your clone.

- **Built:** five PydanticAI agents (`gpt-6-luna` through Portkey); a 10-tool FastMCP server; a FastAPI backend with human-only approval routes; a React dashboard; and two audit records.
- **Run:** five live agent runs on tickets 101–103 (2026-10-10 and 2026-10-11) after one controlled reset.
- **Decided by a person:** six decisions; checking went from $3,400 to $160 through two recorded payments in the simulation (not real bank transfers).
- **Resolved:** all three tickets.
  - Ticket 102: business outcome confirmed.
  - Tickets 101 and 103: "simulated workflow complete; fulfilment not verified".
- **Written up:** the reflection is in the Reflection tab of `output/desk_tickets.html`.
- **Full system documentation:** `output/harness.md` §13.

## For a grader: view the completed project (changes nothing)

The final state is preserved in this folder. Viewing it reads files only.

1. **Open in a browser** (double-click; self-contained, no server needed):
   - `output/desk_tickets.html`: the Problem 6 Expected plans, the Problem 9 Actual sections and Cash tab, and the Problem 10 Reflection;
   - `output/resolved_board.html`: the three resolved tickets, with genuine final-state dashboard screenshots.
2. **Read:**
   - `output/resolved_tickets.json` (per-ticket outcome, agent contributions, human approvals, resolution type, runs, payments);
   - `output/harness.md` §13 (the final system and the run);
   - `output/design.md` (the dashboard);
   - `AI_prompts.md` (the verbatim prompt log, Problems 1–11).
3. **Evidence:**
   - `output/audit_trail.json` (append-only, hash-chained);
   - `output/p9_runs/` (each run's report, events and state);
   - `output/run_responses/` (full agent replies, tickets 102 and 103);
   - `output/p9_screenshots/` (PNGs plus `capture_record.json`);
   - `data/audit.db` (supplementary audit database).
4. **Optional: the live dashboard against the final data, read-only.** Start the backend and the dashboard as below, without `CC_LIVE_AUTHORIZATION`. The dashboard only reads (GET) unless a person submits a dialog. All proposals are already decided and all tickets resolved, so there is nothing to approve. The run buttons are disabled for resolved tickets, and live runs are disabled without the authorization variable.

`data/audit.db` holds the audit side of every decision and the resolution types of tickets 101 and 103. Without it, the dashboard still shows the tickets as resolved (from the working database) but cannot show their resolution type, and the decisions cannot be cross-checked against it.

## Offline checks (no API key, no model calls, no changes to the evidence)

**Supported Python:** 3.14. All results were produced with **CPython 3.14.7 on Windows 11**. Run from the `hw5/` folder:
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pytest tests -q
.venv/Scripts/python mcp_server/verify_tools.py
```
On macOS or Linux, use `.venv/bin/python`, and set `"command": ".venv/bin/python"` in `.mcp.json`.

- **Python tests** run on temporary copies only, behind an offline network guard. A session guard fails the run if a real database or the real audit trail changes. Some tests also *read* the final records and deliverables to check them (`tests/test_problem9_evidence.py`, `tests/test_problem9_harness_docs.py`).
- **The verifier** (`mcp_server/verify_tools.py`, 45 checks) runs two passes:
  - the current working copy, read-only;
  - a fresh copy of the original database, built in a temporary folder.
- **Dashboard checks:**
```bash
cd frontend
npm ci
npm run typecheck
npm test
npm run build
```
- **Claude Code:** open it in `hw5/` and approve the `campus-customs` server to use the MCP tools in development mode (read tools only; write tools refuse).

### Backend (Problem 7)

Start it from the `hw5/backend/` folder, with the project's virtual environment active, using the professor's command:
```bash
uvicorn main:app --reload --port 8000
```
- **Where it runs:** `http://localhost:8000`. Interactive API documentation is at `http://localhost:8000/docs`. Every route is listed in `output/harness.md` §11.1.
- **Port 8000 must be free:** stop any other server using it first.
- **Activating the environment:** on Windows, run `.venv\Scripts\activate` from `hw5/` first. Alternatively, run `..\.venv\Scripts\python -m uvicorn main:app --reload --port 8000` from `hw5/backend/`.
- **Agent runs are disabled by default.** They call `gpt-6-luna` through Portkey and cost money. The run route refuses unless the person starting the server sets `CC_LIVE_AUTHORIZATION` to a short authorization note, which is recorded in the audit trail.
- **LOCAL PROTOTYPE:** approvals record a typed name for attribution only. They are not an authenticated approval system, and a browser button does not prove who clicked it. Every POST must carry the header `X-Human-Decision: confirm`. This blocks accidental and cross-site requests, but it is not authentication.

### Dashboard (Problem 8)

React + Vite + TypeScript, in `frontend/`. Node.js 24 and npm 11 were used (Node 24.19.0, npm 11.17.0).

Install once (exact versions from `package-lock.json`), then start it after the backend:
```bash
cd frontend
npm ci
npm run dev
```
- **Where it runs:** `http://localhost:5173`. It calls the backend at `http://localhost:8000`; set `VITE_API_BASE` to use another address.
- **What it does:**
  - lists the three tickets;
  - runs the agent team on an open ticket, which the backend refuses unless live runs are authorized;
  - follows the run's recorded activity, captioned as historical;
  - lets a person approve, execute or reject proposals, and resolve a ticket, saying whether they confirm the business outcome or only that the simulated workflow is complete (`output/harness.md` §12.9);
  - shows the actual checking balance and the audit status.

  There is no reset button, deliberately. The design is in `output/design.md`.
- **Test fixtures:** `scripts/make_frontend_fixtures.py` regenerates them by recording real backend responses from a temporary database, using scripted offline agents.

## Reproducing a fresh run (deliberate and destructive: use a separate copy)

**Do not do this in the folder that holds the submitted evidence.**
- A reset replaces `data/campus_customs_new.db` with the original. That removes the recorded payments, proposals, drafts and resolutions, and restores $3,400 and three open tickets.
- New runs and decisions **append** to `output/audit_trail.json` and `data/audit.db`. They are never erased.

To reproduce, work in a separate clone or a copy of the whole `hw5/` folder:
1. **Reset (rule R8)** in that copy:
```bash
.venv/Scripts/python scripts/reset_db.py --confirm
```
   The script:
   - checks the original's SHA-256 (`23686a90…f360a`);
   - copies it to a staging file, refusing if the working copy is in use;
   - swaps the staging file into place;
   - adds the extension tables from `mcp_server/schema_ext.sql`;
   - confirms that the nine original tables match the original.

   Without `--confirm` it only shows what it would do. After a reset, the working copy's SHA-256 differs from the original's because of the added tables; that is expected.
2. **Live runs** need a Portkey key (`PORTKEY_API_KEY`; see `.env.example` and `backend/config.py`) and `CC_LIVE_AUTHORIZATION` set when starting the backend. Each run uses at most 40 model requests and 60,000 tokens (`backend/models.py` `RunBudget`). Agent behaviour varies between runs, so a fresh run is not expected to repeat ours exactly.
3. **Decisions and resolutions** are made by a person in the dashboard.
4. **Optional:** set `CC_RUNTIME_DIR` (in `.env`) to an absolute folder outside OneDrive to keep the working copy and `audit.db` there. `scripts/snapshot_runtime.py` copies the runtime working copy back into `data/campus_customs_new.db`.
5. **Problem 9 deliverables:** `scripts/build_p9_evidence.py` regenerates them from the records. It refuses to overwrite existing outputs unless given a folder of verified preserved copies (`--replace-preserved`).

## Model usage (confirmed by Dr. Ryan, 2026-10-08)

| Role | Model | Notes |
|---|---|---|
| **Development assistant** (coding, documentation, debugging) | Claude Opus 5.5 | The vibe coder used to build the project. Not part of the running system. |
| **The five operational PydanticAI agents** (Boss, Inventory, Accounting, Facilities, Customer Service) | `gpt-6-luna` through Portkey, using `PORTKEY_API_KEY` | Required by the assignment. Defined once, in `backend/config.py`, with `openai_reasoning_effort: "none"` (after live attempt 1 failed; `output/harness.md` §12.1). |

- The MCP server makes no model calls.
- The key is read from the process environment or a `.env` file, which is never committed.

## Two kinds of evidence

- **Offline verification:** `mcp_server/verify_tools.py` and the `tests/` suite call the tools and backend from Python, with no model calls. They show the code works.
- **Live evidence:**
  - real tool calls by the vibe coder through the registered MCP server (`output/mcp_smoke.json`, Problem 4; `output/mcp_live_regression_p5.json`);
  - the five live agent runs (`output/audit_trail.json`, `output/p9_runs/`, `output/run_responses/`).

  These are reported separately and never mixed with offline results.

## How the business rules are enforced in code

1. **Human approval (R2):** agents can only propose and draft. Approving, executing, rejecting and resolving go only through `mcp_server/approval.py`, reached by the backend's human routes, never by an agent tool.
2. **Sufficient funds (R1):** a payment or purchase is refused if it would make the balance negative, checked at the moment of writing.
3. **Consistent updates (R4):** the payment row, the cash balance, the related invoice or lease, and the `audit.db` event change together in one SQLite transaction.
4. **Vendor blocks (R3):** shown when a purchase is proposed; purchase execution is refused while the vendor has an open invoice.
5. **Working copy only:** writes go only to `data/campus_customs_new.db`, never to the original.

## Audit records

- **`output/audit_trail.json`:** the required record of agent and human activity. It is append-only and hash-chained; resets append system events and never erase it.
- **`data/audit.db`:** supplementary. It records each database change in the same transaction as the change, and holds the resolution types.
- **To check both, and their links** (read-only by default):
```bash
.venv/Scripts/python scripts/verify_audit.py
```

## Development principles

1. **One problem at a time.** We move to the next problem only after Dr. Ryan reviews the current one and explicitly says to proceed.
2. **Student-led instructions.** Dr. Ryan introduces each problem in their own words. Claude helps implement, explain, test, and refine.
3. **Accuracy.** No invented database values, financial figures, agent actions, test results, or evidence.
4. **Reproducibility.** The documented instructions must be enough to reproduce every result.
5. **Traceability.** Design decisions, changes, tests, and agent actions are recorded in the right files. `AI_prompts.md` is the authoritative prompt record.
6. **Security.** Real API credentials and `.env` secrets are never committed.
7. **Human oversight.** Agents may prepare financial transactions, but a human approves before any payment or purchase.
8. **Efficient model usage.** Offline validation wherever it fits; every live run separately authorized.

## Project structure

| Item | Created | Purpose |
|---|---|---|
| `AI_prompts.md` | Problem 1 | Verbatim prompt log, one section per problem, with Claude's notes |
| `README.md` | Problem 1; updated for the final state | This file |
| `.gitignore` | Problem 1 | Keeps `.env` secrets, caches, the virtual environment, build output and local `.claude/` configuration out of Git |
| `.gitattributes` | Problem 11 | Disables line-ending conversion so every checkout is byte-identical to the fingerprinted evidence |
| `CLAUDE.md` | Problem 4 | Homework 5 conventions for any Claude Code session opened in `hw5/` |
| `.mcp.json` | Problem 4 | Registers the `campus-customs` MCP server with Claude Code |
| `.env.example` | Problem 5 | Placeholders only: optional `CC_RUNTIME_DIR`, and the Portkey settings |
| `requirements.txt` | Problem 3 | Pinned Python dependencies |
| `data.zip` | Supplied | The original archive containing `data/campus_customs.db` |
| `data/campus_customs.db` | Problem 2 | **Original** database: read-only, never modified (SHA-256 `23686a90…`) |
| `data/campus_customs_new.db` | Problem 2 | Working copy, in its **final Problem 9 state** ($160; two payments; three resolved tickets) |
| `data/audit.db` | Problem 5 | Supplementary hash-chained audit database (final Problem 9 state) |
| `data/backups/` | Problems 5 and 9 | Read-only copies of the working copy taken before each controlled reset |
| `mcp_server/server.py` | Problem 3; expanded in Problem 5 | FastMCP server: 10 tools (6 read, 4 propose/draft), per-agent access, identity fixed at launch |
| `mcp_server/README.md` | Problem 3 | Server purpose, tools, setup, verification |
| `mcp_server/verify_tools.py` | Problem 3; two passes since Problem 9 | Offline verification (45 checks) |
| `mcp_server/paths.py`, `maintenance.py`, `schema_ext.sql` | Problem 5 | Path resolver (refuses writes to the original), reset and snapshot, extension tables |
| `mcp_server/ops.py`, `authz.py`, `audit_db.py`, `audit_schema.sql`, `policies.py` | Problem 5 | Proposal and draft operations, restricted write connection, `audit.db`, labelled policies |
| `mcp_server/audit_trail.py`, `audit_reconcile.py`, `locks.py` | Problem 5 | Trail writer and run recorder, reconciliation, OS file locks |
| `mcp_server/run_responses.py` | Problem 9 | Full agent replies per run, reconciled with the trail |
| `mcp_server/approval.py` | Problem 7 | The single human decision path: approve, execute purchase, reject or withdraw, resolve (two resolution types), controlled reset |
| `mcp_server/desk_reads.py` | Problem 7 | Read-only queries behind the backend's GET routes |
| `backend/config.py` | Problem 5 | **The one place the operational model is defined:** `gpt-6-luna` through Portkey; rejects any other model |
| `backend/models.py` | Problem 5 | `AgentReport`, `RunDeps`, `RunBudget` (run limits), `BudgetExceeded` |
| `backend/agents.py` | Problem 5 | Builds the five agents |
| `backend/delegation.py` | Problem 5 | All-to-all `delegate`, with cycle, depth, budget and shared-usage safeguards and delegation IDs |
| `backend/runner.py` | Problem 5 | Runs a task: one MCP server per agent, audit hook on every tool call, run lock, live/simulated guards, full-response file |
| `backend/prompts/*.md` | Problem 5 | One prompt per agent: role, tools, shared rules |
| `backend/main.py` | Problem 7 | FastAPI backend (routes only; no SQL or business rules). Routes in `output/harness.md` §11.1 |
| `frontend/` | Problem 8 | The dashboard (`src/`), with `package.json` and `package-lock.json`; tests in `src/test/` with recorded backend fixtures |
| `scripts/reset_db.py`, `snapshot_runtime.py`, `verify_audit.py` | Problem 5 | Reset (R8), runtime snapshot, audit check |
| `scripts/live_connectivity_check.py` | Problem 5 | The single authorized pre-run connectivity check (no tools) |
| `scripts/stage2_evidence.py`, `stage3_evidence.py` | Problem 5 | Offline evidence generators (temporary databases) |
| `scripts/make_frontend_fixtures.py` | Problem 8 | Records the dashboard test fixtures (temporary database, scripted agents) |
| `scripts/build_p9_evidence.py` | Problem 9 | Builds the Problem 9 deliverables (and inserts the Problem 10 reflection) from the records; refuses unsafe overwrites |
| `tests/` | Problems 5–10 | Offline pytest suite (temporary copies; see "Offline checks") |
| `output/harness.md` | Problem 2; completed in Problem 9 | Database study (§1–8), MCP tools, backend routes, Problem 9 corrections, and the final system (§13) |
| `output/design.md` | Problem 8 | Dashboard design, including the Problem 9 changes |
| `output/desk_tickets.html` | Problems 6, 9, 10 | Expected plans (Problem 6, unchanged), Actual sections and Cash tab (Problem 9), Reflection (Problem 10) |
| `output/p6_desk_tickets_original.html` | Problem 6 | The Problem 6 file as it was before Problem 9; the generator's template (SHA-256 checked) |
| `output/p10_reflection.html` | Problem 10 | Source of the Reflection tab |
| `output/resolved_tickets.json`, `output/resolved_board.html` | Problem 9 | Final outcomes per ticket; the board with the three final-state screenshots |
| `output/github_url.txt` | Problem 11 | The URL of this public repository |
| `output/p9_screenshots/` | Problem 9 | Three unedited final-state PNGs; `capture_record.json` (corrected) and the original record with its error |
| `output/p9_deliverables_v1/`, `output/p9_deliverables_v2/` | Problems 9, 10 | Byte-identical earlier versions of the deliverables, with `SHA256SUMS.txt` |
| `output/audit_trail.json` | Problem 5 | **Required audit trail:** run, tool, delegation and human-decision events |
| `output/p9_runs/` | Problem 9 | Gate and readiness records, each live run's report, events and after-run state (ticket 103's reconstructed and labelled), incident report, check outputs, verification screenshots |
| `output/run_responses/` | Problem 9 | Full agent replies for the ticket 102 and 103 runs |
| `output/mcp_smoke.json`, `output/mcp_live_regression_p5.json` | Problems 4, 5 | Live vibe-coder MCP evidence |
| `output/p5_*`, `output/p7_*`, `output/p8_*` | Problems 5–8 | Architecture, tool specification, test outputs and audits for those problems |
| `output/p9_preflight_plan.md`, `p9_compliance_review.md`, `p9_f3_purchase_design.md`, `p9_ticket101_assessment.md`, `p9_ticket101_decision_brief.md`, `p9_integrated_decision_analysis.md`, `p9_verify_tools.txt` | Problem 9 | Planning, reviews, decision briefs and the verifier output |
| `output/hw5_compliance_audit.md`, `output/hw5_roadmap_p6_p11.md`, `output/pre_p6_acceptance_audit.md` | Problems 5, 9 | Requirement audits and the Problems 6–11 roadmap |
