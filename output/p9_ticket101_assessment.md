# Ticket 101: requirements-focused assessment (Problem 9, follow-up 10)

Read-only analysis. No rerun, code or prompt change, decision or resolution was made.

**Labels:** **[P]** the professor's required outcome as relayed by Dr. Ryan (the official text is not in hand) · **[S]** Dr. Ryan's Problem 9 specification · **[I]** our implementation choice · **[R]** Claude's recommendation.

## 1. What a legitimate resolution of ticket 101 requires

The ticket is a customer (Tauhid Zaman) asking for **one Classic Bulldog Tee, size S**. The verified facts:
- Size S on hand is 0.
- The only restock record is invoice #501 ($840, 3 days overdue, "Rush reprint CC-TEE-WHITE S"). Whether those goods were delivered is not recorded.
- Bulldog Print Co ships nothing new while #501 is open (R3).

A *genuine* resolution means the customer's request reaches a real end state:

| End state | What it needs | Available in our system? |
|---|---|---|
| **Fulfilled** (the customer gets a tee) | Stock, then a hand-over. Stock needs either the #501 goods to be found or delivered, or a new order: pay #501 (R3), order, receive. | **No.** Nothing in the records establishes the #501 goods; purchase approval places no order **[I]**; there is no "ordered/received" step, so inventory never increases **[I]** (decision D7 kept Problem 5 to proposed/approved/rejected). |
| **Declined or cancelled** (the shop cannot fulfil, and the customer is told) | A human business decision, plus communication to the customer | **Partly.** The decision can be recorded (the human resolve route). Communication can only be *drafted* (R7) and would have to be sent by a person outside the system; that cannot be verified here. |
| **Back-ordered, with the customer's agreement** | The customer's acceptance | **No.** Acceptance cannot be observed. |

**Paying #501 does not by itself resolve 101.** It settles a debt and lifts the vendor block, but it delivers nothing (C-3).

## 2. Can 101 be closed by a documented human decision?

**Yes, as a decision, but its wording must match what is known.**
- **What the spec allows [S]:** "where real-world fulfillment or customer acceptance cannot be established, report the limitation instead of inventing an outcome", and "if any ticket remains open, state that truthfully".
- **What our resolve route records [I] (A1):** the person's statement that "the underlying business issue is resolved", plus a summary, and it lists what the system does not verify.
- **When closing is defensible:** if Dr. Ryan decides as the business owner that the request is **declined or cancelled** because it cannot be fulfilled. The summary must say exactly that, and must state that customer notification is not verified (and, if a draft exists, that it is unsent). The record would be accurate: the business *decision* is closed; fulfilment did not happen.
- **What must not be recorded:** fulfilment, notification or acceptance. None of these is verified.
- **One further consideration:** "Resolved" with the summary "declined, customer not yet notified" is a weaker closure than "declined and the customer informed". If Dr. Ryan personally informs the requester outside the system, Dr. Ryan could state that this was done. Claude cannot verify it.

## 3. Was there a design or coordination limitation?

**There was no technical block.** Accounting was reachable: delegation is all-to-all, Accounting has `propose_payment`, and every tool call succeeded. The Boss *chose* Customer Service and Inventory.

**There is, however, a coordination gap in our design [I]:**
1. **No step from "needs a human" to "something the human can act on."**
   - The Boss's prompt and the run task (`main.ticket_task`) ask agents to *report* what is proposed, what is blocked and what a human must decide. Neither asks them to have the responsible specialist **prepare** the proposal or draft that would let the person act.
   - The Boss correctly listed "decide how to address invoice 501" under `needs_human`, but nobody prepared a payment proposal.
   - In our architecture a person can only approve or reject an agent's proposal **[I]** (by design, R2). So **the human cannot act on #501 at all** unless an agent proposes it.
2. **Caution steered the Boss away from Accounting.** `get_ticket` deliberately adds "whether the goods billed on invoice #501 were delivered is not recorded" (our Problem 6 correction), and the Boss concluded "verify delivery before considering a payment decision". That is prudent, but a proposal is not a payment. The human could have weighed delivery when approving or rejecting.
3. **No instruction to draft an outcome message.** Customer Service drafted nothing: no "size S is unavailable" reply. Its prompt allows drafts but never asks for one when a request cannot be met.

**Contrast with ticket 102:** the Facilities prompt *does* say "Rent payments are proposed by Accounting: delegate to Accounting". Ticket 102 is therefore more likely to produce an actionable proposal. Tickets 101 and 103 have no equivalent instruction.

## 4. Is the absence of proposals a material risk?

**Yes: moderate to high.** The reasons:
- **[P]** The assignment centres on agents *preparing* payments and purchases for human approval (R2: "agents may prepare these actions"), and on processing tickets "until they are resolved". Ticket 101's premise (an overdue invoice blocking the only reprint vendor) is plainly built around the pay-to-unblock decision. A grader will probably look for that decision in 101.
- **[S]** The spec lets a ticket stay open if reported truthfully, and allows "$0" cash lines, so honesty is protected. The *demonstration* of the approval workflow on 101 is missing, though.
- If 102 produces a rent proposal, R2 will be demonstrated somewhere. But 101 would still show no attributable cash change, no Accounting involvement, and an open ticket with no actionable path.

## 5. Recommended corrective path [R]

**Recommendation: fix the coordination gap before ticket 102, disclose it, and, only with Dr. Ryan's explicit exception to the one-run policy, run 101 once more (attempt 3).**

1. **A narrowly scoped prompt correction** (text only; no code, tool or permission change):
   - **Shared rules:** "When you identify a decision a human must make about a payment, purchase, quote or message that falls within an agent's authority and the rules, have the responsible agent *prepare* the proposal or draft so the human can approve or reject it. A proposal moves no money and is not a recommendation to approve. State the open questions, for example unverified delivery, in its rationale."
   - **Boss routing:** "Open invoices linked to a ticket go to Accounting. Customer-facing outcomes, including 'cannot fulfil now', go to Customer Service as an unsent draft."
2. **Offline tests (scripted agents):** the rules text is present; existing behavior is unchanged; no new permissions.
3. **Disclosure in the evidence:**
   - Attempt 1 (configuration failure) and attempt 2 (completed, nothing proposed) stay as recorded.
   - The prompt change is documented as a workflow-design correction made *after* observing attempt 2.
   - Attempt 3 is labelled as such.
   - This is a **remedy for a design gap** that applies equally to all remaining tickets. It is **not** a retry aimed at a particular answer. Its result is whatever the agents do.
4. **Then Dr. Ryan decides on the evidence.** Possible decisions:
   - approve or reject a #501 payment proposal (rejecting is legitimate if delivery stays unverified);
   - review a customer draft;
   - decide 101's resolution (decline or keep open), worded truthfully.

**Alternatives considered:**
- **(a) Accept 101 as is,** report it open, and proceed. This is fully honest and needs no change, but it carries the grading risk in §4, and 102 and 103 run with the same gap.
- **(b) Close 101 now** as "declined", by Dr. Ryan's decision. Honest if worded as in §2, but it skips the payment decision the scenario is built around.
- **(c) A human-directed run feature** (the person adds an instruction such as "prepare the #501 payment for my decision"). Also principled, but it is new code and UI. More scope than (1).

## 6. Other grading risks before continuing

| Risk | Source | Mitigation [R] |
|---|---|---|
| Tickets 101 and 103 may stay open: fulfilment, acceptance and supply are unverifiable in our system | [P] "until resolved" vs [S] truthful reporting | The prompt fix (more actionable outputs); truthful human dispositions (§2); clear "open" labels with reasons |
| Ticket 103 has the same gap as 101 (purchase and quote need to be prepared to be decided) | [I] | The same prompt fix, before 103 |
| Re-running 101 conflicts with our one-run policy | [S] C-2 | An explicit, documented exception by Dr. Ryan; all attempts disclosed |
| Each additional live run costs about 30,000 tokens (attempt 2 used 29,652); the price is unknown | — | Capped by the budget; one run per change |
| No ordered/received step: inventory never rises; "resolved" for goods tickets rests on statements | [I] D7 | Report it as a known limitation in the evidence and in `harness.md` |
| `harness.md` completion (D-H) is still pending | [S] | Do it before final evidence (G5) |
| `resolved_board.html` needs genuine final-state screenshots per ticket | [S] | Capture them after each ticket's final decision (D7) |
| The full Boss report is kept in memory only | [I] | Saved after each run (done for 101) |
| The Reflection tab in `desk_tickets.html` is still "Coming later" | Problem 10 | Out of scope for Problem 9 |
