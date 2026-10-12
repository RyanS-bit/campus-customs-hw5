# Homework 5 compliance audit (Problem 9, follow-up 28)

- **Prepared:** 2026-10-11, read-only. No code, database, audit record, evidence, proposal or ticket was changed.
- **Requirements source:** Dr. Ryan's consolidated checklist (follow-up 28). The official assignment text is not in `hw5/`.
- **Statuses:**
  - **Verified:** checked directly in files, the database, code or a test run during this audit.
  - **Partial:** verified in part; the remaining part is missing or unconfirmed.
  - **Incomplete:** required work not yet done.
  - **Not independently checked:** stated, but not checkable from inside the project.

## Checks run during this audit

- **Database:** opened read-only (`mode=ro`).
  - Original SHA-256 `23686a90…`, matching `paths.ORIGINAL_SHA256`.
  - 9 original tables.
- **Audit:**
  - `audit_reconcile.check`: consistent, no gaps, no run active.
  - Trail: 152 events, chain valid.
  - All 22 fingerprints identical to the follow-up 25 baseline.
- **Tests:**
  - `mcp_server/verify_tools.py`: 21/21.
  - Frontend: `tsc` exit 0; Vitest 22/22.
  - Backend: full `pytest` **574 passed**. All runs use temporary copies; the session guard confirms the real files are untouched.
- **Services:** backend HTTP 200; dashboard HTTP 200.
- **Code search:**
  - business dates come only from `desk.date_today`;
  - `lead_days` comes from `vendors`;
  - no code raises a cash balance;
  - no network-sending code (SMTP or HTTP posts);
  - one model pattern (`gpt-6-luna`).

## A. Shop operating rules

| # | Rule | Evidence | Status |
|---|---|---|---|
| 1 | Date from `desk.date_today` | `ops.py:162`, `approval.py:245`, `desk_reads.py`. No real clock is used in business code. | Verified |
| 2 | Lead times from the database | `ops.py:392` (`vendor["lead_days"]`); `server.py` selects `v.lead_days` | Verified |
| 3 | No shipment while an invoice is unpaid (R3) | `ops.py` R3 warning; `approval.py` `_vendor_blocking` refuses purchase execution | Verified |
| 4 | Human approval for every payment | No agent has an approve, execute, reject or resolve tool (`server.ACCESS`). Routes need the `X-Human-Decision` header plus a name and confirmation. Tests p7_14, p7_16, p7_17. | Verified |
| 5 | Refuse insufficient cash; never negative | `_execute_payment` refuses; the SQL guard `balance - ? >= 0` | Verified |
| 6 | Outgoing cash only; no revenue | No code increases a balance; quotes are labelled "no revenue (R6)" | Verified |
| 7 | Preserve the original; work in `_new.db` | Original is read-only, hash matches; `paths.assert_writable` | Verified |
| 8 | Reset before the full run | One reset, trail events 2–3 (2026-10-10 20:36:42 UTC, requested by Dr. Ryan). Opening balance $3,400 recorded in `g1_reset_record.json`. No later reset. | Verified |
| 9 | No real messages | Drafts only; no sending code | Verified |
| 10 | `gpt-6-luna` via Portkey only | `config.py` `REQUIRED_MODEL`, `MODEL_PATTERN`; recorded on every run | Verified |
| 11 | Facts from the database; labels | Tool outputs label computed values; the briefs label Fact, Estimate and Assumption | Verified (sampled) |

## B. Problem by problem

| P | Requirement | Where | Evidence | Missing or incomplete | Extra (not required) | Smallest correction | Status |
|---|---|---|---|---|---|---|---|
| 1 | `AI_prompts.md` by problem, prompts verbatim, reasons for follow-ups | `AI_prompts.md` | 11 problem headings; 85 prompt entries; 77 "Why a follow-up" lines | Problems 10 and 11 have no prompts yet (not started) | "Claude's notes" sections | None now | **Verified** to date |
| 2 | Inspect tables; keep the original; working copy; document tables and ticket links | `harness.md` §1–§8 | All 9 tables documented; §4 connections; §5 tickets | — | §7a/§7b rule tables | None | **Verified** |
| 3 | FastMCP server; 3 initial tools tied to tickets; tables, purpose and ticket per tool; README; no fabricated data | `mcp_server/server.py`, `harness.md` §9, `mcp_server/README.md` | 3 tools in a table with tables, ticket and purpose; `verify_tools` 21/21 | — | 7 more tools (Problem 5) | None | **Verified** |
| 4 | Configured in the vibe coder; 3 tools tested live; prompts, tool names and results in `mcp_smoke.json` | `.mcp.json`, `output/mcp_smoke.json` | 6 real calls (3 tools, an error test, a recovery call), unedited outputs, a SQL cross-check | **No prompt text in the smoke file.** The prompts are only in `AI_prompts.md` (Problem 4). | Error-handling test; cross-check | Add a `prompts` field quoting the Problem 4 prompts verbatim (documentation only) | **Partial** |
| 5 | 5 agents with role prompts, tools and all-to-all delegation; MCP-only data; append-only trail; harness documents every agent, tool, table, safety control and limit; README current | `backend/`, `backend/prompts/`, `mcp_server/`, `output/audit_trail.json` | Tests a6 (all pairs), a21 (no SQLite in agent code); trail chain valid; limits in §12.5 | **Harness:** no per-agent section; 7 of the 10 tools not documented there (they are in the README and `p5_mcp_tools_spec.md`); extension tables and `audit.db` only partly covered. **MCP README:** omits `approval.py`, `desk_reads.py`, `run_responses.py`. | `audit.db`, per-agent launch identity, budgets, full-response log | Add one harness section (agents, 10 tools, extension tables, `audit.db`, limits) and 3 README rows. This is the planned D-H. | **Partial** |
| 6 | `desk_tickets.html`: tabs 101–103, Cash, Reflection; ticket-specific Expected plans kept | `output/desk_tickets.html` | All 5 tabs; Expected plans name the first specialist, the delegation chain, tools, who is not involved, and the end state | Actual, Cash and Reflection are placeholders. These belong to Problems 9 and 10. | — | Completed in Problems 9 and 10 | **Verified** (Problem 6 scope) |
| 7 | Routes: tickets, run, activity, human approval and execution with cash change, balance, reset; documented | `backend/main.py`, `harness.md` §11 | 14 routes; tests p7 (49); payment approval moves cash (p7_14; follow-up 25 simulation $3,400 → $1,000); purchase cash moves on `execute` | §11.9 says "No live run yet" (stale) | Two-step purchase, commitments reserve, decision frame, full-response route | Update the §11.9 bullet | **Verified** (one stale line) |
| 8 | React/Vite/TS; 3 tickets; start runs; activity; summaries; resolution after the run; approval controls; cash changing after payments; `design.md` | `frontend/src/`, `output/design.md` §1–§13 | Vitest 22/22; tsc 0; dashboard live and showing $3,400, 2 awaiting, Consistent | **Cash changing live** has not yet happened (no payment yet; fixture-tested only). **Resolution** is human-confirmed, not automatic (see C below). | Next-steps panel, purchase two-step view, history versus current | None now. Approving #2 demonstrates the live cash change. | **Partial** (live cash change pending) |
| 9 | Run from a reset with the opening balance; process 101, 102, 103 to defensible outcomes; Actual sections; Cash tab; `resolved_tickets.json`; `resolved_board.html` with real screenshots; append-only audit; complete harness | `output/p9_runs/`, `output/run_responses/` | Reset and opening balance verified. 101: 3 runs (attempt 1 error, 2 completed with no actions, 3 proposal 1 + draft 1). 102: 1 run (proposal 2 + draft 2; duplicate refused, event 132). Audit consistent. | **103 not run.** No decisions yet. Actual sections, Cash tab, `resolved_tickets.json`, `resolved_board.html`, screenshots, evidence script and harness completion **not produced**. | Full-response evidence; per-attempt evidence files | Continue the planned sequence (see the priorities list) | **Incomplete** (in progress) |
| 10 | Reflection tab: per ticket, Actual against Expected, one-agent comparison, 3 solvable and 3 unsolvable problems, grounded | `desk_tickets.html` `panel-reflection` | Placeholder | Everything | — | Write it after Problem 9 | **Incomplete** (not started) |
| 11 | Public repository with code, both databases, config examples, prompts, docs, evidence; no secrets; README covers setup, reset, MCP, backend, frontend and the three-ticket workflow; `output/github_url.txt` | `.gitignore`, `.env.example`, `README.md` | `.env` ignored; the key lives in the course-root `.env`, outside `hw5/`; README has Quick start, Backend, Dashboard and reset | **No `output/github_url.txt`.** `hw5/` is untracked in the course repository (`RyanS-bit/ai-foundations`, private), so no public repository yet. README has no three-ticket workflow section, and its "status as of Problem 5" heading is stale. `data/audit.db` is git-ignored. | — | At submission: create the public repository, add a README workflow section, record the URL, and decide whether `audit.db` should be committed | **Incomplete** (not started) |

## C. Operational statements (Dr. Ryan's list), checked

| Statement | Evidence | Status |
|---|---|---|
| Baseline $3,400 | G1 record `opening_balance_cash_accounts` = 3400.0 | Verified |
| 101 Attempt 3: pending $840 Bulldog proposal and unsent customer draft | Proposal 1 `proposed`, invoice 501, run `84270c9b`; draft 1 to Tauhid Zaman, status `draft` | Verified |
| 102 Attempt 1: pending $2,400 rent proposal and unsent landlord draft | Proposal 2 `proposed`, lease 1, run `f2412172`; draft 2 to Elm City Properties | Verified |
| Duplicate rent proposal refused | Trail event 132, `DUPLICATE` | Verified |
| Recorder introduced before 102, responses kept | One response file only, for `f2412172`; reconciliation consistent | Verified |
| 103 not run | No `run_started` event with ticket 103 | Verified |
| No payments | `payments` has 0 rows | Verified |
| All tickets open | 101, 102, 103 `open` | Verified |
| Audit and evidence consistent | `audit_reconcile.check` consistent; 22 fingerprints unchanged | Verified |
| Services restarted and verified; no financial decision authorized | Both HTTP 200; no decision events after proposal creation | Verified |

## Resolution workflow (Problem 8 question)

- **The two recorded requirements differ.**
  - Problem 7 (Dr. Ryan's own requirements): "Do not automatically mark a ticket resolved merely because the agent run finishes."
  - Problem 8 (summary): the dashboard "marks tickets resolved when runs finish."
- **Our dashboard:** when a run finishes, it presents "Next: your decisions", and marks the ticket Resolved as soon as the person confirms (`design.md` §12).
- **Ambiguity:** yes, a literal one. Resolution follows the run, but it is not automatic.
- **Risk:** low to moderate, because the reason is documented and it is consistent with the Problem 7 instruction.
- **Correction:** none needed in code. Quote both statements and the interpretation in `resolved_board.html` and `harness.md`, so a grader sees the decision.

## Genuine grading risks (in order)

1. **Problem 9 deliverables not yet produced, and Ticket 103 not run.** This is the largest risk, but it is planned work, not a defect.
2. **"Three resolved tickets."** If 101 or 103 cannot honestly be resolved (unknown delivery, unknown hoodie supplier, no customer acceptance), the board will show an open ticket. Mitigation: the documented model definition of resolution, with the unverified items listed. Never fabricate.
3. **Problem 11 not started:** no public repository and no URL file.
4. **Harness completeness (Problems 5 and 9)** and small stale lines (§11.9; the README status heading; one pronoun in `p9_preflight_plan.md`).
5. **Problem 4:** the smoke file lacks the prompt text. Minor.

**Optional enhancements (not needed for compliance; avoid unless wanted):**
- storing full tool results;
- draft editing;
- a goods-received tool;
- automatic resolution.

## Prioritized remaining work

1. **Decide proposal #2 (rent).** Nothing in this audit blocks it.
2. **Clarify and decide proposal #1 (#501).**
3. **Ticket 103:** readiness, run, decisions.
4. **Resolve tickets** where defensible. Capture the final-state dashboard screenshots (D7).
5. **Evidence (G5):** Actual sections, Cash tab, `resolved_tickets.json`, `resolved_board.html`, with the resolution interpretation stated.
6. **Harness completion (D-H)** and MCP README rows. Fix the stale §11.9, the README status and the pronoun.
7. **Problem 10:** reflection.
8. **Problem 11:** public repository, README workflow section, `github_url.txt`, secrets check.
9. **Optional:** a `prompts` field in `mcp_smoke.json`.

## Recommendation on proposal #2

**Proceed now.** None of the gaps depends on the order of decisions.
- Approving #2 is consistent with R1, R2 and R4.
- It produces the live cash-change evidence that Problem 8 still lacks, and the Ticket 102 line for the Cash tab.
- Optional: take a dashboard screenshot just before approving, to document the "before" state.
