# Problem 5: Proposed architecture (revision 5, for review)

**Approval (2026-10-09):**
- Dr. Ryan approved D3, D7, D8, D12, D13, D14, D15, and D17, with qualifications. See `AI_prompts.md`, Problem 5, follow-up 5.
- **D16 was approved** (conditionally, then revision 2; follow-up 6) and implemented in Stage 3. The full tool specification, including the agent access matrix that supersedes the §3 tool column, is in `output/p5_mcp_tools_spec.md`.

**Implementation status:**
- **Stage 1, built** (paths, schema extension, reset, snapshot): accepted provisionally on 2026-10-09.
- **Stage 2, built** (operations layer, restricted connection, `audit.db`), awaiting review:
  - **Code:** `mcp_server/ops.py`, `authz.py`, `audit_db.py`, `audit_schema.sql`, `policies.py`.
  - **Tests:** 209 offline tests pass (`output/p5_stage2_tests.txt`).
  - **Evidence:** `output/p5_stage2_authz_evidence.json`.
- **Changes found by testing:**
  - §18.3's B6 fallback was **not needed.** A probe confirmed that the authorizer does not see the implicit deletes of `INSERT OR REPLACE`. They are now blocked by no-delete triggers together with `PRAGMA recursive_triggers=ON` on the restricted connection.
  - `audit.db` setup takes its write lock up front (`BEGIN IMMEDIATE`). The deferred version failed with "database is locked" under concurrent first use.
- **Stage 3, built** (ten MCP tools, per-agent registration, launch identity), awaiting review:
  - **Tests:** 282 offline tests pass.
  - **Evidence:** `output/mcp_regression_p5.json`.
  - **Drafting authority:** restricted by agent in code (follow-up 9).
  - **`call_id`:** arrives in `_meta` as a plain dict in FastMCP 4.1.0.
  - **Live re-check:** done after the authorized reset and a server reload. 16 calls, recorded in `output/mcp_live_regression_p5.json`.
- **Stage 4, built** (the required `output/audit_trail.json`), awaiting review:
  - **Code:** `mcp_server/audit_trail.py`, `audit_reconcile.py`, `locks.py`, and `scripts/verify_audit.py`.
  - **Reset and snapshot scripts:** now append `db_reset` / `db_snapshot` system events.
  - **Tests:** 326 offline tests pass.
  - **Departures from §19, all reported to Dr. Ryan:**
    1. **Module location:** the trail modules live in `mcp_server/`, not `backend/`, because the reset and snapshot scripts and the future runner share them. `audit_trail.py` contains no SQLite.
    2. **Where resets are recorded:** in the JSON trail only, not in `audit.db`. This keeps the Stage 1 guarantee that a reset never touches `audit.db`.
    3. **Reconciliation:** an explicit operator step (`scripts/verify_audit.py reconcile --confirm`), not an automatic "next writer" action. The event is named `incomplete_operation_reconciled`.
    4. **Initialization:** the trail starts with a `trail_initialized` event. Earlier activity is not backfilled.
    5. **Run lock:** reset and snapshot now refuse while a run holds it. This was pending since Stage 1.
- **Stage 5, built** (the five-agent team), awaiting review:
  - **Code:** `backend/config.py`, `models.py`, `agents.py`, `delegation.py`, `runner.py`, and `prompts/`.
  - **Tests:** offline only, with scripted test models.
  - **Implemented as designed in §3, §15, and §16:** one MCP server per agent with launch identity, a client-side tool filter as a second layer, all-to-all `delegate`, and shared usage.
  - **Changes from the design:**
    1. **MCP integration:** `MCPToolset` with `process_tool_call`, because PydanticAI 2.55 has no `MCPServerStdio`. The `call_id` travels as MCP `_meta`.
    2. **Refusals:** a refused tool call reaches the model as a structured `{ok: false}` result, not as a retry, so rule refusals do not use up retries.
    3. **Agent self-reports:** an agent's reported IDs are recorded as `agent_reported_*`, never as verified links.
    4. **Lock file:** the Stage 4 lock module was fixed so it never writes to the lock file. See `mcp_server/README.md`.
- **Live connectivity:** confirmed by one authorized request (2026-10-10).
- **Stage 6, done** (offline integration verification; dependency map in `output/p5_ticket_workflow_map.md`), awaiting review. The architecture is unchanged: a test asserts that `server.ACCESS`, `ops.OPERATION_AGENTS`, `policies.DRAFT_AUTHORITY`, and the agent list agree.
- **Stage 7, done** (acceptance review): traceability in `output/p5_acceptance_matrix.md`; documentation consistency tested; ticket-map assumption about hoodie supply corrected; roadmap in `output/hw5_roadmap_p6_p11.md`.
- **Remediation, done** (`output/p5_remediation_report.md`), awaiting review:
  - **Launch protocol extended:** the server also receives `CC_HW5_ROOT`, `CC_RUNTIME_DIR`, and `CC_WORKING_DB`.
  - **Simulated mode:** an allow-list of offline test models, plus an offline network guard.
  - **Reconciliation:** takes the run lock and matches rows by run and call.
  - **`RunBudget`:** gains limits on tokens, tool calls, output, wall clock, timeout, and retries.
  - **Reset:** extends the copy before the swap and holds the run lock.
- **Not built yet:** a live agent run; Problem 7 approval and execution.

**Revision 5 (2026-10-09):**
- Corrections from Dr. Ryan's comparison against the full official assignment.
- Section 1 is rewritten as the requirements matrix, with Q1–Q5 closed. Section 16 now covers Portkey from its documentation.
- Section 19 is new: the required `output/audit_trail.json`, how it relates to `audit.db`, how the runtime folder fits the repository, the file layout, and the consolidated test plan.
- Section 19 takes precedence over sections 1–18 wherever they conflict.

**Revision 4 (2026-10-09):**
- Dr. Ryan approved D1–D11 in principle, with qualifications.
- Section 18 sets out those qualifications and takes precedence over earlier sections wherever they conflict.
- The register in section 17 is updated.

**Status:** design only. Nothing in this document has been built, installed, or run. Dr. Ryan approved the overall direction on 2026-10-09.
- Revision 2 added sections 1–12.
- Revision 3 adds sections 13–16: approval integrity, audit consistency, delegation enforcement, and the Portkey materials needed. Section 17 replaces the earlier decision register in section 12.

**Labels used in this document:**
- **[A]** an assignment requirement, as supplied by Dr. Ryan (Problem 1 follow-up 1, and rules R1–R8 in `output/harness.md` §7a)
- **[P]** a proposed design choice or business convention. It is not required by the assignment and needs approval.
- **[T]** an ordinary technical choice, made during implementation
- **[?]** unknown. It must not be invented.

---

## 1. Assignment alignment

### Problem 5 requirements matrix (revision 5)
Source: Dr. Ryan's comparison against the full official assignment (Problem 5, follow-up 4, in `AI_prompts.md`), plus rules R1–R8.

| # | Deliverable [A] | Where | How it is met | Section |
|---|---|---|---|---|
| R5-1 | Five PydanticAI agents: Boss, Inventory, Accounting, Facilities, Customer Service | `backend/agents.py` | One `Agent` each | 3 |
| R5-2 | Full connectivity: every agent can delegate to every other | `backend/delegation.py` | A `delegate(to, task)` tool on all five; cycle, depth, and budget safeguards | 3, 15 |
| R5-3 | One prompt file per agent | `backend/prompts/{boss,inventory,accounting,facilities,customer_service}.md` | Loaded at startup; a missing file fails the startup check | 19.4 |
| R5-4 | Data types | `backend/models.py` | Pydantic models: run dependencies, delegation result, proposal, draft, audit event, agent outputs | 19.4 |
| R5-5 | Implementations under `backend/` | `backend/` | All agent-side code | 19.4 |
| R5-6 | Model: `gpt-6-luna` through Portkey, with `PORTKEY_API_KEY` | `backend/config.py` | One configuration point; no other model | 16 |
| R5-7 | Expanded MCP tools | `mcp_server/server.py` | Read, propose, and draft tools. No names or count are mandated. | 3, 19.4 |
| R5-8 | JSON audit logging of agent-loop events | `output/audit_trail.json` (append-only, keeps previous runs) | `backend/audit_trail.py` | 19.1 |
| R5-9 | Expanded `output/harness.md` | `output/harness.md` | New section for Problem 5: agents, tools, delegation, audit | 19.4 |
| R5-10 | Updated `mcp_server/README.md` | `mcp_server/README.md` | New tools, authorizer, runtime path | 19.4 |
| R5-11 | Agents reach shop data only through MCP | `backend/` | No `sqlite3` in agent code (checked by a test) | 19.3 |
| R5-12 | Rules R1–R8 hold | `mcp_server/ops.py` | Checked in code, citing the rule numbers | 5, 18.4 |
| R5-13 | Both database files in the final GitHub repository; works without machine-specific configuration | `data/` | Default paths need no `.env` path settings | 19.3 |

### Q1–Q5: closed (revision 5)
| Q | Answer from the assignment | Consequence for the design |
|---|---|---|
| Q1 Tool names or count? | **Not mandated.** | Our tool set is a design choice **[P]**, kept small. |
| Q2 Write scope in Problem 5? | Human **approval routes are required in Problem 7.** | Problem 5 builds **read, propose, and draft** tools only. Approve and execute (and their atomic payment transaction) move to Problem 7. Problem 5 still uses the attached-transaction design for its own writes (proposal + `audit.db` event). D3 and D11 are re-scoped; see 19.5. |
| Q3 Live run? | Live operation is **needed later**, for the ticket-resolution workflow (Problem 9). Problem 5 does not mandate one. | At most one minimal live connectivity check at the end of Problem 5, **only if separately authorized** (D10). |
| Q4 Audit trail in Problem 5? | **Yes**, explicitly required. | `output/audit_trail.json` (19.1) |
| Q5 Deliverables? | The matrix above | — |

### Runtime connection status
- **Verified on 2026-10-09:**
  - There is no `hw5/.env` yet.
  - `pydantic-ai` is not installed.
  - No Portkey connection has been tested.
- The Portkey details are resolved from Portkey's documentation in section 16 (revision 5).

---

## 2. Overview

```
 Trusted human (CLI now; dashboard in Problems 7–8)
     │  approve / reject / execute         ◀── not an MCP tool, not an agent tool
     ▼
 ops.py (rule checks + atomic executor)
     │
     ▼
 data/campus_customs_new.db  ── original 9 tables + extension tables (§6)
     ▲
     │  read + propose + draft only
 FastMCP server (mcp_server/server.py)
     ▲  each agent receives a filtered subset of tools
 Boss ⇄ Inventory ⇄ Accounting ⇄ Facilities ⇄ Customer Service   (PydanticAI, gpt-6-luna via Portkey)
     │
     ▼
 data/audit.db  (separate file, survives resets, §5)
```

---

## 3. Agents and delegation

### Responsibilities [P]
| Agent | Owns | MCP tools (subset) |
|---|---|---|
| Boss | The ticket queue, sequencing across tickets, the final summary for the human | `get_open_tickets`, `get_ticket`, `get_financial_position`, `list_actions` |
| Inventory | Stock, restock options, vendor blocks (R3) | `check_inventory`, `list_vendors`, `propose_purchase` |
| Accounting | Cash, obligations, commitments (R1), payment preparation | `get_financial_position`, `list_actions`, `propose_payment` |
| Facilities | The lease and rent notices | `get_financial_position` (lease section), `get_ticket` |
| Customer Service | Customer-facing drafts and quotes | `get_ticket`, `check_inventory`, `draft_message`, `propose_quote` |

Every agent also has the `delegate(to, task)` tool. That tool is not on the MCP server; it lives in Python on the agent side.

### How delegation works [P/T]
- Each run carries a shared context object: `run_id`, the **chain** of agents currently active (for example, `[Boss, CustomerService]`), and **one shared usage counter**.
- `delegate(to, task)` runs the target agent with the same context and passes the shared usage counter. The target's answer comes back as the tool result.
- **Cycle prevention:**
  - `to` must not already be in the chain. If it is, the tool returns a plain message instead of raising an error, for example: "Inventory is already working on this chain (Boss → Inventory → Accounting); answer with what you have."
  - The calling agent then continues normally.
  - **This does not block legitimate work:**
    - Answers travel *back* to the caller as return values, so an agent never needs to delegate back up the chain to reply.
    - The same specialist may be consulted more than once *in sequence* (Boss → Inventory, later Boss → Inventory again), because sequential calls are not nested.
- **Depth limit:** with five agents and no cycles, the longest possible chain is 4 hops (all five agents). Setting the limit to 4 is therefore a backstop only. It can never cut off a chain that cycle prevention allows.
- **Shared usage budget:**
  - One limit per run covers the model requests and tokens of *all* agents together. The numbers are set by Dr. Ryan; see D10.
  - If the budget runs out, the run stops, the stop is recorded in the audit log, and the tickets stay open.
  - Because agents can only propose (§4), a stopped run leaves no money moved and no records half-updated.

### Example chain (ticket 103: 20 hoodies, size M)
```
Boss ─delegate─▶ Customer Service: "Prepare a response to Yale AI Club"
   Customer Service ─delegate─▶ Inventory: "Can we supply 20 × CC-HOOD-NAVY M?"
      Inventory: check_inventory → 8 on hand, 12 short
      Inventory: list_vendors → Bulldog Print Co blocked (open invoice #501, R3)
      Inventory ─delegate─▶ Accounting: "What cash is available to commit for a reprint?"
         Accounting: get_financial_position → stored $3,400; approved commitments $X
         Accounting → returns figures to Inventory (no delegation needed to reply)
      Inventory → returns availability, the R3 block, and a labeled cost estimate to Customer Service
   Customer Service: draft_message (stored, never sent, R7); optional propose_quote
   Customer Service → returns its draft summary to Boss
Boss → summary for the human, listing items that need approval
```
Chain depth here is 3. If Accounting then tried to delegate to Inventory, the cycle rule would block it.

---

## 4. Proposal, approval, execution

Three separate steps, done by three separate parties:

| Step | Who | How | What it can change |
|---|---|---|---|
| **Propose** | An agent | MCP tool `propose_*` | Inserts one `pending_actions` row with status `proposed` |
| **Approve / reject** | A trusted human | `scripts/actions.py approve <id> --by "<name>"`; later a dashboard button | Status → `approved` or `rejected`, and records `decided_by` |
| **Execute** | The system, triggered by the human | `ops.execute(id)` | For a payment: the full atomic transaction below |

**Why an agent cannot approve or bypass:**
- `approve` and `execute` are plain Python functions. They are **not** registered on the MCP server or given to any agent, so no agent has a way to call them.
- There is no `status` parameter on any agent tool. Proposals are always created as `proposed`.
- The executor refuses any action whose status is not `approved`, and refuses any action it has already executed.

**Honest limit:** this is a local, single-user project with no login system. A "trusted human" means whoever runs the approval CLI or dashboard. The protection works against *agents*, not against someone at the computer who edits the database directly. See section 13.

---

## 5. Financial safeguards (R1, R2, R4)

### Three kinds of money state
| State | Where | Reserves cash? |
|---|---|---|
| Pending proposal | `pending_actions.status = 'proposed'` | No. Shown as information only. |
| Approved commitment | `status = 'approved'` and not yet executed | **Yes [P]**, counted against cash available to commit |
| Executed payment | A `payments` row, with `status = 'executed'` | Already deducted from the stored balance |

**Available to commit** = stored balance − sum of approved, not-yet-executed commitments. This is a calculation, not a stored fact.

### Checks
1. **When proposing:** the tool reports whether the amount fits within available-to-commit. It does **not** refuse, so a human still sees every option.
2. **When approving [P]:** inside `BEGIN IMMEDIATE`, approval is refused if `amount > balance − other approved commitments`. Two approvals that together exceed cash therefore cannot both succeed. The second one is refused.
3. **When executing [A, R1]:** inside the same transaction, the executor re-reads the **stored** balance and refuses if `balance − amount < 0`. It also re-checks that the invoice is still open, or the lease amount still matches.

Check 2 is a stricter design choice that we would add. R1 itself requires only check 3.

### Atomic payment transaction (R4) [A requirement; exact values P]
```
BEGIN IMMEDIATE
  re-read action (approved, not executed)      else ROLLBACK
  re-read balance; refuse if balance - amount < 0
  INSERT payments (kind, ref_id, amount, account, paid_at = desk.date_today, approved_by)
  UPDATE cash_accounts SET balance = balance - amount
  UPDATE invoices SET status = 'paid'   |  UPDATE leases SET next_due = <+1 month>
  UPDATE pending_actions SET status = 'executed', payment_id = ...
  INSERT audit event (attached audit.db, same transaction)
COMMIT          (any error → ROLLBACK, nothing changes)
```
- `PRAGMA foreign_keys = ON`.
- Amounts are rounded to cents.
- The audit database is attached in rollback-journal mode, so the payment and its audit event commit or roll back together. SQLite does not guarantee this across attached databases in WAL mode, so WAL will not be used. The full reasoning and the crash tests are in section 14.

---

## 6. Purchase workflow [P]

Approval, ordering, shipment, receipt, invoicing, and payment are **separate events**. None of them implies another.

| Status | Meaning | Allowed while the vendor is blocked (R3)? |
|---|---|---|
| `proposed` | An agent prepared it. `vendor_blocked` is recorded and shown prominently. | Yes |
| `approved` / `rejected` | A human authorized or refused the purchase internally | Yes. Approval is internal and ships nothing. |
| `ordered` | A human confirms the order was placed (R7: no real vendor contact) | **No.** Refused while the vendor has an open invoice. |
| `received` | A human confirms the goods arrived. Inventory increases in one transaction. | **No.** Requires `ordered`. |

- The cost is stored as `amount_basis = 'estimate_unit_cost'`. It is never presented as a quote. No vendor quote exists [?].
- Paying for a purchase happens only through a real invoice. No invoice is created automatically.
- **Proposed scope for Problem 5:** `proposed`, `approved`, and `rejected` only. `ordered` and `received` are designed here but built only if Dr. Ryan wants them (D7).

---

## 7. Audit trail (`data/audit.db`) [P]

- **Survives resets:** the reset script (R8) touches only `campus_customs_new.db`. It never opens `audit.db`.
- **Linked to a run:**
  - A `runs` table records `run_id`, start and end time (wall clock), `desk.date_today`, the model ID, and the working database's SHA-256 at the start.
  - Every `events` row has a `run_id`, a sequence number, an actor (an agent name, `human:<name>`, or `system`), an event type, a ticket ID, and a JSON payload.
- **Protection against ordinary changes:**
  - The application code contains only an append function.
  - Triggers raise an error on `UPDATE` or `DELETE`.
  - Each event stores a hash of the previous event, and a verify script checks the chain.
- **Limits:**
  - Triggers stop accidental or ordinary application edits only. Anyone with file access can drop them or edit the file.
  - The hash chain makes such edits *detectable*, not impossible.
  - This is append-only by design, not tamper-proof storage.
- **Evidence:** each run can be exported to `output/` as JSON (D8).

---

## 8. Working-database extensions [P]

Created by `mcp_server/schema_ext.sql` after each reset. The original database and the nine existing tables are not altered.

| Table | Purpose | Relationships |
|---|---|---|
| `pending_actions` | Proposals and their lifecycle: `kind` (payment, purchase, or quote), amount, `amount_basis`, `proposed_by`, reason, `vendor_blocked`, status, `decided_by`, `decided_at`, `executed_at`, `run_id` | `ticket_id` → `tickets`; `invoice_id` → `invoices`; `lease_id` → `leases`; `vendor_id` → `vendors`; `payment_id` → `payments` |
| `drafts` | Customer and vendor messages, never sent (R7) | `ticket_id` → `tickets` |

**Reset behavior (R8):**
1. Copy the original over the working copy.
2. Check that the working copy's hash equals the original's (`23686a90…f360a`).
3. Apply `schema_ext.sql`.

After step 3, the working file's hash will **differ** from the original's. That is expected. Integrity is shown by the step-2 check plus a check that the nine original tables are unchanged.

---

## 9. Business conventions register
| Item | Assignment says | Proposal [P] |
|---|---|---|
| Discount floor (103) | Nothing | A quote must be ≥ `unit_cost` ($22). A human approves every quote. |
| Invoice status after payment | R4: records must update; values not given | `paid` |
| Lease after rent paid | R4: same | `next_due` moves forward one month (2026-10-02) |
| `payments.kind` / `ref_id` | Not given | `invoice` / `lease`, with `ref_id` set to that row's ID |
| `approved_by` | Required field (schema) | The human's name, as typed at approval |
| Ticket closure | Not given | An agent may *recommend* closing. Status changes to `resolved` only after the human confirms. |
| Vendor quotes, deliveries, customer acceptance | Not in the data | **[?]** Never invented. Recorded only when a human enters them. |

---

## 10. Testing and cost control

| Level | What | Model calls | Label in reports |
|---|---|---|---|
| 0 | `ops.py` rules and transactions, on a temporary copy of the database | None | Offline |
| 1 | MCP tools, through FastMCP's in-memory client | None | Offline |
| 2 | Agents and delegation, with PydanticAI test models that script which tools and delegations happen | None | **Simulated delegation**: shows the wiring, not the model's reasoning |
| 3 | Live `gpt-6-luna` through Portkey | Yes | **Runtime demonstration**, only after Dr. Ryan explicitly authorizes it, with a set call budget |

---

## 11. Implementation sequence (after approval)
1. Settle D1–D10 and the Portkey details.
2. Write `schema_ext.sql` and `scripts/reset_db.py`. Run the level-0 tests.
3. Build `ops.py`: propose, approve, execute, and their rule checks. Run the level-0 tests.
4. Build `audit.db` and its verify script. Run the level-0 tests.
5. Add the new MCP tools. Run the level-1 tests and update `verify_tools.py`. Optionally, run live checks through Claude Code, which involve no `gpt-6-luna` cost.
6. Install `pydantic-ai` (pinned). Build the five agents and the `delegate` tool. Run the level-2 tests.
7. Add the model configuration and `.env.example`. Wait for authorization, then run one live level-3 smoke test.
8. Update the documentation and the Problem 5 report.

---

## 12. Decision register

Replaced in revision 3. See section 17.

---

## 13. Approval integrity (revision 3)

**The typed name is attribution, not authentication.** `decided_by` and `payments.approved_by` record *who says* they approved. Nothing verifies that claim. For this local prototype, "trusted human" means whoever has access to the computer and runs the approval script (later, the dashboard). This limit will also be stated in `README.md`.

What the design *does* guarantee is that **no agent can reach the approval pathway**. Four separate layers enforce this:

| Layer | Mechanism | What it stops |
|---|---|---|
| 1. Not exposed | `approve`, `reject`, and `execute` live in `mcp_server/approval.py`. They are not registered as MCP tools, are not in any agent's toolset, and no agent has shell, code-execution, or HTTP tools. | An agent has no tool that leads there |
| 2. Not importable by agent code | A test scans `mcp_server/server.py` and `backend/agents/` and fails if either imports `approval.py` | Future code accidentally wiring approval into an agent path |
| 3. Database permission on the MCP connection | The MCP server's SQLite connection installs an *authorizer* (`sqlite3.Connection.set_authorizer`). It allows reads, `INSERT` into `pending_actions`, `drafts`, and the audit `events` table, and **denies every `UPDATE` and `DELETE`** and every write to `payments`, `cash_accounts`, `invoices`, `leases`, `inventory`, and `tickets`. | Even a buggy or misused MCP tool can't change a status or move money |
| 4. Database trigger | Any `INSERT` into `pending_actions` must have `status = 'proposed'` and empty `decided_by` and `executed_at` | Creating a row that is already "approved" |

The approval script uses its own connection without the restrictions in layer 3. Approval is a separate path, in a separate process, with separate permissions.

**Not protected against:** a person with file access editing the database with another tool. That is out of scope for a local prototype and is stated as a limit.

**Offline tests (no model calls):**
- A1. List the MCP tools through FastMCP's in-memory client. The set must equal the approved list, and no tool may accept a `status` or `approved_by` argument.
- A2. List each agent's toolset. Each must equal its approved subset from section 3.
- A3. Scan imports to confirm that neither `server.py` nor `backend/agents/*` imports `approval`.
- A4. Through the MCP connection, try `UPDATE pending_actions SET status='approved'` and `INSERT INTO payments …`. Both must be refused as "not authorized".
- A5. `INSERT` a `pending_actions` row with `status='approved'`. The trigger must reject it.
- A6. `execute()` on a row that is `proposed`, `rejected`, or already `executed` must be refused, with nothing changed.

---

## 14. Payment and audit consistency (revision 3)

### Options considered
| Option | How | Guarantee | Cost |
|---|---|---|---|
| **A. Attach `audit.db` in the same transaction** | `ATTACH 'data/audit.db' AS audit`, then a single `BEGIN IMMEDIATE … COMMIT` covering the payment rows and the audit event | SQLite commits multi-database transactions atomically through a "super-journal", **but only when** neither database uses WAL mode, the journal mode is not `OFF` or `MEMORY`, `synchronous` is not `OFF`, and the main database is not in memory | Few moving parts. Fails closed: if the audit write fails, the payment rolls back. |
| B. Transactional outbox | Write the audit event to an outbox table in the working database (single-database atomicity), then copy it to `audit.db` after commit; replay anything left on the next start | Strongest SQLite guarantee, and no multi-database conditions | More code: a flusher and a replay step. The reset must refuse to run while the outbox is unflushed. |
| C. Separate writes plus reconciliation | Payment commit, then a best-effort audit write, with a checker | Not atomic; gaps are detectable only | Simplest, but weakest |

### Recommendation: option A, with its conditions enforced and checked
- **Conditions enforced in code:** on every connection that writes money, the code sets `journal_mode=DELETE` and `synchronous=FULL` on both databases, then reads them back. If either is wrong, it refuses to run. WAL mode is never used.
- **Crash during a transaction:**
  - SQLite's rollback journal and super-journal undo *both* databases the next time they are opened.
  - The result is either the payment and its audit event together, or neither.
- **Audit write fails** (file missing, locked, or the hash-chain step errors):
  - The whole transaction rolls back, so no payment happens without its audit record. This is fail-closed by design.
  - The human sees the error and can retry.
- **Concurrency:** `BEGIN IMMEDIATE` plus a busy timeout. The hash-chain step reads the previous event inside the same write transaction. Whether `BEGIN IMMEDIATE` locks the attached database at the start or only at its first write is an implementation detail; it will be **tested** (test T4), not assumed.
- **Detection:**
  - `scripts/verify_audit.py` checks the hash chain.
  - It also checks that every `payments` row has exactly one matching `payment_executed` event, and the reverse.
- **Agent activity:** delegations and tool reads write no business data. They are logged by the agent runner in their own small audit transactions. If one of those writes fails, the run stops, so no agent activity goes unrecorded.

**Known risk: OneDrive.** `hw5/` is inside a OneDrive-synced folder. A sync client can copy or lock database and journal files partway through a write. That does not break SQLite's atomicity on the local disk, but it can produce conflict copies or a busy error. Mitigation for the prototype: avoid running operations while OneDrive is actively syncing `data/`. If it causes errors, revisit (D12).

**Offline tests:**
- T1. A normal execution: `payments`, `cash_accounts`, the invoice or lease, `pending_actions`, and the audit event all change together.
- T2. **Simulated crash:**
  - A subprocess starts the execution and calls `os._exit(1)` after the inserts but before `COMMIT`.
  - Reopen both databases. Nothing should have changed in either, and the hash chain should still be valid.
- T3. **Audit failure:** on a temporary copy, make `audit.db` read-only or remove its `events` table, then execute. An error must be raised, and the balance, invoice, and `payments` must be unchanged.
- T4. **Two executions racing:** both must not succeed when combined cash is short. The second must see the first's committed balance.
- T5. **WAL guard:** a connection with WAL enabled must be refused before any write.

---

## 15. Delegation enforcement (revision 3)

All three safeguards live in **our** Python code, in one shared context object and one `delegate` tool. They do not depend on the model's cooperation.

```python
AgentName = Literal["boss", "inventory", "accounting", "facilities", "customer_service"]

@dataclass(frozen=True)
class RunDeps:
    run_id: str
    chain: tuple[AgentName, ...]        # who is currently active, outermost first
    budget: "RunBudget"                 # ONE mutable object shared by every nested run
    audit: "AuditWriter"

async def delegate(ctx: RunContext[RunDeps], to: AgentName, task: str) -> str:
    chain = ctx.deps.chain
    if to in chain:                                   # cycle prevention
        ctx.deps.audit.log("delegation_refused", reason="cycle", chain=chain, to=to)
        return f"Refused: {to} is already working on this chain ({' → '.join(chain)}). Answer with what you have."
    if len(chain) >= ctx.deps.budget.max_depth + 1:   # depth backstop (hops = len(chain) - 1)
        ctx.deps.audit.log("delegation_refused", reason="depth", chain=chain, to=to)
        return "Refused: delegation depth limit reached. Answer with what you have."
    ctx.deps.budget.count_delegation()                # raises BudgetExceeded past max_delegations
    child = replace(ctx.deps, chain=chain + (to,))
    result = await AGENTS[to].run(task, deps=child, usage=ctx.usage,
                                  usage_limits=ctx.deps.budget.usage_limits)
    return result.output
```

- **Cycle prevention:**
  - Each child run receives `chain + (to,)`. Because the tuple is immutable, sibling calls can't contaminate each other's chains.
  - A refusal is an ordinary return value, so the calling agent continues normally.
  - `to` is typed as a `Literal`, so an unknown agent name fails validation before the tool runs.
- **Depth limit:** this is the same check on `len(chain)`. With five agents, cycle prevention already caps chains at 4 hops, so `max_depth = 4` never blocks a chain that cycle prevention allows.
- **Shared usage budget:** two independent mechanisms.
  1. **PydanticAI usage:**
     - The documented delegation pattern passes the parent's `ctx.usage` into the child run, so every nested run adds to *one* usage total.
     - `UsageLimits(request_limit=…, total_tokens_limit=…)` is checked against that total.
     - The exact behavior in the pinned version will be confirmed by test U1 rather than assumed.
  2. **Our own counter:** `RunBudget.max_delegations`, kept in the shared object, so the limit doesn't rely on library behavior alone.
- **When a limit is hit:**
  - The exception propagates to the top-level run.
  - The runner records `run_stopped` with the reason and marks the run as stopped.
  - Because agents can only propose, no money has moved.

**Offline tests (PydanticAI `FunctionModel`/`TestModel`, which make no network calls; labeled "simulated delegation"):**
- U1. **Shared budget:**
  - Script Boss → Inventory → Accounting, with each agent making 2 model requests, and set `request_limit=5`.
  - The run must stop with a usage-limit error, the top-level usage must show requests from all three agents, and the audit log must contain `run_stopped`.
  - Control: with `request_limit=10`, the same script completes.
- U2. **Delegation counter:**
  - With `max_delegations=2`, script Boss → Inventory, then Boss → Accounting, then Boss → Facilities.
  - The third call must raise `BudgetExceeded` and be logged.
- U3. **Cycle:**
  - Script Boss → Inventory → Accounting, then Accounting tries `delegate("inventory")`.
  - The tool must return the refusal text, Inventory's run count must not increase, the audit log must contain `delegation_refused: cycle`, and the run must still complete.
- U4. **Siblings allowed:** Boss → Inventory, then Boss → Inventory again, in sequence. Both must succeed. This proves the cycle rule doesn't block legitimate repeat consultations.
- U5. **Depth:**
  - With a test-only `max_depth=2`, Boss → Customer Service → Inventory, then Inventory tries Accounting. It must be refused for depth.
  - With the default `max_depth=4`, the full chain Boss → Customer Service → Inventory → Accounting → Facilities must succeed.
- U6. **Unknown agent:** `delegate("ceo", …)` must fail validation, and no run may start.

---

## 16. Portkey configuration (revision 5)

> **Stage 5 update:** implemented in `backend/config.py`.
> - Homework 3 and 4 (inspected with Dr. Ryan's permission, code only, no keys) used the same gateway, `https://api.portkey.ai/v1`. With the course key, they routed a **bare** model name (`gpt-5.6-luna`).
> - The key lives in the course root `.env` (`AGENTS.md`). `config.py` reads the process environment, then `hw5/.env`, then the course root `.env`.
> - **Resolved (2026-10-10):** a single authorized live request confirmed that the bare name `gpt-6-luna` routes through the course key. The provider reported `gpt-6-luna-2026-09-22`. No provider prefix or extra header is needed. See `output/p5_live_connectivity.json`.

**What the assignment specifies [A]:** `PORTKEY_API_KEY`, and the model `gpt-6-luna` only. It gives no gateway URL or headers. Those are technical configuration questions, resolved below from Portkey's own documentation (read 2026-10-09).

**What Portkey's documentation states:**
- **Source:** [Portkey: Pydantic AI integration](https://portkey.ai/docs/integrations/agents/pydantic-ai) and [Portkey: OpenAI-compatible projects](https://portkey.ai/docs/integrations/libraries/openai-compatible).
- **Gateway base URL:** `https://api.portkey.ai/v1`. It is OpenAI-compatible.
- **Pydantic AI pattern:**
  - A standard `AsyncOpenAI` client with `api_key` set to the Portkey key and `base_url` set to the gateway, wrapped in PydanticAI's `OpenAIProvider(openai_client=…)`.
  - That provider is passed to PydanticAI's OpenAI chat model class. The class name depends on the version and is pinned at install.
- **Model string:**
  - Portkey's documented format is `@provider-slug/model-name`.
  - A provider can also be selected with an `x-portkey-provider` header, or through a config attached to the key or sent as `x-portkey-config`.
- **Optional headers:** `x-portkey-trace-id` and `x-portkey-metadata`.
  - **[P]** Send the run's `run_id` as the trace ID, so Portkey's logs line up with `audit_trail.json`.

**Still unresolved (not guessed):**
- Whether the course's Portkey key routes the bare name `gpt-6-luna`, through a default provider or config attached to the key, or needs a workspace-specific `@slug/gpt-6-luna`.
- The slug is workspace-specific, so documentation can't settle it.

**How it will be resolved:**
- `backend/config.py` reads three settings:
  - `PORTKEY_API_KEY`, which is required, never printed, and never logged
  - `PORTKEY_BASE_URL`, defaulting to `https://api.portkey.ai/v1`
  - `PORTKEY_MODEL`, defaulting to `gpt-6-luna`
- **Validation:** `PORTKEY_MODEL` must be exactly `gpt-6-luna` or `@<slug>/gpt-6-luna`. Any other model is rejected at startup, so no other model can be substituted by configuration.
- **At the authorized connectivity check only (D10):**
  1. A model-listing request to the gateway (no text generation; whether Portkey's `/models` endpoint supports this key is to be confirmed).
  2. If that does not settle the routing, one minimal completion with the bare `gpt-6-luna`.
  3. If routing fails, Dr. Ryan can read the slug from the Portkey dashboard (Model Catalog). A slug is not a credential.
- **Never requested:** the key itself. Dr. Ryan puts it in `hw5/.env`, and Claude checks only that the variable exists.

**Offline tests (no network):**
- K1. The configuration loads from a temporary `.env`. The key never appears in `repr`, logs, or `audit_trail.json`.
- K2. `PORTKEY_MODEL=gpt-4o` (or any non-`gpt-6-luna` value) is rejected.
- K3. All five agents are built on the same configured model object.
- K4. A missing `PORTKEY_API_KEY` gives a clear startup error, but only on the live path. Simulated tests don't need it.

---

## 17. Decision register (revision 5)

### Decisions
| ID | Decision | Recommendation (rev. 5) | Status | § |
|---|---|---|---|---|
| D1 | Agent responsibilities and tool subsets | As in §3 | Approved in principle | 3 |
| D2 | Delegation safeguards | Cycle prevention; `max_depth=4`; shared `request_limit` and `max_delegations` | Approved in principle | 15 |
| D3 | Approval path | **Re-scoped:** nothing that approves or executes is built in Problem 5. The human approval routes come in Problem 7 [A], with the separation layers of §13. The typed name is attribution only. | Needs confirmation | 13, 18.5, 19.5 |
| D4 | Reserve cash for approved commitments (stricter than R1) | Yes. Built in Problem 7 with approval. | Approved in principle | 5 |
| D5 | Extension tables and reset | `pending_actions` and `drafts` | Approved in principle | 8, 18.1 |
| D6 | R4 values and `payments.kind` | `paid`; `next_due` +1 month; `invoice` / `lease`. Proposed policy, used in Problem 7. | Approved as a proposed policy | 9, 18.4 |
| D7 | Purchase stages | **Re-scoped:** Problem 5 creates `proposed` only. Approve and reject come in Problem 7. | Needs confirmation | 6 |
| D8 | Audit storage | **Revised:** `output/audit_trail.json` is the required record and is committed. `audit.db` is supplemental and git-ignored. | Needs confirmation | 19.1–19.2 |
| D9 | Discount floor and ticket closure | Quote ≥ unit cost; closure needs the human's confirmation | Approved as a proposed policy | 9, 18.4 |
| D10 | Live calls | None until authorized. Then one connectivity check, with routing resolved as in §16. | Approved in principle | 16 |
| D11 | Consistency between data and `audit.db` | Attached database, rollback journal, fail-closed. In Problem 5 this covers proposal and draft writes; payment execution comes in Problem 7. | Approved in principle (re-scoped) | 14, 18.2 |
| D12 | OneDrive | **Revised:** the default is `hw5/data/` and needs no configuration. The unsynced runtime folder is an *optional* override, with a snapshot back to `data/`. | Needs confirmation | 19.3 |
| D13 | Runtime path (outside `hw5/`) | `%LOCALAPPDATA%\CampusCustomsHW5\`, set by `CC_RUNTIME_DIR`. Used only on Dr. Ryan's machine. | Needs approval | 19.3 |
| D14 | Changing the approved Problem 4 files | `server.py` resolves paths through `paths.py` and the default is unchanged, so `.mcp.json` needs **no** change. The tools are re-checked live, with evidence in a new file. | Needs approval | 19.3 |
| D15 | `audit_trail.json` format and failure policy | One JSON array of hash-chained events; atomic replace; fail-closed | Needs approval | 19.1 |
| D16 | Problem 5 MCP tool set | 10 tools: 3 existing, `get_ticket`, `list_vendors`, `list_pending_actions`, `propose_payment`, `propose_purchase`, `propose_quote`, `draft_message` | Needs approval | 19.4 |
| D17 | System events (reset, snapshot) in the JSON trail | Yes, with actor `system` | Needs approval | 19.1 |

### Information needed (not decisions)
- **Q1–Q5:** closed in section 1.
- **Portkey:** documented in section 16. Only the routing of `gpt-6-luna` remains, and it is settled at the authorized connectivity check.

### Ordinary technical choices (Claude decides, and documents them)
- File layout and module names
- How PydanticAI filters each agent's tools
- Test framework
- Version pins
- Rounding to cents
- The wording of error messages and agent prompts, within the approved responsibilities
- Exact busy-timeout values

---

## 18. Revision 4: Dr. Ryan's qualifications

### 18.1 Runtime folder outside OneDrive (revises D12; needs D13 and D14)
| File | Location | Role |
|---|---|---|
| `campus_customs.db` | `hw5/data/` (unchanged, read-only) | The reset source. Never opened for writing. |
| `campus_customs_new.db` | `<runtime>/` | The active working copy |
| `audit.db` | `<runtime>/` | The audit trail. Survives resets. |
| Per-run evidence exports | `hw5/output/` | Committed evidence. They are written after a run, never while the databases are active. |

- **Where `<runtime>` is:**
  - The `CC_RUNTIME_DIR` setting in `.env`, which is never committed.
  - Proposed value on this machine: `%LOCALAPPDATA%\CampusCustomsHW5\` (D13).
  - If `CC_RUNTIME_DIR` is unset, it defaults to `hw5/data/`, the assignment's layout, so the project still reproduces on Professor Zaman's machine.
- **Single source of the path:**
  - A small `paths.py` module resolves the location.
  - `server.py`, the approval script, the reset script, and the agents all use it.
  - `.mcp.json` keeps the same launch command, and the server reads `.env` itself (D14).
- **Reset (R8), reproducible:**
  1. Create `<runtime>` if it is missing.
  2. Copy `hw5/data/campus_customs.db` → `<runtime>/campus_customs_new.db`.
  3. Check that its SHA-256 equals the original's (`23686a90…f360a`).
  4. Apply `schema_ext.sql`.
  5. Check that the nine original tables are unchanged.
  6. Never touch `audit.db`.
  7. Record a `reset` event in the audit log.
- **Hash check:** the original's SHA-256 is checked before and after every reset, to confirm it never changes.
- **Effect on the Problem 4 evidence:** `mcp_smoke.json` stays as recorded. It describes the configuration at that time, with the working copy in `hw5/data/`. After D14, the MCP tools are re-checked live, and the results go into a new evidence file, not into `mcp_smoke.json`.

### 18.2 The attached-database transaction: settings and limits of the guarantee
- **Required settings, set and read back on every connection that writes money (otherwise it refuses to run):**
  - `journal_mode=DELETE` on both `main` and `audit`
  - `synchronous=FULL`
  - `foreign_keys=ON`
  - Both files on a local disk, in `<runtime>`
  - `busy_timeout` set
- **What SQLite guarantees under these settings:** the payment rows and the audit event commit together or not at all, including after a crash or power loss. On the next open, the hot journals and the super-journal roll back any incomplete transaction in *both* databases.
- **What it does not guarantee:**
  - Hardware or operating-system failures where a disk falsely reports that data is saved.
  - Any WAL, `MEMORY`, or `OFF` journal mode. The code refuses these.
  - Someone copying, moving, or deleting a database **or its journal files** after a crash and before the next open. A journal left behind must stay next to its database.
  - Changes made by other programs outside our code.
  - Durability across a copy of `<runtime>` taken while a transaction is open.
- **Failure policy:** if the audit write fails, the financial transaction rolls back (fail-closed). Tests T1–T5 (§14) run on temporary databases.

### 18.3 MCP database boundary: indirect modifications
The authorizer works from an **allowlist**: anything not explicitly permitted is denied.
- **Permitted:** reads; `INSERT` into `pending_actions`, `drafts`, and `audit.events`; transaction control; and a short list of read-only functions.
- **Order matters:** the MCP connection attaches `audit.db` *before* the authorizer is installed. After that, `ATTACH` is denied.
- **Code-level settings:**
  - Extension loading stays disabled.
  - No tool accepts SQL text. Every tool runs fixed, parameterized statements.

**Offline tests**, each run on its own temporary copy of the database, through the MCP connection:

| # | Attempt | Expected |
|---|---|---|
| B1 | Ordinary `UPDATE` or `DELETE` on any table; `INSERT` into `payments`, `cash_accounts`, `invoices`, `leases`, `inventory`, or `tickets` | Denied |
| B2 | **Trigger route:** a hostile trigger (planted in the temporary database by the test) on `pending_actions` INSERT that updates `cash_accounts`; then a normal proposal insert | Denied as a whole; cash unchanged |
| B3 | `CREATE` / `DROP` / `ALTER` of a table, trigger, view, or index | Denied |
| B4 | `ATTACH` of the original or any other file; `DETACH audit` | Denied |
| B5 | Writable PRAGMAs: `journal_mode`, `foreign_keys=OFF`, `writable_schema`, `synchronous` | Denied |
| B6 | `INSERT OR REPLACE` / `ON CONFLICT … DO UPDATE` on an existing `pending_actions` id | The existing row is unchanged. If the authorizer does not catch the implicit delete, the fallback is an allowlist of exact statements, and the test must pass before continuing. |
| B7 | `VACUUM` / `VACUUM INTO` another file | Denied |
| B8 | `load_extension` | Unavailable / denied |
| B9 | `UPDATE` / `DELETE` on `audit.events` | Denied (by the authorizer and by triggers) |
| B10 | A proposal insert with `status='approved'` | Rejected by trigger |
| B11 | A control case: a legitimate `propose_payment` | Succeeds, and only `pending_actions` and `audit.events` change |

### 18.4 Policies kept separate from requirements
- Proposed policies live in **`policies.py`**, with one labeled constant each, marked `# PROPOSED POLICY, approved by Dr. Ryan 2026-10-09; NOT an assignment requirement`:
  - The discount floor (≥ unit cost)
  - Ticket closure (needs the human's confirmation)
  - The invoice status `paid`
  - `next_due` +1 month
  - `payments.kind` values
  - Reserving cash for approved commitments
- Assignment rules R1–R8 are enforced in `ops.py` and cite their rule numbers.
- Agent prompts and outputs must describe policies as "shop policy (proposed)", never as assignment rules.

### 18.5 The approval script is a local prototype
- `scripts/actions.py` prints this notice on every run: *"LOCAL PROTOTYPE: not an authenticated approval system. The name entered is recorded for attribution only."*
- The same wording goes in `README.md`.
- A real system would need authenticated identities, separation of duties, and tamper-evident signing. These are out of scope here, and the documentation will say so.

### 18.6 Live calls, real transactions, and test isolation
- **No live `gpt-6-luna` calls** without Dr. Ryan's separate authorization (D10).
- **No execution against the runtime working copy** without Dr. Ryan's separate authorization, even through the approval script.
- **All offline tests use isolated temporary databases:**
  - A test fixture creates a fresh temporary folder, copies the original into it, and points `CC_RUNTIME_DIR` there.
  - A guard fails any test whose database path is not inside that temporary folder.
  - The original's hash is checked before and after the test suite.

### 18.7 Minimum source material still needed (no credentials)
1. **The Problem 5 section of the assignment, word for word.** A paste or screenshot is fine. This settles Q1–Q5: required tools, write scope, live-run expectations, audit timing, and deliverables.
2. **The assignment's model and Portkey setup text, word for word.** This settles P1–P4 and P6: the sentence naming `gpt-6-luna`, the base URL if given, the exact model string, any extra header, virtual key, config ID, or environment variable, and any usage limits.
3. **Optional:** the name of any course sample file that calls Portkey (P5). I read it only after you approve that specific file.

Not needed and not requested: the API key itself. When the time comes, Dr. Ryan adds `PORTKEY_API_KEY` to `hw5/.env` personally. Claude checks only that the variable exists, never its value.

*(Revision 5: items 1–2 were settled by Dr. Ryan's assignment comparison and by Portkey's documentation; see sections 1 and 16. Item 3 is no longer needed.)*

---

## 19. Revision 5: assignment corrections

### 19.1 Required agent audit trail: `output/audit_trail.json` [A]
- **Format (D15):**
  - One JSON array of event objects. It is human-readable, parses as standard JSON, and is committed to Git.
  - Previous runs are never removed or rewritten. Only new events are added at the end.
- **Fields in every event:**

| Field | Meaning |
|---|---|
| `event_id` | Unique ID (UUID) |
| `run_id` | Which run the event belongs to |
| `seq` | Position in the whole file |
| `run_seq` | Position within this run |
| `ts_utc` | Wall-clock time (labeled as such) |
| `business_date` | `desk.date_today` |
| `mode` | `live` or `simulated` |
| `actor` | The agent name, or `system` |
| `chain` | The active delegation chain |
| `event_type` | See the table below |
| `ticket_id` | The ticket concerned, if any |
| `tool` | The MCP tool called, if any |
| `args` | The tool arguments. No secrets ever appear here. |
| `outcome` | `ok`, `refused`, or `error`, with a message |
| `result_summary` | A short summary, plus the `result_sha256` of the full tool output |
| `links` | `pending_action_id`, `draft_id`, `audit_db_event_id` |
| `usage` | Requests and tokens used so far in the run |
| `prev_hash`, `hash` | The hash chain |

- **Event types:**

| Group | Events |
|---|---|
| Run | `run_started` (model ID, database location and SHA-256, limits), `run_finished` (`completed` / `stopped` / `error`, with totals) |
| Agent | `agent_started`, `agent_finished` (summary of the output) |
| Delegation | `delegation_requested`, `delegation_completed`, `delegation_refused` (reason: cycle, depth, budget) |
| MCP tool | `tool_call_started`, `tool_call_finished` |
| Limits | `budget_exceeded` |
| System (D17) | `db_reset`, `db_snapshot` |

- **How events are captured:**
  - The `delegate` tool logs delegations.
  - A wrapper on each agent's MCP toolset logs every tool call before and after. PydanticAI offers a hook for this on MCP toolsets; the exact API is confirmed against the pinned version.
  - The runner logs the start and end of runs.
- **How appending works:**
  - The runner process is the only writer, and it takes a lock file.
  - **Step 1:** read the file and verify that it parses and that its hash chain is intact.
  - **Step 2:** write `old events + new event` to a temporary file in `output/`, flush it to disk, then atomically replace the original.
  - **Step 3:** if the existing file fails verification, the writer refuses, leaves the file untouched, and the run stops.
- **Failure policy (fail-closed):**
  - If `tool_call_started` can't be written, the tool is **not** called and the run stops.
  - If a later event can't be written, the run stops, and the reconciliation in 19.2 reports any gap.
- **Simulated versus live:**
  - Offline tests write only to temporary files and never touch `output/audit_trail.json`.
  - Any demonstration run written to the real file carries `mode` = `simulated` or `live`.
- **Limits:**
  - Anyone with file access can edit the file. The hash chain makes edits *detectable*, not impossible.
  - OneDrive may sync the file. The atomic replace means it never holds a half-written state, but a conflict copy is still possible if two machines edit it.

### 19.2 How `audit.db` relates to `audit_trail.json` (supplemental safeguard)
| | `output/audit_trail.json` | `<data dir>/audit.db` |
|---|---|---|
| Status | **Required [A]** | **Supplemental [P]**. It does not replace the JSON file. |
| Records | Every agent-loop event (and system events) | Only changes to the working database: proposals and drafts (Problem 5); approvals and executions (Problem 7); resets |
| Consistency | Fail-closed append | Committed in the **same transaction** as the data change (§14, §18.2) |
| Survives resets | Yes | Yes |
| In Git | Yes | No (git-ignored, rebuildable) |

- **Linking the two:**
  - Each MCP tool that changes data returns `pending_action_id` or `draft_id`, plus `audit_db_event_id`. The runner records these in the `tool_call_finished` event.
  - The `run_id` and agent name reach the MCP server through the toolset wrapper. The model never supplies them.
- **Reconciliation:** `scripts/verify_audit.py` checks four things and only *reports* mismatches; it never repairs anything:
  1. The JSON hash chain
  2. The `audit.db` hash chain
  3. That every `audit.db` event with a `run_id` has a matching JSON `tool_call_finished` event
  4. The same in reverse

### 19.3 Database paths: assignment layout and optional runtime folder (revises 18.1)
- **Default (no configuration) [A]:**
  - The databases are `data/campus_customs.db` (the original) and `data/campus_customs_new.db` (the working copy), exactly as the assignment specifies. `audit.db` is also in `data/`.
  - A fresh clone of the repository works with **no** machine-specific setting. `.mcp.json` is unchanged.
- **Optional override (D12, D13):**
  - Setting `CC_RUNTIME_DIR` in `.env`, on Dr. Ryan's machine only, moves the *active* working copy and `audit.db` to an unsynced local folder.
  - In that mode, the runtime copy is authoritative, and `data/campus_customs_new.db` is a **snapshot**.
- **Snapshot:**
  - `scripts/snapshot_runtime.py` uses SQLite's online backup API to copy the runtime working copy into `data/campus_customs_new.db`. This gives a consistent copy even while the source is open.
  - It runs only when no run is active, and it records a `db_snapshot` event with the SHA-256.
  - The repository therefore always contains both required database files, showing the final state.
- **One path resolver:**
  - `mcp_server/paths.py` is the only place paths are worked out. The MCP server, backend, reset, snapshot, and verify scripts all use it.
  - Every `run_started` event records which location was active (`default` or `runtime`).
- **Writes to the original are refused:** `paths.py` will never return the original's path for writing.
- **Reset:** the same steps as 18.1, against whichever location is active. It never touches `audit.db` or `audit_trail.json`.
- **MCP as the agents' only data route [A]:**
  - Agent code under `backend/` never opens SQLite; a test checks this.
  - The human approval routes in Problem 7 are not agent code, so they may use `ops.py` directly.

### 19.4 Files (Problem 5)
```
backend/
  config.py          Portkey/gpt-6-luna configuration (§16)
  models.py          Pydantic data types [A]
  agents.py          the five agents [A]
  delegation.py      delegate tool, RunDeps, RunBudget (§15)
  audit_trail.py     append-only writer and verifier for output/audit_trail.json [A]
  runner.py          starts a run (simulated or live) and wires the MCP toolsets
  prompts/boss.md, inventory.md, accounting.md, facilities.md, customer_service.md   [A]
mcp_server/
  server.py          expanded tools (D16); uses paths.py and authz.py
  ops.py             rule checks (R1, R3, R5, R7) and proposal/draft writes
  policies.py        proposed policies, labeled (§18.4)
  authz.py           SQLite authorizer allowlist (§18.3)
  paths.py           single path resolver (§19.3)
  schema_ext.sql     pending_actions, drafts
  verify_tools.py    extended offline checks
  README.md          updated [A]
scripts/  reset_db.py, snapshot_runtime.py, verify_audit.py
tests/    offline tests (§19.6)
output/   audit_trail.json [A], harness.md (expanded) [A]
.env.example         PORTKEY_API_KEY=, PORTKEY_BASE_URL=, PORTKEY_MODEL=gpt-6-luna, CC_RUNTIME_DIR=   (placeholders only)
requirements.txt     + pydantic-ai (pinned, with OpenAI and MCP support), pytest
```

### 19.5 Built in Problem 5 versus deferred
| Built in Problem 5 | Deferred to Problem 7 [A: approval routes] |
|---|---|
| Five agents, delegation, prompts, models, config | `approve` / `reject` / `execute` |
| Read, propose, and draft MCP tools; authorizer; extension tables | Atomic payment execution, and the R1 check at write time |
| `audit_trail.json`; `audit.db` for proposals and drafts | Reserving cash for approved commitments (D4) |
| Reset, snapshot, and path resolver | Approval tests A6 and T4 (racing payments) |

### 19.6 Offline test plan (all on isolated temporary databases and files; no model or network calls)
| Group | Tests | Covers |
|---|---|---|
| **J: JSON audit trail** | J1 a new file is created as a valid array; J2 a second run appends and the earlier events stay byte-identical; J3 a new process appends and the earlier runs are preserved; J4 a crash between the temporary write and the replace leaves the original intact and valid; J5 a tampered file is refused and left untouched, and the run stops; J6 an unwritable file means the tool is not called, no proposal is created, and the run stops; J7 the verifier detects an edited or deleted event; J8 `run_id` and `seq` are monotonic and the `mode` label is correct | Append persistence, audit failures |
| **T: Data plus `audit.db`** | T1 a proposal and its `audit.db` event commit together; T2 a simulated crash before commit leaves neither; T3 an `audit.db` write failure rolls back the proposal; T5 a WAL connection is refused | Audit failures (§14, §18.2) |
| **S: Paths and reset** | S1 with no environment setting, everything resolves to `hw5/data/`; S2 with `CC_RUNTIME_DIR` set, the server, backend, reset, snapshot, and verify all resolve the same paths; S3 a reset gives working = original hash before extension, leaves the original unchanged, and leaves `audit.db` and `audit_trail.json` untouched; S4 a snapshot produces a valid database and records its hash; S5 any attempt to open the original for writing is refused | Reset, path consistency |
| **B: MCP boundary** | B1–B11 (§18.3) | Authorization, including indirect routes |
| **A: Approval separation** | A1 no approve or execute tool exists; A2 each agent has exactly its approved toolset; A3 a scan finds no approval imports in agent code; A4 the authorizer blocks it; A5 the trigger blocks pre-approved inserts; plus a scan that agent code under `backend/` contains no `sqlite3` | Approval integrity, MCP-only data access |
| **U: Delegation** | U1–U6 (§15), labeled **simulated delegation** | Shared budget, cycles, depth |
| **K: Configuration** | K1–K4 (§16) | `gpt-6-luna` only; key never exposed |
| **R: Reconciliation** | R1 `verify_audit.py` passes on consistent data and reports a planted gap in either direction | JSON ↔ `audit.db` |

A **live** test is never part of this suite. It happens only as the separately authorized connectivity check (D10).

### 19.7 Implementation sequence (revision 5; replaces §11)
1. `paths.py`, `schema_ext.sql`, `reset_db.py`, `snapshot_runtime.py`. Run the S tests.
2. `authz.py`, `ops.py`, `policies.py`, `audit.db` writes. Run the T and B tests.
3. The expanded MCP tools (D16). Update `verify_tools.py`. Run the A tests. Re-check the tools live through Claude Code, with evidence in a new file (D14).
4. `audit_trail.py`. Run the J tests.
5. `models.py`, `delegation.py`, `agents.py`, prompts, `runner.py`, `config.py`. Run the U and K tests (simulated).
6. `verify_audit.py`. Run the R tests.
7. Documentation: `harness.md` (expanded), `mcp_server/README.md`, `README.md`, `.env.example`, `requirements.txt`.
8. **Stop.** Wait for Dr. Ryan's separate authorization before any live connectivity check.
