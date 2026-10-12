-- Campus Customs schema extension (Problem 5, Stage 1).
-- Applied to the WORKING copy after every reset (maintenance.reset_working_db).
-- Adds tables only; the nine original tables are never altered.
-- Spec: output/p5_mcp_tools_spec.md sections 1.6 and 7; architecture section 8.

BEGIN;

CREATE TABLE schema_ext_meta (
    version     INTEGER NOT NULL,
    description TEXT    NOT NULL
);
INSERT INTO schema_ext_meta VALUES (2, 'pending_actions + drafts; no-delete triggers (Problem 5, Stage 2)');

-- Agent-prepared proposals. Agents may only INSERT rows with status 'proposed';
-- approval, rejection and execution are human-only (Problem 7).
CREATE TABLE pending_actions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                 TEXT    NOT NULL CHECK (kind IN ('payment', 'purchase', 'quote')),
    status               TEXT    NOT NULL DEFAULT 'proposed'
                                 CHECK (status IN ('proposed', 'approved', 'rejected', 'executed', 'failed')),
    ticket_id            INTEGER REFERENCES tickets(id),
    invoice_id           INTEGER REFERENCES invoices(id),
    lease_id             INTEGER REFERENCES leases(id),
    vendor_id            INTEGER REFERENCES vendors(id),
    sku                  TEXT,
    size                 TEXT,
    qty                  INTEGER CHECK (qty IS NULL OR qty > 0),
    unit_price           REAL    CHECK (unit_price IS NULL OR unit_price > 0),
    amount               REAL    NOT NULL CHECK (amount > 0),
    amount_basis         TEXT    NOT NULL
                                 CHECK (amount_basis IN ('invoice_amount', 'lease_rent', 'estimate_unit_cost', 'quoted_unit_price')),
    account              TEXT,
    fundable_at_proposal INTEGER CHECK (fundable_at_proposal IS NULL OR fundable_at_proposal IN (0, 1)),
    vendor_blocked       INTEGER NOT NULL DEFAULT 0 CHECK (vendor_blocked IN (0, 1)),
    blocking_invoice_ids TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(blocking_invoice_ids)),
    alternative_to       TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(alternative_to)),
    rationale            TEXT    NOT NULL CHECK (length(rationale) BETWEEN 10 AND 1000),
    warnings             TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(warnings)),
    proposed_by          TEXT    NOT NULL
                                 CHECK (proposed_by IN ('boss', 'inventory', 'accounting', 'facilities', 'customer_service')),
    run_id               TEXT    NOT NULL,
    call_id              TEXT,                      -- correlation with audit_trail.json only, never authorisation
    business_date        TEXT    NOT NULL,          -- desk.date_today at proposal time (R5)
    created_at           TEXT    NOT NULL,          -- wall-clock UTC, ISO 8601
    -- Problem 7 (human-only) columns; must be empty on insert:
    decided_by           TEXT,
    decided_at           TEXT,
    decision_note        TEXT,
    executed_at          TEXT,
    payment_id           INTEGER REFERENCES payments(id),
    -- Shape of each kind (spec section 3, tools 7-9):
    CHECK (
        (kind = 'payment'
            AND amount_basis IN ('invoice_amount', 'lease_rent')
            AND (invoice_id IS NULL) <> (lease_id IS NULL)
            AND (amount_basis = 'invoice_amount') = (invoice_id IS NOT NULL)
            AND account IS NOT NULL AND fundable_at_proposal IS NOT NULL
            AND vendor_id IS NULL AND sku IS NULL AND size IS NULL AND qty IS NULL AND unit_price IS NULL)
        OR (kind = 'purchase'
            AND amount_basis = 'estimate_unit_cost'
            AND vendor_id IS NOT NULL AND sku IS NOT NULL AND size IS NOT NULL AND qty IS NOT NULL
            AND invoice_id IS NULL AND lease_id IS NULL AND unit_price IS NULL AND fundable_at_proposal IS NULL)
        OR (kind = 'quote'
            AND amount_basis = 'quoted_unit_price'
            AND ticket_id IS NOT NULL AND sku IS NOT NULL AND size IS NOT NULL AND qty IS NOT NULL
            AND unit_price IS NOT NULL
            AND invoice_id IS NULL AND lease_id IS NULL AND vendor_id IS NULL AND fundable_at_proposal IS NULL)
    )
);

CREATE TRIGGER pending_actions_insert_as_proposed
BEFORE INSERT ON pending_actions
WHEN NEW.status IS NOT 'proposed'
  OR NEW.decided_by IS NOT NULL OR NEW.decided_at IS NOT NULL OR NEW.decision_note IS NOT NULL
  OR NEW.executed_at IS NOT NULL OR NEW.payment_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'pending_actions: new rows must be status proposed with no decision or execution fields');
END;

-- Proposals are never deleted (a decision changes status instead). With PRAGMA recursive_triggers=ON
-- this also fires for the hidden deletes of INSERT OR REPLACE, which the SQLite authorizer never sees
-- (probe, 2026-10-09: with recursive_triggers OFF, REPLACE overwrote an existing row).
CREATE TRIGGER pending_actions_no_delete
BEFORE DELETE ON pending_actions
BEGIN
    SELECT RAISE(ABORT, 'pending_actions: rows are never deleted');
END;

-- Accidental duplicates among OPEN proposals are impossible (spec section 7.3);
-- genuine alternatives (different vendor/sku/size/qty, or different quote price) are allowed.
CREATE UNIQUE INDEX ux_pending_payment_invoice ON pending_actions (invoice_id)
    WHERE kind = 'payment' AND invoice_id IS NOT NULL AND status IN ('proposed', 'approved');
CREATE UNIQUE INDEX ux_pending_payment_lease ON pending_actions (lease_id)
    WHERE kind = 'payment' AND lease_id IS NOT NULL AND status IN ('proposed', 'approved');
CREATE UNIQUE INDEX ux_pending_purchase ON pending_actions (COALESCE(ticket_id, -1), vendor_id, sku, size, qty)
    WHERE kind = 'purchase' AND status IN ('proposed', 'approved');
CREATE UNIQUE INDEX ux_pending_quote ON pending_actions (ticket_id, unit_price)
    WHERE kind = 'quote' AND status IN ('proposed', 'approved');
CREATE INDEX ix_pending_ticket ON pending_actions (ticket_id);

-- Unsent message drafts (R7: the system never sends anything).
CREATE TABLE drafts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id         INTEGER NOT NULL REFERENCES tickets(id),
    recipient         TEXT    NOT NULL CHECK (length(recipient) > 0),
    recipient_source  TEXT    NOT NULL CHECK (recipient_source IN ('tickets.requester', 'leases.landlord')),
    channel           TEXT    NOT NULL DEFAULT 'email' CHECK (channel = 'email'),
    subject           TEXT    NOT NULL CHECK (length(subject) BETWEEN 1 AND 200),
    body              TEXT    NOT NULL CHECK (length(body) BETWEEN 20 AND 4000),
    related_action_id INTEGER REFERENCES pending_actions(id),
    warnings          TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(warnings)),
    status            TEXT    NOT NULL DEFAULT 'draft' CHECK (status = 'draft'),
    created_by        TEXT    NOT NULL CHECK (created_by IN ('customer_service', 'facilities')),
    run_id            TEXT    NOT NULL,
    call_id           TEXT,
    business_date     TEXT    NOT NULL,
    created_at        TEXT    NOT NULL
);
CREATE INDEX ix_drafts_ticket ON drafts (ticket_id);

CREATE TRIGGER drafts_no_delete
BEFORE DELETE ON drafts
BEGIN
    SELECT RAISE(ABORT, 'drafts: rows are never deleted');
END;

COMMIT;
