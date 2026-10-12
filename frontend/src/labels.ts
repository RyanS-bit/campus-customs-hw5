// Plain-language wording for values the backend returns. These only DESCRIBE backend values; nothing here
// decides or computes a business outcome.

import type { Action, ActionStatus, RunOutcome, RunPhase, TicketResolution, UnverifiedOutcome } from "./types";

export const money = (n: number | null | undefined): string =>
  n === null || n === undefined ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD" });

export const time = (iso: string | null | undefined): string => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString("en-US", { hour12: false });
};

const AGENT_LABELS: Record<string, string> = {
  boss: "Boss", inventory: "Inventory", accounting: "Accounting", facilities: "Facilities",
  customer_service: "Customer Service", system: "System",
};

export const agentLabel = (actor: string): string =>
  actor.startsWith("human:") ? `Person: ${actor.slice(6)}` : AGENT_LABELS[actor] ?? actor;

export const RUN_PHASE_LABEL: Record<RunPhase, string> = {
  queued: "Queued", active: "Running", completed: "Completed", stopped: "Stopped (limit reached)",
  failed: "Failed", interrupted: "Interrupted",
};

export const ACTION_STATUS_LABEL: Record<ActionStatus, string> = {
  proposed: "Awaiting decision", approved: "Approved", rejected: "Rejected", executed: "Executed (paid)", failed: "Failed",
};

/** Kind-aware wording (Problem 9 F3): an approved purchase is an authorization, not a payment. */
export function actionStatusLabel(status: ActionStatus, kind?: string, withdrawn?: boolean): string {
  if (kind === "purchase" && status === "approved") return "Authorized, not paid";
  if (kind === "purchase" && status === "rejected" && withdrawn) return "Withdrawn (was authorized, never paid)";
  if (kind === "quote" && status === "approved") return "Approved (not sent)";
  return ACTION_STATUS_LABEL[status] ?? status;
}

/** Follow-up 36: the outcomes a person can name as NOT verified when only the simulated workflow is complete. */
export const UNVERIFIED_LABEL: Record<UnverifiedOutcome, string> = {
  delivery: "Physical production, shipment, delivery or receipt of goods",
  customer_acceptance: "Acceptance by the customer or counterparty",
  message_sent: "That any message was actually sent (drafts are never sent)",
  outside_arrangement: "Any payment, refund or arrangement made outside this system",
};

/** How a resolved ticket was resolved, in one line. Never implies fulfilment for the simulated basis. */
export function resolutionLabel(res: TicketResolution | null | undefined): string {
  if (!res) return "Resolved";
  return res.basis === "simulated_workflow_complete"
    ? "Resolved: workflow complete; fulfilment not verified"
    : "Resolved: business outcome confirmed";
}

/** What a run's outcome means. Always says that a run alone never resolves the ticket. */
export function outcomeText(outcome: RunOutcome, error?: string | null): { title: string; detail: string } {
  switch (outcome) {
    case "in_progress":
      return { title: "Agents are working", detail: "The activity below updates as the agents record their steps." };
    case "awaiting_human_approval":
      return { title: "Run finished: proposals await your decision",
               detail: "The agents prepared proposals. Nothing is approved or paid until a person decides. The ticket is still open." };
    case "awaiting_human_review_of_drafts":
      return { title: "Run finished: unsent drafts await review",
               detail: "The agents stored draft messages. Nothing has been sent. The ticket is still open." };
    case "completed_nothing_proposed":
      return { title: "Run finished: nothing was proposed",
               detail: "The agents did not prepare any proposal or draft. The ticket is still open." };
    case "incomplete_limit_reached":
      return { title: "Run stopped at a limit: work is incomplete",
               detail: "A request, token, tool-call, delegation or time limit was reached. Review what was recorded below." };
    case "ticket_resolved_by_human":
      return { title: "Ticket resolved by a person",
               detail: "A person resolved the ticket. See \"Business outcome\" for exactly what they confirmed and what remains unverified." };
    case "failed":
      return { title: "Run failed", detail: error ? `The run stopped with an error: ${error}` : "The run stopped with an error." };
    case "interrupted":
      return { title: "Run interrupted",
               detail: "The run started but never finished (for example, the server stopped). Any unfinished step is shown in the audit status and reconciled before the next decision." };
    default:
      return { title: `Run outcome: ${outcome}`, detail: "" };
  }
}

/** The exact consequence of a decision, by kind, shown before a person confirms it. */
export function decisionConsequence(action: Action, verb: "approve" | "reject"): { headline: string; points: string[] } {
  if (verb === "reject") {
    if (action.kind === "purchase" && action.status === "approved") {
      return { headline: `Withdraw the authorization for purchase #${action.id}`,
               points: ["The approved purchase is cancelled before any money is spent.", "Cash and records do not change, apart from recording your decision."] };
    }
    return { headline: `Reject proposal #${action.id}`,
             points: ["The proposal is rejected and nothing is executed.", "Cash and records do not change, apart from recording your decision."] };
  }
  if (action.kind === "payment") {
    const target = action.invoice_id !== null ? `invoice #${action.invoice_id}` : `the lease rent (lease #${action.lease_id})`;
    return { headline: `Approve and EXECUTE a payment of ${money(action.amount)}`,
             points: [`This pays ${target} from the ${action.account ?? "checking"} account now.`,
                      `If the backend accepts it, the checking balance falls by ${money(action.amount)}.`,
                      "The backend re-checks the rules first (cash cannot go negative; the invoice must still be open).",
                      "A payment cannot be undone from this dashboard."] };
  }
  if (action.kind === "purchase") {
    return { headline: `Approve purchase proposal #${action.id} (authorization only)`,
             points: ["Step 1 of 2: this only AUTHORIZES the purchase. No money moves and nothing is ordered or shipped.",
                      "Spending cash is a separate step 2, \"Execute purchase\", which needs a confirmed amount and supplier.",
                      `${money(action.amount)} is an estimate (quantity × unit cost), not a vendor quote.`,
                      ...(action.current_vendor_status?.blocked_now
                        ? [`Now: ${action.current_vendor_status.vendor} has unpaid invoice(s) ${action.current_vendor_status.open_invoice_ids.join(", ")} (R3: it ships nothing until paid). Approval is allowed, but execution is refused while that remains true.`]
                        : action.current_vendor_status ? [`Now: ${action.current_vendor_status.vendor} has no unpaid invoices, so R3 does not block it today.`] : [])] };
  }
  return { headline: `Approve quote proposal #${action.id} (authorization only)`,
           points: [`This authorizes offering ${action.qty} × ${action.sku} (${action.size}) at ${money(action.unit_price)} each.`,
                    "It does NOT send the quote to the customer, record acceptance, or add revenue or cash."] };
}

export function actionTitle(a: Action): string {
  if (a.kind === "payment") {
    return a.invoice_id !== null ? `Payment of invoice #${a.invoice_id}` : `Rent payment (lease #${a.lease_id})`;
  }
  if (a.kind === "purchase") return `Restock purchase: ${a.qty} × ${a.sku} (${a.size})`;
  return `Customer quote: ${a.qty} × ${a.sku} (${a.size}) at ${money(a.unit_price)} each`;
}

export function amountLabel(a: Action): string {
  switch (a.amount_basis) {
    case "estimate_unit_cost": return `${money(a.amount)} (estimate, not a vendor quote)`;
    case "quoted_unit_price": return `${money(a.amount)} quote total (not revenue)`;
    default: return money(a.amount);
  }
}
