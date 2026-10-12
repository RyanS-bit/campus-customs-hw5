# Tickets 101, 102, 103: dependency and approval map (Problem 5, Stage 6)

**What this is:** preparation for the three-ticket workflow.
- It maps facts, dependencies, agents, tools, and approval boundaries, so the eventual run can be checked against it.
- It is **not** Problem 6 ("Plan the three tickets"), which has not begun.
- It does **not** resolve any ticket, and no ticket is marked resolved.

**Source of facts:** every figure below was returned by the MCP tools against the working database. The live development-mode calls of 2026-10-09 are in `output/mcp_live_regression_p5.json`. The business date is `desk.date_today` = 2026-08-31 (R5).

---

## 1. Facts the agents will see

| Fact | Value | Tool |
|---|---|---|
| Cash (checking) | $3,400 stored | `get_financial_position` |
| Invoice 501 | Bulldog Print Co, $840, due 2026-08-28, **open, 3 days overdue** ("Rush reprint CC-TEE-WHITE S") | `get_financial_position`, `get_ticket(101)` |
| Lease 1 | Elm City Properties, $2,400, next due 2026-09-02 (**2 days**) | `get_financial_position`, `get_ticket(102)` |
| Bulldog Print Co (vendor 1) | Apparel reprint, 5-day lead time, **blocked (R3)** by invoice 501; no arrival date can be estimated | `list_vendors` |
| Elm City Gifts / QuickShip CT | Mugs and small goods (3 days) / local courier (1 day); not blocked | `list_vendors` |
| Tee CC-TEE-WHITE | S **0**, M 5, L 3, XL 2; cost $8, list $28 | `check_inventory` |
| Hoodie CC-HOOD-NAVY | S 4, **M 8**, L 14, XL 6; cost $22, list $58 | `check_inventory` |
| Payments recorded | 0 | `get_financial_position` |
| Not recorded anywhere | Whether the goods on invoice 501 were delivered; any vendor quote; any customer or landlord contact address | `get_ticket` `unknowns` |

## 2. Per ticket

### Ticket 101: Tauhid Zaman wants one Classic Bulldog Tee, size S
- **Situation:** size S stock is 0. The only restock record is invoice 501, a rush reprint of exactly this item, which is unpaid and overdue. Its delivery status is unknown.
- **Agents and tools:**
  - Boss: `get_ticket` → delegates.
  - Inventory: `check_inventory`, `list_vendors`; optionally `propose_purchase`.
  - Accounting: `propose_payment(invoice 501, $840)`.
  - Customer Service: `draft_message` to the requester.
- **Agents can do on their own:** read the facts, propose paying 501, draft an honest status reply ("out of stock; restock in progress; no date"), and flag the unknown delivery status.
- **Needs a human:**
  - Approve or reject the 501 payment (R2).
  - Find out whether the 501 tees were delivered; this is not in the data.
  - Approve the draft before anything is said to the customer (R7).
- **External events, outside the system:**
  - Bulldog ships the reprint, once paid and unblocked (R3).
  - The tee arrives.
  - The customer accepts.
- **Ticket can be closed:** only through the human ticket-status mechanism of Problem 7, after the tee is actually available or the customer is otherwise answered. Agents cannot close it.

### Ticket 102: Elm City Properties rent notice
- **Situation:** $2,400 rent is due 2026-09-02. It is fundable from $3,400 on its own.
- **Agents and tools:**
  - Boss → Facilities: `get_ticket(102)`.
  - Facilities → Accounting: `propose_payment(lease 1, $2,400)`.
  - Facilities: `draft_message` to the lease landlord. The recipient comes from `leases.landlord`.
- **Agents can do on their own:** propose the rent payment and draft an acknowledgement. The draft must not promise payment or a date before approval; the commitment checker flags dollar amounts and dates, and those warnings stand.
- **Needs a human:** approve the payment (R2), and approve the draft (R7).
- **External events, outside the system:** the landlord receives payment.
- **Ticket can be closed:** after a human-approved payment is executed (Problem 7 executor) and the status is updated by a human.

### Ticket 103: Yale AI Club wants 20 hoodies, size M, at a bulk discount
- **Situation:** 8 on hand, so 12 short. **No vendor is recorded as able to supply navy hoodies.**
  - Bulldog Print Co's stored specialty is the free text "apparel reprint", and its only record is invoice 501, a reprint of **tees**, not hoodies.
  - Bulldog is also **blocked by invoice 501** (R3).
  - *(Revised in Stage 7: an earlier wording implied that paying 501 would make a hoodie reprint possible. The records do not establish that.)*
- **Cost:** the restock *estimate* is 12 × $22 = **$264**, using the stored unit cost. It is not a quote from any vendor.
- **Agents and tools:**
  - Boss → Customer Service: `check_inventory`, then `propose_quote` (between $22 and $58, exactly 20 × M), then `draft_message` to the requester.
  - Customer Service → Inventory: `list_vendors`, `propose_purchase(vendor 1, 12 × M)`. The proposal is flagged as blocked and as an estimate.
- **Agents can do on their own:** propose a quote within policy, propose a restock flagged as blocked, and draft a reply that promises no delivery date.
- **Needs a human:**
  - Choose the discount; any quote between cost and list price is allowed by policy, but the level is a business judgment.
  - Approve the quote.
  - Approve the purchase, but only after 501 is paid (R3) and only if cash allows (R1).
  - Identify a supplier for navy hoodies and obtain a quote. Whether Bulldog can supply them is **unknown**: its specialty is stored text, and its only record is a tee reprint. A purchase proposal naming Bulldog is a *hypothesis for human review*; `propose_purchase` itself asks a human to confirm the supplier.
- **External events, outside the system:**
  - A vendor quote.
  - The shipment.
  - The customer's acceptance of the quote, which the system never records on its own.
- **Ticket can be closed:** only after a human records the outcome (Problem 7).

## 3. Cross-ticket dependencies

```
invoice 501 paid (human approval, Problem 7) ──► Bulldog no longer blocked by R3
      ├──► the 501 tee reprint can proceed IF not already delivered (delivery status unknown) ── ticket 101
      └──► a hoodie order from Bulldog becomes PERMISSIBLE under R3, but Bulldog's ABILITY to supply
           navy hoodies is UNKNOWN (no record) ── ticket 103 needs a human to confirm a supplier and a quote
cash $3,400 ──► −$840 (501) −$2,400 (rent) = $160 hypothetical remaining
                └──► estimated $264 hoodie restock > $160  →  a human must prioritise (R1; flagged by the tools)
```
- **Rent (102) and invoice 501 (101):** both are fundable, together and separately (R1).
- **Paying 501:** a necessary condition for any *new* Bulldog shipment (R3). It is **not** a sufficient condition for a hoodie reprint. Paying it removes the block; it does not create a supplier.
- **Ticket 103's restock:** depends on a confirmed supplier, which is unknown, and on cash. The tools flag the cash conflict; they do not decide it.
- **No revenue:** quotes or sales add no cash (R6), so ticket 103's sale cannot fund its own restock.

## 3a. What kind of claim each statement is (Stage 7 review)

| Kind | Items |
|---|---|
| **Verified database facts** (read through MCP; matched by SQL in `output/p5_stage3_live_crosscheck.txt`) | Cash $3,400. Invoice 501 ($840, open, 3 days overdue, Bulldog, "Rush reprint CC-TEE-WHITE S"). Lease $2,400 due 2026-09-02. Stock levels. Unit costs and list prices. Vendor specialties (as stored text) and lead times. Tickets 101–103 as stored. 0 payments. Business date 2026-08-31. |
| **Calculations on those facts** | 3 days overdue; 2 days until rent; $3,240 of obligations; **hypothetical** $160 remaining if both were paid; hoodie M shortfall of 12. |
| **Estimates** | Restock 12 × $22 = **$264** (stored unit cost; not a vendor quote). Hypothetical arrival dates (business date + lead days) for unblocked vendors; no order exists. |
| **Assignment rules** | R1–R8 (`output/harness.md` §7a). |
| **Proposed shop policies (not assignment requirements)** | Payment = the full stored amount. Quote between unit cost and list price, matching the ticket exactly. Drafts only to the recorded requester or landlord. Commitment wording flagged. Reserve cash for approved commitments (Problem 7). Invoice → `paid`; rent due date +1 month (Problem 7). |
| **Unavailable; must come from outside the system** | Whether the goods on invoice 501 were delivered. **Whether Bulldog (or anyone) can supply navy hoodies, and at what price.** Any vendor quote or shipment date. Customer acceptance of any quote. Contact addresses. The landlord's response. |

## 4. Approval boundaries

| Action | Who can prepare it | Who decides | Built? |
|---|---|---|---|
| Read facts | All agents (within their tools) | — | Yes |
| Payment proposal | Accounting | Human | Proposal: yes. Approval and execution: **Problem 7** |
| Purchase proposal | Inventory | Human (after R3 is cleared) | Proposal: yes. Approval: **Problem 7** |
| Quote proposal | Customer Service | Human; then the customer accepts outside the system | Proposal: yes. Approval: **Problem 7** |
| Draft message | Customer Service (customers), Facilities (landlord) | Human sends it (R7) | Draft: yes. Review: **Problem 7** |
| Ticket status | Nobody (agents can only recommend) | Human | **Problem 7** (spec §7.7) |

## 5. Verified offline (Stage 6)
`tests/test_stage6_workflows.py` uses the real MCP servers, rules, database writes, delegation, and audit trail. Scripted offline models stand in for `gpt-6-luna`, on temporary databases. It covers:
- Ticket 101, ticket 102, and ticket 103 individually
- All three in one run, with the cash competition
- Stopping at approval boundaries and refusals
- Reporting unavailable information
- A failure in the middle of a workflow
- A shared usage limit stopping a multi-ticket run
- Run history preserved across runs

In every case, only proposals and drafts are created; cash, invoices, leases, inventory, payments, and ticket statuses stay unchanged.

**Limit:** the scripted models make sensible choices *by construction*. These tests show the system enforces the rules whatever the model decides. They do not show what `gpt-6-luna` will decide.

## 6. Unresolved before a real ticket run
1. **The Problem 7 approval routes are not built:** human approval and rejection, payment execution with the R1 check at write time, ticket-status updates, and draft review. Without them, a real run can only end in proposals and drafts.
2. **Missing data:** whether the invoice 501 goods were delivered; vendor quotes; contact addresses; whether Bulldog makes hoodies.
3. **The live behavior of `gpt-6-luna`** as an agent is untested. Only connectivity is confirmed.
4. **A real run needs Dr. Ryan's separate authorization.** Before it, the working database should be reset (R8), with the run recorded in `output/audit_trail.json`.
