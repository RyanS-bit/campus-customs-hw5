# Campus Customs Database: Harness Notes (Problem 2)

How to read this file:
- **Fact**: read directly from the database.
- **Calculation**: arithmetic on those facts.
- **Question**: something the data does not settle.

Nothing in the database has been changed.

> **Scope of this file.** Sections 1–8 are the Problem 2 study of the supplied database. They are kept as written, except for dated notes marking what later changed. Sections 9–12 were added problem by problem. **Section 13 documents the final system as built and tested, and the completed three-ticket run (Problem 9).** Where an early section describes a plan or a state that later changed, section 13 is authoritative.

## 1. Files and integrity

| File | Role | SHA-256 | Size |
|---|---|---|---|
| `data.zip` | Supplied archive (one entry, `data/campus_customs.db`) | `DF5F6381…C541DEB99B` | 2,114 B |
| `data/campus_customs.db` | **Original.** Extracted from the zip, set read-only, never written to | `23686A90…5717275F3321F360A` | 53,248 B |
| `data/campus_customs_new.db` | **Working copy** for all operations | Same as the original at creation | 53,248 B |

Full hash of the original: `23686A90D698F7FA6F901A3F0A9774DB9CBB31E13151C9E5717275F3321F360A`

**How the working copy was made:**
1. Extract `data/campus_customs.db` from `data.zip` into `hw5/data/`.
2. Copy it byte for byte to `campus_customs_new.db`. Dr. Ryan confirmed this filename from the assignment; the first copy was named `campus_customs_working.db` and was renamed.
3. Mark the original read-only.

The inspection opened the original with SQLite's read-only mode (`?mode=ro`). To reset the working copy, copy the original over it again. A scripted reset will come in a later problem. *(Done: `mcp_server/maintenance.py` `reset_working_db` (Problem 5), and the controlled `POST /api/reset` (Problem 7, §11.6). The working copy now also holds the extension tables in §13.4, so the reset rebuilds them after copying.)*

## 2. Snapshot: business date 2026-08-31

The `desk` table holds the shop's "today": **2026-08-31** (Fact).

| Table | Rows | One-line summary |
|---|---|---|
| `desk` | 1 | Today's business date |
| `cash_accounts` | 1 | One checking account: **$3,400.00** as of 2026-08-31 |
| `inventory` | 10 | 4 SKUs, stock counted per size |
| `pricing` | 4 | Unit cost and list price per SKU |
| `vendors` | 3 | Suppliers and how many days they take to deliver |
| `invoices` | 1 | Bills owed to vendors |
| `leases` | 1 | Shop rent obligation |
| `payments` | 0 | Ledger of money paid out (empty) |
| `tickets` | 3 | Open work items for the agents |

The file has no views or triggers. Its only indexes are the automatic ones SQLite creates for primary keys.

## 3. Tables, fields, and why each matters to the agents

### `desk`
| Field | Type | Notes |
|---|---|---|
| `date_today` | TEXT, required | `2026-08-31` |
| `notes` | TEXT | NULL |

**Why it matters:** this is the shop's clock. "Overdue" and "due in 2 days" are only meaningful against this date, not the real calendar date. Every agent needs it.

### `cash_accounts`
| Field | Type | Notes |
|---|---|---|
| `name` | TEXT, primary key | `checking` |
| `balance` | REAL, required | `3400.0` |
| `date` | TEXT, required | `2026-08-31` |

**Why it matters:** **Accounting** uses it to check whether the shop can afford a payment. The dashboard displays it. Any cash-balance rule is enforced against this number.

### `inventory`
Primary key: (`sku`, `size`).

| Field | Type | Notes |
|---|---|---|
| `sku` | TEXT, required | Product code |
| `name` | TEXT, required | Product name |
| `size` | TEXT, required | S/M/L/XL, or `OS` (one size) |
| `qty` | INTEGER, required | Units on hand |
| `location` | TEXT, required | Aisle |

| SKU | Name | S | M | L | XL | OS | Aisle |
|---|---|---|---|---|---|---|---|
| CC-HOOD-NAVY | Basic Hoodie Big Yale | 4 | 8 | 14 | 6 | – | A |
| CC-TEE-WHITE | Classic Bulldog Tee | **0** | 5 | 3 | 2 | – | B |
| CC-HAT-BLUE | Yale Cap | – | – | – | – | 40 | C |
| CC-MUG-CREST | Crest Mug | – | – | – | – | **0** | D |

**Why it matters:** **Inventory** uses it to answer "can we fill this order?" Stock is tracked per size, so a SKU can be in stock overall but out of stock in the size a customer wants.

### `pricing`
| Field | Type | Notes |
|---|---|---|
| `sku` | TEXT, primary key | One row per SKU, the same price for every size |
| `unit_cost` | REAL, required | What the shop pays per unit |
| `list_price` | REAL, required | What the shop charges |

| SKU | Cost | List | Margin at list (Calculation) |
|---|---|---|---|
| CC-HOOD-NAVY | $22.00 | $58.00 | $36.00 (62.1%) |
| CC-TEE-WHITE | $8.00 | $28.00 | $20.00 (71.4%) |
| CC-HAT-BLUE | $6.00 | $22.00 | $16.00 (72.7%) |
| CC-MUG-CREST | $3.50 | $14.00 | $10.50 (75.0%) |

**Why it matters:** **Customer Service** and **Accounting** need it to evaluate discount requests. Cost is the floor below which a sale loses money.

### `vendors`
| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER, primary key | |
| `name` | TEXT, required | |
| `specialty` | TEXT, required | |
| `lead_days` | INTEGER, required | Days to deliver |

| id | Name | Specialty | Lead days |
|---|---|---|---|
| 1 | Bulldog Print Co | apparel reprint | 5 |
| 2 | Elm City Gifts | mugs and small goods | 3 |
| 3 | QuickShip CT | local courier | 1 |

**Why it matters:** **Inventory** and **Facilities** use it to choose a supplier and estimate when goods will arrive.

### `invoices`
Foreign key: `vendor_id` → `vendors.id`.

| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER, primary key | |
| `vendor_id` | INTEGER, required | Links to `vendors.id` |
| `amount` | REAL, required | |
| `due_date` | TEXT, required | |
| `status` | TEXT, required | Only `open` appears |
| `description` | TEXT | |

The only row: **#501**, Bulldog Print Co, **$840.00**, due **2026-08-28**, status `open`, "Rush reprint CC-TEE-WHITE S".

**Why it matters:** these are **Accounting**'s payables. Invoice #501 is three days past due as of the desk date (Calculation).

### `leases`
| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER, primary key | |
| `space_name` | TEXT, required | |
| `landlord` | TEXT, required | |
| `monthly_rent` | REAL, required | |
| `next_due` | TEXT, required | |
| `notes` | TEXT | |

The only row: **#1**, Chapel Street shop, landlord Elm City Properties, **$2,400.00/month**, next due **2026-09-02**.

**Why it matters:** **Facilities** owns the lease. **Accounting** pays it. It is the shop's largest fixed obligation.

### `payments`
This table has no foreign keys. `ref_id` is not enforced.

| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER, primary key | |
| `kind` | TEXT, required | |
| `ref_id` | INTEGER | Optional |
| `amount` | REAL, required | |
| `account` | TEXT, required | |
| `paid_at` | TEXT, required | |
| `approved_by` | TEXT, required | |

The table is **empty**.

**Why it matters:** this is the ledger of money paid out. Because `approved_by` is required, every payment must name who approved it, which is where the human-approval rule lands. `kind` + `ref_id` probably point to an invoice or lease, but this is an assumption: the schema does not say so and no example rows exist.

### `tickets`
Foreign keys: `lease_id` → `leases.id`, `invoice_id` → `invoices.id`.

| Field | Type | Notes |
|---|---|---|
| `id` | INTEGER, primary key | |
| `type` | TEXT, required | |
| `requester` | TEXT, required | Free text, not linked to any table |
| `subject` | TEXT, required | |
| `sku` | TEXT | |
| `size` | TEXT | |
| `qty` | INTEGER | |
| `lease_id` | INTEGER | Links to `leases.id` |
| `invoice_id` | INTEGER | Links to `invoices.id` |
| `status` | TEXT, required | |
| `notes` | TEXT | |
| `created_at` | TEXT, required | |

**Why it matters:** this is the work queue. **Boss** reads it and routes each ticket to the right specialist agent.

### Which agent uses which table

This mapping is a **proposed** division of work, based on each agent's name. It is not taken from the assignment text.

| Table | Boss | Inventory | Accounting | Facilities | Customer Service |
|---|---|---|---|---|---|
| `desk` | ● | ● | ● | ● | ● |
| `tickets` | ● routes | | | | ● replies |
| `inventory` | | ● | | | ● availability |
| `pricing` | | ● | ● margin | | ● quotes |
| `vendors` | | ● reorder | ● | ● | |
| `invoices` | | | ● | | |
| `leases` | | | ● pays | ● | |
| `cash_accounts` | ● oversight | | ● | | |
| `payments` | | | ● prepares (human approves) | | |

## 4. How the tables connect

```
tickets ──invoice_id──▶ invoices ──vendor_id──▶ vendors
   │
   ├──lease_id──▶ leases
   │
   └──(sku, size)··▶ inventory ··sku··▶ pricing      (·· = matching values, not a declared foreign key)

payments ··(kind, ref_id)··▶ invoices / leases        (implied only)
cash_accounts, desk                                   (standalone: balance and date)
```

Two things the schema does **not** contain:
- **No customers table.** A requester is just a name stored on the ticket.
- **No purchase-order or reorder table.** The only record of a reprint is invoice #501.

## 5. The three open tickets

All three are `open` and were created on 2026-08-31, the desk date (Fact).

### Ticket 101: `customer_order`
**Request:** Tauhid Zaman wants 1 Classic Bulldog Tee (CC-TEE-WHITE) in size S. Notes: "Needs a tee in size S." Linked to **invoice 501**.

| Linked record | What it shows (Fact) |
|---|---|
| `inventory` CC-TEE-WHITE / S | **0 on hand.** Other sizes have stock (M 5, L 3, XL 2). |
| `invoices` #501 | $840 rush reprint of exactly this SKU and size, from Bulldog Print Co. Due 2026-08-28; still `open` and 3 days past due. |
| `vendors` #1 | Bulldog Print Co: 5-day lead time |
| `pricing` | The tee sells for $28 and costs $8. |

**Why this is not straightforward:** a $28 sale is tied to an overdue $840 bill.
- Under assignment rule R3 (section 7), Bulldog Print Co ships nothing new while #501 is unpaid. Any new tee stock from this vendor therefore requires paying #501 first, with human approval (R2).
- The assignment does not say whether the goods billed on #501 were already delivered. Size S showing 0 on hand is consistent with "not delivered", but it is not proof.
- Under R6, the $28 sale brings in no modeled cash.

### Ticket 102: `rent_notice`
**Request:** from Elm City Properties, "Rent due." Notes: "Email: shop rent due in 2 days." Linked to **lease 1**.

| Linked record | What it shows (Fact) |
|---|---|
| `leases` #1 | $2,400, next due 2026-09-02, two days after the desk date, which matches the email. |
| `cash_accounts` | $3,400 available |

**Why this is not straightforward:** the rent fits within cash on its own. Combined with invoice #501, though:

**Hypothetical calculation:** $3,400 − $840 − $2,400 = **$160**, the balance *if* both existing obligations were paid. The current stored balance is $3,400. This is not a cash-flow forecast.

Under R1 (no negative balance, no extra reserve), paying both would be permitted, because $160 ≥ $0 (Calculation). Each payment still needs human approval (R2) and must update the related records (R4).

### Ticket 103: `price_override`
**Request:** Yale AI Club wants **20** Basic Hoodies (CC-HOOD-NAVY) in size **M**, at a bulk discount. No invoice or lease is linked.

| Linked record | What it shows (Fact) |
|---|---|
| `inventory` CC-HOOD-NAVY / M | **8 on hand.** The order is 12 short. Other sizes: S 4, L 14, XL 6 (32 hoodies in total). |
| `pricing` | $58 list price, $22 cost |
| `vendors` #1 | Bulldog Print Co (apparel reprint, 5 days) is the likely restock source. This is an assumption. |

**Calculations:**
- At list price: 20 × $58 = **$1,160** revenue, against 20 × $22 = $440 cost.
- Any price above $22 per hoodie makes a profit.
- **Estimate (not a verified purchasing cost):** reprinting 12 hoodies might cost about 12 × $22 = $264. This assumes the reprint price equals the `pricing.unit_cost`. The database has no vendor quote, and the only reprint on record (invoice #501, $840 for size S tees) suggests rush reprints can cost far more than unit cost.

**Why this is not straightforward:**
- The shop can't fill the order from stock.
- The assignment sets no discount limits.
- A reprint is a purchase, so it needs human approval (R2). If the reprint comes from Bulldog Print Co, R3 blocks it until #501 is paid.
- The sale itself brings in no modeled cash (R6), so any reprint must be funded from the existing balance.

### How the tickets interact
The tickets compete for the same **$3,400**, and no sales revenue replenishes it (R6).
- *If* #501 and the rent were both paid, $160 would remain (hypothetical; the stored balance is $3,400).
- That is less than the estimated $264 hoodie reprint (an estimate, not a quote). If the estimate held, R1 would require refusing that purchase after the other two payments.
- R3 links the tickets further: paying #501 is a precondition for any new Bulldog Print Co shipment, for tees (101) or hoodies (103).

The order in which tickets are resolved and how they are funded both matter. This is why the work needs agents that delegate to each other, rather than three independent answers.

## 6. Other observations

- **Also out of stock:** the Crest Mug is at 0, and no ticket mentions it. Elm City Gifts (mugs) and QuickShip CT (courier) are not referenced by any record.
- **Inventory value (Calculation):**
  - At cost: hoodies 32 × $22 = $704; tees 10 × $8 = $80; caps 40 × $6 = $240; mugs 0. Total **$1,024**.
  - At list price: $1,856 + $280 + $880 = **$3,016**.
- **Foreign keys are not enforced.** SQLite enforces declared foreign keys only when `PRAGMA foreign_keys=ON` is set per connection. Our MCP server should turn it on.
- **Dates use mixed formats.** Dates are stored as text: plain dates (`2026-08-31`) in most tables, timestamps with timezone (`2026-08-31T09:15:00-04:00`) in `tickets`.
- **Money is stored as REAL** (floating point). It's fine at this scale, but totals should be rounded to cents.

## 7. Business rules

### 7a. Official assignment rules (authoritative)

**Source:** Dr. Ryan read these from the official Homework 5 instructions and supplied them on 2026-10-08. The wording is preserved verbatim in `AI_prompts.md`, Problem 2, follow-up 2.

| # | Rule (assignment) | What the database shows | How it applies to the tickets |
|---|---|---|---|
| **R1** | Refuse any payment that would leave insufficient cash. Negative balances are prohibited. There is **no additional minimum reserve**. | Checking holds $3,400 | *If* both #501 and the rent were paid, $160 would remain (≥ $0), so both are permitted (hypothetical calculation). A purchase larger than the remaining balance must be refused. |
| **R2** | Human approval is required for payments and purchases. Agents may prepare these actions but cannot execute them. | `payments.approved_by` is required (NOT NULL). | Applies to #501, the rent, and any reprint. |
| **R3** | A vendor does not ship new products while it has an open unpaid invoice. | Bulldog Print Co (vendor 1) has invoice #501 `open`. | Bulldog Print Co ships no new tees (101) or hoodies (103) until #501 is paid. |
| **R4** | When a payment occurs, the related business records must be updated. | `payments` is empty. `invoices.status` and `leases.next_due` exist. | *Which* fields change, and to what values, is not specified (see 7b). |
| **R5** | `desk.date_today` defines the shop's current date for judging overdue obligations. | `2026-08-31` | #501 (due 08-28) is overdue. Rent (due 09-02) is not yet overdue. |
| **R6** | Cash only goes out. Incoming sales revenue is not modeled. | — | Selling a tee or hoodies does not add to the balance. Reprints must be funded from the existing $3,400. |
| **R7** | Customer emails and vendor calls are never actually sent. Drafts stay on the dashboard. | No messages table exists. | Replies to Tauhid Zaman, the Yale AI Club, and Elm City Properties stay as drafts. |
| **R8** | Before a complete ticket-resolution run, reset the working database to the original values. | Original: `data/campus_customs.db` (read-only). Working copy: `data/campus_customs_new.db`. | Reset = copy the original over the working copy, then verify the SHA-256 hash (section 1). |

### 7b. Left unspecified by the assignment

None of the following are business facts. Each recommendation is a conservative **proposal** that keeps a human in control, pending Dr. Ryan's decision.

| Issue | What the database establishes | What is unspecified | Conservative proposal |
|---|---|---|---|
| **Discount limits** (ticket 103) | `pricing` has cost $22 and list $58. No policy table exists. | The maximum discount and who may grant it | Agents draft an offer, never below `unit_cost`, and a human approves it. |
| **Fulfillment status of invoice #501** | #501 is `open`. Tee size S shows 0 on hand. | Whether the reprinted tees were already delivered | Don't assume delivery. Report stock as recorded (0) and treat new tee stock as blocked by R3 until #501 is paid. |
| **Payment-status conventions** (R4 details) | `invoices.status` holds only `open`. `leases.next_due` = 2026-09-02. `payments.kind` has no examples. | The paid-status value, whether `next_due` moves forward, and the values of `kind` and `ref_id` | Use `paid` for the invoice and move `next_due` forward one month (2026-10-02). Use `kind` = `invoice`/`lease`, with `ref_id` pointing to that row. Write these only after a human approves, and treat them as assumptions until confirmed. |
| **How approval is captured** (R2 details) | `approved_by` is required free text. | Approver identity and the mechanism | Agents create *pending* proposals only. A human action on the dashboard writes the `payments` row, with that person's name in `approved_by`. |
| **Reorder pricing and records** | `vendors` lists lead times. No quote or purchase-order table exists. | Reprint unit prices; where a purchase is recorded | Agents propose the vendor, quantity, and an *estimated* cost labeled as an estimate. Purchases follow R1, R2, and R3. |
| **Date progression** | `desk.date_today` is fixed at 2026-08-31. | Whether the date ever advances | Use `desk.date_today` as "today" (R5). Never use the real system clock. |

*Superseded by R1:* an earlier draft proposed flagging any payment that would leave less than the next known obligation. That implied a cash reserve, which the assignment explicitly does not establish, so it was removed.

## 8. Question log

| # | Question | Status |
|---|---|---|
| 1 | Cash-balance rule | **Resolved by R1:** no negative balance, no extra reserve |
| 2 | Discount limits for ticket 103 | **Open.** Not specified by the assignment (see 7b) |
| 3a | Is paying #501 a precondition for new Bulldog Print Co shipments? | **Resolved by R3:** yes |
| 3b | Were the #501 tees already delivered? | **Open.** Not specified (see 7b) |
| 4 | Which records change after payment, and to what values? | **Partly resolved.** R4 requires updates; the exact values are open (see 7b) |
| 5 | `payments.kind` values and `approved_by` format | **Open.** Not specified (see 7b) |
| 6 | Working-copy filename | **Resolved:** `data/campus_customs_new.db`, confirmed by Dr. Ryan |
| 7 | Which date counts as "today"? | **Resolved by R5:** `desk.date_today` |

## 9. MCP tools (Problem 3)

**Server:** `mcp_server/server.py` (FastMCP 4.1.0). It connects only to `data/campus_customs_new.db`, in SQLite read-only mode. Details are in `mcp_server/README.md`.

| Tool | Tables read | Tickets | What the agents learn |
|---|---|---|---|
| `check_inventory(sku?)` | `inventory`, `pricing` | 101, 103 | Tee size S = 0 on hand. Hoodie size M = 8 on hand (12 short of 20). Cost and list price, which give the margin floor for any discount. |
| `get_financial_position()` | `desk`, `cash_accounts`, `invoices`, `vendors`, `leases`, `payments` | 101, 102, 103 | Cash $3,400. Invoice #501 is 3 days overdue. Rent is due in 2 days. Bulldog Print Co is blocked by an open invoice (R3). Stored balance: $3,400. Computed (hypothetical): balance *if* all open obligations were paid = $160, used for the R1 check. |
| `get_open_tickets()` | `tickets`, `invoices`, `vendors`, `leases` | 101, 102, 103 | Each open ticket with its linked invoice and vendor (101) or lease (102). Ticket 103 has no linked record. |

The values above are the tools' actual output on 2026-10-08, confirmed by `mcp_server/verify_tools.py` (21/21 checks passed). *(Problem 9, follow-up 42: the checker now runs two passes, so it stays valid after the simulation's legitimate payments. The first checks the current working copy against its own records. The second checks a temporary fresh copy of the original, asserting these 2026-10-08 values exactly. 45/45 on 2026-10-11; output in `output/p9_verify_tools.txt`. See `mcp_server/README.md`.)*

**Not yet covered (by design):**
- No tool writes anything: no payments, ticket updates, or inventory changes. Writes require human approval (R2) and come in a later problem. *(As of Problem 3. Superseded: Problem 5 added proposal and draft tools, which still change no business records; see §10.)*
- No tool lists vendors that have no open invoice (Elm City Gifts, QuickShip CT). That can be added if the agents need it.

## 10. Problem 5 implementation status (offline; updated as stages complete)

This section describes what has actually been built. The design is in `output/p5_architecture.md`, and the tool specification is in `output/p5_mcp_tools_spec.md`.

| Stage | Built | Verified by |
|---|---|---|
| 1. Paths and reset | `mcp_server/paths.py` (default `data/`; optional `CC_RUNTIME_DIR`), `schema_ext.sql` (`pending_actions`, `drafts`), `maintenance.py` (reset R8, snapshot) | 43 offline tests (`output/p5_stage1_tests.txt`) |
| 2. Operations and safeguards | `ops.py` (propose payment, purchase, and quote; draft message), `authz.py` (allowlist authorizer), `audit_db.py` + `audit_schema.sql` (supplementary hash-chained `audit.db`), `policies.py` (labeled proposed policies) | 209 offline tests in total (`output/p5_stage2_tests.txt`); evidence in `output/p5_stage2_authz_evidence.json` |
| 3. Expanded MCP server | `mcp_server/server.py`: 10 tools (6 read, 4 propose/draft), the per-agent access matrix, and identity fixed at server launch (`CC_RUN_ID` / `CC_AGENT` / `CC_RUN_MODE`); `verify_tools.py` updated for 10 tools | 282 offline tests in total (`output/p5_stage3_tests.txt`); offline regression and evidence in `output/mcp_regression_p5.json` |
| 5. Agent team | `backend/`: five PydanticAI agents (prompts in `backend/prompts/`, types in `models.py`), all-to-all `delegate` with cycle, depth, budget, and shared-usage limits, one MCP server per agent with launch identity, an audit hook on every tool call, and a single `gpt-6-luna` configuration | 381 offline tests in total, with scripted offline models (`output/p5_stage5_tests.txt`) |
| 4. JSON audit trail | `mcp_server/audit_trail.py` (append-preserving, hash-linked JSON array; OS file lock; atomic replace; fail-closed), `audit_reconcile.py` (JSON ↔ `audit.db` ↔ rows), `locks.py` (trail lock, run lock), `scripts/verify_audit.py`; reset and snapshot scripts append system events | 326 offline tests in total (`output/p5_stage4_tests.txt`) |

**How the rules are enforced in code (Stage 2):**
- **R1:**
  - A payment proposal larger than the stored balance is recorded but labeled `NOT EXECUTABLE`.
  - Competing proposals are flagged, with the hypothetical cash remaining.
  - Money never moves. Execution, and its refusal of any negative balance, is Problem 7.
- **R2:** agents can only insert rows with status `proposed`.
  - A database trigger rejects any other status.
  - The authorizer blocks every `UPDATE` and `DELETE`.
  - There is no approve or execute code yet.
- **R3:** a purchase from a vendor with an open invoice is recorded with `vendor_blocked = 1`, along with the blocking invoice IDs and a warning. No arrival date is estimated while the vendor is blocked.
- **R4:** a payment proposal for a non-open invoice is refused.
- **R5:** the business date comes from `desk.date_today`.
- **R6:** quotes state that they add no cash.
- **R7:** drafts can only have status `draft`. There is no send path.
- **R8:** reset as in Stage 1.
- **Proposed policies** (in `policies.py`, not assignment requirements): payments must be the full stored amount; quotes must sit between unit cost and list price and match the ticket exactly; the draft recipient comes from a stored record; commitment wording raises warnings only.

**Real databases:** never reset or modified. Both still hash to `23686a90…f360a`. Every test uses temporary copies.

**Agent identity (Stage 3):**
- Each agent's MCP server is launched with that agent's identity, and **registers only that agent's tools**.
- A server launched without identity (as Claude Code launches it from `.mcp.json`) is development mode: all ten tools are listed, read tools work, and write tools refuse with `NO_RUN_CONTEXT`.
- Forged request metadata is ignored. Forged arguments are rejected by the tool schema.
- Drafting authority is enforced in code: Customer Service drafts only to a ticket's requester, and Facilities only to the lease landlord. Anything else must be delegated.

**Backward compatibility:** the original three tools reproduce `output/mcp_smoke.json` exactly, including the unknown-SKU error text. That file is unchanged.

**Real working database and live check (Stage 3, authorized):**
- **Reset:** the real working copy was reset with the new tables. Its hash is now `dbaf24cb…8ede`; the original is unchanged. Details in `output/p5_stage3_reset_record.md`.
- **Live calls:** 16 calls in development mode, through the reloaded server, recorded in `output/mcp_live_regression_p5.json`:
  - The original three tools are identical to Problem 4.
  - The new read tools match the database.
  - All four write tools were refused.
- **Nothing created:** no proposals, drafts, or payments.

**Audit trail (Stage 4):**
- **`output/audit_trail.json`:** one valid JSON array, hash-linked. It was created with a single real `trail_initialized` event. Earlier activity is documented in `AI_prompts.md` and `output/`, not backfilled.
- **Content:** every event records who acted (from the trusted launch identity), whether the run was `live` or `simulated`, and the outcome.
- **Resets:** never erase the trail; they append `db_reset` events.

**Agent team (Stage 5):**
- **Built and tested offline only.** Every agent run so far used a scripted test model, on temporary databases and temporary audit trails.
- **No live `gpt-6-luna` call has been made.**
- The real working database and the real audit trail were not touched.

**Live connectivity (authorized single request, 2026-10-10 00:31 UTC):**
- **Request:** `gpt-6-luna` through `api.portkey.ai`, with no tools, agent, MCP, or database.
- **Result:** the gateway **accepted** it. The provider reported model `gpt-6-luna-2026-09-22`, with finish reason `stop`.
- **Usage (provider-reported):** 48 input and 13 output tokens.
- **Reply:** valid JSON, `{"role":"AI assistant","status":"ok"}`.
- **Unchanged:** both databases and the audit trail. Details in `output/p5_live_connectivity.json`.

**Stage 6 (integration verification, offline):**
- **Workflow tests:** 11 tests in `tests/test_stage6_workflows.py` run the real MCP servers, rules, delegation, and audit trail through ticket 101, ticket 102, ticket 103, and all three together, using scripted models on temporary databases. Only proposals and drafts are ever created.
- **Dependency map:** in `output/p5_ticket_workflow_map.md`.

**Stage 7 (acceptance review):**
- **Acceptance matrix:** `output/p5_acceptance_matrix.md` traces every supplied Problem 5 requirement to code, tests, and evidence, with its verification level.
- **Documentation consistency:** checked by machine (`tests/test_stage7_docs.py`).
- **Ticket map corrected:** paying invoice 501 does **not** establish that Bulldog can supply navy hoodies (see `output/p5_ticket_workflow_map.md` §3–3a).
- **Roadmap for Problems 6–11:** `output/hw5_roadmap_p6_p11.md`.

**Remediation (after the pre–Problem 6 audit):** findings M1–M5 and the necessary minor items are fixed, with 30 regression tests (431 tests in total). Details in `output/p5_remediation_report.md`.
- Agent servers use exactly the database the runner selected.
- Simulated runs accept only offline test models and run behind an offline network guard.
- Reconciliation refuses to run during an active run.
- Rows are matched by run and call, not by ID alone.
- Live runs have enforced token, output, tool-call, request, time, timeout, and retry limits.

**Not yet done (as of Problem 7):** no live agent run, and no ticket resolved. Problem 7's approval routes, which are needed before a real run can do more than propose, are now built and tested offline (section 11). *(Superseded in Problem 9: five live runs took place and all three tickets were resolved by a person; see §13.9.)*

---

## 11. Backend routes (Problem 7)

The FastAPI backend is `backend/main.py`. It connects the future dashboard (Problem 8) to the five agents, the MCP tools, the working database and the audit trail. Start it from `hw5/backend/` with `uvicorn main:app --reload --port 8000`; it is then at `http://localhost:8000`, with interactive documentation at `http://localhost:8000/docs`.

### 11.1 Every route, one line each

- `GET /` — service information: the route list, the documentation link, and whether agent runs are enabled.
- `GET /api/tickets` — the three tickets from the working database, each marked open or resolved, with proposal counts and the latest run.
- `GET /api/tickets/{ticket_id}` — one ticket with its linked invoice or lease, its proposals (and their decisions), its unsent drafts, its runs, and (added in Problem 8) the stored stock and pricing for its product.
- `POST /api/tickets/{ticket_id}/run` — starts the Boss-led agent team on one open ticket in the background and returns the run ID at once (202).
- `GET /api/runs/{run_id}` — a run's phase and outcome: proposals awaiting a human, decided proposals, unsent drafts, the ticket's real status, usage and the Boss's report.
- `GET /api/runs/{run_id}/responses` — every agent's **full** reply for one run, in order, with its reconciliation against the audit trail (read-only; Problem 9, follow-up 20). Runs recorded earlier return `recorded: false`.
- `GET /api/events` — recorded agent activity from `output/audit_trail.json` in order: who acted, what each agent said, which tools it called, and who delegated to whom; filters `run_id`, `ticket_id`, `after_seq`, `limit`.
- `GET /api/actions/pending` — agent proposals still awaiting a human decision (optional `ticket_id`).
- `POST /api/actions/{action_id}/approve` — human approval: a payment is executed atomically (cash, invoice or lease, payments row, both audit records); a purchase or quote is only authorized.
- `POST /api/actions/{action_id}/execute` — human execution of an approved purchase (step 2 of 2): pays a confirmed amount, with its recorded basis, from checking in one atomic transaction (Problem 9, F3).
- `POST /api/actions/{action_id}/reject` — human rejection of a proposal, or withdrawal of an approved, unexecuted purchase; nothing is executed.
- `POST /api/tickets/{ticket_id}/resolve` — a person resolves a ticket in their own words, on one of two explicit bases: the business outcome is confirmed, or only the simulated workflow is complete, with the unverified outcomes named (§12.9). It is recorded as their statement and refused while proposals still await a decision.
- `GET /api/cash` — the checking balance as stored in `cash_accounts`; proposals, estimates and quotes are listed separately and never counted as cash.
- `POST /api/reset` — restores the working database to its original values (R8) through the existing controlled reset; recorded before and after, and the audit history is kept.
- `GET /api/audit` — read-only integrity status of both audit records, including any decision or reset whose final JSON event is missing.

### 11.2 How the backend connects to the existing system

| Need | Reused component | Notes |
|---|---|---|
| Ticket, proposal and cash reads | `mcp_server/desk_reads.py` | Read-only SQLite connections (`mode=ro`); rows shaped by the MCP server's own helpers |
| Agent runs | `backend/runner.py` `run_task` | Unchanged rules, delegation limits, MCP identities, budgets and audit hooks. The backend now passes a run ID and ticket ID |
| Agent activity | `output/audit_trail.json` | Hash chain verified on every read; refused (503) if damaged. Nothing is generated or invented |
| Human decisions | `mcp_server/approval.py` | The single approval and execution path, built to the Problem 5 design (architecture §4, §5, §13, §14; spec §7.7) |
| Reset | `mcp_server/approval.py` `controlled_reset` → `mcp_server/maintenance.py` | The same verified reset used by `scripts/reset_db.py`, with intent and outcome records and a reset stamp |
| Audit status and gap recovery | `mcp_server/audit_reconcile.py` | The Problem 5 reconciliation, extended to human decisions and resets |

`backend/main.py` contains no SQL and no business rules.

### 11.3 Formats

- **Requests:** JSON, validated by typed Pydantic models. Unknown fields are refused, so a request cannot carry a `status` or `approved_by` field.
  - Decision body: `{"decided_by": "<person's name>", "confirm": true, "note": "<optional, at most 500 characters>"}`.
  - Resolve body: the decision body plus `"business_outcome_confirmed": true` and `"resolution_summary": "<10–1000 characters, in the person's own words>"`.
  - Reset body: `{"requested_by": "<person's name>", "confirm": "RESET"}`.
- **Responses:** JSON with typed models; the full schemas are at `/docs` and `/openapi.json`.
- **Errors:** always `{"error": {"code": "...", "message": "..."}}`, with these status codes:
  - 403: `HUMAN_CONFIRMATION_REQUIRED`, `RUNS_NOT_AUTHORIZED`
  - 404: `NOT_FOUND`
  - 409: `NOT_PENDING`, `REFUSED_RULE`, `RUN_ACTIVE`, `BUSY`, `TICKET_NOT_OPEN`
  - 422: `INVALID_INPUT`, listing the problem fields
  - 503: `DB_UNAVAILABLE`, `AUDIT_FAILED`, `AUDIT_TRAIL_UNREADABLE`
  - 500: `RESET_FAILED`, `UNSAFE_WRITE`

### 11.4 Ticket runs and event polling

1. **Start.** `POST /api/tickets/{id}/run` checks that:
   - the ticket exists (404 otherwise) and is open (409 otherwise);
   - no run, reset or decision is in progress (409 otherwise).

   It then starts `run_task` in the background and returns `run_id` at once.
2. **One at a time.** Only one run can be active. An in-process gate covers this server; the run lock covers every process.
3. **Poll.** The dashboard polls `GET /api/runs/{run_id}` and `GET /api/events?run_id=…&after_seq=<next_after_seq>` every one to two seconds.
   - Events come back in `seq` order.
   - `next_after_seq` is the cursor for the next poll; `has_more` means another page is waiting.
4. **Phases.** A run is `queued`, `active`, `completed`, `stopped` (a budget limit was reached), `failed`, or `interrupted` (it started but never finished, for example because the server stopped).
5. **Outcome.** This separates the run's execution from the business result:
   - `awaiting_human_approval`
   - `awaiting_human_review_of_drafts`
   - `completed_nothing_proposed`
   - `incomplete_limit_reached`
   - `ticket_resolved_by_human`
   - `failed`
   - `interrupted`

   A finished run never approves, pays, sends or resolves anything.
6. **Live runs are off by default.** They call `gpt-6-luna` through Portkey and cost money, so the run route refuses (403) unless the person starting the server sets `CC_LIVE_AUTHORIZATION` to a short authorization note. That note is recorded in the audit trail. The offline tests inject scripted models instead.

### 11.5 Human approvals

- **Payment approval.** Approve and execute happen in one transaction:
  - The decision re-checks that the proposal is still `proposed`, that the invoice is still open (R4), and that the amount equals the stored invoice or rent.
  - It refuses if cash would go negative (R1), counting approved, unexecuted commitments (D4).
  - It inserts the `payments` row, lowers the balance, marks the invoice paid or moves the lease due date one month (D6), and marks the proposal `executed`.
  - It writes `action_approved` and `payment_executed` to `audit.db`. Everything commits together, or nothing does.
- **Purchase approval.** Only authorizes the purchase (step 1 of 2).
  - Nothing is ordered, shipped or paid, and cash does not change.
  - *Revised in Problem 9 (F3):* approval is allowed while the vendor has an open unpaid invoice, with an R3 warning. Execution, step 2, is refused while the vendor is blocked (see §12.6).
- **Quote approval.** Only authorizes the quote.
  - Nothing is sent (R7), and no revenue is added (R6).
  - Only one quote per ticket can be approved.
- **Rejection.** Changes only the decision fields, and is audited.
- **Ticket resolution (A1).** Deciding every proposal is not the same as solving the business problem, so resolving needs the person's explicit confirmation (`business_outcome_confirmed: true`) and their own summary of how it was resolved. *(Follow-up 36 adds a second, explicit basis, "simulated workflow complete; fulfilment not verified"; see §12.9.)*
  - Approving proposals never resolves a ticket.
  - The record stores the statement as the person's (attribution only), plus the system's own records at that moment: the ticket's proposals, their statuses and payment IDs, and the count of unsent drafts.
  - It lists what the system does **not** verify: deliveries, shipments, customer acceptance, messages sent, and payments outside the system. Nothing is inferred.
- **Every decision:**
  - holds the run lock;
  - first reconciles any earlier gap (see 11.5a);
  - writes `human_decision_requested` to the JSON trail (if that fails, nothing changes);
  - writes `human_decision_finished` afterwards, with actor `human:<name>`, retried up to 3 times.

  A second decision on the same proposal is refused (`NOT_PENDING`).

### 11.5a What is atomic, and what is reconciled (A2)

- **Atomic:** the business change and its `audit.db` events (one SQLite transaction across both files). The `audit.db` events carry the `decision_id` and the decider's name, so a committed payment is always discoverable there and attributed.
- **Not atomic:** the JSON trail cannot be committed together with SQLite, and this project does not claim it is. The JSON `human_decision_requested` event is always written *before* any change. If `human_decision_finished` is then lost (an error after the retries, or a crash), the gap is:
  - **reported** at once in the response (`audit_trail_recorded: false`, `reconciliation_pending: true`, with the `decision_id`);
  - **detectable** at any time through `GET /api/audit` and `scripts/verify_audit.py`;
  - **reconciled automatically**, under the run lock, before the next decision or reset (or with `scripts/verify_audit.py reconcile --confirm`). This adds one `incomplete_operation_reconciled` event, recorded once, stating what `audit.db` shows.
- **Reconciliation outcomes for a decision:**
  - `committed`: the decision's `audit.db` events exist and their rows still match. The event lists the payment IDs and the person's name.
  - `committed_row_no_longer_present`: `audit.db` proves the commit, but a later reset replaced the rows.
  - `not_found`: `audit.db` has no event for it. Because the change and its events commit together, nothing from the decision was committed.
  - `indeterminate`: a file could not be read.
- **Cross-check:** a human event in `audit.db` with no JSON request is reported as a problem.

### 11.6 Reset

`POST /api/reset` needs the human header and `confirm: "RESET"`. It is refused (409) while a run or decision is active.

It runs `approval.controlled_reset` (A3), which does everything under one hold of the run lock:

1. Reconcile any earlier gap.
2. Write the JSON `db_reset_requested` event, with a new `reset_id`. If this cannot be written, nothing happens.
3. Write the `audit.db` `db_reset_started` boundary marker. If this cannot be written, nothing happens, and the failure is recorded as the outcome.
4. Run the existing verified reset (`maintenance.reset_working_db_locked`), which:
   - checks the original's hash;
   - prepares and verifies a copy;
   - **stamps the `reset_id` into the new copy** (table `reset_stamp`) *before* the atomic swap;
   - never opens the original for writing.
5. Write `audit.db` `db_reset_finished` and the JSON `db_reset` outcome (same `reset_id`, retried).

**If the outcome record is lost** (an error after step 4, or a crash), the working copy's stamp still proves whether the reset took effect. Reconciliation reports:
- `committed`: the copy carries this stamp;
- `not_found`: no stamp and no later reset, so the swap did not happen;
- `indeterminate`: a later reset replaced the copy.

A failed reset changes nothing, and its error outcome is recorded.

Nothing is erased. Proposal IDs restart at 1 after a reset, but every human decision records the proposal's own `run_id` and `created_at`, and the `db_reset_started` marker shows where the new working copy begins. An old decision can therefore never appear to belong to a new transaction.

Resets made with `scripts/reset_db.py` keep their earlier behavior: a single `db_reset` event and no stamp.

### 11.7 Safety boundaries

- **Agents cannot reach these routes.** Their only tools are their MCP tools: no HTTP, shell or code. The approval module is not an MCP tool and is not imported by any agent module (`tests/test_stage3_safeguards.py` s2b).
- **Every POST needs the header `X-Human-Decision: confirm`.** It blocks accidental and cross-site requests. CORS allows only the Vite dashboard (`http://localhost:5173`, `http://127.0.0.1:5173`), and uvicorn listens on 127.0.0.1 only.
- **Local-demo limit, stated plainly:**
  - The header and the typed name are not authentication. A browser button does not prove who clicked it.
  - Anyone who can use this computer can make decisions. `decided_by` records who *says* they decided.
  - A real deployment would need authenticated users, separation of duties and signed records.

### 11.8 Connecting the React dashboard (Problem 8)

The dashboard runs on Vite at `http://localhost:5173` and calls `http://localhost:8000/api/...`.
- It sends `Content-Type: application/json` and, on every POST, `X-Human-Decision: confirm`.
- It polls run status and events as described in 11.4.
- It shows `outcome` (not only `phase`) and the ticket's `status` from `GET /api/tickets`.

### 11.9 Known limitations

- **Live runs.** The run route was first tested only with scripted offline models. In Problem 9, with Dr. Ryan's separate authorizations, it ran five live runs (`gpt-6-luna` through Portkey): three for ticket 101 (one failed with a provider configuration error, §12.1), one for 102 and one for 103 (§13.9).
- **Run state is in memory.** The `queued` and `running` state lives in the server process. The audit trail is the durable record. If the server stops or reloads during a run, the run shows as `interrupted`. Any unfinished tool call can then be checked with `scripts/verify_audit.py`.
- **"What the agent said" is truncated in the trail.** It comes from the trail's recorded `result_summary` (at most 300 characters). Since Problem 9 follow-up 20, every new run also keeps each agent's full reply (§12.8). Runs recorded before that, including ticket 101 Attempts 1–3, have only the summaries.
- **Port conflict.** Port 8000 must be free. On 2026-10-10 a Homework 4 backend was already using it on this computer.

---

## 12. Problem 9 corrections

### 12.1 Model setting (after failed live run `53e4f001…`)
- **The problem:** `gpt-6-luna` reasons by default, and the provider rejects function tools combined with reasoning on `/v1/chat/completions`.
- **The fix:** `backend/config.py` sets `MODEL_SETTINGS = {"openai_reasoning_effort": "none"}` on every `gpt-6-luna` model, and the runner's live guard refuses a live model without it. The model, tools, agents and limits are unchanged.
- **Records:** `output/p9_runs/101_attempt1_incident.md`; tests in `tests/test_problem9_live_config.py`.

### 12.2 Agent coordination (F1, after ticket 101 attempt 2 produced no actionable items)
Prompt text only; no tool, permission or code change.
- **Shared rule 11** (identical in all five prompts): make human decisions actionable, *but only when the records support it*.
  - The responsible agent prepares the proposal or draft a person needs. A proposal moves no money and is not a recommendation; open questions go in its rationale.
  - Never manufacture: no invented amounts, prices, suppliers, quantities or facts, no unsupported transaction, nothing created only for activity.
  - When the evidence does not support an action, it goes under `needs_human` and `unknowns`.
- **Boss routing by the actual business problem:**
  - **Accounting:** invoices, payments, cash constraints, financial decisions, including an invoice linked to a customer-order ticket.
  - **Inventory:** stock and suppliers.
  - **Customer Service:** quotes and requester drafts, including "what can and cannot be done now".
  - **Facilities:** lease, rent and landlord drafts.
  - Delegate to every material specialist, and to none only for activity.
- **Specialist clarifications:**
  - **Accounting:** prepares full-amount payment proposals for material open obligations, noting open questions, for example unrecorded delivery.
  - **Inventory:** proposes purchases only when supported (a stored cost, a recorded vendor whose specialty plausibly covers the item, a quantity from the ticket or shortfall). It says supplier capability and price are unconfirmed, and makes no second order while an invoice of unknown fulfilment may cover the item.
  - **Customer Service:** prepares a factual unsent draft when the customer should be told where the request stands; quotes only when pricing is requested.
  - **Facilities:** prepares the landlord draft when the lease or rent is the subject.
- **Tests:** `tests/test_problem9_coordination.py`. They check the instructions, prove the intended delegation and proposal flow is supported (scripted), prove seven kinds of unsupported transaction are refused by the tools with nothing created, and prove resolution stays a human step. They cannot predict live-model choices; the live runs show those.

### 12.3 Resolution after a finished run (F2)
The interpretation and the dashboard change are in `output/design.md` §12. A finished run presents "Next: your decisions". A ticket is marked resolved only on the person's confirmation, within the assignment's model (R5, R7), while the record lists what is not verified.

### 12.4 Run policy (F4)
Additional runs of a ticket are permitted when necessary to resolve it, including after a documented correction. Each run needs Dr. Ryan's separate authorization, and all earlier runs and audit evidence are preserved and reported. Re-running to obtain a preferred business decision is not done, and prompts never dictate a specific business choice.

### 12.6 Purchase authorization versus expenditure (F3)
Decided by Dr. Ryan (Problem 9, follow-up 13). The design is in `output/p9_f3_purchase_design.md`.

- **Two human steps.**
  1. **Approve** (authorize): no cash moves.
  2. **Execute** (`approval.execute_purchase`, `POST /api/actions/{id}/execute`): spends cash.

  An approved, unexecuted purchase can be withdrawn (rejected) before execution.
- **Execution requires:**
  - an approved purchase;
  - an explicitly confirmed actual amount (`amount_confirmed`), with a recorded basis:
    - `recorded_unit_cost_estimate`: must equal quantity × stored unit cost; a database estimate, not a supplier quote;
    - `supplier_confirmed_price`: needs a 10–500 character note on how and when the supplier confirmed it;
  - the person's statement that the supplier agreed to supply this item and quantity (`supplier_confirmed`), because a recorded specialty is not enough;
  - the person's name. All of these are attributions, not verified facts: R7 means the system contacts no supplier.
- **R3, applied precisely.**
  - **The rule** (the professor's): a vendor with an unpaid invoice ships nothing.
  - **Our implementation choices:**
    - Approval is allowed, because authorizing ships nothing; an R3 warning is shown.
    - **Execution is refused** while the vendor has an open invoice: we choose not to pay for goods that cannot ship.
- **Cash constraints: purchases yield to obligations.** Commitments are never deducted from `cash_accounts`.
  - **Invoice and rent payments:** actual balance − approved, unexecuted payments (D4).
  - **Purchase execution:** actual balance − approved, unexecuted payments − other approved, unexecuted purchases ≥ amount (R1).
  - **Why:** an authorized purchase can never block paying a real obligation, consistent with R1's "no additional minimum reserve". `GET /api/cash` shows both computed limits (`available`) next to the actual balance.
- **Records, in one transaction:**
  - a `payments` row (`kind = 'purchase'`, `ref_id` = the proposal, `approved_by` = the executor);
  - the cash decrease (guarded, verified);
  - proposal status `executed` with `payment_id` and `executed_at`;
  - an `audit.db` `purchase_executed` event (basis, attestations, note, database estimate and difference, balances, commitments);
  - the JSON `human_decision_requested` and `human_decision_finished` events (operation `execute_purchase`).
- **Not changed:**
  - **Inventory:** paying is not delivery, and the date is fixed (R5).
  - **The vendor's invoices:** no invoice is invented.
- **Consistency.** Every check and write happens in one SQLite transaction with `audit.db`. A failure rolls everything back. Duplicate or concurrent requests execute once (the run lock plus a guarded status update). A lost completion event or a crash is reconciled from `audit.db` (§11.5a).
- **Agents cannot reach it:** it is not an MCP tool, agent modules cannot import `approval`, and the MCP authorizer denies writes to `payments` and `cash_accounts`.
- **Tests:** `tests/test_problem9_purchase_execution.py` (29). Dashboard: four tests in `frontend/src/test/App.test.tsx`.

### 12.7 History versus current state (Problem 9, follow-up 17)
- **Withdrawn authorization versus rejected proposal.** Both end with `status = 'rejected'`.
  - **What is recorded:** withdrawing an approved purchase records, in its `audit.db` `action_rejected` event, `withdrawn_after_authorization: true` with the authorizer, time and note. The original `action_approved` event is also kept, so the full history is authorized → withdrawn → never executed or paid.
  - **What the API adds:** a `withdrawal` object on each action (`desk_reads._withdrawals`, read-only from `audit.db`). It is keyed by the proposal's ID **and** its run ID, so after a reset, when IDs restart, an old withdrawal never attaches to a new proposal.
  - **On the dashboard:** "Withdrawn (was authorized, never paid)", with who authorized and who withdrew, when, and with what notes. A plain rejection still reads "Rejected".
- **Proposal-time warnings versus current conditions.**
  - **Historical:** the stored `warnings`, `vendor_blocked` and `fundable_at_proposal` describe the moment the proposal was made. The API adds `warnings_recorded_at` (business date and creation time), and the dashboard shows them under "At proposal time (…); these may no longer be true".
  - **Current:** for purchases, the API adds `current_vendor_status`, computed from the **current** `invoices` table (open invoice IDs, `blocked_now`). The dashboard shows a separate "Now (business date …):" line, and the approval dialog uses it, never the stored warning, for today's restriction.
- **Tests:** `tests/test_problem9_display_history.py` (4); dashboard tests "distinguishes a withdrawn authorization…" and "labels stored warnings as proposal-time…".

### 12.8 Full agent responses (Problem 9, follow-up 20)
- **Problem:** the trail stores each agent's reply as a 300-character `result_summary` plus a SHA-256 of the full reply. In ticket 101 Attempt 3 the specialists' full replies were therefore lost.
- **Change (narrow):** each new run also writes `output/run_responses/<run_id>.jsonl` (`mcp_server/run_responses.py`).
  - **Created exclusively** when the run starts, so an existing file is never overwritten. If it cannot be created or appended to, the run stops with an `error` event (`RESPONSE_LOG`) and `run_finished`.
  - **One record per line, append-only, hash-chained** (`record_seq`, `prev_hash`, `hash`). Record 1 is `responses_opened` and names the run's `run_started` event.
  - **One record per agent that finished**, in the order they finished, written by trusted code (`delegation.run_agent`), never by a model:
    - who: `agent`, `role` (`entry` or `specialist`), `chain`, `delegated_by`, `delegation_id`;
    - what: the full `task` and the full `response` (the complete `AgentReport`, untruncated), with `response_sha256`;
    - link: `trail_event_id` of the matching `agent_finished` event.
  - **Gaps are explicit:** an agent that ends without a reply gets `agent_response_unavailable` (with the error code, no text). A reply that contains a credential value gets `agent_response_withheld` (hash only, no text).
- **Delegation IDs:** each `delegate()` call gets a `delegation_id` (uuid4, trusted code). It appears in the `details` of `delegation_requested`, `delegation_refused` or `delegation_completed`, and on the child's `agent_started` and `agent_finished` events.
- **Trail unchanged:** the same event types, the same 300-character summaries, the same hash chain. New keys appear only in `details`: `delegation_id`, `response_record` (file, `record_seq`, record hash) on `agent_finished`, and `responses_file` on `run_started`. Earlier events are not touched.
- **Reconciliation:** `run_responses.reconcile(trail_events, run_id, path)` checks, read-only:
  - the file's hash chain and order;
  - that each record matches its `agent_finished` event (same agent and chain, `response_sha256` = `result_sha256`, the trail's `record_seq` points back);
  - that each specialist's `delegation_id` matches a `delegation_requested` to that agent;
  - that every record the trail references exists.

  It reports problems and never repairs anything. `GET /api/runs/{run_id}/responses` serves the records with this check.
- **Earlier runs:** Attempts 1–3 (`53e4f001…`, `ae8d9bf4…`, `84270c9b…`) have no response file. They return `recorded: false` with a note that only the summaries exist, that these are not complete transcripts, and that nothing has been reconstructed. Their evidence files are unchanged (fingerprints checked).
- **Evidence for later runs:** the read-only observer also saves `<ticket>_<label>_responses.json` from the new route (exclusive create, like the other three files).
- **Dashboard:** no change. The run view and the events feed keep the same fields, so the existing dashboard and its 22 tests are unaffected.
- **Tests:** `tests/test_problem9_full_responses.py` (18).
  - **Coverage:**
    - full ~5,000-character replies from the Boss and all four specialists;
    - identity, order and delegation IDs;
    - the summary and event types unchanged;
    - reconciliation;
    - detection of edited, removed or missing records;
    - no overwrite;
    - unavailable and withheld records;
    - concurrent appends;
    - Attempts 1–3 not reconstructed;
    - the route (including 404s for unknown or non-canonical IDs, and no POST);
    - unchanged run-view and event shapes.
  - **Mutation check:** truncating the stored reply makes 3 tests fail; dropping the child's delegation ID makes 5 fail.
- **Remaining limit:** tool results in the trail are still 300-character summaries with a SHA-256. Their effects (proposals, drafts) are stored in full in the database.

### 12.9 Two resolution bases (Problem 9, follow-up 36)
- **Why:** Ticket 101's shirt delivery and customer acceptance cannot be recorded in this simulation (R5: the date never advances; R7: nothing is sent). The only resolution option recorded "the underlying business issue is resolved", which Dr. Ryan declined to certify.
- **Change:** `resolve_ticket` (`mcp_server/approval.py`) and `POST /api/tickets/{id}/resolve` accept a `resolution_basis`.

  | Basis | The person confirms | Required | Recorded |
  |---|---|---|---|
  | `business_outcome` (default; unchanged) | The underlying business issue is resolved | `business_outcome_confirmed: true`; a summary | `business_outcome_confirmed: true` |
  | `simulated_workflow_complete` | Only that the desk's simulated workflow is complete | `workflow_complete_confirmed: true`, `business_outcome_confirmed: false`, at least one of `delivery`, `customer_acceptance`, `message_sent`, `outside_arrangement` (no repeats), a summary | `business_outcome_confirmed: false`, `unverified_outcomes`, the ticket's `completed_runs` |

- **Extra rules for `simulated_workflow_complete`** (refused otherwise):
  - at least one completed agent run for that ticket in the trail (a failed run does not count);
  - no purchase authorized but not executed;
  - the existing rule: no proposal awaiting a decision.
- **Request fields** are strict booleans, and the outcome list is fixed. Bad combinations are refused (422, `INVALID_INPUT`): for example, both bases' fields at once, an empty or repeated list, or `"yes"` for true.
- **Unchanged:** `tickets.status` becomes `resolved` either way. Nothing resolves automatically. Agents have no resolve tool. Payment, purchase and cash rules are unchanged.
- **Records:** the `audit.db` `ticket_resolved` payload and the trail's `human_decision_requested` and `human_decision_finished` details carry `resolution_basis`, `business_outcome_confirmed` and `unverified_outcomes`. Earlier events are not altered; one without a basis (Ticket 102, 2026-10-11) reads as `business_outcome`.
- **Reads:** `desk_reads._resolutions` (read-only `audit.db`) adds a `resolution` object to each resolved ticket in `GET /api/tickets` and `GET /api/tickets/{id}`. It holds the basis, the unverified outcomes, who resolved it, when, and the summary. Only events after the latest completed reset count.
- **Tests:**
  - `tests/test_problem9_resolution_basis.py` (22): basis B recorded as not a business outcome; reads; 8 refused inputs; no completed run; failed run; undecided proposal and unexecuted purchase; basis A unchanged; A refuses B's fields; function-level refusals; no second resolution; legacy events; reset; no automatic route; the real Ticket 102 read-only.
  - Mutation check: recording `business_outcome_confirmed: true` for basis B makes 2 tests fail.
  - Dashboard: 24 tests.

### 12.10 Two display corrections (Problem 9, follow-up 39)
- **`awaiting_human_action`** in `GET /api/runs/{run_id}`:
  - Now `false` once the run's ticket is resolved, even if an unsent draft remains. The draft itself is unchanged and still listed under `drafts_unsent`.
  - An open ticket still shows `true` while proposals or drafts await review.
  - Tests: `tests/test_problem9_resolution_basis.py` b15 and b16. Reverting the condition makes b15 fail.
- **Activity caption on the dashboard:**
  - The run's Activity list is now introduced as "Recorded during this run (started …, finished …)", with a note that agent statements and tool results describe the shop as it was then and may no longer describe its current condition.
  - The recorded events are shown exactly as stored.
  - Dashboard test: the run-streaming test checks the caption.
  - **Follow-up 41:** the caption moved above "The team's summary", so it also covers the Boss's recorded (possibly truncated) summary, which is shown unedited. The test checks that the caption precedes both headings; putting the summary first makes it fail.

### 12.5 Unchanged (F5, F6)
- **Limits:** 40 requests, 60,000 tokens, 60 tool calls, 12 delegations, 300 s per run.
- **Still enforced:** vendor restrictions (R3), actual cash constraints (R1), and the estimate-versus-confirmed distinction.

---

## 13. The final system and the completed three-ticket run (Problem 9)

This section describes what was built, tested and run. Every figure was read from the code, the database schema, the audit records or the generated evidence on 2026-10-11 (§13.13 lists the checks). Requirement wording follows the assignment rules R1–R8 (§7a) and Dr. Ryan's summaries in `AI_prompts.md`.

### 13.1 Architecture: planned versus built

| Area | Original plan | What was built, and why it changed |
|---|---|---|
| MCP tools | Three read tools (Problem 3) | **Ten** tools: six read, four that propose or draft (Problem 5, decision D16; §13.3). The agents needed to read one ticket, the vendors and pending work, and to prepare proposals and drafts. |
| Agents | Five PydanticAI agents with all-to-all delegation (Problem 5) | As planned (§13.2). Each agent gets its **own** MCP server process launched with its identity, so permissions come from the launch, never from the model. |
| Writes | Proposals only, human approval later | Agents write only `pending_actions` and `drafts` rows (§13.4). People approve, execute, reject and resolve through the backend's human-only path (`mcp_server/approval.py`, §13.7). |
| Audit | `output/audit_trail.json` (required) | Required JSON trail, plus a supplementary hash-chained `data/audit.db` and a reconciliation between them (§13.8). |
| Model | `gpt-6-luna` through Portkey | Unchanged. After live attempt 1 failed, `openai_reasoning_effort: "none"` was added (§12.1). |
| Purchases | One approval that also spends | Two human steps: approve authorizes only; **execute** spends (F3, §12.6). |
| Resolution | A person confirms the business issue is resolved | Two explicit types: business outcome confirmed, or simulated workflow complete with fulfilment not verified (follow-up 36, §12.9). |
| Evidence | Trail events with 300-character summaries | Plus full agent replies per run from follow-up 20 (§12.8), observer files per run, and final-state screenshots. |

### 13.2 The five agents (`backend/agents.py`, `backend/prompts/*.md`)

All five use one configured model, `gpt-6-luna` through Portkey (`backend/config.py`), with `{"openai_reasoning_effort": "none"}`. Each has its role prompt, the shared rules (rule 11 included), the `delegate` tool, and **only** the MCP tools below (`mcp_server/server.py`, `ACCESS`).

| Agent | Responsibility (from its prompt) | MCP tools | Can create |
|---|---|---|---|
| **Boss** | Reads the queue and the shop's position, decides the order of work, delegates by business problem. Read-only. | `get_open_tickets`, `get_ticket`, `check_inventory`, `get_financial_position`, `list_vendors`, `list_pending_actions` | nothing |
| **Inventory** | Stock, shortfalls, restocking, suppliers, vendor blocks (R3). | `check_inventory`, `get_ticket`, `list_vendors`, `list_pending_actions`, `propose_purchase` | purchase proposals (estimates) |
| **Accounting** | Cash and obligations; full-amount payments of an open invoice or the rent. | `get_financial_position`, `get_ticket`, `list_vendors`, `list_pending_actions`, `propose_payment` | payment proposals |
| **Facilities** | The lease, the rent, drafts to the landlord. | `get_financial_position`, `get_ticket`, `list_pending_actions`, `draft_message` | landlord drafts (unsent) |
| **Customer Service** | Customers: quotes and drafts to the ticket's requester. | `check_inventory`, `get_ticket`, `list_pending_actions`, `propose_quote`, `draft_message` | quote proposals; customer drafts (unsent) |

- **Delegation:** every agent can delegate to every other agent (`backend/delegation.py`).
  - Enforced in code: cycle refusal, at most 4 hops, at most 12 delegations per run, and one shared usage budget.
  - Each delegation has a `delegation_id` (follow-up 20).
- **Limits per run** (`RunBudget`): 40 model requests, 60,000 tokens, 60 tool calls, 1,200 output tokens per request, 12 delegations, depth 4, 300 s, 60 s request timeout, 1 retry.

### 13.3 The ten MCP tools (`mcp_server/server.py`)

| Tool | Kind | Reads / writes | Agents | Added |
|---|---|---|---|---|
| `check_inventory` | read | inventory, pricing | Boss, Inventory, Customer Service | Problem 3 |
| `get_financial_position` | read | desk, cash_accounts, invoices, vendors, leases, payments | Boss, Accounting, Facilities | Problem 3 |
| `get_open_tickets` | read | tickets, invoices, vendors, leases | Boss | Problem 3 |
| `get_ticket` | read | tickets, invoices, vendors, leases, inventory, pending_actions, drafts | all five | Problem 5 |
| `list_vendors` | read | desk, vendors, invoices (R3 blocks; hypothetical arrival = date + `lead_days`) | Boss, Inventory, Accounting | Problem 5 |
| `list_pending_actions` | read | pending_actions, cash_accounts, desk | all five | Problem 5 |
| `propose_payment` | propose | writes `pending_actions` (+ `audit.db` event) | Accounting | Problem 5 |
| `propose_purchase` | propose | writes `pending_actions` (+ `audit.db` event); estimate, R3 flagged | Inventory | Problem 5 |
| `propose_quote` | propose | writes `pending_actions` (+ `audit.db` event); between unit cost and list price | Customer Service | Problem 5 |
| `draft_message` | draft | writes `drafts` (+ `audit.db` event); never sent (R7) | Customer Service (requester), Facilities (landlord) | Problem 5 |

- **Read tools** open the working copy read-only.
- **Write tools** go through `mcp_server/ops.py` on a connection restricted by an allowlist authorizer (`authz.py`). Each write is one row plus one `audit.db` event in one transaction.
- **Without a trusted launch identity** (development mode), the write tools refuse.
- **Offline check:** `mcp_server/verify_tools.py`, 45/45 checks on 2026-10-11. It runs two passes: the current working copy, and a fresh copy of the original (§9 note).

### 13.4 Extension tables and the audit database

| Where | Table | Purpose |
|---|---|---|
| Working copy (`schema_ext.sql`, version 2) | `pending_actions` | Agent proposals: `kind` ∈ payment, purchase, quote; `status` ∈ proposed, approved, rejected, executed, failed. Each row carries its decision and execution fields (`decided_by`, `decided_at`, `executed_at`, `payment_id`). Triggers force new rows to `proposed` and forbid deletes. Unique indexes block duplicate open proposals; one refused a duplicate rent proposal in the 102 run. |
| Working copy | `drafts` | Unsent messages: `status` can only be `draft`. Deletes are forbidden. |
| Working copy | `schema_ext_meta` | Extension version. |
| Working copy | `reset_stamp` | The ID, requester and time of the latest controlled reset (`maintenance.py`), used by reconciliation. |
| `data/audit.db` (`audit_schema.sql`) | `events`, `audit_meta` | Supplementary hash-chained log. Every proposal, draft, decision, payment, resolution and reset writes an event in the same transaction as the change. Triggers forbid update and delete. Kept across resets; committed to the repository as part of the final evidence. |

### 13.5 FastAPI backend (`backend/main.py`)

- **Routes:** 14 API routes plus `GET /` (§11.1). They cover tickets, runs, events, full responses, pending actions, approve, execute, reject, resolve, cash, reset and audit.
- **Every POST** needs `X-Human-Decision: confirm`. Live runs need `CC_LIVE_AUTHORIZATION`. CORS allows only `localhost:5173`.
- **No route** lets an agent approve, pay, send or resolve. Agents have no HTTP tools.

### 13.6 React dashboard (`frontend/`, design in `output/design.md`)

- **Stack:** React 19.3, Vite 8.3.4, TypeScript 7.0.2.
- **Shows:**
  - the three tickets, with three separate status vocabularies (ticket, run, proposal);
  - live run activity with agents, tools and delegations;
  - the Boss's summary;
  - decision dialogs: approve, reject, execute purchase, and resolve with the two resolution types;
  - the actual checking balance, kept apart from proposals, estimates and quotes;
  - the audit status.
- **Labelling of what the records do and do not show:**
  - historical warnings are marked "at proposal time";
  - a withdrawn authorization is distinguished from a plain rejection;
  - the run's recorded activity is captioned as historical (follow-ups 39 and 41).
- **Tests:** 24 Vitest tests on recorded backend responses.

### 13.7 Proposed, approved, executed, verified

| Stage | Who | What it means in the records | Cash |
|---|---|---|---|
| **Proposed** | an agent | a `pending_actions` row with `status = proposed`; reserves nothing | none |
| **Approved** | a person | payment: approval **is** execution (next row). Purchase: `approved` = authorized, nothing ordered. Quote: `approved` = internal price decision; nothing sent, no revenue (R6, R7) | none for purchases and quotes |
| **Executed** | a person | a `payments` row, cash reduced, the invoice `paid` or the lease `next_due` +1 month (D6), and the proposal marked `executed` with its `payment_id`, all in one transaction with its `audit.db` events | yes |
| **Rejected / withdrawn** | a person | `status = rejected`; a withdrawn authorization is recorded with its earlier approval | none |
| **Resolved** | a person | `tickets.status = resolved`, with a resolution type and the person's summary (§12.9) | none |
| **Verified business outcome** | outside the system | Delivery, shipment, customer acceptance and the sending of messages **cannot be recorded or verified here**: the date never advances (R5) and nothing is sent (R7). A resolution of type `business_outcome` is the person's statement, not a system verification. | — |

### 13.8 Audit integrity

- **`output/audit_trail.json`** (required):
  - append-only and hash-chained (`prev_hash`, `hash`), verified on every read;
  - written under an OS file lock;
  - fails closed: a tool is not called if its start event cannot be written.
- **`data/audit.db`:** hash-chained, with no update or delete, written inside the same transaction as each business change.
- **Reconciliation** (`audit_reconcile.check`): cross-checks the two records and the rows. It reports gaps, never repairs them silently, and runs before every decision.
- **Run lock:** one run, decision or reset at a time.
- **Full replies:** per-run response files are hash-chained and reconciled with the trail (§12.8).
- **State on 2026-10-11 after the run:** 192 trail events and 16 `audit.db` events, chains valid, consistent, no gaps.

### 13.9 The three-ticket run (Problem 9)

- **Reset:** one controlled reset at 2026-10-10 20:36:42 UTC, requested by Dr. Ryan (trail events 2–3). The opening checking balance of **$3,400.00** was recorded right after it (`output/p9_runs/g1_reset_record.json`). There was no later reset.
- **Ticket 101:**
  - **Attempt 1** (`53e4f001…`): failed with provider HTTP 400 (reasoning combined with tools); corrected offline (§12.1).
  - **Attempt 2** (`ae8d9bf4…`): completed with no proposal or draft; coordination corrected (F1, §12.2).
  - **Attempt 3** (`84270c9b…`): Boss → Accounting, Inventory, Customer Service; proposal 1 (pay invoice 501, $840) and draft 1.
  - Dr. Ryan approved proposal 1 (payment 2) and resolved the ticket as **simulated workflow complete; fulfilment not verified** (unverified: delivery, customer acceptance, message sent).
- **Ticket 102:**
  - Run `f2412172…`: Boss → Facilities and Accounting; Facilities → Accounting; proposal 2 (rent, $2,400); one duplicate `propose_payment` refused (`DUPLICATE`); landlord draft 2.
  - Dr. Ryan approved proposal 2 (payment 1) and resolved the ticket with the business outcome confirmed (§13.10).
- **Ticket 103:**
  - Run `fa38a75b…`: Boss → Customer Service; quote 3 (20 × navy M at $50, $1,000) and draft 3.
  - Dr. Ryan approved the quote internally and resolved the ticket as **simulated workflow complete; fulfilment not verified** (unverified: delivery, customer acceptance, message sent).
- **Cash:** $3,400.00 − $2,400.00 (payment 1, ticket 102) − $840.00 (payment 2, ticket 101) = **$160.00** = `cash_accounts`. Quote 3 moved no cash and generated no revenue (R6). These are recorded payments within the simulation, not bank transfers.
- **Deliverables** (generated by `scripts/build_p9_evidence.py` from these records; validated by `tests/test_problem9_evidence.py`, 41 tests):
  - the Actual sections and Cash tab of `output/desk_tickets.html` (Expected sections byte-for-byte unchanged);
  - `output/resolved_tickets.json`;
  - `output/resolved_board.html`, with the three unedited final-state screenshots from `output/p9_screenshots/`.

### 13.10 Ticket 102's resolution type (a legacy record)

Ticket 102 was resolved at 2026-10-11 13:32:18 UTC, **before** the resolution-type field existed. The field was added in follow-up 36; the first record carrying it is ticket 101's resolution at 19:17:18 UTC (`audit.db` event 15). Ticket 102's stored records are:

- **`audit.db` event 9 (`ticket_resolved`, decision `45bb864b…`):** payload keys `basis`, `business_outcome_confirmed`, `decided_by`, `not_verified_by_system`, `note`, `resolution_summary`, `status_after`, `status_before`, `system_records_at_resolution`, `ticket_id`.
  - `business_outcome_confirmed` is `true`.
  - There is **no** `resolution_basis` and **no** `unverified_outcomes`.
  - The key named `basis` holds "the person's own confirmation (attribution only)". It describes the nature of the statement; it is not a resolution type.
- **Trail events 155–156:** details carry `business_outcome_confirmed: true` and the summary, with no `resolution_basis`.

At that time the only way to resolve was the business-outcome confirmation. The system therefore reads a record **without** `resolution_basis` and with `business_outcome_confirmed: true` as `business_outcome` (`desk_reads._resolutions`; the same rule is in `scripts/build_p9_evidence.py` and the tests). Nothing was added to or changed in the stored record.

### 13.11 Limitations of the evidence

- **Ticket 101's full agent replies were not stored.** All three attempts predate the full-response recorder (follow-up 20). Only 300-character summaries exist in the trail.
- **Tool results are 300-character summaries** (with a SHA-256) for every run. The records they created are complete in the database.
- **Ticket 103's observer files were reconstructed after the run** (follow-up 32). No observer was running. They were rebuilt from the contemporaneous trail, the response file and the database rows, and labelled as reconstructed (`103_attempt1_recovery_record.json`). A state snapshot at the moment the run ended cannot be recovered.
- **Screenshots show only part of the activity.** The dashboard's Activity panel scrolls, so each final-state screenshot shows only the first 8–9 events (of 46, 52 and 28). The complete events are in the trail. Times in the images are local (UTC−4).
- **The first capture record was wrong.** `capture_record.json` first reported the activity fields wrongly because of a selector error. The original is kept as `capture_record_original_with_error.json`, and the corrected record documents the fix (follow-up 46).
- **No physical fulfilment is verified for tickets 101 and 103.** No record shows the reprint delivered, size S received, hoodies supplied, customer acceptance, or any message sent. Inventory is unchanged from the original.

### 13.12 Remaining documentation outside this file

- `output/desk_tickets.html` is titled "Desk Tickets: Plan (Problem 6) and Results (Problem 9)", and keeps the original Problem 6 description. Its Reflection tab holds the Problem 10 reflection, inserted unchanged by the generator from `output/p10_reflection.html`. The generator was run with `--only-desk` after preserving copies in `output/p9_deliverables_v2/`; `resolved_tickets.json` and `resolved_board.html` were not rewritten.
- **Regeneration (follow-up 53).** The deliverables were regenerated once to correct wording and presentation (the not-verified wording, the title, the ticket 102 legacy note, the run and error wording, the header contrast, and historical labels on the planning-time facts). The procedure:
  - The previous versions are preserved byte-for-byte in `output/p9_deliverables_v1/` with `SHA256SUMS.txt`.
  - The generator builds `desk_tickets.html` from the unchanged Problem 6 file `output/p6_desk_tickets_original.html` (SHA-256 checked).
  - It replaces outputs only when given verified preserved copies (`--replace-preserved`), after building and checking all three.
  - No record, screenshot or Expected block changed.
- The Problem 11 repository instructions (README workflow section, `output/github_url.txt`) are not written yet.

### 13.13 Checks behind this section (2026-10-11)

- **Code:** agent tools from `server.ACCESS`; limits from `RunBudget().describe()`; model settings from `config.MODEL_SETTINGS`; 14 routes from `GET /`.
- **Schema:** `schema_ext.sql`, `audit_schema.sql`, and `pragma table_info` on the working copy and `audit.db`.
- **Records:** the trail, `audit.db` and the working database (read-only); the cash reconciliation.
- **Evidence:** `output/resolved_tickets.json` and the screenshot fingerprints.
- **Test results:** backend `pytest` 667 passed (after follow-up 53); frontend Vitest 24/24; `verify_tools.py` 45/45; `tests/test_problem9_evidence.py` 41/41 (after the follow-up 53 regeneration).

