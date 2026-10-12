// Types for the Campus Customs backend API (Problem 7, backend/main.py).
// Top-level shapes follow the backend's OpenAPI schemas; nested objects (typed there only as dictionaries)
// follow the real responses recorded in src/test/fixtures/backend_samples.json and the code that builds them
// (mcp_server/desk_reads.py, mcp_server/approval.py). Nothing here is computed by the dashboard.

/** The three status vocabularies are deliberately separate types. */
export type RunPhase = "queued" | "active" | "completed" | "stopped" | "failed" | "interrupted";
export type ActionStatus = "proposed" | "approved" | "rejected" | "executed" | "failed";
export type TicketStatus = "open" | "resolved" | (string & {});

export type RunOutcome =
  | "in_progress"
  | "failed"
  | "interrupted"
  | "ticket_resolved_by_human"
  | "awaiting_human_approval"
  | "awaiting_human_review_of_drafts"
  | "incomplete_limit_reached"
  | "completed_nothing_proposed"
  | (string & {});

export type ActionKind = "payment" | "purchase" | "quote";
export type AgentName = "boss" | "inventory" | "accounting" | "facilities" | "customer_service";

export interface ApiErrorBody {
  error: { code: string; message: string; problems?: { field: string; message: string }[]; run_id?: string };
}

export interface ServiceIndex {
  service: string;
  docs: string;
  dashboard_origins: string[];
  live_runs_enabled: boolean;
  run_mode: string | null;
  routes: string[];
}

// ---------------------------------------------------------------- tickets

export interface TicketSummary {
  id: number;
  type: string;
  requester: string;
  subject: string;
  status: TicketStatus;
  is_open: boolean;
  is_resolved: boolean;
  sku: string | null;
  size: string | null;
  qty: number | null;
  invoice_id: number | null;
  lease_id: number | null;
  notes: string | null;
  created_at: string;
  proposals: Record<ActionStatus, number>;
  awaiting_human_decision: number;
  drafts: number;
  latest_run_id: string | null;
  latest_run_phase: RunPhase | null;
  /** Follow-up 36: how a resolved ticket was resolved (null while open). */
  resolution?: TicketResolution | null;
}

/** Problem 9, follow-up 36: what the person confirmed when resolving. */
export type ResolutionBasis = "business_outcome" | "simulated_workflow_complete";
export type UnverifiedOutcome = "delivery" | "customer_acceptance" | "message_sent" | "outside_arrangement";

export interface TicketResolution {
  basis: ResolutionBasis | null;
  business_outcome_confirmed: boolean;
  unverified_outcomes: UnverifiedOutcome[];
  resolved_by: string | null;
  resolved_at: string | null;
  summary: string | null;
  source: string;
}

export interface TicketsResponse {
  business_date: string;
  open_count: number;
  resolved_count: number;
  tickets: TicketSummary[];
}

export interface LinkedInvoice {
  id: number;
  amount: number;
  due_date: string;
  status: string;
  description: string | null;
  vendor_id: number;
  vendor_name: string;
  specialty: string;
  lead_days: number;
}

export interface LinkedLease {
  id: number;
  space_name: string;
  landlord: string;
  monthly_rent: number;
  next_due: string;
  notes: string | null;
}

export interface TicketRecord {
  id: number;
  type: string;
  requester: string;
  subject: string;
  sku: string | null;
  size: string | null;
  qty: number | null;
  lease_id: number | null;
  invoice_id: number | null;
  status: TicketStatus;
  notes: string | null;
  created_at: string;
  linked_invoice: LinkedInvoice | null;
  linked_lease: LinkedLease | null;
  is_open: boolean;
  is_resolved: boolean;
  resolution?: TicketResolution | null;
}

export interface Action {
  id: number;
  kind: ActionKind;
  status: ActionStatus;
  ticket_id: number | null;
  invoice_id: number | null;
  lease_id: number | null;
  vendor_id: number | null;
  sku: string | null;
  size: string | null;
  qty: number | null;
  unit_price: number | null;
  amount: number;
  amount_basis: "invoice_amount" | "lease_rent" | "estimate_unit_cost" | "quoted_unit_price";
  account: string | null;
  rationale: string;
  proposed_by: AgentName;
  run_id: string;
  business_date: string;
  created_at: string;
  fundable_at_proposal: boolean | null;
  vendor_blocked: boolean;
  blocking_invoice_ids: number[];
  alternative_to: number[];
  warnings: string[];
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string | null;
  executed_at: string | null;
  payment_id: number | null;
  /** When the stored warnings and flags were captured (they describe conditions at that time). */
  warnings_recorded_at?: { business_date: string; created_at: string };
  /** A purchase authorization that was later withdrawn (from audit.db); null for a plain rejection. */
  withdrawal?: {
    authorized_by: string | null; authorized_at: string | null; authorization_note: string | null;
    withdrawn_by: string | null; withdrawn_at: string | null; withdrawal_note: string | null;
    executed: false; paid: false; source: string;
  } | null;
  /** Purchases only: the vendor restriction TODAY, from the current invoices table. */
  current_vendor_status?: {
    vendor_id: number; vendor: string | null; open_invoice_ids: number[]; blocked_now: boolean; as_of: string; source: string;
  } | null;
}

export interface Draft {
  id: number;
  ticket_id: number;
  recipient: string;
  recipient_source: string;
  channel: string;
  subject: string;
  body: string;
  related_action_id: number | null;
  warnings: string[];
  status: string;
  created_by: AgentName;
  run_id: string;
  business_date: string;
  created_at: string;
  sent: false;
}

export interface TicketRunRef {
  run_id: string;
  mode: string | null;
  phase: RunPhase;
  outcome: RunOutcome;
  started_at: string | null;
  finished_at: string | null;
}

export interface InventoryFacts {
  sku: string;
  name: string;
  location: string;
  unit_cost: number | null;
  list_price: number | null;
  sizes: { size: string; qty: number }[];
  requested_size: string | null;
  requested_qty: number | null;
  on_hand_requested_size: number | null;
  computed: { shortfall: number | null; note: string };
  source: string;
}

export interface TicketDetail {
  business_date: string;
  ticket: TicketRecord;
  pending_actions: Action[];
  drafts: Draft[];
  runs: TicketRunRef[];
  inventory?: InventoryFacts | null;
}

// ---------------------------------------------------------------- runs and events

export interface RunAccepted {
  run_id: string;
  ticket_id: number;
  phase: "queued";
  mode: string;
  status_url: string;
  events_url: string;
  note: string;
}

export interface AgentReportFull {
  summary: string;
  facts: string[];
  proposals_created: number[];
  drafts_created: number[];
  estimates: string[];
  needs_human: string[];
  unknowns: string[];
  delegations: string[];
}

export type AgentReport = AgentReportFull | { recorded_summary: string };

export interface RunView {
  run_id: string;
  ticket_id: number | null;
  mode: string | null;
  phase: RunPhase;
  outcome: RunOutcome;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  awaiting_human_action: boolean;
  proposals_awaiting_decision: Action[];
  proposals_decided: Action[];
  drafts_unsent: { id: number; ticket_id: number; recipient: string; subject: string; created_by: string; created_at: string; sent: false }[];
  ticket_status: TicketStatus | null;
  ticket_resolved: boolean;
  usage: { requests: number; input_tokens: number; output_tokens: number; tool_calls: number; source: string } | null;
  agent_report: AgentReport | null;
  agent_report_source: string | null;
  events: number;
  events_url: string;
}

export interface EventView {
  seq: number;
  run_seq: number | null;
  event_id: string;
  ts_utc: string;
  event_type: string;
  actor: string;
  actor_kind: "agent" | "system" | "human";
  mode: string | null;
  run_id: string | null;
  ticket_id: number | null;
  chain: string[] | null;
  description: string;
  tool: string | null;
  args: Record<string, unknown> | null;
  call_id: string | null;
  outcome: { status: string; code?: string; message?: string; run_status?: string; [k: string]: unknown } | null;
  delegated_by: string | null;
  delegated_to: string | null;
  task: string | null;
  said: string | null;
  said_summary: string | null;
  result_summary: string | null;
  links: Record<string, unknown> | null;
  usage: Record<string, unknown> | null;
  details: Record<string, unknown> | null;
}

export interface EventsResponse {
  events: EventView[];
  next_after_seq: number;
  has_more: boolean;
  trail_events: number;
  trail_valid: boolean;
  source: string;
}

// ---------------------------------------------------------------- approvals, cash, audit

export interface ActionsResponse {
  business_date: string;
  count: number;
  actions: Action[];
  note: string;
}

export interface DecisionRequest {
  decided_by: string;
  confirm: true;
  note?: string | null;
}

export interface ResolveRequest extends DecisionRequest {
  resolution_basis: ResolutionBasis;
  /** business_outcome: true. simulated_workflow_complete: false. */
  business_outcome_confirmed: boolean;
  /** simulated_workflow_complete only: true. */
  workflow_complete_confirmed?: boolean;
  /** simulated_workflow_complete only: at least one. */
  unverified_outcomes?: UnverifiedOutcome[];
  resolution_summary: string;
}

/** Problem 9 F3: spending cash on an APPROVED purchase (step 2 of 2). */
export type PurchaseAmountBasis = "recorded_unit_cost_estimate" | "supplier_confirmed_price";

export interface ExecutePurchaseRequest extends DecisionRequest {
  amount_basis: PurchaseAmountBasis;
  amount: number;
  amount_confirmed: true;
  supplier_confirmed: true;
  confirmation_note?: string | null;
}

export interface DecisionResponse {
  result: string;
  decision_id: string;
  decided_by: string;
  business_date: string;
  status: string;
  warnings: string[];
  links: Record<string, unknown>;
  audit_trail_recorded: boolean;
  reconciliation_pending?: boolean;
  notice: string;
  meaning?: string;
  kind?: ActionKind;
  action_id?: number;
  ticket_id?: number;
  payment?: { payment_id: number; kind: string; ref_id: number; amount: number; account: string; paid_at: string; approved_by: string };
  cash?: { account: string; balance_before: number; balance_after: number; [k: string]: unknown };
  execution?: { amount_basis: PurchaseAmountBasis; database_estimate: number | null; inventory_changed: boolean; [k: string]: unknown };
  changes?: Record<string, unknown>;
  business_outcome_confirmed_by?: string;
  workflow_complete_confirmed_by?: string;
  resolution_basis?: ResolutionBasis;
  business_outcome_confirmed?: boolean;
  unverified_outcomes?: UnverifiedOutcome[];
  resolution_summary?: string;
  not_verified_by_system?: string[];
}

export interface CashResponse {
  business_date: string;
  checking_balance: number | null;
  checking_as_of: string | null;
  source: string;
  accounts: { name: string; balance: number; as_of: string }[];
  payments_executed: { id: number; kind: string; ref_id: number; amount: number; account: string; paid_at: string; approved_by: string }[];
  not_counted_as_cash: {
    proposed_payments_total: number;
    approved_unexecuted_payments_total: number;
    proposed_or_approved_purchase_estimates: number;
    proposed_purchase_estimates?: number;
    approved_unexecuted_purchases_total?: number;
    proposed_or_approved_quotes_total: number;
    note: string;
  };
  /** Problem 9 F3: computed limits, never stored and never deducted from the actual balance. */
  available?: { for_obligations: number; for_purchases: number; note: string } | null;
}

export interface AuditStatus {
  consistent: boolean;
  run_active: boolean;
  json_trail: { events: number; valid: boolean; problems: string[]; exists?: boolean };
  audit_db: { events: number; valid: boolean; problems: string[] };
  incomplete: { kind?: string; db_outcome?: string; reason?: string; [k: string]: unknown }[];
  cross_reference_problems: string[];
  note: string;
}

export const TERMINAL_PHASES: ReadonlySet<RunPhase> = new Set(["completed", "stopped", "failed", "interrupted"]);
