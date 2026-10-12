# Integrated decision analysis: Tickets 101, 102 and 103 (Problem 9, follow-up 22)

- **Prepared:** 2026-10-11, read-only.
- **Sources:**
  - the working database (read-only);
  - the Attempt 3 evidence;
  - R1–R8 and §7b in `harness.md`;
  - the decision code (`mcp_server/approval.py`);
  - `p9_ticket101_decision_brief.md`.
- **Not done:** nothing was decided, executed, created, sent, resolved or reset, and no run was started.

**Labels:**
- **Confirmed** = a stored amount or record.
- **Estimate** = a stored unit cost × a quantity (not a supplier price).
- **Hypothetical** = a transaction that has not happened.
- **Unknown** = no record establishes it.

---

## 1. Starting position

| Item | Value | Label |
|---|---|---|
| Shop date (R5) | 2026-08-31. Fixed: the database date never advances. | Confirmed |
| Checking | $3,400.00; no payments made; no approved, unexecuted commitments | Confirmed |
| Invoice #501 (Ticket 101) | $840.00 to Bulldog Print Co, due 2026-08-28: **3 days overdue**. "Rush reprint CC-TEE-WHITE S". | Confirmed |
| Rent (Ticket 102) | $2,400.00 to Elm City Properties, due **2026-09-02** (in 2 days; not overdue) | Confirmed |
| Ticket 101 | 1 size S Classic Bulldog Tee; size S stock 0 | Confirmed |
| Ticket 103 | Yale AI Club: 20 navy hoodies size M with a bulk discount; size M stock 8 | Confirmed |
| Hoodie list price / unit cost | $58 / $22. No discount policy exists (§7b). | Confirmed |
| Tee unit cost | $8 | Confirmed |
| Proposals | Only proposal 1 exists (pay #501, $840). There is no rent proposal and nothing for Ticket 103. | Confirmed |
| Incoming revenue | None modeled. Sales and quotes add no cash (R6). | Rule |

**Confirmed obligations total:** $840 + $2,400 = **$3,240**. That leaves **$160** if both are paid. Both are permitted under R1, which has no minimum reserve.

**Estimates (only if pursued):**
- **12 hoodies to complete 20 size M:** 12 × $22 = **$264**. This is an estimate: no supplier price, minimum order or supplier is recorded.
- **One size S tee:** 1 × $8 = **$8**. Also an estimate; the minimum order is unknown.

**Unknowns that affect every course:**
- whether the #501 reprint was delivered or is being held;
- who supplies hoodies (the database has no table linking vendors to products);
- shipping dates;
- whether either customer accepts any offer;
- how strictly the landlord treats the due date.

---

## 2. Courses of action

### Course 1: pay invoice #501 first

| | Checking | Label |
|---|---|---|
| After approving proposal 1 | **$2,560** | Hypothetical, computed |
| If rent is also paid later | **$160** | Hypothetical, computed |

- **Still outstanding:**
  - rent of $2,400 (needs a proposal first; see §3);
  - Ticket 101 fulfilment;
  - Ticket 103.
- **Advantages:**
  - Clears the only overdue obligation.
  - The proposal already exists, so this is executable today.
  - Lifts the R3 block on Bulldog Print Co, so a tee reprint purchase could be executed later.
- **Disadvantages:**
  - Pays $840 without any record that the reprint was delivered or will be released.
  - Once both obligations are paid, only $160 remains. That is below the $264 hoodie estimate, so a 12-hoodie restock could not be funded (R1, and purchases yield to obligations).
  - Rent still needs its own run and proposal before 2026-09-02.
- **Unresolved:**
  - whether payment releases the reprint;
  - whether Bulldog Print Co is a possible hoodie source (unknown, not assumed).

### Course 2: pay the rent first

| | Checking | Label |
|---|---|---|
| After paying the rent | **$1,000** | Hypothetical, computed |
| If #501 is also paid later | **$160** | Hypothetical, computed |

- **Still outstanding:**
  - #501 ($840, overdue);
  - Ticket 101 fulfilment;
  - Ticket 103.
- **Advantages:**
  - Secures the shop lease, the obligation with the largest operational consequence if missed (the consequence is a judgement, not recorded).
  - $1,000 stays available while #501 is clarified.
- **Disadvantages:**
  - **Not executable today:** no rent proposal exists. It requires your authorization of a Ticket 102 agent run, then your approval.
  - #501 stays overdue, and the R3 block on Bulldog Print Co continues.
  - If both are eventually paid, the result is the same $160.
- **Unresolved:**
  - #501 delivery status;
  - the hoodie supplier.

### Course 3: preserve cash while seeking clarification

| | Checking | Label |
|---|---|---|
| Now | **$3,400** (unchanged) | Confirmed |

- **Still outstanding:**
  - #501 (overdue, now possibly longer);
  - rent (due 2026-09-02);
  - Tickets 101 and 103.
- **Advantages:**
  - No money committed before the facts are known: whether the reprint was delivered, who supplies hoodies and at what price, and whether the club accepts.
  - Keeps every option open.
- **Disadvantages:**
  - #501 remains overdue and the R3 block continues.
  - The rent deadline is close. In the records the date never advances, but in practice a delay could make the rent late.
  - Clarification happens outside the system and records nothing in it.
- **Unresolved:** all of the unknowns above. This course exists to answer them.

### Comparison

| | Course 1 | Course 2 | Course 3 |
|---|---|---|---|
| Cash after the first step | $2,560 | $1,000 | $3,400 |
| Cash if both obligations are paid | $160 | $160 | $160 (when eventually paid) |
| Overdue obligations left | none | #501 | #501 |
| Rent secured | no | yes | no |
| Bulldog R3 block lifted | yes | no | no |
| Executable now in the system | yes (proposal 1) | no (needs a 102 run) | yes (no action) |
| Pays before facts are known | yes (#501) | no (rent is undisputed) | no |

**Main point:** cash is enough for both confirmed obligations. The order changes only:
- what is exposed while facts are unknown;
- whether R3 is lifted early.

The real constraint comes after both are paid: $160 cannot fund the $264 hoodie estimate.

---

## 3. What the system can execute versus what needs outside action

**The system can execute, through your dashboard decisions:**
- **Approve proposal 1.** Pays #501 immediately: cash −$840, invoice `paid`, payment recorded.
- **Reject proposal 1.** Nothing moves.
- **Approve a quote.** Records authorization only; nothing is sent and no revenue is added. A quote proposal must exist first.
- **Approve, then separately execute, a purchase.** Execution pays a confirmed amount (F3). It requires the vendor not to be blocked (R3) and cash after obligations. Inventory is **not** updated.
- **Resolve a ticket.** Your recorded statement. Refused while proposals await a decision.

**Requires your authorization of an agent run** (this is the only way proposals are created; there is no human route to create one):
- the rent payment proposal (Ticket 102);
- a quote and/or hoodie purchase proposal (Ticket 103);
- any tee reprint purchase proposal (Ticket 101).

**Requires outside information or human follow-up (cannot be recorded in the system):**
- the #501 reprint status, from the supplier or a stock count;
- the hoodie supplier, price, minimum order and lead time;
- the customers' acceptance (Tauhid Zaman; the Yale AI Club);
- sending any message (R7; no customer contact addresses are stored);
- recording goods received (no tool updates inventory).

---

## 4. Recommended sequence of human decisions (Recommendation; the decisions are yours)

1. **Rent first (Course 2).**
   - Authorize the Ticket 102 run so Accounting can propose the $2,400 rent payment, then approve it if the proposal is correct. Cash becomes $1,000.
   - **Why:** rent is undisputed, due in 2 days, and protects the shop. It needs no outside facts.
2. **Clarify #501 in parallel (from Course 3), outside the system.** Ask the supplier whether the size S reprint shipped or is held pending payment.
3. **Decide proposal 1.**
   - If #501 is valid, approve it. Cash becomes $160 and the R3 block lifts.
   - If it is disputed, reject it or keep it pending, and record your reason.
   - **Trade-off:** paying promptly limits how long the invoice is overdue, but committing $840 before the facts are known risks paying for goods not received. Holding protects $840 but prolongs the overdue status and the R3 block.
4. **Ticket 103 last, sized to the cash actually left.**
   - Authorize its run only after steps 1–3, so the agents see the real balance.
   - If both obligations are paid ($160), an order of 20 can be met only from the 8 in stock, unless a funded restock source is confirmed. A quote for the 8, or a declined or reduced discount, are the options that need no purchase.
   - Whether the club accepts any offer is unknown.
5. **Ticket 101 fulfilment.**
   - Once #501 is settled and the reprint status is known, either the reprint arrives or a one-tee reprint purchase becomes possible (estimate $8, executable once R3 is lifted).
   - Send the revised customer email yourself.
   - Resolve the ticket only under the conditions in the Ticket 101 brief, §6.

**Why this order:**
- It pays the certain, time-critical obligation first.
- It commits no money against the unverified delivery until the facts are checked.
- It decides the discretionary request (Ticket 103) last, when the remaining cash is known.

**Its cost:** #501 stays overdue a little longer, and Bulldog Print Co stays blocked until step 3.

**If you prefer Course 1:** the end state is the same ($160 once both are paid). The difference is only that $840 leaves before the delivery question is answered.
