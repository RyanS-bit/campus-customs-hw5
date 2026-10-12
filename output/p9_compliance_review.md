# Problems 8 and 9: compliance review (Problem 9, follow-up 11)

Read-only review against Dr. Ryan's independent summary of the assignment (follow-up 11). It builds on `p9_ticket101_assessment.md`. Nothing was changed, rerun or decided.

**Labels:** **[M]** mandatory, from Dr. Ryan's summary of the assignment or the official rules R1–R8 (`harness.md` §7a) · **[D]** our own design choice · **[R]** recommendation.

## Prioritized findings

### F1 · Critical: agents report decisions but do not make them actionable (root cause of ticket 101)
- **[M]** Tickets are run "until each is resolved". Agents prepare payments and purchases; humans approve them (R2).
- **[D]** A person can act only on an agent's proposal. The dashboard cannot create a payment. The Boss's prompt and the run task (`main.ticket_task`) ask agents to *report* what a human must decide. Nothing asks the responsible specialist to *prepare* the proposal or draft for it.
- **Evidence (101, attempt 2):** the Boss listed "decide how to address invoice 501" as needing a human, but never involved Accounting, and Customer Service drafted no reply. The person was left with nothing to act on. All tools and permissions worked; this is coordination, not capability.
- **Who it affects:** 101 and 103 (103 has no routing instruction either). 102 is partly covered: the Facilities prompt sends rent to Accounting.
- **[R]** A prompt-only correction, tested offline and disclosed:
  - **Shared rule:** prepare the proposal or draft for every human decision you identify, within authority and the rules. A proposal is not a recommendation to approve; open questions go in its rationale.
  - **Boss routing:** invoices linked to a ticket go to Accounting; purchases and restocking to Inventory; customer outcomes to Customer Service (a draft, plus a quote when one is requested); rent to Facilities and Accounting.
  - **Never** steer a specific business choice (for example, never "pay #501").

### F2 · Critical: "marks tickets resolved when runs finish" versus our human-confirmed resolution
- **[M]** Problem 8: the dashboard marks tickets resolved when runs finish. Problem 9: run each ticket until it is resolved; `resolved_board.html` shows the **three resolved tickets**.
- **[D]** Resolution is a separate human confirmation with a written summary (Problem 7, A1). A finished run never resolves a ticket (C-3).
- **Assessment:** our mechanism *does* mark tickets resolved on the dashboard, but only after a person confirms, not when a run finishes. Taken literally, this is a deviation. It also creates a second risk: if the evidence-based decision leaves 101 or 103 open, the "three resolved tickets" deliverable cannot be met.
- **Key point: resolution can be defined within the assignment's own model.** The rules define the simulation's boundary:
  - R7: communications are *drafts*, never sent.
  - R5: the date never advances, so nothing arrives.
  - Delivery and acceptance are not modelled.

  Within that model, the desk *has* resolved a ticket when the run has finished, every proposal for the ticket has a human decision, the required counterparty draft exists, and a person confirms. Recording it that way is honest, provided the record keeps listing what the model cannot show (delivery, acceptance, actual sending).
- **[R]** Keep the human confirmation, which protects R2 and is your prior instruction. Make it immediate, and define it inside the model:
  - **(a)** When a run finishes, the dashboard shows "Confirm resolution" in the run outcome panel, with the run's evidence alongside. This is a small UI change.
  - **(b)** The confirmation text and `resolution_summary` use the in-model definition above, and still list what is not verified.
  - **(c)** Document the choice in `design.md` and `harness.md` as an explicit interpretation of "marks resolved when runs finish".
  - **Alternative:** auto-mark at run end. **Not recommended:** it infers a business outcome from a run, which you prohibited (C-3).

### F3 · High: purchase approval moves no cash, but the rules imply purchases are paid from the $3,400
- **[M]** R1 ("refuse any payment that would leave insufficient cash"), R2 ("human approval is required for payments **and purchases**"), and R6 (cash only goes out; our own `harness.md` §7a note: "reprints must be funded from the existing $3,400"). The Cash tab must show "each ticket's actual cash changes and their causes".
- **[D]** In Problems 5 and 7, a purchase is "authorization only, $0". Payment was assumed to follow a later vendor invoice that the system never creates. Our own Problem 2 analysis (`harness.md` §5) had applied R1 to a $264 hoodie purchase, which implies purchases spend cash.
- **Risk:** as built, an approved purchase can never change cash. That may contradict the assignment's model, and make 101 and 103 cash attribution look incomplete.
- **[R]** Dr. Ryan to decide; this needs a backend change if adopted. Option "purchase execution on approval":
  - re-check R3 (vendor not blocked) and R1 (balance − estimate ≥ 0);
  - insert a `payments` row (`kind = 'purchase'`, `ref_id` = the proposal, `amount` = the estimate);
  - reduce cash and mark the proposal `executed`; record both audit records.
  - **Inventory stays unchanged:** with the date fixed (R5), nothing can arrive, and this is stated.
  - **Costs:** a moderate code change with new tests, and documentation updates (Problem 7 behavior changes).
  - **Alternative:** keep as is, and document it as our interpretation, at grading risk.

### F4 · High: the one-run-per-ticket policy
- **[D]** C-2 was our policy, not an assignment rule.
- **[R]** Revise it to allow additional runs, each separately authorized and fully preserved, in three cases:
  1. a technical failure;
  2. a disclosed system correction (such as F1 or F3) that changes what the agents or system can do;
  3. a run whose result leaves a required human decision unactionable.

  Still prohibited: reruns to "shop" for a preferred business decision, and prompt changes that dictate a particular choice.

### F5 · Medium: the run budget may be tight for richer runs
- **[D]** 60,000 tokens and 40 requests per run. Ticket 101 used 29,652 tokens and 12 requests with three agents doing reads only. After F1, runs will prepare proposals and drafts, and ticket 103 involves three or four agents.
- **[R]** Keep the limits; a stop is recorded honestly. If 103 stops at a limit, raising the token limit (for example to 100,000) would be a separate decision for Dr. Ryan, with its cost exposure.

### F6 · Medium: business-rule dependencies between tickets
- **R3:** a 103 purchase from Bulldog can be approved only after #501 is paid, so the order 101 → 103 matters (it is already planned).
- **Unrecorded supplier:** no vendor is recorded as supplying navy hoodies. Bulldog's specialty is "apparel reprint", so whether it can supply them is a human judgment.
- **Cash:** after paying #501 and the rent, $160 remains, below the $264 estimate. Under F3 execution, R1 would refuse the purchase after both payments. That is a correct and demonstrable outcome, and Dr. Ryan's decisions determine it.

### F7 · Medium: evidence generation depends on F2 and F3
- "Three resolved tickets" screenshots need resolved states (F2).
- Cash-tab causes depend on F3.
- **Already sound:** the Expected sections are preserved; the trail is append-only (verified at every gate); reconciliation is designed; the screenshots are taken from the real board.

### F8 · Low: documentation and records still to do
- `harness.md` completion (D-H).
- Failed attempt 1 must appear in 101's Actual section (planned).
- The G2 authorization wording is recorded in each run (documented, with G3 recorded separately).

## Answers to the six questions
1. **Human-confirmed resolution versus "marked resolved when runs finish":** adequate in substance, but a literal deviation (F2). Make the confirmation immediate at run end, defined within the assignment's model, and documented.
2. **Can the team produce the needed actions?** Yes in capability (every tool, permission and delegation path exists). Not reliably in coordination (F1). The prompt fix is needed. F3 decides whether purchases can complete in cash terms.
3. **Why 101 produced nothing:** F1 (no "prepare" instruction; the Boss's caution about unverified delivery; Customer Service drafting nothing). A legitimate resolution needs: a payment proposal for #501 decided by Dr. Ryan; a customer draft for size S (back-order or decline); and Dr. Ryan's confirmation within the in-model definition, still listing what is not verified.
4. **Policy:** yes, revise it (F4).
5. **Blocking limitations:**
   - F1 (coordination);
   - F2 (resolution semantics);
   - F3 (purchase cash model);
   - F5 (budget, possible);
   - F6 (supplier unknown, a judgment).

   None is a technical impossibility: each can be resolved by a decision, or a small change plus tests.
6. **Corrections before continuing live execution,** in priority order:
   1. **Decisions** on F2 (resolution definition and UI), F3 (purchase execution or not) and F4 (policy).
   2. **The F1 prompt correction,** plus offline tests.
   3. **Any change approved for F2 or F3,** with offline tests.
   4. **Regression, verifier and fingerprints;** then a readiness check (G2-style, no model calls).
   5. **The runs:** 101 attempt 3, then 102, then 103, each separately authorized, with Dr. Ryan's decisions in between. Attempts 1 and 2 are preserved.
