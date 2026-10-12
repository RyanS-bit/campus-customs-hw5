# Controlled reset of the real working database (Problem 5, Stage 3)

**Authorized by:** Dr. Ryan, Problem 5 follow-up 10. The scope is Stage 3 completion only.
**Performed:** 2026-10-09, about 21:44 UTC, by Claude Code, from `hw5/`.
**Target:** `data/campus_customs_new.db` only. The original `data/campus_customs.db` was read, never written.

## Prerequisites (all passed before the reset)
| Check | Command | Result |
|---|---|---|
| Original fingerprint | `sha256sum data/campus_customs.db` | `23686a90d698f7fa6f901a3f0a9774db9cbb31e13151c9e5717275f3321f360a`, the expected value |
| No open transaction | `ls -la data/` | No `-journal`, `-wal`, or `-shm` files |
| No runtime override | `echo $CC_RUNTIME_DIR`; `ls .env` | Unset; no `.env`, so the target is `data/` |
| No lock holder | `maintenance._ensure_not_in_use(...)` (takes and releases an exclusive lock, writes nothing) | No other connection held a lock |
| No agent run or MCP write session | Process list (`Get-CimInstance Win32_Process`) | No agent, runner, or backend for hw5. Two development-mode `mcp_server/server.py` processes (Claude Code's own, running pre-Stage-3 read-only code) open the database only during a call. An unrelated hw4 backend uses a different database. |
| Recoverable snapshot | `cp -p data/campus_customs_new.db data/backups/campus_customs_new.pre_stage3_reset.20261009T214359Z.db`, then `chmod a-w` | Snapshot SHA-256 `23686a90…f360a`, identical to the working copy (git-ignored) |
| Target confirmation | `.venv/Scripts/python scripts/reset_db.py` (dry run, exit 2, nothing changed) | `mode: default`, `working_db: …\hw5\data\campus_customs_new.db` |

## Reset
`.venv/Scripts/python scripts/reset_db.py --confirm` exited with code 0.

| Fingerprint | Value |
|---|---|
| Working copy **before** the reset | `23686a90d698f7fa6f901a3f0a9774db9cbb31e13151c9e5717275f3321f360a` |
| Working copy after copying the original, before extension | `23686a90d698f7fa6f901a3f0a9774db9cbb31e13151c9e5717275f3321f360a` |
| Working copy **after** the reset (with the Problem 5 tables) | `dbaf24cb083ec7b3f41420b18f59026eaafbaa511f233c2ed181557076228ede` |
| Original, after the reset | `23686a90d698f7fa6f901a3f0a9774db9cbb31e13151c9e5717275f3321f360a` (byte-identical to the pre-reset snapshot, checked with `cmp`) |

The working copy's fingerprint changes **by design**: tables were added. Correctness is shown by the content checks below, not by the file hash.

## Verification after the reset
- **Extension objects:**
  - Tables `pending_actions`, `drafts`, and `schema_ext_meta` (version 2)
  - Triggers `pending_actions_insert_as_proposed`, `pending_actions_no_delete`, and `drafts_no_delete`
  - Unique indexes `ux_pending_payment_invoice`, `ux_pending_payment_lease`, `ux_pending_purchase`, and `ux_pending_quote`, plus two lookup indexes
- `PRAGMA integrity_check` = `ok`; journal mode `delete`.
- The nine original tables are content-identical to the original (digest `1308b65a…`).
- **Business records:**
  - Business date 2026-08-31; checking balance $3,400
  - Invoice 501: vendor 1, $840, due 2026-08-28, open
  - Lease 1: Elm City Properties, $2,400, next due 2026-09-02
  - 0 payments; tickets 101, 102, and 103 all open
  - All 10 inventory rows unchanged (for example, tee S = 0 and hoodie M = 8)
  - 0 proposals and 0 drafts
- `audit.db` was not created or touched by the reset.

## Recovery
To restore the pre-reset state, copy the snapshot back over `data/campus_customs_new.db` while nothing is using it. That state is also exactly the original, so `scripts/reset_db.py --confirm` followed by removing the extension tables would be equivalent. The snapshot is the simpler route.
