# Campus Customs Operations Dashboard: Design (Problem 8)

The dashboard is a React + Vite + TypeScript app in `frontend/`. It talks only to the Problem 7 FastAPI backend at `http://localhost:8000`, through the routes listed in `output/harness.md` §11. Agents prepare work; people decide. The dashboard shows both, and keeps them apart.

## 1. Layout

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ Campus Customs  Operations Dashboard     Checking (actual) | Awaiting decision | Agent runs | Audit │  Refresh
│ Shop date 2026-08-31 · Agents prepare; people decide.                                         Updated hh:mm:ss
├──────────────────────────────┬───────────────────────────────────────────────────────────────┤
│ Tickets                      │ Ticket 101: Bulldog tee                          [Ticket Open] │
│  #101 Bulldog tee            │  issue, requester, requested item                              │
│   [Ticket Open][Agent run …] │  Stock facts (on hand, shortfall, cost/price)                  │
│  #102 Rent due               │  Invoice #501 / Lease #1 facts                                 │
│  #103 Bulk hoodie discount   ├───────────────────────────────────────────────────────────────┤
│                              │ Business outcome: [Confirm ticket resolved…]                    │
├──────────────────────────────┼───────────────────────────────────────────────────────────────┤
│ Cash                         │ Agent team                                   [Agent run: …]    │
│  $3,400.00 (actual)          │  [Run agents on ticket 101]                                    │
│  executed payments           │  Outcome box · the team's summary · Activity timeline          │
│  not counted as cash         ├───────────────────────────────────────────────────────────────┤
├──────────────────────────────┤ Proposals and drafts                                           │
│ Audit records                │  #1 Payment of invoice #501 [Proposal: Awaiting decision]      │
│                              │      [Approve and pay…] [Reject…]   · unsent drafts             │
└──────────────────────────────┴───────────────────────────────────────────────────────────────┘
```

Below 960 px wide, the two columns stack (ticket list and cash first). Dialogs are centred overlays.

| Area | Component (`frontend/src/…`) | Data source |
|---|---|---|
| Header: actual balance, decisions waiting, run availability, audit state | `components/Panels.tsx` `Header` | `GET /api/cash`, `GET /api/actions/pending`, `GET /`, `GET /api/audit` |
| Ticket list with business and run status | `TicketList` | `GET /api/tickets` |
| Ticket issue and facts (stock, invoice, lease) | `TicketFacts` | `GET /api/tickets/{id}` |
| Business outcome (human resolution) | `App.tsx`, `components/Dialogs.tsx` `ResolveDialog` | `POST /api/tickets/{id}/resolve` |
| Run control, status, outcome, team summary, timeline | `RunPanel`, `Timeline`, `useRunTracker.ts`, `RunDialog` | `POST /api/tickets/{id}/run`, `GET /api/runs/{id}`, `GET /api/events` |
| Proposals (approve/reject) and unsent drafts | `ProposalsPanel`, `DecisionDialog` | ticket detail; `POST /api/actions/{id}/approve` and `/reject` |
| Cash | `CashPanel` | `GET /api/cash` |
| Audit records | `AuditPanel` | `GET /api/audit` |

## 2. Three statuses, kept apart

| Status | Values (from the backend) | Badge style | Changed by |
|---|---|---|---|
| **Agent run** | queued, active ("Running"), completed, stopped (limit reached), failed, interrupted | square tag, label "Agent run" | the agent runner |
| **Proposal** | proposed ("Awaiting decision"), approved, rejected, executed ("Executed (paid)"), failed | dashed outline, label "Proposal" | a person's decision |
| **Ticket** | open, resolved | solid pill, label "Ticket" | only a person's explicit confirmation |

- **A finished run is never shown as a solved problem.** When a run ends, the outcome box says what happened (for example "Run finished: proposals await your decision … The ticket is still open").
  - It then shows the ticket's own badge with the sentence "Only a person's confirmation resolves a ticket."
  - The outcome wording comes from the backend's `outcome` value; the dashboard only translates it into plain language (`labels.ts` `outcomeText`).
- **Why resolution is a separate step.** The professor wants tickets to show resolution as work finishes, and the dashboard does show it, but only after a person confirms the business outcome. The "Business outcome" panel explains why: approving a payment, or finishing a run, is not the same as the customer or landlord having their issue solved.

## 3. Information flow

1. **On load,** the dashboard requests the service index, tickets, cash, audit status and pending proposals in parallel (GET only).
   - It selects the first ticket and loads its detail.
   - If any request fails, an error banner shows the backend's message, or "Cannot reach the backend …" with the start command. No placeholder data is shown.
2. **Refresh.** The same GETs are repeated every 15 seconds and after every run end or decision; the Refresh button does the same on demand. The header shows the time of the last refresh, so stale data is visible.
3. **Selecting a ticket** loads `GET /api/tickets/{id}`. If the ticket has earlier runs, the latest is shown with its recorded history.
4. **Starting a run** opens a confirmation dialog. Only its "Start run" button sends `POST /api/tickets/{id}/run` with `X-Human-Decision: confirm`. The returned `run_id` is then tracked (section 4).
5. **Deciding** opens a dialog (section 5). After a success, everything is refreshed from the backend.

All backend access is in `src/api.ts`.
- **GET helpers:** used by loading, refresh and polling. They change nothing.
- **POST helpers:** start a run, approve, reject, resolve. They are called only from the confirm buttons inside dialogs, never from effects, timers or rendering.
- **No reset helper:** the dashboard has none (section 8).

## 4. Run tracking and polling

`src/useRunTracker.ts` follows one run, using the run ID and event cursor designed in Problem 7:
- **Every 1.5 s** it calls `GET /api/runs/{run_id}`, then `GET /api/events?run_id=…&after_seq=<cursor>&limit=200`.
  - While `has_more` is true it fetches the next page at once, up to 50 pages per tick.
  - The cursor is the backend's `next_after_seq`.
- **Ordering:** events are stored once per `seq` (no duplicates) and shown in `seq` order. Events are fetched *after* the run view, so a terminal view always comes with its final events.
- **Polling stops** when the phase is terminal (completed, stopped, failed or interrupted). The recorded history stays on screen, and tickets, cash and audit are then refreshed once.
- **Errors:** a network error shows "Retrying…" and polling continues. A 404 (unknown run) stops polling with a message.
- **The timeline** shows, for each event: time, the agent (or system or person), and what happened:
  - "called `check_inventory`", with the arguments;
  - "result of … · ok/refused", with the recorded result or refusal;
  - "delegated to Accounting", with the task;
  - what the agent reported (`said_summary`, or the recorded text).

  Nested delegations are indented by chain depth. Nothing is generated: every line is a recorded event.
- **The team's summary** is the entry agent's final report (summary, needs-human, facts, estimates, unknowns, delegations). It is labelled as the agents' own account; the events are the evidence.
- **Unhappy outcomes are shown explicitly:**
  - interrupted runs: "started but never finished";
  - failures, with the recorded error;
  - budget stops: "work is incomplete".

## 5. Human decisions

Every decision is a dialog opened by a deliberate click. It states the consequences before anything is sent:

| Decision | What the dialog says |
|---|---|
| **Approve a payment** | "Approve and EXECUTE a payment of $840.00": pays the invoice or rent now; the checking balance falls by that amount if the backend accepts it; the backend re-checks the rules; it cannot be undone here |
| **Approve a purchase** | authorization only: no order, no shipment, no vendor payment, cash unchanged; the amount is an estimate, not a vendor quote; a blocked vendor is refused (R3) |
| **Approve a quote** | authorization only: not sent to the customer, no acceptance recorded, no revenue or cash |
| **Reject** | the proposal is rejected, nothing executed, cash unchanged |
| **Resolve a ticket** | finishing a run or deciding proposals does not resolve a ticket; the system records the person's statement and does not verify deliveries, acceptance, messages or outside payments |

- **Required input:** the person's name, plus an "I have read the consequences …" checkbox, before the submit button is enabled. Resolving also needs the business-outcome checkbox and a 10–1,000 character explanation.
  - These map exactly to the backend fields: `decided_by`, `confirm: true`, `note`, `business_outcome_confirmed: true`, `resolution_summary`.
  - The name is labelled "Recorded for attribution only. This local prototype has no login, so the name is not proof of identity."
- **One submission only:** a request guard and a disabled "Sending…" button make repeated clicks send at most one request (tested).
- **Outcomes:**
  - **Success:** the dialog closes. The message comes from the backend's response (for a payment: "the backend reports the checking balance went from $3,400.00 to $2,560.00"), and tickets, proposals, cash and audit are reloaded.
  - **Refusal or error:** the dialog stays open and shows the backend's code and message (for example `REFUSED_RULE: R3 …` or `NOT_PENDING`). No success is shown.
  - **Warnings** returned by the backend (for example a pending reconciliation) are shown as separate messages.

## 6. Cash

- **The balance** shown is `checking_balance` from `GET /api/cash`, labelled "Checking balance (actual)", with its date and source. It is never calculated from proposals.
- **Executed payments** are listed from the same response.
- **"Not counted as cash"** shows the backend's figures for proposed payments, approved-but-unexecuted payments, purchase estimates and quotes, with the backend's note that they move no money.
- **"Changed by −$840.00 since this page loaded"** is the difference between two balances that the backend reported; it is not a forecast.
- **The audit panel** shows `GET /api/audit`:
  - consistent, with event counts;
  - or the incomplete operations and cross-reference problems, noting that gaps are reconciled automatically before the next decision or reset.

## 7. The three tickets: user journeys

These describe how a person would use the dashboard. The real outcomes come only from the live, authorized runs in Problem 9.

- **Ticket 101 (Bulldog tee, size S).**
  1. Select it: the facts show size S on hand = 0, invoice #501 ($840, overdue, "Rush reprint CC-TEE-WHITE S") and Bulldog blocked (R3). It also says that delivery of the invoiced goods is not recorded.
  2. Run the agents and watch Inventory and Accounting work.
  3. Review the payment proposal. "Approve and pay…" explains that cash falls by $840. After the backend executes it, the actual balance changes.
  4. Resolve only when the customer's need is actually met, describing how, in your own words.
- **Ticket 102 (rent).**
  1. Facts: lease #1, $2,400, due 2026-09-02.
  2. The run produces a rent payment proposal and, typically, an unsent draft to Elm City Properties. The draft is marked "Not sent"; a person sends it outside the system (R7).
  3. Approve the payment, then resolve with a confirmation, for example that the landlord acknowledged receipt.
- **Ticket 103 (20 navy hoodies, size M).**
  1. Facts: 8 on hand, 12 short, unit cost $22, list price $58.
  2. The run can produce a quote proposal and a restock proposal. The restock figure is labelled an estimate; the quote is labelled "not revenue".
  3. Approving the quote does not send it. Approving a purchase from a blocked vendor is refused, and the refusal is shown.
  4. Resolve only after a person confirms the outcome with the customer.

## 8. Safety decisions

- **No business logic in React.** Decisions, rules, balances, run outcomes and audit status all come from the backend. The dashboard only displays them, and maps status values to plain words.
- **Live runs.** The dashboard cannot enable them. It shows the backend's run availability (from `GET /`) in the header and warns in the run dialog. A `403 RUNS_NOT_AUTHORIZED` is explained ("Agent runs are disabled on this server … set `CC_LIVE_AUTHORIZATION` … Nothing was started").
- **No reset button.** The reset endpoint exists, but a one-click control would make a destructive operation too easy, and no assignment requirement needs it on the dashboard. For Problem 9, an operator resets deliberately with `scripts/reset_db.py --confirm` or through `POST /api/reset` (for example from the API docs page at `/docs`), with Dr. Ryan's approval.
- **Polling and rendering can never mutate.** Only GET helpers are reachable from effects and timers (tested: browsing and polling send no POST).
- **Accessibility:**
  - real buttons with descriptive labels (for example "Approve and pay proposal #1");
  - dialogs with `role="dialog"`, `aria-modal`, focus on the first field, Escape to cancel (except while sending) and focus returned to the opener;
  - live regions for results and errors, and visible focus outlines;
  - colour is never the only signal: every badge carries a text label.

## 9. Backend contract and one backend addition

- **Types.** The TypeScript types (`src/types.ts`) follow the backend's OpenAPI schemas. For nested objects, which OpenAPI types only as dictionaries, they follow real responses recorded from the backend.
- **The one backend addition:** `GET /api/tickets/{id}` now includes a read-only `inventory` object, built in `mcp_server/desk_reads.py`.
  - It contains stored stock by size, unit cost and list price, and the computed shortfall, clearly labelled as arithmetic on stored values.
  - Problem 7's ticket detail had no stock facts, and the dashboard must not compute business facts itself.
  - It writes nothing and changes no rule. It is tested in `tests/test_problem7_api.py` (`test_p7_03`).

## 10. Tests

`frontend/src/test/App.test.tsx`, 15 tests, run with `npm test` (Vitest + Testing Library, jsdom).
- **Mocked backend:** `src/test/mockBackend.ts` stubs `fetch` and serves responses recorded from the real backend (`src/test/fixtures/backend_samples.json`). Those were made by `scripts/make_frontend_fixtures.py`, running the backend in-process on a temporary database with scripted offline agents. No server, model or network is used.
- **Covered:**
  - the three tickets and their statuses;
  - ticket facts;
  - an unreachable backend;
  - a refused (unauthorized) run;
  - a mocked run streamed in order, paged with a cursor, without duplicates. Polling stops at the end and the ticket stays open;
  - interrupted and failed runs;
  - consequences shown for each proposal kind;
  - payment approval with name and confirmation, exactly one POST despite repeated clicks, and the balance changing only after the backend reports it;
  - backend refusals (R3, `NOT_PENDING`);
  - resolution needing an explicit confirmation and explanation;
  - resolution refused while proposals are undecided;
  - cash kept separate from proposals;
  - audit gaps;
  - no POST from loading, polling or browsing.
- **Mutation checks** on copies: each of these made at least one test fail:
  - polling forever;
  - showing a balance computed from proposals;
  - treating a completed run as resolution;
  - removing both duplicate-submit protections.

## 11. Limitations

- **Visual appearance has not been inspected by Claude.** Starting the dev server, or the backend, was outside this step's authorization. The layout was checked through the DOM in tests and by a successful production build. It should be viewed with `npm run dev` once the backend is started.
- **Report text may be shortened.** The team's full report is available only for runs started by the current backend process. Older runs show the recorded summary, which may be truncated to 300 characters.
- **Names are not authentication.** The typed name is attribution only (local prototype).
- *(Updated in Problem 9.)* The dashboard was later viewed in the browser against the running backend (Problem 8 demonstration; `output/p8_demo_screenshots/`).

## 12. Problem 9 change: the resolution step after a finished run (F2)

**The requirement and our interpretation.** The assignment asks the dashboard to mark tickets resolved when runs finish. In this system, a finished agent run is **not** proof that the business issue is settled (Dr. Ryan's instruction C-3). We interpret the requirement as follows: *when a run finishes, the dashboard immediately presents the human steps that lead to resolution, and marks the ticket resolved as soon as the person confirms it.*

**What changed.**
- When a run that did its work finishes (`completed`, or `stopped` at a limit) on an open ticket, the Agent team panel shows **"Next: your decisions"**. It says plainly that the run does not resolve the ticket. It then lists:
  1. how many proposals await a decision;
  2. how many unsent drafts there are to review;
  3. "confirm resolution only if the business issue is actually settled; otherwise leave the ticket open".
- Its **"Review and confirm resolution…"** button opens the existing resolution dialog. That dialog still requires the name, the business-outcome confirmation and the written summary. The backend still refuses while proposals are undecided.
- Nothing is resolved, approved or sent automatically. The step is not offered after a failed or interrupted run, or on a resolved ticket.
- On the board, a ticket becomes "Resolved" as soon as the person confirms; the 15-second refresh and the post-decision refresh show it.

**Definition of "resolved" within the assignment's model** (also shown in the panel). The assignment's own rules set the simulation boundary: R7 (communications are drafts, never sent) and R5 (the date never moves, so deliveries never arrive). A ticket is resolved when:
- the agents' work is done;
- every proposal for the ticket has the person's decision;
- the needed messages are drafted;
- the person confirms the underlying issue is settled.

The resolution record continues to list what the system does not verify: deliveries, customer acceptance, actual sending, and payments outside the system.

**Tests** (`src/test/App.test.tsx`):
- The step appears after a completed run, with correct counts, and opens the dialog. No POST is sent, and the ticket stays open.
- The step is not offered after a failed run, or on a resolved ticket.

**Two resolution bases** (follow-up 36). The single confirmation ("the underlying business issue is resolved") cannot be given truthfully when delivery or customer acceptance is outside the simulation. The resolve dialog therefore asks **what the person is confirming**. Nothing is preselected, and the submit button reads "Choose what you are confirming" until a choice is made.
- **A. Business outcome confirmed.** The original meaning, unchanged.
- **B. Simulated workflow complete; fulfilment not verified.** The person confirms only that the desk's work in this simulation is done: the agents ran, every proposal is decided, messages are drafted.
  - They must tick at least one unverified outcome: delivery or receipt of goods; customer or counterparty acceptance; that a message was sent; an arrangement outside the system.
  - They must tick "I am NOT confirming delivery or customer acceptance".
  - They must write a summary of what was done and what remains unverified.
  - Switching between A and B clears the earlier ticks.
- **Display:** the ticket status stays **Resolved** either way, and the basis is always shown with it.
  - The "Business outcome" section reads "Resolved: business outcome confirmed" or "Resolved: workflow complete; fulfilment not verified", with the unverified items listed.
  - The ticket list adds a "fulfilment not verified" pill.
  - The run outcome line no longer claims the issue is solved; it points to the Business outcome section.
- **Backend:** rules and records are in `harness.md` §12.9.

**History is labelled as history** (follow-up 39). The Agent team panel's recorded content is introduced as "Recorded during this run (started …, finished …)". Since follow-up 41, the caption sits above "The team's summary", so it covers both the summary and the Activity list. Agent statements and tool results there describe the shop at that time, for example "open invoice #501" before it was paid, and may no longer be current. The current ticket status, proposals and cash are shown elsewhere on the page. After a ticket is resolved, the run no longer reports `awaiting_human_action`, although its unsent draft stays listed unchanged (`harness.md` §12.10).
- **Tests:** "resolves as 'workflow complete' only with an explicit choice…" and "switching basis clears the earlier confirmation…". The four earlier resolution tests now choose basis A explicitly.

## 13. Problem 9 change: purchase approval versus purchase execution (F3)

So that a commitment can never be mistaken for a payment:
- **Proposed purchase:** the approve button reads **"Approve (authorize only)…"**. The dialog says: "Step 1 of 2: this only AUTHORIZES the purchase. No money moves…" and explains that step 2 spends cash. While the vendor is blocked, it adds that execution will be refused until the invoice is paid.
- **Approved purchase:** the badge reads **"Proposal: Authorized, not paid"**, and the row says "No money has moved". It offers **"Execute purchase (spends cash)…"** and **"Withdraw authorization…"**.
- **The execute dialog:**
  - **It states:** step 2 pays the vendor now, and cash falls by the confirmed amount; paying is not delivery; the backend refuses if the vendor is blocked or cash available for purchases is too low; supplier facts are the person's statement.
  - **It requires:** a basis (the database estimate, quantity × stored unit cost, shown and labelled "not a supplier quote", or a supplier-confirmed price with a note), the actual amount, a supplier-agreement checkbox, an amount checkbox, and the name.
  - **Its button names the amount:** "Pay $X and execute purchase #N".
  - It uses the same single-flight guard.
- **Executed purchase:** the row reads "Executed: paid from checking (payment #N)… Paying is not delivery: receipt of the goods is not recorded and inventory is unchanged."
- **Cash panel:**
  - The actual balance (only executed payments and purchases change it).
  - Separate lines for proposed payments, approved payments not executed, proposed purchase estimates, **authorized purchases not paid**, and quotes.
  - The two computed limits from the backend, **available for invoices and rent** and **available for purchases**, each labelled computed and not stored.

**History versus current state** (follow-up 17):
- **A withdrawn authorization** reads **"Proposal: Withdrawn (was authorized, never paid)"**, with "Authorized by … (time) · note; authorization withdrawn by … (time) · note. It was never executed, and nothing was paid." The facts come from `audit.db`. A plain rejection still reads "Rejected".
- **Stored warnings** sit under **"At proposal time (business date …, recorded …); these may no longer be true"**.
- **Purchases** also show a separate **"Now (business date …):"** line with today's vendor restriction, computed from current invoices.

**Tests:**
- approval shows "Authorized, not paid", cash is unchanged, and the commitment and availability figures come from the backend;
- a withdrawn authorization is distinguished from a plain rejection;
- stored warnings are labelled proposal-time, and the "Now" line follows current facts;
- execution is enabled only after the amount, supplier statement, amount confirmation and name, sends one POST despite a double click, and the balance changes only after the backend reports it;
- a supplier price needs its note;
- an R3 refusal at execution is shown verbatim and nothing is claimed.
