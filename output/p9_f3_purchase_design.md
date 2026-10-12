# F3: Purchase authorization versus expenditure

**Status: IMPLEMENTED** (Problem 9, follow-up 13) with Dr. Ryan's decisions:
- A: two steps.
- B: a purchase-kind payment linked to the proposal.
- C: both bases, with a confirmed amount and a recorded basis; a database estimate is labelled distinct from a supplier price.
- D: no inventory change.
- R3: approval allowed with a warning; execution refused while blocked (our choice).
- Commitments: purchases yield to obligations.

The as-built description is in `harness.md` §12.6 and `design.md` §13. The original proposal (follow-up 12) follows unchanged below for the record. Where it differs from the decisions above, the decisions prevail: R3 now blocks execution only, and commitments follow the rule just listed.

## 1. Current model (analysed read-only)

| Element | Today |
|---|---|
| **Purchase proposal** (`pending_actions`, kind `purchase`) | Created only by Inventory (`propose_purchase`): vendor, SKU, size, quantity. `amount` = qty × `pricing.unit_cost`, with `amount_basis = 'estimate_unit_cost'`. It records `vendor_blocked` and `blocking_invoice_ids` (R3). Status starts as `proposed`. |
| **Approval** (`approval._approve_purchase`) | Human-only. R3 is re-checked at decision time (refused while the vendor has an open invoice). Status becomes `approved`. **No cash, ledger or inventory change.** A purchase can never reach `executed`, although the column's CHECK allows it. |
| **Payment ledger** (`payments`, original table) | `kind`, `ref_id`, `amount`, `account`, `paid_at`, `approved_by`. Used today only by invoice and rent payments (D6 kinds `invoice` and `lease`). |
| **Cash** (`cash_accounts`) | Decreases only in `_execute_payment`: one transaction, R1 check including the D4 reserve, guarded update, two `audit.db` events. |
| **Inventory** | No code path changes it. There is no receipt step. |
| **Reads** | `get_financial_position` totals all `payments`. `list_pending_actions` and `GET /api/cash` show purchase *estimates* separately as "not cash". |
| **Reconciliation** | `audit_reconcile._row_matches` checks a `payments` row by kind, ref_id, amount, approved_by and paid_at, whatever the kind. |

**Relevant rules:**
- **R1:** no payment may leave cash insufficient.
- **R2:** human approval for payments **and purchases**.
- **R3:** a blocked vendor ships nothing.
- **R4:** when a payment occurs, update the related records.
- **R5:** the date is fixed.
- **R6:** cash only goes out; reprints are funded from the existing cash.
- **R7:** no vendor calls or messages, so the system cannot obtain a supplier confirmation itself.

**The gap:** an approved purchase can never become an expenditure. The Cash tab therefore can never show a purchase's cash effect, even when a person has every basis to spend.

## 2. Design principles (from Dr. Ryan's direction)
1. **Authorization ≠ expenditure.** Approving authorizes the order; a separate, explicit human act spends cash.
2. **No estimated cost becomes an executed payment** unless all three hold:
   - (a) a **supported transaction amount**;
   - (b) a **supplier**;
   - (c) **explicit human authorization**.
3. **Because of R7, supplier facts reach the system only as the person's attestation.** Every attestation is recorded as a statement, never as a verified fact.
4. **Reuse the proven payment machinery:** the same atomic transaction, R1/D4 check, guarded updates, both audit records, and reconciliation.

## 3. Proposed implementation: a two-step purchase lifecycle

```
proposed --(human: approve = authorize; R3)--> approved --(human: execute = spend; R3, R1, supported amount, supplier)--> executed
    \--(human: reject)--> rejected                \--(human: reject before execution)--> rejected
```

### 3.1 New human operation: `approval.execute_purchase(paths, trail, action_id, *, decided_by, amount_basis, amount=None, supplier_confirmed, confirmation_note, note=None)`
It uses the existing decision frame (`_decide`): run lock, reconcile-first, JSON `human_decision_requested`, one SQLite transaction with audit.db, then `human_decision_finished`.

**Checks, all inside one transaction:**
1. The proposal exists, `kind = 'purchase'`, and `status = 'approved'`. A proposed purchase must be approved first; rejected or executed ones are refused (`NOT_PENDING`).
2. **R3**, re-checked now: the vendor has no open invoice.
3. **Supported amount**, one of two declared bases:
   - **`recorded_unit_cost`:** amount = qty × the current `pricing.unit_cost`, exactly. The person attests that the supplier agreed to supply at the shop's recorded unit cost.
   - **`supplier_confirmed`:** the person enters the amount (positive, two decimals) and a `confirmation_note` (at least 10 characters) saying how and when the supplier confirmed it. Both are recorded as an attestation.
   - The estimate is never used silently. The proposal's estimate and the executed amount are both recorded, with their difference.
4. **Supplier:** `supplier_confirmed` must be `true`, attesting that "this vendor has agreed to supply this item and quantity". The vendor's recorded *specialty* alone never suffices.
5. **R1 with the D4 reserve:** stored balance − approved, unexecuted payment commitments − amount ≥ 0. Otherwise `REFUSED_RULE` and nothing changes.

**Writes, in the same transaction:**
1. **`payments`:** `INSERT (kind 'purchase', ref_id = the proposal ID, amount, account 'checking', paid_at = desk.date_today, approved_by = the person)`. This is a new `kind` value alongside `invoice` and `lease`, documented as a D6-style convention.
2. **`cash_accounts`:** `balance = balance − amount`. The update is guarded (expected balance, ≥ 0), and the after-balance is verified.
3. **`pending_actions`:** `status 'executed'`, `executed_at`, `payment_id`. The approval fields (`decided_by`, `decided_at`) are kept, and the executor is recorded in the payment and the audit event.
4. **`audit.db`:** one `purchase_executed` event (actor `human:<name>`, `call_id` = decision ID). It records the payment row, the basis, the attestation and note, the estimate, the amount, the difference, the vendor, and the balance before and after.
5. **Inventory: unchanged.** Executing pays for an order; it does not deliver goods. Arrival needs lead days, and the date never moves (R5), so receipt cannot occur in the model. The record says so.

**No vendor invoice row is created** (recommended option, B below). There is no real vendor bill to record, so the purchase proposal is the related record (R4).

### 3.2 API and dashboard
- **`POST /api/actions/{id}/execute`** (human header required). The body is typed with unknown fields forbidden: `{decided_by, confirm: true, amount_basis, amount?, supplier_confirmed: true, confirmation_note?, note?}`. Errors use the existing codes: 404, 409 `NOT_PENDING` or `REFUSED_RULE` (R1 or R3), 422, 503.
- **Dashboard:** an approved purchase shows **"Execute purchase (spends cash)…"**.
  - The dialog states that cash falls by the amount, that this pays the vendor for the order, and that it is not proof of delivery.
  - It asks for the basis, the amount if needed, the supplier attestation and the confirmation note, plus the name and the confirmation box.
  - It uses the same single-flight guard.
  - Cash shown afterwards comes only from `GET /api/cash`.

### 3.3 Effects on reads, the Cash tab and reconciliation
- `GET /api/cash` `payments_executed` lists purchase payments. "Not counted as cash" keeps showing only proposed and approved (unexecuted) estimates.
- `get_financial_position` already totals all `payments`, so agents in later runs see the spend.
- **Reconciliation:** starting cash − Σ `payments.amount` = ending cash. Each purchase payment is attributed: payment → proposal (`ref_id`) → ticket → executor → audit.db `purchase_executed` (decision ID) → JSON decision events. `_row_matches` already validates `payments` rows of any kind.

### 3.4 Unchanged
Agents still cannot approve or execute: there is no MCP tool, the authorizer denies it, and agent code never imports `approval`. R3 is enforced at both approval and execution. Proposals still reserve no cash.

## 4. Options for Dr. Ryan

| Decision | Options | Recommendation |
|---|---|---|
| **A. Steps** | (1) Two steps: approve, then execute. (2) Approve and execute in one decision. | **(1):** it keeps authorization and expenditure distinct, as directed |
| **B. Ledger record** | (1) Create a vendor invoice row (status `paid`) and pay it as `kind 'invoice'`. (2) No invoice row; `payments.kind = 'purchase'`, `ref_id` = the proposal. | **(2):** avoids recording a vendor bill the shop never received |
| **C. Supported amount bases** | `recorded_unit_cost` (an attested agreement at the recorded cost); `supplier_confirmed` (an attested amount with a note); or both | **Both,** each labelled as an attestation |
| **D. Receipt step** (inventory += qty on an attested receipt) | Add it now, or not | **Not now.** R5 means nothing arrives; an attestation-only receipt would imply fulfilment the system cannot show |

## 5. Business effects (illustrative, not decisions)
- **Ticket 103:** 12 hoodies, estimated $264. Even after approval, execution needs:
  - Bulldog unblocked (#501 paid, R3);
  - a supplier attestation (no vendor is recorded as making navy hoodies);
  - enough cash (R1).

  After both obligations are paid, $160 remains, so executing $264 is **refused (R1)**. That is a correct, demonstrable result. If rent is unpaid, cash would allow it; the choice is Dr. Ryan's.
- **Ticket 101:** a second tee order is normally not proposed while #501's fulfilment is unknown (Inventory rule). If one were, the same checks apply.

## 6. Tests (offline, to be written with the implementation)
- Execution only from `approved`; refused from `proposed`, `rejected` and `executed`; a double execution is refused.
- R3 is refused at execution even if approval happened earlier (an invoice reopened in setup).
- R1 is refused, with the D4 reserve counted. Nothing changes on refusal (digest check).
- `recorded_unit_cost` must equal qty × unit cost. `supplier_confirmed` needs an amount and a note. Missing attestations are refused (422).
- The atomic write is verified: the payment row, cash, proposal status and audit.db event. Inventory is unchanged. Reconciliation stays consistent.
- A crash after the commit is reconciled (as A2). No agent can reach the route or the function (import scan, tool list).
- API: the header, typed body, codes. Dashboard: the dialog text, single submit, the balance shown only from the backend, and the refusal shown.

## 7. Size and documents
- **Code:** about 150 lines (`approval.py` function, one route, a dialog), plus about 25 backend and 4 frontend tests.
- **Documents:** `harness.md` §11 and §12, and `design.md` (routes, the Cash tab, the purchase lifecycle). The documented Problem 7 statement "purchase approval moves no cash" stays true; execution is a new, separate step.
