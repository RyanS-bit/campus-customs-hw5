# Problem 5: MCP tool interface specification (D16, revision 2, approved)

**Approval:**
- Dr. Ryan conditionally approved D16 on 2026-10-09 (Problem 5, follow-up 6).
- His corrections are in **section 7**, which takes precedence over sections 1–6 wherever they conflict.
- The statements that conflicted have also been corrected in place, each marked *(rev. 2)*.

**Status:** specification only. Nothing in this document is implemented. Written 2026-10-09 for Dr. Ryan's review (Problem 5, follow-up 5). It refines `output/p5_architecture.md` §3 and §19. It does not redesign them.

**Labels:**
- **[A]** an assignment requirement (rules R1–R8 in `output/harness.md` §7a)
- **[P]** a proposed shop policy, not required by the assignment
- **[E]** an engineering convention

---

## 1. Conventions that apply to every tool

### 1.1 Connections [E]
- **Read tools:** they open the working database as they do today, in SQLite read-only mode (`?mode=ro`), so they physically cannot write.
- **Write tools:** `propose_*` and `draft_message` open a read-write connection with:
  - the allowlist authorizer (architecture §18.3)
  - `audit.db` attached (§14, §18.2)
  - `journal_mode=DELETE`, `synchronous=FULL`, `foreign_keys=ON`, and a 5-second busy timeout
  - `BEGIN IMMEDIATE`
- **Paths:** every path comes from `mcp_server/paths.py`. The default is `data/`, with no configuration needed.

### 1.2 Run context for write tools [E]

> *(rev. 2, D16a)* **Superseded by §7.1.** The server no longer trusts anything the caller sends (`_meta`, `run_id`, or `agent` arguments). Identity comes from how the server process was launched.
- **What it is:** each write tool requires a *run context* (`run_id`, `agent`, `mode`).
- **How it travels:** the agent runner's toolset wrapper sends it in the MCP request metadata (`_meta`), so the model never sees or supplies it.
  - The exact PydanticAI mechanism is confirmed against the pinned version in Stage 5.
  - **Fallback:** hidden arguments that are removed from the schema the model sees.
- **Missing context:** a call without it is refused with `NO_RUN_CONTEXT`. This covers, for example, an ad-hoc call from Claude Code, so development sessions can't create proposals in the working database.
- **Agent permissions:** the server also checks that `agent` is allowed to use the tool (§3). This is a second layer behind the agent-side tool filtering.

### 1.3 Shared output fields
- Every successful result includes:
  - `business_date` (`desk.date_today`, R5)
  - `human_judgment` (a list of strings; may be empty)
  - `unknowns` (a list of strings; may be empty)
- **Facts versus arithmetic:** stored facts appear as stored. Arithmetic appears under a `computed` key with a note, as in the existing tools.
- **Write results** also include:
  - `created: true`
  - `status: "proposed"` or `"draft"`
  - `requires_human_approval: true`
  - `checks` (each rule check with `pass` / `warn`)
  - `warnings`
  - `links` (`action_id` or `draft_id`, and `audit_db_event_id`)

### 1.4 Errors [E]
- Every refusal or failure raises an MCP tool error, so **nothing is created**.
- Each error message begins with a stable code:

| Code | Meaning |
|---|---|
| `INVALID_INPUT` | Wrong type, range, or format. Type errors are caught by FastMCP's schema; domain checks by our code. |
| `NOT_FOUND` | The ID or SKU doesn't exist. The message lists valid values, as `check_inventory` already does. |
| `REFUSED_RULE` | A business rule forbids it. The message names the rule, for example `R1`, or `policy:quote_floor`. |
| `DUPLICATE` | An equivalent open proposal exists. The message gives its ID. |
| `NOT_PERMITTED` | This agent may not use this tool. |
| `NO_RUN_CONTEXT` | A write tool was called without run context. |
| `DB_UNAVAILABLE` | The database is missing, locked past the timeout, or has the wrong settings. |
| `AUDIT_FAILED` | The `audit.db` write failed, so the whole transaction rolled back. |
| `DB_DENIED` | *(added in Stage 2 implementation)* The SQLite authorizer refused a statement, for example one fired by a planted trigger. Nothing was saved. |

### 1.5 Audit behavior (all tools)
| Layer | What is recorded | Where |
|---|---|---|
| Agent runner (required) | `tool_call_started` (tool, args, agent, chain, run, mode) **before** the call; `tool_call_finished` (outcome, error code, `result_sha256`, short summary, `links`) after it | `output/audit_trail.json` |
| MCP server (supplemental) | For write tools only: `proposal_created` / `draft_created`, in the **same transaction** as the row | `audit.db` |

- **Fail-closed rules (D15):**
  - If `tool_call_started` cannot be saved, the tool is not called and the run stops.
  - If the database change commits but `tool_call_finished` cannot be saved, the run stops. The JSON trail then has a `started` event with no `finished` event.
  - `verify_audit.py` reports that gap and the matching `audit.db` event (with `run_id` and `action_id`), so the change is never silent.
  - The next run's writer records a `recovered_gap` system event that links the two.
  - *(rev. 2)* **A missing completion event never means the database action was rolled back.** The recovery event states what `audit.db` and the data show: `committed` (a matching row and event exist), `not_found` (no matching change), or `indeterminate`. See §7.6.
- **Read tools:** they write nothing to `audit.db`. They are audited in the JSON trail only.

> *(Stage 4 implementation)* The JSON layer is `mcp_server/audit_trail.py`:
> - `RunRecorder.tool_call` writes `tool_call_started` before the call (if that fails, the tool is not called) and `tool_call_finished` after it.
> - The `call_id` is sent in `_meta`, for correlation only, and is stored with the `pending_actions` / `drafts` row and the `audit.db` event.
> - Gaps are reported by `scripts/verify_audit.py` and recorded by `verify_audit.py reconcile --confirm` as `incomplete_operation_reconciled` events (see §7.6).

### 1.6 The new tables (written only by write tools)
- **`pending_actions`:**
  - Columns:
    - `id`, `kind`, `status`
    - the links: `ticket_id`, `invoice_id`, `lease_id`, `vendor_id`
    - the item: `sku`, `size`, `qty`, `unit_price`, `amount`, `amount_basis`, `account`
    - the vendor block: `vendor_blocked`, `blocking_invoice_ids`
    - `rationale`, `warnings`, `proposed_by`, `run_id`, `business_date`, `created_at`
    - columns used only in Problem 7: `decided_by`, `decided_at`, `decision_note`, `executed_at`, `payment_id`
  - `kind` is one of `payment` / `purchase` / `quote`. A trigger forces `status='proposed'` on insert.
- **`drafts`:**
  - Columns: `id`, `ticket_id`, `recipient`, `channel`, `subject`, `body`, `related_action_id`, `warnings`, `status`, `created_by`, `run_id`, `business_date`, `created_at`
  - `status` is always `draft`. `sent` doesn't exist as a status (R7).
  - `related_action_id` references `pending_actions`.

---

## 2. Comparative table

| # | Tool | Type | Agents | Tickets | Reads | Writes | Key rules |
|---|---|---|---|---|---|---|---|
| 1 | `check_inventory` | Read (unchanged) | Boss, Inventory, Customer Service | 101, 103 | `inventory`, `pricing`, `desk` | — | — |
| 2 | `get_financial_position` | Read (unchanged) | Boss, Accounting, Facilities | 101, 102, 103 | `desk`, `cash_accounts`, `invoices`, `vendors`, `leases`, `payments` | — | R1 (hypothetical), R3, R5 |
| 3 | `get_open_tickets` | Read (unchanged) | Boss | all | `tickets`, `invoices`, `vendors`, `leases`, `desk` | — | — |
| 4 | `get_ticket` | Read | all five | all | as #3, plus `pending_actions`, `drafts` | — | R7 (drafts shown as unsent) |
| 5 | `list_vendors` | Read | Boss, Inventory, Accounting | 101, 103 | `vendors`, `invoices`, `desk` | — | R3, lead time, R5 |
| 6 | `list_pending_actions` | Read | all five | all | `pending_actions`, `cash_accounts`, `desk` | — | R1 (hypothetical), no double-counting |
| 7 | `propose_payment` | Proposal | Accounting | 101, 102 | `invoices`, `leases`, `cash_accounts`, `pending_actions`, `desk` | `pending_actions` and the `audit.db` event | R1, R2, R5, R6, duplicates |
| 8 | `propose_purchase` | Proposal | Inventory | 101, 103 | `vendors`, `invoices`, `inventory`, `pricing`, `cash_accounts`, `pending_actions`, `desk` | `pending_actions` and the `audit.db` event | R2, R3, lead time, labeled estimate |
| 9 | `propose_quote` | Proposal | Customer Service | 103 (also 101 at list price) | `tickets`, `inventory`, `pricing`, `pending_actions`, `desk` | `pending_actions` and the `audit.db` event | Quote floor [P], cap [P], R6, no availability promise |
| 10 | `draft_message` | Draft | Customer Service, Facilities | 101, 102, 103 | `tickets`, `pending_actions`, `desk` | `drafts` and the `audit.db` event | R7, recipient = requester [P], commitment check [P] |

*Changed from architecture §3, as evaluated in §5:*
- **Boss reads everything and writes nothing.** Boss gains `check_inventory` and `list_vendors`.
- Accounting gains `list_vendors`.
- Inventory gains no financial tool, because the vendor block comes from `list_vendors`.

---

## 3. Detailed specifications

### Tool 1: `check_inventory(sku: str | None = None)` (unchanged)
- **Purpose:**
  - 101: is size S of the tee in stock? (0)
  - 103: is size M of the hoodie in stock? (8, so 12 short)
  - Also gives cost and list price for quotes.
- **Interface, output, and errors:** exactly as in Problem 3/4 (see `output/mcp_smoke.json`), **including the exact current error wording** *(rev. 2, D16e)*. An unknown SKU gives a tool error listing the valid SKUs. Error codes apply to the new tools only. The regression evidence checks that the output is unchanged.
- **Database:** read-only connection; `inventory` (`sku`, `name`, `size`, `qty`, `location`) and `pricing` (`unit_cost`, `list_price`).
- **Rules:** none. It is a reporting tool and returns stock as recorded. It never infers an incoming delivery.

### Tool 2: `get_financial_position()` (unchanged)
- **Purpose:**
  - Cash, open invoices (101: #501 is 3 days overdue), and the lease (102: $2,400 due in 2 days).
  - The R3 vendor block.
  - The hypothetical figure if everything were paid.
- **Interface and output:** unchanged.
- **Proposals are deliberately not included:** this keeps Problem 4's output stable. Commitments come from tool 6.
- **Rules:**
  - Days overdue are counted from `desk.date_today` (R5).
  - The `computed` block is labeled as hypothetical.

### Tool 3: `get_open_tickets()` (unchanged)
- **Purpose:** the Boss's queue view, with each ticket's linked invoice, vendor, and lease. Unchanged.

### Tool 4: `get_ticket(ticket_id: int)`
- **Purpose:**
  - A specialist's single-ticket view, for any status.
  - Includes the **work state**: proposals and drafts already created for the ticket.
  - Prevents duplicate work across delegations.
  - Used for all three tickets.
- **Validation:** `ticket_id` ≥ 1. If it doesn't exist: `NOT_FOUND` listing the ticket IDs.
- **Output:**
```json
{ "business_date": "2026-08-31",
  "ticket": { "id": 101, "type": "customer_order", "requester": "...", "subject": "...", "sku": "...", "size": "...",
              "qty": 1, "status": "open", "notes": "...", "created_at": "...",
              "linked_invoice": { "...": "as get_open_tickets" } | null, "linked_lease": { } | null },
  "pending_actions": [ { "id": 1, "kind": "payment", "status": "proposed", "amount": 840.0, "proposed_by": "accounting", "created_at": "..." } ],
  "drafts": [ { "id": 1, "recipient": "...", "subject": "...", "status": "draft", "created_by": "customer_service" } ],
  "human_judgment": [], "unknowns": ["Whether the goods billed on invoice #501 were delivered is not recorded."] }
```
  `unknowns` comes from fixed, documented checks, not free text. One example: a ticket linked to an open invoice for the same SKU and size, while stock is 0.
- **Database:** read-only. Tables `tickets`, `invoices`, `vendors`, `leases`, `pending_actions`, `drafts`.
- **Agents:** all five.

### Tool 5: `list_vendors()`
- **Purpose:** for Inventory, Accounting, and Boss, to answer "who could supply this, how fast, and are they blocked?"
  - 101/103: Bulldog Print Co is blocked by #501 (R3).
  - Shows the specialties of Elm City Gifts and QuickShip CT.
- **Interface:** no arguments.
- **Output (per vendor):**
```json
{ "id": 1, "name": "Bulldog Print Co", "specialty": "apparel reprint", "lead_days": 5,
  "open_invoices": [ { "id": 501, "amount": 840.0, "due_date": "2026-08-28", "days_overdue": 3 } ],
  "blocked": true, "blocked_reason": "R3: open unpaid invoice(s) 501; no new shipments until paid",
  "computed": { "earliest_arrival_if_ordered_today": null,
                "note": "Not estimable while blocked. If unblocked, arrival >= order date + lead_days. No order exists." } }
```
  For an unblocked vendor, `earliest_arrival_if_ordered_today` = `business_date + lead_days`, labeled hypothetical.
- **What it does not do:** it doesn't judge whether a vendor *can* make a product. It returns the stored `specialty` and leaves the judgment to people.
- **Database:** read-only. `vendors` (`id`, `name`, `specialty`, `lead_days`); `invoices` (`id`, `vendor_id`, `amount`, `due_date`, `status`).

### Tool 6: `list_pending_actions(ticket_id: int | None = None, kind: "payment"|"purchase"|"quote" | None = None)`
- **Purpose:** commitments versus cash, for Accounting and Boss. It also stops duplicate proposals.
- **Validation:**
  - `ticket_id` must exist, if given.
  - `kind` is limited to the three listed values.
  - `status` filtering isn't offered: Problem 5 creates only `proposed`.
- **Output:**
```json
{ "business_date": "2026-08-31",
  "actions": [ { "id": 1, "kind": "payment", "status": "proposed", "ticket_id": 101, "invoice_id": 501, "lease_id": null,
                 "vendor_id": null, "sku": null, "size": null, "qty": null, "unit_price": null,
                 "amount": 840.0, "amount_basis": "invoice_amount", "vendor_blocked": false,
                 "rationale": "...", "warnings": [], "proposed_by": "accounting", "run_id": "...", "created_at": "..." } ],
  "cash": { "stored_balance": 3400.0, "account": "checking" },
  "computed": { "proposed_payments_total": 840.0, "approved_commitments_total": 0.0,
                "cash_if_all_proposed_payments_executed": 2560.0,
                "estimated_purchases_total": 0.0,
                "note": "Hypothetical. Proposals reserve no cash and move no money. Purchase totals are estimates, not quotes, and are not payments (payment would follow a real invoice)." } }
```
- **Database:** read-only. `pending_actions`; `cash_accounts` (`balance`).
- **Agents:** all five.

### Tool 7: `propose_payment(kind: "invoice"|"lease", ref_id: int, amount: float, rationale: str, ticket_id: int | None = None, account: str = "checking")`
- **Purpose:**
  - 101: pay #501, $840, which unblocks Bulldog Print Co (R3).
  - 102: pay the rent, $2,400.
  - Only Accounting prepares payments (R2: agents prepare, a human approves).
- **Validation:**
  - `ref_id` must exist.
  - `amount` must be > 0, with at most 2 decimals.
  - `rationale` must be 10–1000 characters.
  - `account` must exist in `cash_accounts`.
  - `ticket_id` must exist, if given.
- **Rules, in order:**

| Check | Result if it fails | Basis |
|---|---|---|
| The invoice is `open` | `REFUSED_RULE` (no paying a closed invoice) | R4 consistency |
| The amount equals the stored amount exactly: `invoices.amount`, or `leases.monthly_rent` | `REFUSED_RULE policy:full_amount_only` | **[P]**. The assignment doesn't cover partial payments. |
| No other `proposed` or `approved` payment exists for the same invoice or lease | `DUPLICATE` with the existing ID | [E] |
| `amount` ≤ current stored balance | *(rev. 2, D16b)* If not, the proposal is **created and labeled unfundable**: `fundable_at_proposal=false`, the R1 check marked `fail`, and the `human_judgment` entry "NOT EXECUTABLE: amount exceeds the stored balance; execution would be refused (R1). Recorded for human review only." | **[A] R1** at execution (Problem 7); the label is [E] |
| `amount` ≤ balance − other open payment proposals | **Created**, with a `warn` and `human_judgment`: "competing proposals exceed cash; a human must choose" | [A] R1 is enforced at execution (Problem 7) |

- **Due date:** `days_overdue` / `days_until_due`, counted from `desk.date_today` (R5), is reported in `checks`. It informs the decision but is not a gate.
- **Output:**
```json
{ "created": true, "status": "proposed", "requires_human_approval": true,
  "links": { "action_id": 1, "audit_db_event_id": 7 },
  "proposal": { "kind": "payment", "invoice_id": 501, "amount": 840.0, "amount_basis": "invoice_amount", "account": "checking" },
  "checks": [ { "rule": "R1", "result": "pass", "detail": "840.00 <= stored balance 3400.00" },
              { "rule": "R1-competing", "result": "pass", "detail": "cash if all proposed payments executed: 2560.00" },
              { "rule": "R5", "result": "pass", "detail": "invoice 3 days overdue as of 2026-08-31" } ],
  "warnings": [], "human_judgment": ["Approval and execution are human-only (Problem 7)."], "unknowns": [] }
```
- **Writes:** one `pending_actions` row (`kind='payment'`, `status='proposed'`) and one `audit.db` `proposal_created` event, in a single transaction.
- **What it never touches:** `payments`, `cash_accounts`, `invoices`, or `leases`. The authorizer denies those.

### Tool 8: `propose_purchase(vendor_id: int, sku: str, size: str, qty: int, rationale: str, ticket_id: int | None = None)`
- **Purpose:**
  - 103: restock 12 hoodies in size M.
  - 101: restock tees in size S.
  - Inventory prepares a purchase. A human decides in Problem 7.
- **Validation:**
  - The vendor must exist.
  - The `(sku, size)` pair must exist in `inventory`. New products aren't allowed.
  - `qty` must be between 1 and 500.
  - `rationale` must be 10–1000 characters.
  - `ticket_id` must exist, if given.
- **Rules:**

| Check | Result | Basis |
|---|---|---|
| The vendor has an open invoice | **Created anyway**, with `vendor_blocked=true` and `blocking_invoice_ids`. Warning: "No shipment, order placement, or receipt is possible until invoice(s) are paid." | **[A] R3**, as Dr. Ryan directed. Proposing is allowed; fulfilment is not. |
| Cost basis | `amount = qty × pricing.unit_cost`, with `amount_basis='estimate_unit_cost'`. Unknown: "No vendor quote exists; the actual cost is unknown and may differ substantially (invoice #501: $840 for a rush reprint)." | **[A] no invented quotes** |
| Cash | Warning if the estimate exceeds `cash_if_all_proposed_payments_executed`. Not a refusal: it isn't a payment, and the actual cost is unknown. | R1 informs |
| Lead time | Reported as in tool 5. `null` while blocked. | Lead time |
| Specialty | The vendor's `specialty` is returned. `human_judgment`: "Confirm this vendor supplies this product." | No inference |
| *(rev. 2)* An open (`proposed`/`approved`) purchase exists with the same ticket, vendor, SKU, size, **and** quantity | **`DUPLICATE`** with the existing ID. Enforced by a unique database index, so concurrent retries cannot slip through. | [E] |
| An open purchase exists for the same ticket that differs in vendor, SKU, size, or quantity | Created as an **alternative**: `alternative_to` lists the other IDs, and a `human_judgment` entry asks a human to choose | [E] |

- **Output:** the shape of tool 7, with `proposal.{vendor_id, sku, size, qty, amount, amount_basis, vendor_blocked, blocking_invoice_ids}` and `computed.earliest_arrival_if_ordered_today`.
- **Writes:** `pending_actions` (`kind='purchase'`) and the `audit.db` event.
- **What it never does:** create an invoice, an order, a shipment, or an inventory change.

### Tool 9: `propose_quote(ticket_id: int, sku: str, size: str, qty: int, unit_price: float, rationale: str)`
- **Purpose:**
  - 103: a bulk-discount offer to Yale AI Club.
  - 101: a list-price confirmation.
  - Customer Service prepares the price. A human approves it before anything is said to the customer.
- **Validation:**
  - The ticket must exist and be `open`.
  - The `(sku, size)` pair must exist.
  - `qty` must be ≥ 1.
  - `unit_price` must be > 0, with at most 2 decimals.
  - `rationale` must be 10–1000 characters.
  - **Mismatch** *(rev. 2, D16c)*: if the SKU, size, or quantity differs from the ticket's request, the tool refuses with `REFUSED_RULE policy:quote_matches_ticket`. A mismatched quote is never converted into an alternative offer.
- **Rules:**

| Check | Result | Basis |
|---|---|---|
| `unit_price` ≥ `pricing.unit_cost` | `REFUSED_RULE policy:quote_floor` | **[P] D9 (approved policy)** |
| `unit_price` ≤ `pricing.list_price` | `REFUSED_RULE policy:quote_cap` | **[P], new.** Agents can discount but cannot mark up. Needs approval. |
| Availability | `qty` against on-hand stock. If short, the result has `fulfillable_from_stock=false, shortfall=12`, and the `human_judgment` entry "The quote must not promise a delivery date." | No invented delivery |
| Cash | `computed.revenue` and `margin` are shown, with the note "R6: sales revenue is not modeled; this quote adds no cash." | **[A] R6** |
| *(rev. 2)* An open quote exists for the same ticket at the same `unit_price` | `DUPLICATE` with the existing ID (unique index) | [E] |
| An open quote exists for the same ticket at a different price | Created as an alternative (`alternative_to`). A human chooses. | [E] |
| Lifecycle | *(rev. 2)* The output states `quote_state: "proposed"` and `customer_acceptance: "not recorded"`. A **proposed** quote, an **approved** quote (Problem 7), and **customer acceptance** are three distinct facts. The system records only the first two, and acceptance only if a human records it. | No invented acceptance |

- **Output:** the shape of tool 7, with `proposal.{ticket_id, sku, size, qty, unit_price, amount=qty×unit_price, amount_basis='quoted_unit_price'}` and `computed.{list_total, discount_pct, unit_margin, total_margin}`.
- **Writes:** `pending_actions` (`kind='quote'`) and the `audit.db` event.
- **What it never records:** customer acceptance.

### Tool 10: `draft_message(ticket_id: int, subject: str, body: str, related_action_id: int | None = None)`
- **Purpose:**
  - Replies to Tauhid Zaman (101), Elm City Properties (102), and Yale AI Club (103).
  - Nothing is ever sent (R7). Drafts wait for a human on the dashboard.
- **Validation:**
  - The ticket must exist.
  - `subject` must be 1–200 characters, and `body` 20–4000.
  - `related_action_id`, if given, must exist and belong to the same ticket.
- **Recipient [P]** *(rev. 2, D16d)*:
  - There is no recipient argument. The server takes the recipient from a stored record:
    - For a ticket with a `lease_id`, such as 102, the recipient is `leases.landlord` (`recipient_source='leases.landlord'`). If the ticket's `requester` differs from that landlord, a warning is added.
    - For any other ticket, the recipient is `tickets.requester` (`recipient_source='tickets.requester'`).
  - No contact address is stored in the database. `recipient_contact` is always `null`, with the unknown "No contact address is stored; a human must address the message."
- **Channel [E]:** `email`, since every ticket arrived by message.
- **Commitment check [P]:**
  - A fixed list of phrases triggers a warning: *confirmed, approved, guaranteed, will ship, has shipped, in stock now, paid, deal, accepted*, a dollar figure, or a date.
  - *(rev. 2)* **A linked proposal never clears a warning.** A link only annotates it, for example: "matches quote proposal 3 (status: proposed, NOT approved)".
  - These are **warnings for the human reviewer, not refusals.** Phrase matching can't verify meaning.
- **Output:**
```json
{ "created": true, "status": "draft", "sent": false, "requires_human_approval": true,
  "links": { "draft_id": 1, "audit_db_event_id": 9 },
  "draft": { "ticket_id": 103, "recipient": "Yale AI Club", "channel": "email", "subject": "...", "related_action_id": 3 },
  "warnings": [ "Body asserts a price ($45.00). Matches quote proposal 3 (status: proposed, NOT approved); warning stands." ],
  "human_judgment": [ "R7: this message will never be sent by the system." ], "unknowns": [] }
```
- **Writes:** `drafts` and the `audit.db` `draft_created` event.

---

## 4. Agent access matrix (enforced agent-side by toolset filtering and server-side by the run-context check)

| Tool | Boss | Inventory | Accounting | Facilities | Customer Service |
|---|---|---|---|---|---|
| check_inventory | ✓ | ✓ | | | ✓ |
| get_financial_position | ✓ | | ✓ | ✓ | |
| get_open_tickets | ✓ | | | | |
| get_ticket | ✓ | ✓ | ✓ | ✓ | ✓ |
| list_vendors | ✓ | ✓ | ✓ | | |
| list_pending_actions | ✓ | ✓ | ✓ | ✓ | ✓ |
| propose_payment | | | ✓ | | |
| propose_purchase | | ✓ | | | |
| propose_quote | | | | | ✓ |
| draft_message | | | | ✓ | ✓ |
| `delegate` (agent-side, not MCP) | ✓ | ✓ | ✓ | ✓ | ✓ |

- **Approve, reject, and execute:** not tools at all, for anyone (architecture §13).
- **Delegation:** every agent can delegate to every other agent, independently of its MCP access. The design relies on this: an agent without a tool asks the agent that has it.

---

## 5. Organizational evaluation

| Question | Answer | Correction (smallest effective) |
|---|---|---|
| Can Boss understand, prioritize, and delegate? | Yes. The queue (3), each ticket's work state (4), due dates and cash (2), and commitments (6). | **Boss reads everything and writes nothing:** add `check_inventory` and `list_vendors` to Boss. *Trade-off:* Boss can triage without needless delegation ("is 103 even fillable?"), but still has no way to act, so all action goes through specialists. Read access gives no new authority. |
| Can Inventory separate on-hand, shortage, lead time, block, and uncertain replenishment? | On-hand: tool 1. Shortage: tool 9's output, or arithmetic. Lead time and block: tool 5. Uncertain replenishment: `unknowns` in tools 4 and 8. | None. The design deliberately does **not** add a delivery-status tool: the database has no such data, so a tool could only invent it. |
| Can Accounting separate obligations, available cash, and commitments, and prepare payments without moving money? | Obligations and cash: tool 2. Commitments: tool 6. Payments: tool 7, with the authorizer denying any money tables. | Add `list_vendors` to Accounting, to see which payment unblocks which vendor. Tool 2 stays unchanged, preserving the Problem 4 output. |
| Facilities for ticket 102? | The lease and due date (tools 2 and 4), and the landlord draft (tool 10). The payment is delegated to Accounting. | None. |
| Customer Service without invented facts? | Price and stock (1), quote with floor and cap (9), drafts addressed to the requester with commitment warnings (10). It has no tool that could record acceptance, delivery, or a vendor quote. | None. One limit remains: phrase matching can't catch every unsupported claim, so human review of drafts remains essential. |
| Collaboration without unnecessary delegation? | Each specialist holds the read tools for its own decisions. Delegation is needed only to cross authority (proposing in another domain) or expertise. Tools 4 and 6 show existing work, which stops repeated proposals. | None. |
| Redundant, broad, missing, or misassigned? | 3 vs 4: different scope (queue vs one ticket plus work state); kept. Tool 2 vs 6: facts vs commitments, which keeps Problem 4 stable. *Vendor messages:* omitted, because none of the three tickets needs one and any vendor contact means ordering or payment, which belongs to Problem 7. | None added. Considered and rejected: a ticket-status tool. Closure needs the human's confirmation (D9), so agents record recommendations in their run output instead. |
| Full delegation despite different access? | Yes. `delegate` is agent-side and given to all five (§4). | — |

**Worked check against the three tickets (simulated reasoning, not a test result):**
- **101:** Boss (1, 2, 3, 5) sees stock 0 and #501 overdue → Accounting `propose_payment(invoice 501, 840)` → Customer Service `draft_message`, with no promised date.
- **102:** Facilities sees the rent is due in 2 days → Accounting `propose_payment(lease 1, 2400)`. The cash check passes: $3,400 − $840 − $2,400 = $160 if both were executed, which is hypothetical.
- **103:** Inventory sees 8 on hand and Bulldog Print Co blocked → `propose_purchase(…, 12)`, flagged as blocked and an estimate. The estimated $264 exceeds the hypothetical $160, which is flagged for a human. Customer Service then makes `propose_quote` plus a draft.
- Every step ends as a *proposal or draft* awaiting a human. No ticket appears resolved.

---

## 6. Decisions for Dr. Ryan (as first proposed; outcomes are in §7)

| ID | Decision | Recommendation |
|---|---|---|
| D16 | The ten tools and the access matrix in §4 (Boss reads everything and writes nothing) | Approve |
| D16a | Write tools require run context; ad-hoc calls (including from Claude Code) are refused | Approve. *Consequence:* Stage 3's live regression through Claude Code can show positive results only for the read tools, plus refusals for the write tools. Positive write behavior is shown offline. |
| D16b | Payments: full amount only [P]; refused if over the stored balance (R1 + R6); created with a warning when competing proposals exceed cash | Approve |
| D16c | Quote cap at list price [P, new]; a SKU, size, or quantity mismatch with the ticket is a warning, not a refusal | Approve, or make the mismatch a refusal |
| D16d | Drafts: recipient fixed to the ticket's requester [P]; commitment-phrase warnings, not refusals [P] | Approve |
| D16e | Unknown-SKU errors gain a `NOT_FOUND:` prefix (the only visible change to the original three tools) | Approve, or keep the old text exactly |

---

## 7. Revision 2: Dr. Ryan's approved corrections (2026-10-09)

| ID | Outcome |
|---|---|
| D16 | **Approved conditionally:** the ten tools, the access matrix (§4), and the separation of read, propose, and execute |
| D16a | Approved, with a **trusted run identity** (§7.1) |
| D16b | Full stored amount approved [P]. Unfundable proposals are **recorded and labeled**, not refused (§3, tool 7). Execution (Problem 7) refuses any negative balance [A, R1]. |
| D16c | Floor = unit cost and ceiling = list price, approved as **shop policies [P]**. A mismatch is **refused**. Proposed quote, approved quote, and customer acceptance stay distinct (§3, tool 9). |
| D16d | Approved. The recipient comes from a stored record (requester or lease landlord). A linked proposal never clears a warning. Nothing is sent (R7). |
| D16e | **Rejected:** the original three tools keep their exact interfaces, outputs, and error wording. The codes apply to new tools only. |
| Purchases | An exact duplicate returns `DUPLICATE`. A differing proposal is an explicit alternative. |

### 7.1 Trusted run identity (D16a)
**Principle:** nothing a caller sends can grant write permission. That includes `_meta`, any `run_id` or `agent` argument, and any hidden argument.

**Mechanism: identity set when the server process is launched:**
1. **Launch:**
   - For each run, the backend runner starts **one MCP server subprocess per agent** (stdio), with the environment variables `CC_RUN_ID` (a fresh UUID4), `CC_AGENT` (one of the five names), and `CC_RUN_MODE` (`simulated` or `live`).
   - PydanticAI's stdio MCP client lets the runner set the subprocess environment. That parameter is confirmed against the pinned version in Stage 5. If it is missing, the fallback is a small wrapper that sets the variables before launching.
2. **Startup checks:**
   - `CC_AGENT` must be one of the five names, and `CC_RUN_ID` must parse as a UUID4. If either fails, **the server exits** and serves nothing.
   - **Missing context:** if both are absent, as with Claude Code's `.mcp.json` launch or Professor Zaman running the server by hand, the server starts in **read-only development mode**. Write tools exist but always refuse with `NO_RUN_CONTEXT`. This keeps the Problem 4 setup working unchanged.
3. **Per-agent registration:**
   - In agent mode, the server **registers only the tools in that agent's column** of the §4 matrix. Every other tool doesn't exist for that process.
   - A write tool also re-checks `CC_AGENT` against its allowed list before writing (`NOT_PERMITTED`), as defense in depth.
4. **Attribution:**
   - `proposed_by` / `created_by` and `run_id` are taken **only** from the process environment.
   - Any caller-supplied `run_id`, `agent`, or `_meta` value is ignored for authorization.
   - A caller-supplied `call_id` in `_meta` may be stored, but **only for correlation** with the JSON trail, never for permission.

*(Stage 5 implementation)* `backend/runner.py` launches one server per agent per run, with `CC_RUN_ID`, `CC_AGENT`, and `CC_RUN_MODE` in its environment. Credential variables are stripped from that environment. The server-side registration is the primary boundary; the agent-side filter in `backend/agents.py` is an independent second layer, and each layer is tested alone.

**Trust boundary:**
- **Trusted:** the person at the computer and the runner code launched by that person. Both can set process environment variables.
- **Untrusted:** the model's output and anything carried inside an MCP request.
- **What this stops:** the model can't influence how its MCP server was launched, so it can't forge identity or reach another agent's tools.
- **Stated limit:** a local user who launches a server by hand with chosen variables can act as any agent. That is the same trust as the human operator, and there is no authentication in this local prototype (architecture §13, §18.5).

**Offline tests (Stage 3, through FastMCP's in-memory client, on temporary databases):**
- C1. With no context, each write tool returns `NO_RUN_CONTEXT`, and `pending_actions`, `drafts`, and `audit.db` are unchanged.
- C2. A server launched as `customer_service`: `propose_payment` is not listed. Calling it by name fails, with no row written.
- C3. **Forged arguments:** a `customer_service` server is called with `_meta={"agent":"accounting","run_id":…}` and extra `agent`/`run_id` fields. Any write is attributed to `customer_service` with its launch `run_id`, or the call is refused. The forged values never appear in the row.
- C4. An invalid `CC_AGENT` (`"ceo"`) or a malformed `CC_RUN_ID`: the server refuses to start.
- C5. The `.mcp.json`-style launch (no context): the three original tools return exactly the outputs recorded in Problem 4 (on a temporary copy).

### 7.2 Payments (D16b)
- Proposal fields: `fundable_at_proposal` (true/false) and `executable: false`. **Every** proposal is non-executable by agents, and the output says so.
- An unfundable proposal also carries "NOT EXECUTABLE: amount exceeds stored balance".
- Competing proposals and the hypothetical `cash_if_all_proposed_payments_executed` are still reported (tools 6 and 7).
- **Execution (Problem 7):** refuses if `balance − amount < 0` at the moment of writing [A, R1].

### 7.3 Duplicates and alternatives
| Kind | Accidental duplicate (→ `DUPLICATE` + existing ID) | Genuine alternative (→ created, `alternative_to` set) |
|---|---|---|
| Payment | An open payment for the same invoice, or the same lease | None. One obligation has one payment proposal. |
| Purchase | Same ticket, vendor, SKU, size, and quantity | Differs in vendor, SKU, size, or quantity |
| Quote | Same ticket and unit price | Same ticket, different unit price |

- **Enforced by partial unique indexes** on open statuses (`proposed`, `approved`), so two concurrent retries can't both succeed. The second hits the index, and the tool returns `DUPLICATE`.
- **Effect:** a retried or re-delegated call is idempotent.

### 7.4 Drafts (D16d)
- **Recipient:** `leases.landlord` for tickets with a `lease_id`; otherwise `tickets.requester`.
- **Contact address:** none exists. It is never invented.
- **Warnings:** always stand. Linked unapproved proposals only annotate them.
- **Sending:** `status='draft'` only (R7), and there is no send path.

### 7.4a Drafting authority (Dr. Ryan, Problem 5 follow-up 9; implemented in Stage 3)
- **Customer Service:** may draft only where the recipient is `tickets.requester`, meaning a ticket with no lease.
- **Facilities:** may draft only where the recipient is `leases.landlord`.
- **Anything else:** returns `NOT_PERMITTED: … delegate this communication to <agent>`.
- **Where it's enforced:** in `ops.draft_message` (`policies.DRAFT_AUTHORITY`), not in prompts.

### 7.5 Backward compatibility (D16e)
- **The original three tools:** keep their interface, output keys, value formats, and error text.
- **Regression evidence:** goes in a separate file, `output/mcp_regression_p5.json` (Stage 3). It compares outputs on a fresh reset against `output/mcp_smoke.json`, which is never modified.
- **Known unavoidable change:** none at present. If Stage 3 finds one, it will be recorded and brought to Dr. Ryan before proceeding.

### 7.6 Database changes committed before their JSON completion event
**Scenario:** `tool_call_started` is saved, the database transaction and its `audit.db` event commit, then the process dies or `tool_call_finished` can't be saved.

**Detection:** `verify_audit.py` and the writer's startup check pair every `tool_call_started` (by `run_id` + `call_id`) with a `tool_call_finished`. A missing pair is an **incomplete operation**.

**Recovery:**
- The next writer appends a system event, `incomplete_operation_reconciled`, with:
  - `db_outcome`: `committed` when `audit.db` has an event with the same `run_id` and `call_id` and the row exists; `not_found` when there is no such event or row; `indeterminate` otherwise, for example when `audit.db` is unreadable
  - the IDs of any rows found
- **Wording rule:** the JSON trail must never say "rolled back" unless the database confirms no change. `not_found` means only that "no matching change was found".

**Offline tests (Stage 4):**
- G1. Kill the process after the commit and before `tool_call_finished`. The row exists, and reconciliation reports `committed` with the right IDs.
- G2. Kill before the commit. Reconciliation reports `not_found`, and the database is unchanged.
- G3. Make `audit.db` unreadable. Reconciliation reports `indeterminate`.
- G4. Concurrent activity: two writer processes append at the same time. There are no lost updates, the counts are exact, and the hash chain stays valid.
- G5. Earlier runs stay byte-identical across a reconciliation append.
- G6. Labels: an incomplete operation is never labeled `ok` or "rolled back".

### 7.7 Future requirement for Problem 7 (documented only; nothing built now)
- **What Problem 7 must provide:** an **auditable, human-controlled** way to update ticket status, so the real three-ticket run in Problem 9 can meet the assignment.
- **How:**
  - Approval routes change `tickets.status` only on a human action.
  - That change is recorded in `audit.db` and in `output/audit_trail.json` as a human event.
  - Agents keep **no** ticket-status tool.
- No MCP tool is added for this in Problem 5.
