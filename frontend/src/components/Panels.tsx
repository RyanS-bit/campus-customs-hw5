import { actionTitle, agentLabel, amountLabel, money, outcomeText, resolutionLabel, time } from "../labels";
import type {
  Action, AgentReport, AuditStatus, CashResponse, Draft, EventView, InventoryFacts, ServiceIndex, TicketRecord,
  TicketSummary,
} from "../types";
import type { RunTracker } from "../useRunTracker";
import { ActionBadge, RunBadge, Section, TicketBadge } from "./common";

// ---------------------------------------------------------------- header

export function Header({ businessDate, cash, index, audit, pendingCount, lastUpdated, refreshing, onRefresh }: {
  businessDate: string | null; cash: CashResponse | null; index: ServiceIndex | null; audit: AuditStatus | null;
  pendingCount: number | null; lastUpdated: Date | null; refreshing: boolean; onRefresh: () => void;
}) {
  const runs = index === null ? "Unknown" : index.run_mode ? (index.live_runs_enabled ? "Live runs enabled" : `Enabled (${index.run_mode})`)
    : "Disabled on this server";
  return (
    <header className="app-header">
      <div>
        <h1>Campus Customs <span className="subtitle">Operations Dashboard</span></h1>
        <p className="muted">Shop date (desk.date_today): <strong>{businessDate ?? "—"}</strong> · Agents prepare; people decide.</p>
      </div>
      <dl className="header-stats">
        <div><dt>Checking balance (actual)</dt><dd className="big" data-testid="header-balance">{money(cash?.checking_balance)}</dd></div>
        <div><dt>Awaiting your decision</dt><dd>{pendingCount ?? "—"}</dd></div>
        <div><dt>Agent runs</dt><dd data-testid="runs-availability">{runs}</dd></div>
        <div><dt>Audit records</dt><dd>{audit === null ? "—" : audit.consistent ? "Consistent" : `${audit.incomplete.length + audit.cross_reference_problems.length} issue(s)`}</dd></div>
      </dl>
      <div className="header-refresh">
        <button type="button" onClick={onRefresh} disabled={refreshing}>{refreshing ? "Refreshing…" : "Refresh"}</button>
        <span className="muted small">Updated {lastUpdated ? lastUpdated.toLocaleTimeString("en-US", { hour12: false }) : "—"}</span>
      </div>
    </header>
  );
}

// ---------------------------------------------------------------- ticket list

export function TicketList({ tickets, selected, onSelect }: {
  tickets: TicketSummary[]; selected: number | null; onSelect: (id: number) => void;
}) {
  return (
    <Section title="Tickets">
      {tickets.length === 0 ? <p className="muted">No tickets found.</p> : (
        <ul className="ticket-list">
          {tickets.map((t) => (
            <li key={t.id}>
              <button type="button" className={`ticket-item ${selected === t.id ? "selected" : ""}`}
                      aria-pressed={selected === t.id} onClick={() => onSelect(t.id)} aria-label={`Ticket ${t.id}: ${t.subject}, ${t.status}`}>
                <span className="ticket-line"><strong>#{t.id}</strong> {t.subject}</span>
                <span className="muted small">{t.requester} · {t.type.replace(/_/g, " ")}</span>
                <span className="badges">
                  <TicketBadge status={t.status} />
                  {t.resolution?.basis === "simulated_workflow_complete" && (
                    <span className="pill" title={resolutionLabel(t.resolution)}>fulfilment not verified</span>
                  )}
                  <RunBadge phase={t.latest_run_phase} />
                  {t.awaiting_human_decision > 0 && <span className="pill">{t.awaiting_human_decision} awaiting decision</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------- ticket facts

export function TicketFacts({ ticket, inventory, businessDate }: {
  ticket: TicketRecord; inventory: InventoryFacts | null | undefined; businessDate: string;
}) {
  const inv = ticket.linked_invoice;
  const lease = ticket.linked_lease;
  return (
    <Section title={`Ticket ${ticket.id}: ${ticket.subject}`} aside={<TicketBadge status={ticket.status} />}>
      <p className="lead">{ticket.notes ?? "No notes."}</p>
      <dl className="facts">
        <div><dt>Requester</dt><dd>{ticket.requester}</dd></div>
        <div><dt>Type</dt><dd>{ticket.type.replace(/_/g, " ")}</dd></div>
        <div><dt>Received</dt><dd>{ticket.created_at}</dd></div>
        {ticket.sku && <div><dt>Requested</dt><dd>{ticket.qty} × {ticket.sku} (size {ticket.size})</dd></div>}
      </dl>
      {inventory && (
        <div className="fact-block">
          <h3>Stock: {inventory.name}</h3>
          <dl className="facts">
            <div><dt>On hand, size {inventory.requested_size}</dt><dd data-testid="on-hand">{inventory.on_hand_requested_size ?? "—"}</dd></div>
            <div><dt>Requested</dt><dd>{inventory.requested_qty ?? "—"}</dd></div>
            <div><dt>Shortfall (computed)</dt><dd data-testid="shortfall">{inventory.computed.shortfall ?? "—"}</dd></div>
            <div><dt>Unit cost / list price</dt><dd>{money(inventory.unit_cost)} / {money(inventory.list_price)}</dd></div>
          </dl>
          <p className="small muted">All sizes: {inventory.sizes.map((s) => `${s.size} ${s.qty}`).join(" · ")} · {inventory.location}. {inventory.computed.note}</p>
        </div>
      )}
      {inv && (
        <div className="fact-block">
          <h3>Invoice #{inv.id}: {inv.vendor_name}</h3>
          <dl className="facts">
            <div><dt>Amount</dt><dd>{money(inv.amount)}</dd></div>
            <div><dt>Due</dt><dd>{inv.due_date}{inv.status === "open" && inv.due_date < businessDate ? " (overdue)" : ""}</dd></div>
            <div><dt>Status</dt><dd>{inv.status}</dd></div>
            <div><dt>Description</dt><dd>{inv.description ?? "—"}</dd></div>
          </dl>
          {inv.status === "open" && <p className="small warn-text">While this invoice is unpaid, {inv.vendor_name} ships nothing new (rule R3).</p>}
          <p className="small muted">Whether the goods on this invoice were delivered is not recorded.</p>
        </div>
      )}
      {lease && (
        <div className="fact-block">
          <h3>Lease #{lease.id}: {lease.space_name}</h3>
          <dl className="facts">
            <div><dt>Landlord</dt><dd>{lease.landlord}</dd></div>
            <div><dt>Monthly rent</dt><dd>{money(lease.monthly_rent)}</dd></div>
            <div><dt>Next due</dt><dd>{lease.next_due}</dd></div>
          </dl>
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------- run status, outcome and timeline

function isFullReport(r: AgentReport): r is Exclude<AgentReport, { recorded_summary: string }> {
  return "summary" in r;
}

function ReportView({ report, source }: { report: AgentReport; source: string | null }) {
  const lists: [string, string[]][] = isFullReport(report)
    ? [["Needs a human decision", report.needs_human], ["Facts the agents read", report.facts], ["Estimates", report.estimates],
       ["Not known from the records", report.unknowns], ["Delegations", report.delegations]]
    : [];
  return (
    <div className="report">
      <h3>The team's summary</h3>
      <p>{isFullReport(report) ? report.summary : report.recorded_summary}</p>
      {lists.filter(([, items]) => items.length > 0).map(([title, items]) => (
        <div key={title}><h4>{title}</h4><ul>{items.map((x) => <li key={x}>{x}</li>)}</ul></div>
      ))}
      <p className="small muted">Source: {source}. This is the agents' own account; the events below are the recorded evidence.</p>
    </div>
  );
}

function eventDetail(e: EventView): string | null {
  switch (e.event_type) {
    case "agent_started": return e.task ? `Task: ${e.task}` : null;
    case "agent_finished": return e.said_summary ?? e.said;
    case "tool_call_started":
      return e.args && Object.keys(e.args).length ? Object.entries(e.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ") : "no arguments";
    case "tool_call_finished": return e.outcome?.status === "ok" ? e.result_summary : e.outcome?.message ?? null;
    case "delegation_requested": return typeof e.details?.task === "string" ? `Task: ${e.details.task}` : null;
    case "delegation_refused": return e.outcome?.message ?? null;
    case "run_finished": return e.usage ? `Usage: ${JSON.stringify(e.usage)}` : null;
    case "budget_exceeded": case "error": return e.outcome?.message ?? null;
    default: return null;
  }
}

export function Timeline({ events }: { events: EventView[] }) {
  if (events.length === 0) return <p className="muted">No agent activity recorded for this run yet.</p>;
  return (
    <ol className="timeline" aria-label="Agent activity">
      {events.map((e) => {
        const depth = Math.max((e.chain?.length ?? 1) - 1, 0);
        const detail = eventDetail(e);
        const status = e.outcome?.status;
        return (
          <li key={e.seq} className={`event event-${e.event_type} ${status ? `outcome-${status}` : ""}`} style={{ marginLeft: `${depth * 1.25}rem` }}
              data-seq={e.seq}>
            <span className="event-time">{time(e.ts_utc)}</span>
            <span className={`actor actor-${e.actor_kind}`}>{agentLabel(e.actor)}</span>
            <span className="event-desc">
              {e.event_type === "delegation_requested" && e.delegated_to
                ? <>delegated to <strong>{agentLabel(e.delegated_to)}</strong></>
                : e.tool ? <>{e.event_type === "tool_call_started" ? "called" : "result of"} <code>{e.tool}</code>{status ? ` · ${status}` : ""}</>
                  : e.description}
            </span>
            {detail && <span className="event-detail">{detail}</span>}
          </li>
        );
      })}
    </ol>
  );
}

/** Problem 9 (F2): when a run that did its work finishes, present the human steps that follow. Nothing here resolves the
 *  ticket: the person decides each proposal, reviews the drafts, and confirms resolution only if the issue is settled. */
function NextSteps({ ticket, undecided, drafts, onResolveClick }: {
  ticket: TicketRecord; undecided: number; drafts: number; onResolveClick: () => void;
}) {
  return (
    <div className="next-steps" role="region" aria-label="Next: your decisions">
      <h3>Next: your decisions</h3>
      <p className="small">The agent run is finished. <strong>That does not resolve ticket {ticket.id}.</strong> In this system a ticket
        is resolved when every proposal has your decision, the needed messages are drafted, and you say what you confirm: that
        the underlying issue is settled, or only that the simulated workflow is complete (with what remains unverified).</p>
      <ol className="small">
        <li data-testid="step-proposals">{undecided === 0 ? "No proposals await your decision." : `Decide ${undecided} proposal(s) awaiting your decision (below).`}</li>
        <li data-testid="step-drafts">{drafts === 0 ? "No unsent drafts to review." : `Review ${drafts} unsent draft(s) (below); the system never sends them.`}</li>
        <li>Resolve only when you can state truthfully what you confirm; otherwise leave the ticket open.</li>
      </ol>
      <button type="button" onClick={onResolveClick}>Review and confirm resolution…</button>
    </div>
  );
}

export function RunPanel({ ticket, tracker, runId, onRunClick, undecided = 0, drafts = 0, onResolveClick }: {
  ticket: TicketRecord; tracker: RunTracker; runId: string | null; onRunClick: () => void;
  undecided?: number; drafts?: number; onResolveClick?: () => void;
}) {
  const v = tracker.view;
  const busy = tracker.polling;
  const out = v ? outcomeText(v.outcome, v.error) : null;
  const terminal = v !== null && !busy;
  const workDone = terminal && (v.phase === "completed" || v.phase === "stopped");
  return (
    <Section title="Agent team" aside={<RunBadge phase={v?.phase ?? null} />}>
      <div className="run-controls">
        <button type="button" className="primary" onClick={onRunClick} disabled={!ticket.is_open || busy}>
          Run agents on ticket {ticket.id}
        </button>
        <span className="small muted">
          {!ticket.is_open ? "This ticket is resolved; only open tickets are run."
            : busy ? "A run is in progress for this ticket." : "Starts the Boss-led team. You will be asked to confirm."}
        </span>
      </div>
      {runId && !v && !tracker.error && <p className="muted">Loading run {runId}…</p>}
      {tracker.error && <div className="notice notice-warn" role="status">{tracker.error}</div>}
      {v && out && (
        <div className={`outcome outcome-${v.outcome}`} aria-live="polite">
          <h3>{out.title}</h3>
          <p>{out.detail}</p>
          <dl className="facts">
            <div><dt>Run</dt><dd className="mono small">{v.run_id}</dd></div>
            <div><dt>Mode</dt><dd>{v.mode ?? "—"}</dd></div>
            <div><dt>Started / finished</dt><dd>{time(v.started_at)} / {time(v.finished_at)}</dd></div>
            <div><dt>Recorded events</dt><dd>{tracker.events.length}{busy ? " (updating)" : ""}</dd></div>
          </dl>
          {terminal && <p className="small"><strong>Ticket status is separate:</strong> <TicketBadge status={v.ticket_status ?? ticket.status} /> Only a person's confirmation resolves a ticket.</p>}
          {v.usage && <p className="small muted">Usage: {v.usage.requests} model requests, {v.usage.tool_calls} tool calls ({v.usage.source}).</p>}
        </div>
      )}
      {workDone && ticket.is_open && onResolveClick && (
        <NextSteps ticket={ticket} undecided={undecided} drafts={drafts} onResolveClick={onResolveClick} />
      )}
      {runId && (
        // Follow-up 41: placed above the team's summary so it covers both the summary and the Activity list.
        <p className="small muted" data-testid="activity-caption">
          <strong>Recorded during this run</strong>
          {v ? ` (started ${time(v.started_at)}${v.finished_at ? `, finished ${time(v.finished_at)}` : ""})` : ""}.
          The team&apos;s summary and the Activity list below describe the shop as it was at that time and may no longer
          describe its current condition; the current ticket status, proposals and cash are shown elsewhere on this page.
        </p>
      )}
      {v?.agent_report && <ReportView report={v.agent_report} source={v.agent_report_source} />}
      {runId && (
        <>
          <h3>Activity {busy && <span className="small muted">(refreshing every few seconds)</span>}</h3>
          <Timeline events={tracker.events} />
        </>
      )}
      {!runId && <p className="muted">No agent run recorded for this ticket yet.</p>}
    </Section>
  );
}

// ---------------------------------------------------------------- proposals and drafts

/** Warnings and flags stored when the proposal was created: labelled as the conditions AT THAT TIME (Problem 9). */
function HistoricalWarnings({ a }: { a: Action }) {
  const items = [
    ...(a.vendor_blocked ? [`Vendor blocked by unpaid invoice(s) ${a.blocking_invoice_ids.join(", ")} (R3).`] : []),
    ...(a.kind === "payment" && a.fundable_at_proposal === false ? ["Not fundable from the stored balance (R1)."] : []),
    ...a.warnings,
  ];
  if (items.length === 0) return null;
  const when = a.warnings_recorded_at ? `business date ${a.warnings_recorded_at.business_date}, recorded ${a.warnings_recorded_at.created_at}` : "when proposed";
  return (
    <div className="historical" data-testid={`historical-${a.id}`}>
      <p className="small muted"><strong>At proposal time</strong> ({when}); these may no longer be true:</p>
      <ul className="small">{items.map((w) => <li key={w} className="warn-text">{w}</li>)}</ul>
    </div>
  );
}

function PurchaseState({ a, disabled, onDecide, onExecute }: {
  a: Action; disabled: boolean; onDecide: (a: Action, verb: "approve" | "reject") => void; onExecute?: (a: Action) => void;
}) {
  if (a.status === "approved") {
    return (
      <div className="purchase-state" data-testid={`purchase-${a.id}-authorized`}>
        <p className="small"><strong>Authorized by {a.decided_by}, not paid.</strong> No money has moved. Spending cash is a
          separate step that needs a confirmed amount and supplier.</p>
        <div className="proposal-buttons">
          {onExecute && <button type="button" className="primary" disabled={disabled} onClick={() => onExecute(a)}>Execute purchase (spends cash)…</button>}
          <button type="button" className="danger" disabled={disabled} onClick={() => onDecide(a, "reject")}>Withdraw authorization…</button>
        </div>
      </div>
    );
  }
  if (a.status === "rejected" && a.withdrawal) {
    const w = a.withdrawal;
    return (
      <p className="small" data-testid={`purchase-${a.id}-withdrawn`}>
        <strong>Authorized by {w.authorized_by}</strong> ({w.authorized_at}){w.authorization_note ? ` · "${w.authorization_note}"` : ""};{" "}
        <strong>authorization withdrawn by {w.withdrawn_by}</strong> ({w.withdrawn_at}){w.withdrawal_note ? ` · "${w.withdrawal_note}"` : ""}.{" "}
        It was never executed, and nothing was paid.
      </p>
    );
  }
  if (a.status === "executed") {
    return (
      <p className="small" data-testid={`purchase-${a.id}-executed`}>
        <strong>Executed: paid from checking</strong> (payment #{a.payment_id}, {a.executed_at ?? ""}). Paying is not delivery:
        receipt of the goods is not recorded and inventory is unchanged.
      </p>
    );
  }
  return null;
}

export function ProposalsPanel({ actions, drafts, onDecide, onExecute, disabled }: {
  actions: Action[]; drafts: Draft[]; onDecide: (a: Action, verb: "approve" | "reject") => void;
  onExecute?: (a: Action) => void; disabled: boolean;
}) {
  return (
    <Section title="Proposals and drafts">
      {actions.length === 0 && <p className="muted">No proposals for this ticket.</p>}
      <ul className="proposals">
        {actions.map((a) => (
          <li key={a.id} className={`proposal status-${a.status}`}>
            <div className="proposal-head">
              <strong>#{a.id} {actionTitle(a)}</strong>
              <ActionBadge status={a.status} kind={a.kind} withdrawn={Boolean(a.withdrawal)} />
            </div>
            <p className="small">{amountLabel(a)} · proposed by {agentLabel(a.proposed_by)} · {a.rationale}</p>
            <HistoricalWarnings a={a} />
            {a.current_vendor_status && (a.status === "proposed" || a.status === "approved") && (
              <p className={`small ${a.current_vendor_status.blocked_now ? "warn-text" : "ok-text"}`} data-testid={`current-vendor-${a.id}`}>
                <strong>Now (business date {a.current_vendor_status.as_of}):</strong>{" "}
                {a.current_vendor_status.blocked_now
                  ? `${a.current_vendor_status.vendor} has unpaid invoice(s) ${a.current_vendor_status.open_invoice_ids.join(", ")}; it ships nothing until they are paid (R3).`
                  : `${a.current_vendor_status.vendor} has no unpaid invoices; R3 does not block it today.`}
              </p>
            )}
            {a.status === "proposed" ? (
              <div className="proposal-buttons">
                <button type="button" className="primary" disabled={disabled} onClick={() => onDecide(a, "approve")}>
                  {a.kind === "payment" ? "Approve and pay…" : a.kind === "purchase" ? "Approve (authorize only)…" : "Approve…"}
                </button>
                <button type="button" className="danger" disabled={disabled} onClick={() => onDecide(a, "reject")}>Reject…</button>
              </div>
            ) : a.kind === "purchase" && (a.status === "approved" || a.status === "executed" || (a.status === "rejected" && a.withdrawal)) ? (
              <PurchaseState a={a} disabled={disabled} onDecide={onDecide} onExecute={onExecute} />
            ) : (
              <p className="small muted">Decided by {a.decided_by ?? "—"} at {a.decided_at ?? "—"}{a.payment_id ? ` · payment #${a.payment_id}` : ""}{a.decision_note ? ` · "${a.decision_note}"` : ""}</p>
            )}
          </li>
        ))}
      </ul>
      {drafts.length > 0 && (
        <>
          <h3>Unsent drafts</h3>
          <ul className="drafts">
            {drafts.map((d) => (
              <li key={d.id}>
                <strong>To {d.recipient}: {d.subject}</strong> <span className="pill">Not sent</span>
                <p className="small">{d.body}</p>
                {d.warnings.map((w) => <p key={w} className="small warn-text">{w}</p>)}
                <p className="small muted">Drafted by {agentLabel(d.created_by)}. The system never sends messages (R7); a person must send it outside the system.</p>
              </li>
            ))}
          </ul>
        </>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------- cash and audit

export function CashPanel({ cash, initialBalance }: { cash: CashResponse | null; initialBalance: number | null }) {
  if (!cash) return <Section title="Cash"><p className="muted">Loading…</p></Section>;
  const n = cash.not_counted_as_cash;
  const change = initialBalance !== null && cash.checking_balance !== null ? cash.checking_balance - initialBalance : 0;
  return (
    <Section title="Cash">
      <p className="balance" data-testid="cash-balance">{money(cash.checking_balance)}</p>
      <p className="small muted">Checking balance as of {cash.checking_as_of}. Source: {cash.source}.</p>
      {change !== 0 && (
        <p className="small" data-testid="balance-change">
          Changed by {money(change)} since this page loaded (both figures reported by the backend).
        </p>
      )}
      <h3>Executed payments</h3>
      {cash.payments_executed.length === 0 ? <p className="muted small">None.</p> : (
        <ul className="small">
          {cash.payments_executed.map((p) => (
            <li key={p.id}>#{p.id}: {money(p.amount)} for {p.kind} {p.ref_id} on {p.paid_at}, approved by {p.approved_by}</li>
          ))}
        </ul>
      )}
      <h3>Not counted as cash</h3>
      <dl className="facts small">
        <div><dt>Proposed payments</dt><dd>{money(n.proposed_payments_total)}</dd></div>
        <div><dt>Approved payments, not executed</dt><dd>{money(n.approved_unexecuted_payments_total)}</dd></div>
        <div><dt>Proposed purchase estimates</dt><dd>{money(n.proposed_purchase_estimates ?? n.proposed_or_approved_purchase_estimates)}</dd></div>
        <div><dt>Authorized purchases, not paid</dt><dd data-testid="committed-purchases">{money(n.approved_unexecuted_purchases_total ?? 0)}</dd></div>
        <div><dt>Quotes (not revenue)</dt><dd>{money(n.proposed_or_approved_quotes_total)}</dd></div>
      </dl>
      <p className="small muted">{n.note}</p>
      {cash.available && (
        <>
          <h3>Available to spend (computed)</h3>
          <dl className="facts small">
            <div><dt>For invoices and rent</dt><dd>{money(cash.available.for_obligations)}</dd></div>
            <div><dt>For purchases</dt><dd data-testid="available-for-purchases">{money(cash.available.for_purchases)}</dd></div>
          </dl>
          <p className="small muted">{cash.available.note}</p>
        </>
      )}
    </Section>
  );
}

export function AuditPanel({ audit }: { audit: AuditStatus | null }) {
  if (!audit) return null;
  return (
    <Section title="Audit records">
      {audit.consistent ? (
        <p className="small">Consistent: {audit.json_trail.events} trail events, {audit.audit_db.events} database audit events, hash chains valid.</p>
      ) : (
        <div className="notice notice-warn" role="status">
          <strong>{audit.incomplete.length} operation(s) need reconciliation; {audit.cross_reference_problems.length} cross-reference problem(s).</strong>
          <ul className="small">
            {audit.incomplete.map((g, i) => <li key={i}>{String(g.kind ?? "operation")}: {String(g.db_outcome ?? "")}. {String(g.reason ?? "")}</li>)}
            {audit.cross_reference_problems.map((p) => <li key={p}>{p}</li>)}
          </ul>
          <p className="small">Gaps are reconciled automatically before the next decision or reset.</p>
        </div>
      )}
      <p className="small muted">{audit.note}</p>
    </Section>
  );
}
