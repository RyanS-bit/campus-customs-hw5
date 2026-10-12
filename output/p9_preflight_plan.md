# Problem 9: Preflight and Execution Plan (for review; nothing executed)

Prepared 2026-10-10 under Dr. Ryan's preparation-only authorization (Problem 9 prompt). No reset, live run, model call, decision or resolution has been made.

**Revision 2 (Problem 9, follow-up 1).** Dr. Ryan approved the planning approach and the ticket order 101 → 102 → 103, subject to verification against the official assignment document. Five clarifications are now binding:
- **C-1 Reference document.** *(Superseded in revision 3.)* The original assignment document will not be provided. Dr. Ryan's own Problem 9 execution specification is the reference (follow-ups 2 and 4; §0 below).
- **C-2 Run policy.** One live run per ticket. A technical re-run needs Dr. Ryan's explicit approval. Unsuccessful or interrupted runs are recorded and reported, never hidden.
  - **Revised (Problem 9, follow-up 12, F4):** additional runs are permitted when necessary to resolve a ticket, including after documented corrections. Each needs Dr. Ryan's separate authorization, and all earlier runs and evidence are preserved and reported. No reruns are made to obtain a preferred business decision. See `harness.md` §12.4.
- **C-3 Run versus resolution.** A completed agent run is not a resolved business issue.
  - **Ticket 101:** paying invoice #501 is not proof of delivery.
  - **Ticket 103:** approving a quote is not customer acceptance or confirmed supply.
  - Resolution decisions are Dr. Ryan's, based on the evidence actually available.
- **C-4 Evidence generation.** The approach in §7 is approved, but it is **not to be executed yet**: the script is not run and the Actual sections are not modified.
- **C-5 Financial decisions.** Every financial approval is Dr. Ryan's decision. **Nothing is pre-authorized:** not the $840 vendor payment, not the $2,400 rent, not any purchase or quote approval.

**Revision 3 (Problem 9, follow-up 4).** Compared with Dr. Ryan's specification, *Campus Customs | Problem 9: Independently authored execution specification for Dr. Claude* (2 pages). The specification is now the reference. Differences are resolved in §0, and §8 is the final checklist.

---

## 0. Specification comparison (revision 3)

| Spec item | Plan before | Result |
|---|---|---|
| **§1** Reset only `campus_customs_new.db` with the approved mechanism; preserve the original and the audit history | Controlled reset via `POST /api/reset` | ✅ Matches |
| **§1** *Record the opening checking balance immediately after the reset* | Only "confirm $3,400" | **Added:** `GET /api/cash` saved right after the reset (B2) and used as the reconciliation's starting figure |
| **§1** Process tickets *through the React board*, 101 → 102 → 103, pausing for human decisions | Order and pauses planned; how runs start not specified | **Added:** runs are started with the board's "Run agents" button, and decisions and resolutions are entered through its dialogs |
| **§1** Record what was observed, not anticipated; do not hide failed or interrupted runs; retries need separate authorization; completion ≠ approval ≠ resolution | C-2, C-3 | ✅ Matches |
| **§2** Actual sections: name the agents, delegations and MCP tools actually used; keep Expected | Planned from recorded events | ✅ Matches (Expected stays untouched) |
| **§2** Cash tab: (a) starting checking after reset; (b) per ticket, *the particular payment or purchase and amount, including $0*; (c) ending equals `cash_accounts` | Reconciliation planned | **Refined:** one line per ticket. It lists each executed payment, each approved purchase (always $0 cash in this system: approval places no order and creates no invoice) and "$0, no cash change" where nothing executed. The ending figure must equal `cash_accounts` or generation stops. |
| **§2** `resolved_tickets.json`: ticket ID, final status, concise outcome, each agent's contribution, human approvals; open tickets stated truthfully | Richer draft schema | **Aligned:** these five fields are required. Our extras are added under clearly named keys: run IDs, payment IDs, and "unverified" items. |
| **§2** `resolved_board.html`: self-contained, double-clickable, **a screenshot of the actual React board for each ticket in its final state**; open tickets labelled | Planned as a generated board plus screenshots | **Changed:** the page's core is three real screenshots of the dashboard (one per ticket, final state), embedded as base64 images so one file opens anywhere. Each is captioned and labelled "OPEN" or "RESOLVED" from the database, not from the image. |
| **§2** `audit_trail.json`: append genuine events; never rewrite | Built that way (Problem 5) | ✅ Matches |
| **§2** `harness.md`: finish the system documentation (tables, MCP tools, five agents, API routes, dashboard, safety and business rules) | Not in the plan | **Added (new deliverable D-H).** The gaps: a five-agents section, a dashboard section, the extension tables (`pending_actions`, `drafts`, `reset_stamp`) and `audit.db`, an up-to-date ten-tool MCP list, and one consolidated safety and business rules section. It is system documentation, not results, so it can be written before or after the runs; that is Dr. Ryan's choice. |
| **§3** Starting facts to verify | Verified 2026-10-10 (§2) | ✅ Match. Re-verified after the reset (B2), including the product name "Classic Bulldog Tee". |
| **§4** Database authoritative; proposals, estimates, unexecuted approvals, unsent quotes and sales are not cash; reconciliation; attribute each payment to ticket, action, approval and audit event; no predetermined choices | §4, §7, C-5 | ✅ Matches. Attribution: `payments.id` → `pending_actions.payment_id` → `ticket_id`, `decided_by` → `audit.db` `payment_executed` (decision ID) → JSON `human_decision_*`. |
| **§5** Only `gpt-6-luna`; credentials confirmed without values; budgets, MCP, logging, reconciliation | §5 | ✅ Matches. Re-confirmed at B1. |
| **§5** No bypassing of the safeguards; nothing sent, ordered or assumed; resolution deliberate and documented | Built-in; C-3 | ✅ Matches |
| **§6** The five separate approvals | §8 list | ✅ Matches. The decision-entry method is also asked (Dr. Ryan clicks, or states it for Claude to enter). |

Nothing in the specification conflicts with the existing system or with rules R1–R8.

---

## 1. Assignment checklist

**Source caution.** The official Problem 9 text is not in `hw5/`. Items marked **[A]** come from the official rules supplied earlier (R1–R8, `harness.md` §7a) or from deliverables named in Dr. Ryan's Problem 9 prompt. Items marked **[O]** are our own choices. Formats marked **[?]** need the official wording.

| # | Deliverable | Source | Status / how produced |
|---|---|---|---|
| 1 | Reset the working database before the complete run | [A] R8 | Planned: one controlled reset (§5, step 2) |
| 2 | Three real ticket runs (101, 102, 103) by the five-agent team on `gpt-6-luna` | [A] | Planned: one live run per ticket (§3) |
| 3 | Human approval for payments and purchases; agents only prepare them | [A] R2 | Dashboard decisions by Dr. Ryan (§4, §6) |
| 4 | Actual sections of `output/desk_tickets.html` (tickets 101–103) | [A] | Generated from recorded data (§7); placeholders today |
| 5 | Cash tab: starting balance, cash changes per ticket, ending balance | [A] | Generated reconciliation (§7) |
| 6 | `output/resolved_tickets.json` | [A] format **[?]** | Generated; schema proposed in §7 |
| 7 | `output/resolved_board.html` and screenshot evidence | [A] format **[?]** | Generated board, plus dashboard screenshots (§7) |
| 8 | Agent audit trail `output/audit_trail.json` | [A] (Problem 5) | Written by the runner; verified (§7) |
| 9 | Reproducibility documentation | [A] (assignment architecture) | README and `harness.md` update after the runs |
| 10 | Separate `audit.db` record, reconciliation, `GET /api/audit` | [O] | Already built (Problem 7) |
| 11 | Reset stamp, decision IDs, evidence-builder script, before/after fingerprints | [O] | §5, §7 |

---

## 2. Verified starting state (read-only, 2026-10-10)

| Item | Value | Source |
|---|---|---|
| Business date | 2026-08-31 | `desk.date_today` |
| Tickets | 101 open (customer order, Tauhid Zaman), 102 open (rent notice, Elm City Properties), 103 open (price override, Yale AI Club) | `tickets` |
| Checking balance | $3,400.00 (as of 2026-08-31) | `cash_accounts` |
| Tee CC-TEE-WHITE | S 0, M 5, L 3, XL 2; unit cost $8, list price $28 | `inventory`, `pricing` |
| Hoodie CC-HOOD-NAVY | S 4, M 8, L 14, XL 6; unit cost $22, list price $58 | `inventory`, `pricing` |
| Invoice #501 | Bulldog Print Co, $840, due 2026-08-28 (3 days overdue), open, "Rush reprint CC-TEE-WHITE S"; delivery **not recorded** | `invoices` |
| Vendor restriction | Bulldog Print Co (apparel reprint, 5 days lead time) ships nothing new while #501 is open (R3). Other vendors: Elm City Gifts (mugs and small goods), QuickShip CT (courier). **No vendor is recorded as supplying navy hoodies.** | `vendors` |
| Lease | #1 Chapel Street shop, Elm City Properties, $2,400, next due 2026-09-02 (2 days after the business date) | `leases` |
| Proposals, payments, drafts | 0, 0, 0 | extension tables |
| Agent audit trail | 1 event (`trail_initialized`, 2026-10-09). No runs, decisions or resets recorded since. | `output/audit_trail.json` |
| `audit.db` | Does not exist yet; it is created on the first write | `data/` |
| Run lock | Not held | `data/.run.lock` |
| Working copy vs original | The original tables' **content is identical** to the original. The copy is unchanged since the Problem 5 Stage 3 reset (CLI reset, so it has no reset stamp). | `content_digest` |

**Fingerprints (SHA-256) before Problem 9:**
- `data/campus_customs.db` (original): `23686a90d698f7fa6f901a3f0a9774db9cbb31e13151c9e5717275f3321f360a`
- `data/campus_customs_new.db` (working): `dbaf24cb083ec7b3f41420b18f59026eaafbaa511f233c2ed181557076228ede`
- `output/audit_trail.json`: `ec8e507a06a123b99a976992693820669c6b7971f05ad1a1e26e82566ca1c75e`
- `output/mcp_smoke.json`: `64a0c1b364cd49f4f09020088ab26ff9638a426201be6868ec4e55a1a7521f08`

**Reset (R8).** R8 requires a reset before a complete ticket-resolution run, so we do one, even though the content already matches the original.

| What it changes | What it preserves |
|---|---|
| Replaces `campus_customs_new.db` with a verified fresh copy of the original, plus the extension tables, plus a `reset_stamp` row with a new `reset_id`. The original-table content stays identical; the file hash changes. | The original database (hash checked before and after). The whole `audit_trail.json` (it gains `db_reset_requested` and `db_reset` events). `audit.db` gains `db_reset_started` and `db_reset_finished` boundary events. `data/backups/` is untouched. |

It is done through `POST /api/reset` (needs `confirm: "RESET"` and `requested_by`), so it is recorded and stamped. Optional extra: copy the current working file into `data/backups/` first. It holds no data beyond the original, but this keeps a byte-level record.

---

## 3. Proposed ticket sequence

**Order: 101, then 102, then 103. Each run is followed by its human decisions, before the next run starts.**

Why this order:
- Ticket 101 contains the invoice #501 question. Its outcome (paid or not) changes the facts for 103 (whether Bulldog is blocked).
- 102 is independent but time-sensitive: rent is due in 2 days.
- Deciding after each run is how a real desk works, and each later run then sees the true current state. The alternative (run all three first, then decide) gives every run the starting state, but later proposals may then rest on outdated facts.

**Run policy (confirmed by Dr. Ryan, C-2):**
- One live run per ticket.
- A run that fails for *technical* reasons (network or Portkey) may be repeated only with Dr. Ryan's explicit approval.
- Every unsuccessful, stopped or interrupted run is recorded and appears in the evidence alongside any re-run.
- No re-runs to "improve" an outcome. What the agents do is the evidence.

These are expectations, not predictions; the real runs decide.

### Ticket 101: Bulldog tee, size S, 1 unit (Tauhid Zaman)

| | |
|---|---|
| **Problem** | Size S is out of stock. The only restock record is invoice #501 ("rush reprint", $840, overdue, unpaid, so Bulldog is blocked). Delivery of the invoiced tees is not recorded. |
| **First agent** | Inventory: stock and vendor status are its responsibility. |
| **Others** | Accounting for the overdue invoice and payment proposal. Optionally Customer Service for an unsent reply to the customer. |
| **Tools** | `get_ticket`, `check_inventory`, `list_vendors`, `get_financial_position`, `list_pending_actions`, `propose_payment` (Accounting), `draft_message` (Customer Service) |
| **Rules** | R1 (fundable: $840 ≤ $3,400), R2, R3, R4, R5 (overdue), R7 |
| **Possible output** | A payment proposal for #501. A customer draft. Inventory should **not** propose a second reprint while fulfillment of #501 is unknown. |
| **Human decisions** | Approve or reject the #501 payment. Review the draft (it is never sent). |
| **Successful run** | The facts are read correctly; a payment proposal at most; no second order; refusals reported; the run completes within budget. |
| **Resolved when** | A person confirms the customer's need is actually met (for example a tee was supplied, or the customer agreed to a backorder). **Paying #501 is not proof of delivery (C-3).** The system cannot show delivery, so this rests on Dr. Ryan's judgment of the evidence actually available. **Leaving 101 open may be the honest outcome.** |
| **Evidence** | `run_id`, the run view, all events, the proposal and decision IDs, the payment ID, cash before and after, screenshots |

### Ticket 102: Rent notice (Elm City Properties)

| | |
|---|---|
| **Problem** | $2,400 rent on lease #1 is due 2026-09-02. |
| **First agent** | Facilities (lease and landlord), then Accounting (payment). |
| **Tools** | `get_ticket`, `get_financial_position`, `list_pending_actions`, `draft_message` (Facilities), `propose_payment` (Accounting) |
| **Rules** | R1 ($2,400 fundable; if #501 is also paid, $160 remains, which is ≥ $0), R2, R4 (the rent's `next_due` moves to 2026-10-02, policy D6), R5, R7 |
| **Possible output** | A rent payment proposal; an unsent acknowledgement draft to the landlord. |
| **Human decisions** | Approve or reject the rent payment; review the draft. |
| **Successful run** | Correct lease facts, a rent proposal, the draft addressed to the landlord on record. |
| **Resolved when** | The rent payment is executed in the system (a recorded `payments` row) **and** Dr. Ryan confirms the obligation is settled. The landlord's acknowledgement is not verifiable (R7: nothing is sent). |
| **Evidence** | As for 101 |

### Ticket 103: 20 navy hoodies, size M, bulk discount (Yale AI Club)

| | |
|---|---|
| **Problem** | A discount request on 20 units. 8 are on hand, so 12 are short. No vendor is recorded as supplying navy hoodies. Bulldog (apparel reprint) is blocked while #501 is open, and its ability to supply them is unconfirmed. |
| **First agent** | Customer Service: a customer pricing request. Then Inventory for supply. |
| **Tools** | `get_ticket`, `check_inventory`, `list_vendors`, `list_pending_actions`, `propose_quote` (Customer Service), `draft_message`, `propose_purchase` (Inventory) |
| **Rules** | The quote must stay between unit cost $22 and list price $58 (policy). The quote must match the ticket exactly (20 × M). R3, R6 (a quote adds no cash), R7 |
| **Possible output** | A quote proposal (a price between $22 and $58). Possibly a restock proposal: $264 = 12 × $22, an **estimate**, not a quote. A customer draft. |
| **Human decisions** | Approve or reject the quote (approval sends nothing and records no revenue). Approve or reject any purchase (approval places no order; the backend refuses it while the vendor is blocked). |
| **Successful run** | Correct stock and shortfall; a policy-compliant quote; any purchase labelled as an estimate with the supplier uncertainty stated. |
| **Resolved when** | Dr. Ryan's decision on the evidence actually available. **Approving a quote is not customer acceptance or confirmed supply (C-3).** Neither can be observed in this system: nothing is sent (R7) and no supplier of navy hoodies is recorded. The record must say which of these remain unverified. |
| **Evidence** | As for 101 |

---

## 4. Financial decision tree (checking starts at $3,400; no inflows, R6)

| Decisions on payments | Cash after | Notes |
|---|---|---|
| Pay #501 and the rent (in either order) | **$160** | Both pass R1; Bulldog is unblocked; rent is settled |
| Rent only | $1,000 | Bulldog stays blocked: no restock for 101 or 103 |
| #501 only | $2,560 | Rent is unpaid with 2 days to go |
| Neither | $3,400 | Overdue invoice stays open; rent is unpaid |

What does **not** move cash:
- **A proposal:** no cash at all.
- **Purchase approval:** places no order and moves no cash. A real vendor invoice would come later. **Caution:** after both payments, only $160 remains, below the $264 estimate. A future invoice of that size would then be refused under R1. Approving a purchase in that state creates a commitment the shop could not currently pay.
- **Quote approval:** sends nothing and adds no revenue (R6).
- **Rejection:** changes nothing.
- **Unsent drafts:** nothing.

**Grounded view, not a recommendation to spend.** Both payments are real obligations backed by the records: one overdue, one due in 2 days. Both are fundable under R1. Paying neither has costs too: a blocked vendor and late rent. A purchase is weakly supported: no confirmed supplier, only an estimated cost, and cash after the obligations is below the estimate.

**Nothing is pre-authorized (C-5).** The $840 vendor payment, the $2,400 rent payment, and every purchase or quote decision are Dr. Ryan's, made one at a time at the decision checkpoints after Dr. Ryan has seen what each run actually proposed. This table describes consequences; it is not a decision.

---

## 5. Live-run readiness (checked without model calls; secrets not displayed)

| Item | Finding |
|---|---|
| Model and gateway | `backend/config.py` accepts **only** `gpt-6-luna` (or `@slug/gpt-6-luna`) through `https://api.portkey.ai/v1`. `PORTKEY_API_KEY` is present in the course root `.env` (name checked; the value was not printed). No `PORTKEY_MODEL` or `PORTKEY_BASE_URL` override is set, so the defaults apply. One authorized connectivity request succeeded on 2026-10-10. |
| Live authorization | The backend starts live runs only if `CC_LIVE_AUTHORIZATION` is set when it is launched. It is unset now (in this session, the user settings and the machine settings), and the running backend reports `live_runs_enabled: false`. **Enabling it means restarting the backend with the variable set.** |
| Budgets (per run) | 40 model requests, 60,000 tokens, 60 tool calls, 1,200 output tokens per request, 12 delegations, depth 4, 300 s wall clock, 60 s HTTP timeout, 1 retry. For three runs: at most 120 requests and 180,000 tokens. **The price per token for `gpt-6-luna` is not in our records, so no dollar estimate is given.** |
| Agents and MCP | Live runs launch one MCP server subprocess per agent, with that agent's identity and the selected database (stdio launcher; tested). **New check today:** under uvicorn `--reload` on Windows the app runs on a Selector event loop, which normally cannot start subprocesses. An offline check (scratch sandbox, scripted agents, real stdio MCP subprocesses) completed under both the Selector and Proactor loops, so the risk is cleared. |
| Logging and run IDs | Every agent, delegation and tool event goes to `audit_trail.json`, with `run_id` and `ticket_id`. The dashboard polls it. |
| Reconciliation | `GET /api/audit` and `scripts/verify_audit.py`. Any gap is reconciled automatically before the next decision or reset. |
| Approval interface | Dashboard dialogs. Names are attribution only. |
| Reset and recovery | Controlled reset with stamp, intent and outcome records. A failed reset changes nothing. |
| Servers now | Backend :8000 and dashboard :5173 are running (started for the demonstration; live runs disabled). HW4's server was stopped with approval. |

**Risks and mitigations:**
1. **Agent behavior is unpredictable** (wrong tool, refused calls, limit reached). The budgets cap it; refusals are recorded; no fishing re-runs.
2. **Network or Portkey failure:** the run is recorded as failed, and a technical re-run needs approval.
3. **A server reload or stop during a run:** the run is recorded as interrupted, then reconciled. Mitigation: no file edits in `backend/` during the runs, and one run at a time.
4. **Full reports are kept in memory only** for runs started by the current server process. Mitigation: keep the backend running through the exercise, and save `GET /api/runs/{id}` immediately after each run.
5. **OneDrive sync may lock files.** Mitigation: avoid heavy sync during the runs; a busy error is refused cleanly.
6. **Cost:** capped by tokens and requests; the price is unknown (see above).
7. **Unknown official formats** for `resolved_tickets.json`, `resolved_board.html` and the screenshots (§1).
8. **"Resolved" for 101 and 103 depends on outside events** the system cannot see. Mitigation: Dr. Ryan's explicit confirmation and summary (A1), or leave them open.

---

## 6. Approval checkpoints (in order)

| # | Checkpoint | Who |
|---|---|---|
| C0 | Approve this plan, including the ticket order, the run policy and the definition of "resolved" per ticket | Dr. Ryan |
| C1 | Restart the backend with `CC_LIVE_AUTHORIZATION="<Dr. Ryan's note>"` (the dashboard keeps running) | Dr. Ryan authorizes; Claude or Dr. Ryan restarts |
| C2 | Controlled reset (`POST /api/reset`, `requested_by: Dr. Ryan`), after recording fingerprints | Dr. Ryan authorizes |
| C3 | Live run for ticket 101 | Dr. Ryan authorizes the start |
| C4 | Decisions for 101's proposals | **Dr. Ryan decides each one** (clicks in the dashboard, or states the decision in chat for Claude to enter, which is recorded as such) |
| C5–C6 | Run and decisions for 102 | As for C3–C4 |
| C7–C8 | Run and decisions for 103 | As for C3–C4 |
| C9 | Resolution of each ticket, with Dr. Ryan's confirmation and summary, or leave it open | Dr. Ryan |
| C10 | Disable live runs again (restart the backend without the variable) | Claude, after the runs |
| C11 | Generate the evidence and review it | Claude generates; Dr. Ryan reviews |

---

## 7. Evidence and reconciliation plan

**Status (C-4):** the approach is approved. The script will **not** be written or run, and the Actual sections will **not** be modified, until Dr. Ryan authorizes it (after the official formats are verified).

**Captured during the exercise (real records only):**
- **Per run:** the `run_id`; `GET /api/runs/{id}` saved as JSON right after the run; all events filtered by `run_id`. These show delegations, tool calls with arguments and results, refusals, what each agent said, and usage.
- **Per decision:** the backend response, which gives the decision ID, the payment ID, and the balance before and after.
- **Screenshots** of the dashboard: the timeline, the outcome, the proposals, and cash before and after.
- **Fingerprints** before and after; `GET /api/audit` and `scripts/verify_audit.py` must report "consistent".

**Reconciliation [O], using database rows only:**
```
starting cash (original checking balance, $3,400; confirmed by the reset's content check)
+ inflows: none (R6: revenue is not modelled)
− outflows: Σ payments.amount (each payment linked to its proposal → ticket → run, and to its audit.db payment_executed event)
= ending cash, which must equal cash_accounts.balance exactly (otherwise stop and report)
```
Proposals, approved purchases (estimates), approved quotes and drafts are listed separately as "not cash".

**Generation.** A new script, `scripts/build_p9_evidence.py` [O], reads only the working database (read-only), `audit.db` and `audit_trail.json`, and writes:
- `output/resolved_tickets.json`. Proposed schema **[?]**: per ticket, the final status, `resolved_by`, `resolution_summary`, runs with `run_id`, phase and outcome, the agents involved, tool calls, delegations, proposals with their decisions, payment IDs, drafts (unsent), plus a cash section and the source fingerprints.
- `output/resolved_board.html`: a static board built from the same data.
- The Actual sections and Cash tab of `output/desk_tickets.html`, filled by targeted replacement of the existing placeholders. Every figure is linked to its `run_id`, event `seq` or payment ID.
- The script refuses to run if a run is still active or the audit check is not consistent. Nothing is written by hand.

---

## 8. Final execution checklist (revision 3)

Status key: ✅ settled · 🔒 needs Dr. Ryan's explicit approval at that step (spec §6 gates are numbered G1–G5)

**A. Settled before execution**

| # | Item | Status |
|---|---|---|
| A1 | Plan, ticket order 101 → 102 → 103, decisions after each run | ✅ |
| A2 | Compared with Dr. Ryan's specification; differences resolved (§0) | ✅ |
| A3 | One live run per ticket; technical re-run only with separate approval; failed or interrupted runs recorded | ✅ |
| A4 | Run completion ≠ proposal approval ≠ business resolution; payment ≠ delivery (101); quote approval ≠ acceptance or supply (103) | ✅ |
| A5 | No financial or business decision pre-authorized | ✅ |

**B. Precise reset procedure (gate G1)**
1. Confirm no run is active (`GET /api/audit` → `run_active: false`) and the audit is consistent.
2. Record the SHA-256 of the original, the working copy and `audit_trail.json` (expected: `23686a90…`, `dbaf24cb…`, `ec8e507a…`).
3. *(Optional, Dr. Ryan's choice)* copy the current working file to `data/backups/campus_customs_new.pre_p9_reset.<UTC>.db`.
4. `POST /api/reset` with header `X-Human-Decision: confirm` and body `{"requested_by": "Dr. Ryan", "confirm": "RESET"}`.
   - Sent from the API docs page or a single command, on Dr. Ryan's go-ahead. The dashboard has no reset button.
   - This is the approved `controlled_reset`: intent recorded first, then the verified copy, the stamp and the atomic swap, then the outcome in both audit records. It is refused if a run or decision is active.
5. Check the response: `reset: true`, `audit_trail_recorded: true`, and a `reset_id`.
6. **Immediately record the opening balance:** save `GET /api/cash` (expected `checking_balance` 3400.0). This is the reconciliation's starting figure.
7. Re-verify the starting facts (spec §3): all tickets open; tee S = 0; hoodie M = 8; #501 open; rent $2,400 due 09-02. Confirm the original's hash is unchanged, `GET /api/audit` is consistent, and the reset stamp equals the `reset_id`.

**C. Live-run enablement (gate G2)**
1. Stop only the HW5 backend (the dashboard stays up).
2. Restart it with the documented command, with `CC_LIVE_AUTHORIZATION="<Dr. Ryan's note>"` set for that process only.
3. Confirm `GET /` shows `live_runs_enabled: true` and `run_mode: "live"`.

Exposure accepted at this gate: at most 40 requests and 60,000 tokens per run, three runs. The price per token is not on record.

**D. Per-ticket checkpoints (gates G3 and G4), repeated for 101, then 102, then 103**

| Step | Action | Captured |
|---|---|---|
| D1 (G3) | Dr. Ryan approves the start; "Run agents on ticket N" is clicked on the board and confirmed | `run_id` |
| D2 | Watch the run to its terminal phase; touch nothing else | dashboard screenshots during and after |
| D3 | Save `GET /api/runs/{run_id}` and `GET /api/events?run_id=…` | `output/p9_runs/<ticket>_run.json`, `<ticket>_events.json` |
| D4 | Report to Dr. Ryan: phase, outcome, agents, delegations, tools, proposals, refusals, drafts (observed, not anticipated) | chat summary |
| D5 (G4) | Dr. Ryan decides each proposal (approve or reject) and states it. Entered through the board's dialog, as Dr. Ryan directs (Dr. Ryan clicks, or Claude enters exactly what Dr. Ryan stated) | each decision response → `output/p9_runs/<ticket>_decisions.json`; cash after |
| D6 (G4) | Dr. Ryan decides whether the ticket is resolved, on the available evidence, with Dr. Ryan's own summary, or leaves it open | the resolve response, or "left open" with the reason |
| D7 | Final-state screenshot of the board for this ticket (tall viewport, whole page) | `output/p9_screenshots/<ticket>_final.jpg` |

If a run fails, stops or is interrupted, it is recorded as it is. Any re-run is a new G3 approval.

**E. After the runs**
1. Disable live runs: restart the backend without the variable, and confirm `GET /` shows them disabled.
2. `GET /api/audit` and `scripts/verify_audit.py` must be consistent (reconcile any gap first). Record the fingerprints.
3. **Gate G5, writing the evidence (only after real results exist):** write `scripts/build_p9_evidence.py`. Its sources are the working database (read-only), `audit.db` and `audit_trail.json`, plus the saved screenshots. It produces:
   - the Actual sections and Cash tab of `desk_tickets.html` (targeted placeholder replacement; Expected untouched);
   - `resolved_tickets.json`;
   - `resolved_board.html` (screenshots embedded).

   It stops if the reconciliation does not equal `cash_accounts`, a run is active, or the audit is inconsistent.
4. **D-H:** complete `harness.md`, before or after the runs as Dr. Ryan prefers.
5. Dr. Ryan reviews all evidence.

**Unresolved risks**
- **Agent behavior is unpredictable:** refusals, limits, or unexpected delegations. Recorded as observed.
- **Portkey or network failure, or an interruption:** recorded; re-run only with a G3 approval.
- **The full Boss report is in memory only:** the backend stays up between the runs, and D3 saves it at once.
- **Unknown token price:** exposure is capped.
- **OneDrive locks** (rare): refused cleanly, then retried.
- **Unverifiable outside events** (delivery for 101, acceptance and supply for 103): stated as unverified. Tickets may honestly remain open.
- **Screenshot clarity:** the tall-viewport capture is checked before G5. If it is unreadable, two or three captures per ticket are used, all genuine.

**Separate approvals requested (spec §6):**
1. **G1:** the reset (and whether to make the optional backup).
2. **G2:** enabling live runs, with Dr. Ryan's authorization note, and accepting the budget exposure.
3. **G3:** the start of each ticket run (three approvals).
4. **G4:** each financial or business decision, and each resolution decision, plus the entry method (Dr. Ryan clicks, or Claude enters what Dr. Ryan stated).
5. **G5:** writing the final evidence once real results exist; plus D-H (when to complete `harness.md`).
