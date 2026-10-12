# Ticket 101 decision brief (Problem 9, follow-up 21)

- **Prepared:** 2026-10-11 (UTC), read-only.
- **Sources:**
  - the working database `data/campus_customs_new.db`, opened read-only;
  - the Attempt 3 evidence in `output/p9_runs/101_attempt3_*.json`;
  - business rules R1–R8 (`harness.md` §7a) and the unspecified points (§7b);
  - the decision code in `mcp_server/approval.py`.
- **Not done:** no proposal decided, no message sent, no payment or purchase, no reset, no run and no status change.

**Labels:**
- **[Fact]** is read directly from the records.
- **[Computed]** is arithmetic on recorded values.
- **[Assumption]** is not established by any record.
- **[Recommendation]** is my advice; the decision is Dr. Ryan's.

---

## 1. Basis for payment proposal 1

| Item | Value | Label |
|---|---|---|
| Proposal | ID 1, kind `payment`, status `proposed`, created by Accounting in Attempt 3 (run `84270c9b…`) | Fact |
| Ticket link | Ticket 101 (Tauhid Zaman, one size S "Classic Bulldog Tee") carries `invoice_id = 501` | Fact |
| Invoice | #501, "Rush reprint CC-TEE-WHITE S", status `open` | Fact |
| Vendor | Vendor 1, Bulldog Print Co (apparel reprint, lead time 5 days) | Fact |
| Amount | $840.00, `amount_basis = invoice_amount` (the full stored invoice amount) | Fact |
| Due date | 2026-08-28; shop date (`desk.date_today`, R5) 2026-08-31 | Fact |
| Overdue | 3 days overdue | Computed (R5) |
| Account | Checking, $3,400.00 | Fact |
| Checks at proposal | `fundable_at_proposal = true`; R1 passed (840 ≤ 3,400); cash if all open payment proposals executed = $2,560 | Fact (Attempt 3 tool result) |
| Warnings | None stored on the proposal. `vendor_blocked = false`, because a payment to a vendor is not restricted by R3 (only new shipments are). | Fact |
| Caveat in the rationale | "Whether the invoiced goods were delivered is not recorded; human review should resolve that before deciding whether to approve payment." | Fact (agent's own words) |

---

## 2. What approving would and would not do

**What approval does** (from `approval.py`; each is a fact about the code):
- Approval of a **payment** executes it immediately, in one atomic transaction. There is no separate execute step for payments.
- It writes a `payments` row: kind `invoice`, ref 501, $840, paid on 2026-08-31, `approved_by` = your typed name.
- It reduces checking from $3,400 to $2,560.
- It sets invoice #501 from `open` to `paid`.
- It marks proposal 1 `executed`, and records the decision in `audit.db` and the JSON trail.

**What it accomplishes:**
- **[Fact]** The overdue obligation is settled in the records.
- **[Fact, via R3]** Bulldog Print Co then has no open unpaid invoice. A new purchase from them (for example, a tee reprint) would no longer be blocked at execution.

**What it does NOT establish:**
- **[Fact]** It does not record that any shirts were delivered. No table records deliveries or receipts, and approval does not change `inventory`. Size S stays at 0.
- **[Fact]** It does not fulfil Tauhid Zaman's order, send any message (R7), or resolve Ticket 101.
- **[Assumption, not established]** Under one reading of R3, the vendor holds the rush reprint until #501 is paid, so payment would release it. Under another reading, the reprint was already shipped. No record supports either reading.

---

## 3. Cash consequences

Starting point **[Fact]**: checking $3,400; no payments made; no approved, unexecuted commitments.

| Scenario | Checking afterwards | Label |
|---|---|---|
| Pay #501 only | $2,560 | Computed |
| Pay #501, then rent ($2,400, due 2026-09-02, ticket 102) | **$160** (≥ $0, so R1 permits both; there is no minimum reserve) | Computed |
| Pay rent only | $1,000 | Computed |
| Pay neither | $3,400 | Computed |

**Facts:**
- Rent is due in 2 days and is not yet overdue (R5).
- There is no rent payment proposal yet; ticket 102 has had no run.

**Ticket 103** (Yale AI Club, 20 navy hoodies size M, bulk discount):
- **[Fact]** 8 size M hoodies are in stock. A quote adds no cash (R6).
- **[Computed]** Filling all 20 would need 12 more. At the stored unit cost of $22, that is an **estimate** of $264. No vendor price exists.
- **[Computed]** If #501 and the rent are both paid, $160 remains. That is less than the $264 estimate, so a 12-hoodie restock could not be funded (R1), and purchases yield to obligations (F3).
- **[Assumption]** `harness.md` §7a reads R3 as applying to hoodies as well, which treats Bulldog Print Co as the hoodie supplier. The database has no table linking vendors to products, so this is an interpretation.
- **[Computed]** A one-tee reprint for ticket 101 at the stored $8 unit cost would be an estimated $8. That fits within $160.

**[Recommendation]** Decide #501 together with the rent, not in isolation:
- Paying both is allowed but leaves $160.
- That excludes a hoodie restock for ticket 103 unless 103 is served from the 8 in stock, or the discount request is declined or reduced.

---

## 4. Can any record establish the status of the rush reprint?

**No.** What the records show **[Fact]**:
- Invoice #501 exists, is `open`, is for $840, and is described as "Rush reprint CC-TEE-WHITE S". It has no quantity, ship date or delivery field.
- Size S tee stock is 0. The `inventory` table has no history, so it cannot show whether stock arrived and was sold, or never arrived.
- There is no purchase-order, shipment or receipt table, and no message history (R7).
- No agent tool can record a delivery.

**What cannot be inferred:**
- **[Assumption, rejected]** $840 ÷ $8 stored unit cost = 105 shirts. Rush pricing is unknown, so the quantity cannot be inferred.
- **[Assumption, rejected]** "Stock is 0, so nothing arrived." This is equally consistent with goods that arrived and were sold, or were never entered.

**[Recommendation]** Treat delivery status as **unknown**. Establishing it requires information from outside the database: the vendor's shipment confirmation, or a physical stock count. The system has no field in which to record it.

---

## 5. Customer email draft

**Draft 1** **[Fact]**:
- Unsent, created by Customer Service.
- Recipient "Tauhid Zaman", taken from `tickets.requester`. **No email address is recorded.**
- The stored warning flags the word "confirm".

Issues in the current text:

| Issue | Text |
|---|---|
| Internal product code | "CC-TEE-WHITE" |
| Internal supplier and invoice details | "an open invoice with Bulldog Print Co for a rush reprint" |
| Internal records language | "Our current stock record", "The linked record" |
| No promise made (acceptable) | "can't confirm availability or shipment" |

**[Recommendation]** Suggested revision. It contains no code, supplier, invoice or delivery promise. Every statement is supported by the records.

> **Subject:** Your Classic Bulldog Tee order
>
> Hi Tauhid,
>
> Thank you for your order of a Classic Bulldog Tee in size S. That size is currently out of stock, so we can't fill your order right away. We are looking into restocking it and will write to you again as soon as we have a firm update.
>
> If a different size would suit you in the meantime, sizes M, L and XL are currently in stock. Just reply and let us know.
>
> Best regards,
> Campus Customs

Support for each statement:
- **The product name** is `inventory.name`.
- **Size S = 0, M = 5, L = 3, XL = 2** are `inventory` facts.
- **"Looking into restocking"** describes a review in progress. It is not a delivery date or a promise.
- **The other-sizes paragraph** is optional; delete it if you prefer not to suggest alternatives.

**Delivery and editing:**
- Per R7 the system never sends this. You would send it yourself, and need a contact address the database does not hold.
- No tool or route edits an existing draft. Using the revision means either:
  - you send the revised wording yourself; or
  - a later authorized run creates a new draft with it.

---

## 6. Conditions before Ticket 101 can truthfully be marked resolved

The system already enforces two conditions **[Fact]**:
- It refuses resolution while any Ticket 101 proposal awaits a decision. Proposal 1 must therefore be approved or rejected first.
- Resolution requires your explicit confirmation and your own written summary of the business outcome. That summary is recorded as your statement, not as a verified fact.

Business conditions **[Recommendation]**. The customer's need is met in one of these ways, each confirmed by you:
- **(a)** A size S tee is in hand and has been given or shipped to Tauhid Zaman. That would require the reprint (or a new purchase) to arrive, which is outside the records.
- **(b)** Tauhid Zaman has accepted another size.
- **(c)** Tauhid Zaman has cancelled or withdrawn the order.

And:
- **Customer told:** Tauhid Zaman has been informed by you, outside the system (R7), and the message makes no unsupported promise.
- **Invoice #501 dealt with:** either paid after its validity is accepted, or deliberately left unpaid with the reason noted. The ticket must not be marked resolved on the assumption that the reprint arrived.
- **Nothing pending:** no purchase for ticket 101 left approved but unexecuted, unless you intend that and note it.

Not sufficient on their own **[Fact]**:
- the agents' run finishing (outcome `awaiting_human_approval`);
- paying #501;
- writing the draft.

---

## Summary of open decisions (all yours)

1. **Proposal 1** ($840 to invoice #501): approve (pays immediately; checking becomes $2,560) or reject (nothing changes). Consider it together with the $2,400 rent and ticket 103.
2. **Delivery status of the reprint:** obtain it outside the system, if you wish, before deciding.
3. **Customer email:** use the original, the revision above, or your own wording. You send it yourself.
4. **Resolution:** only after section 6 is satisfied.
