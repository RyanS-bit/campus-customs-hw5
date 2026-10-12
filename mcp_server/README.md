# Campus Customs MCP Server

## Purpose

This server is the agents' only path to the Campus Customs database. The agents call clearly defined tools instead of querying the database themselves. That keeps every answer tied to actual records and makes each data access visible and testable.

## Database

- **Uses:** `data/campus_customs_new.db` (the working copy). The path is fixed in `server.py` (`DB_PATH`).
- **Never opens:** `data/campus_customs.db` (the read-only original).
- **Read-only:** every connection uses SQLite's read-only mode (`?mode=ro`). The tools cannot change data, even by mistake.
- **"Today":** the shop's current date is `desk.date_today` (2026-08-31), not the real calendar date (rule R5).
- **Missing working copy:** if the working copy is missing, the tools return an error asking for a reset from the original.

## Tools (Problem 3: read-only)

| Tool | Input | Tables read | Returns | Helps with |
|---|---|---|---|---|
| `check_inventory` | `sku` (optional) | `inventory`, `pricing` | Per product: name, aisle, unit cost, list price, quantity per size, total. An unknown SKU returns an error listing the valid SKUs. | 101 (tee S stock), 103 (hoodie M stock and margin) |
| `get_financial_position` | none | `desk`, `cash_accounts`, `invoices`, `vendors`, `leases`, `payments` | Cash per account; open invoices with vendor and days overdue; leases with days until due; vendors blocked by an open invoice (rule R3); count and total of payments recorded; and a `computed` block (total cash, total obligations, and the *hypothetical* balance if all were paid; not the current balance). | 101 (invoice #501), 102 (rent), 103 (can a reprint be funded?) |
| `get_open_tickets` | none | `tickets`, `invoices`, `vendors`, `leases` | Each open ticket with all its fields, plus the full linked invoice (with vendor) or lease | All three tickets |

Every tool is annotated `read_only_hint: true`, `destructive_hint: false`. Values in a `computed` block are arithmetic on database values, not stored facts.

**Why three tools rather than one or many:** each tool answers one question an agent will ask. "Do we have it?" (Inventory, Customer Service). "Can we afford it, and who are we blocked by?" (Accounting, Facilities). "What work is waiting, and which records is it about?" (Boss). Narrow tools are easier to test and easier for a model to choose correctly.

## Problem 5 tools and launch identity (Stage 3)

The server now has **ten tools**. The original three above are unchanged, and their outputs and error text match `output/mcp_smoke.json` exactly. Full specification: `output/p5_mcp_tools_spec.md`.

| Tool | Type | Agents |
|---|---|---|
| `get_ticket(ticket_id)` | Read | all five |
| `list_vendors()` | Read | Boss, Inventory, Accounting |
| `list_pending_actions(ticket_id?, kind?)` | Read | all five |
| `propose_payment(kind, ref_id, amount, rationale, ticket_id?, account?)` | Proposal | Accounting |
| `propose_purchase(vendor_id, sku, size, qty, rationale, ticket_id?)` | Proposal | Inventory |
| `propose_quote(ticket_id, sku, size, qty, unit_price, rationale)` | Proposal | Customer Service |
| `draft_message(ticket_id, subject, body, related_action_id?)` | Unsent draft | Customer Service (to customers), Facilities (to the landlord) |

**Identity is fixed when the process starts, from environment variables set by the launcher:**
- **None set:** development mode. This is how Claude Code starts it from `.mcp.json`. All tools are listed and the read tools work, but every write tool refuses with `NO_RUN_CONTEXT`.
- **All of `CC_RUN_ID` (a UUID4), `CC_AGENT`, and `CC_RUN_MODE` (`simulated`/`live`) set:** agent mode. Only that agent's tools are registered, and writes are attributed to that agent and run.
  - **The launcher must also state the database** (remediation M1): `CC_HW5_ROOT`, `CC_RUNTIME_DIR` (may be empty), and `CC_WORKING_DB`.
  - The server resolves paths from these alone, never from `hw5/.env` or defaults. It refuses to start if any is missing or if they do not reproduce `CC_WORKING_DB` exactly.
- **Anything else:** the server refuses to start.

**What callers cannot change:**
- A caller's `_meta` (agent, run ID) is ignored for authority. Only an optional `call_id` is stored, for correlation.
- Unknown arguments such as `agent` or `run_id` are rejected by the tool schema.

**Errors from new tools start with a stable code:**
- `INVALID_INPUT`, `NOT_FOUND`, `REFUSED_RULE`, `DUPLICATE`
- `NOT_PERMITTED`, `NO_RUN_CONTEXT`
- `DB_UNAVAILABLE`, `DB_DENIED`, `AUDIT_FAILED`

**Unprepared database:** `get_ticket` and `list_pending_actions` need the Problem 5 tables. On a working copy that hasn't been reset, they return `DB_UNAVAILABLE`.

## Problem 5 write layer (Stages 1–2)

These modules sit behind the write tools. Every write goes through `ops.py` on the restricted connection.

| Module | Role |
|---|---|
| `paths.py` | The single path resolver. Uses `data/` by default, or `CC_RUNTIME_DIR` if set. Refuses any write to the original. |
| `schema_ext.sql` | Adds `pending_actions` and `drafts` to the working copy. Triggers force `status='proposed'` on insert and forbid deletes. Unique indexes block duplicate open proposals. |
| `maintenance.py` | Reset (R8) and runtime snapshot |
| `ops.py` | The only code that writes agent work. `propose_payment`, `propose_purchase`, `propose_quote`, and `draft_message`. Each writes one row and one `audit.db` event in a single transaction. |
| `authz.py` | Allowlist SQLite authorizer on the restricted connection. Permits only reads, transaction control, inserts into `pending_actions`, `drafts`, and `audit.events`, and a short list of functions. |
| `audit_db.py`, `audit_schema.sql` | Supplementary hash-chained `audit.db`, with triggers that keep it append-only. Not the required `output/audit_trail.json` (Stage 4). |
| `policies.py` | Proposed shop policies, each labeled as **not** an assignment requirement |
| `audit_trail.py` | *(Stage 4)* Writer for the required `output/audit_trail.json`, plus `RunRecorder` for run, tool-call, and delegation events. It uses no database, is never imported by `server.py`, and is not an MCP tool. |
| `audit_reconcile.py` | *(Stage 4)* Cross-checks the JSON trail, `audit.db`, and rows; records gaps as `incomplete_operation_reconciled` events (never "rolled back") |
| `locks.py` | *(Stage 4)* OS file locks: the trail lock serializes writers, and the run lock makes reset and snapshot refuse during a run. A crashed holder's lock is released by the OS. **Stage 5 fix:** the lock file is never written to. The earlier version wrote a byte into a new, empty lock file, and on Windows (where byte-range locks are mandatory) that write failed with "Permission denied" whenever a competing process had already locked that byte. That was the intermittent concurrency failure. Locking a byte past the end of an empty file is allowed, so no write is needed. Regression test: `test_j15`. |

**Restricted connection settings:**
- `journal_mode=DELETE` and `synchronous=FULL` on both databases
- `foreign_keys=ON`
- `recursive_triggers=ON`, which makes the no-delete triggers also stop the hidden deletes of `INSERT OR REPLACE`. The authorizer cannot see those deletes.
- Extension loading off

These are read back after being set, and the connection is refused if any is wrong.

## Setup and run (from `hw5/`)

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python mcp_server/server.py
```

The server communicates over stdio. It is meant to be launched by an MCP host (Problem 4), not used interactively.

## Verification (offline, no model calls)

```bash
.venv\Scripts\python mcp_server/verify_tools.py
```

This runs 45 checks in two passes (since Problem 9, follow-up 42; the earlier version ran 21 checks and assumed invoice 501 was unpaid):
- **Current:** the real working copy, read-only, in whatever state the simulation has reached.
  - Every tool's output is compared against independent SQL queries.
  - Invoice 501's stored facts are checked: $840.00, due 2026-08-28, Bulldog Print Co.
  - Its status must agree with the records:
    - **open:** listed with correct overdue days, no payment, vendor blocked (R3);
    - **paid:** exactly one $840.00 payment, not listed, vendor no longer blocked.
  - Ticket links are checked with `get_ticket`, which works for any status.
- **Baseline:** a fresh copy of the read-only original, prepared in a temporary folder by the existing reset routine (never the real working copy). The original facts are asserted exactly: $3,400; invoice 501 open and 3 days overdue; Bulldog blocked; $160 after all obligations; tickets 101–103 open.
- **Also checked:** an unknown SKU is rejected; the server runs as a real stdio subprocess; a write attempt is refused; neither real database's SHA-256 hash changes.

**Expected result:** `45/45 checks passed`. `Error calling tool 'check_inventory'` log lines are expected; they come from the deliberate unknown-SKU test (once per pass).

**Tests:** `tests/test_problem9_verify_tools.py` runs the checks on temporary copies. Both the open and the paid states pass. These inconsistencies are caught:
- a duplicate payment;
- an invoice marked paid without a payment;
- a changed amount;
- an unexpected status.
