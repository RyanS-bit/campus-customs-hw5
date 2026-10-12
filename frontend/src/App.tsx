import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";
import { DecisionDialog, ExecutePurchaseDialog, ResolveDialog, RunDialog } from "./components/Dialogs";
import { AuditPanel, CashPanel, Header, ProposalsPanel, RunPanel, TicketFacts, TicketList } from "./components/Panels";
import { Notice, Section } from "./components/common";
import { UNVERIFIED_LABEL, money, resolutionLabel } from "./labels";
import type { Action, AuditStatus, CashResponse, DecisionResponse, ServiceIndex, TicketDetail, TicketsResponse } from "./types";
import { useRunTracker } from "./useRunTracker";

type Dialog = null | { type: "run" } | { type: "decide"; action: Action; verb: "approve" | "reject" } | { type: "resolve" }
  | { type: "execute"; action: Action };
type Message = { kind: "ok" | "error" | "info" | "warn"; text: string };

export default function App({ pollMs = 1500, refreshMs = 15000 }: { pollMs?: number; refreshMs?: number }) {
  const [index, setIndex] = useState<ServiceIndex | null>(null);
  const [tickets, setTickets] = useState<TicketsResponse | null>(null);
  const [cash, setCash] = useState<CashResponse | null>(null);
  const [audit, setAudit] = useState<AuditStatus | null>(null);
  const [pendingCount, setPendingCount] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<TicketDetail | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [personName, setPersonName] = useState("");
  const initialBalance = useRef<number | null>(null);
  const selectedRef = useRef<number | null>(null);
  const trackedTicket = useRef<number | null>(null);

  const showDetail = useCallback((d: TicketDetail) => {
    setDetail(d);
    if (trackedTicket.current !== d.ticket.id) {               // a newly selected ticket: show its latest run
      trackedTicket.current = d.ticket.id;
      setRunId(d.runs.length ? d.runs[d.runs.length - 1].run_id : null);
    }
  }, []);

  /** Reload everything shown (GET requests only). */
  const refreshAll = useCallback(async () => {
    setRefreshing(true);
    const sel = selectedRef.current;
    const [t, c, a, p, i, d] = await Promise.allSettled([
      api.tickets(), api.cash(), api.audit(), api.pending(), api.index(), sel !== null ? api.ticket(sel) : Promise.resolve(null),
    ]);
    const errors: string[] = [];
    const take = <T,>(r: PromiseSettledResult<T>, set: (v: T) => void) => {
      if (r.status === "fulfilled") set(r.value);
      else errors.push(r.reason instanceof ApiError ? r.reason.message : String(r.reason));
    };
    take(t, (v) => {
      setTickets(v);
      if (selectedRef.current === null && v.tickets.length > 0) select(v.tickets[0].id);
    });
    take(c, (v) => {
      if (initialBalance.current === null) initialBalance.current = v.checking_balance;
      setCash(v);
    });
    take(a, setAudit);
    take(p, (v) => setPendingCount(v.count));
    take(i, setIndex);
    take(d, (v) => { if (v && v.ticket.id === selectedRef.current) showDetail(v); });
    setLoadError(errors.length ? [...new Set(errors)].join(" ") : null);
    setLastUpdated(new Date());
    setRefreshing(false);
  }, [showDetail]);

  const select = (id: number) => {
    selectedRef.current = id;
    setSelected(id);
    api.ticket(id).then((d) => { if (selectedRef.current === id) showDetail(d); })
      .catch((e) => setLoadError(e instanceof ApiError ? e.message : String(e)));
  };

  useEffect(() => {
    void refreshAll();
    const timer = setInterval(() => void refreshAll(), refreshMs);
    return () => clearInterval(timer);
  }, [refreshAll, refreshMs]);

  const tracker = useRunTracker(runId, pollMs, () => void refreshAll());

  const say = (m: Message) => setMessages((ms) => [...ms.slice(-3), m]);

  const decided = (res: DecisionResponse, name: string) => {
    setPersonName(name);
    setDialog(null);
    if (res.result === "payment_executed" && res.cash && res.payment) {
      say({ kind: "ok", text: `Payment #${res.payment.payment_id} executed (${money(res.payment.amount)}). The backend reports the checking balance went from ${money(res.cash.balance_before)} to ${money(res.cash.balance_after)}.` });
    } else if (res.result === "purchase_executed" && res.cash && res.payment) {
      say({ kind: "ok", text: `Purchase #${res.action_id} executed: ${money(res.payment.amount)} paid (payment #${res.payment.payment_id}). The backend reports the checking balance went from ${money(res.cash.balance_before)} to ${money(res.cash.balance_after)}. Paying is not delivery; inventory is unchanged.` });
    } else if (res.result === "ticket_resolved") {
      say({ kind: "ok", text: res.resolution_basis === "simulated_workflow_complete"
        ? `Ticket ${res.ticket_id} marked resolved by ${res.decided_by}: simulated workflow complete; fulfilment NOT verified. Recorded as their statement.`
        : `Ticket ${res.ticket_id} marked resolved by ${res.decided_by}: business outcome confirmed. Recorded as their statement; the system does not verify deliveries, acceptance or messages.` });
    } else if (res.result === "authorization_withdrawn") {
      say({ kind: "ok", text: `The authorization for purchase #${res.action_id} was withdrawn. It was never executed; nothing was paid.` });
    } else if (res.result === "rejected") {
      say({ kind: "ok", text: `Proposal #${res.action_id} rejected. Nothing was executed.` });
    } else {
      say({ kind: "ok", text: `Proposal #${res.action_id} ${res.status}. ${res.meaning ?? ""}` });
    }
    for (const w of res.warnings) say({ kind: "warn", text: w });
    void refreshAll();
  };

  const ticket = detail?.ticket ?? null;
  const undecided = detail ? detail.pending_actions.filter((a) => a.status === "proposed").length : 0;

  return (
    <div className="app">
      <Header businessDate={tickets?.business_date ?? cash?.business_date ?? null} cash={cash} index={index} audit={audit}
              pendingCount={pendingCount} lastUpdated={lastUpdated} refreshing={refreshing} onRefresh={() => void refreshAll()} />
      <div className="messages" aria-live="polite">
        {loadError && <Notice kind="error">{loadError}</Notice>}
        {messages.map((m, i) => (
          <Notice key={i} kind={m.kind} onDismiss={() => setMessages((ms) => ms.filter((_, j) => j !== i))}>{m.text}</Notice>
        ))}
      </div>
      <main className="layout">
        <aside className="col-side">
          {tickets ? <TicketList tickets={tickets.tickets} selected={selected} onSelect={select} />
            : <Section title="Tickets"><p className="muted">{loadError ? "Unavailable." : "Loading…"}</p></Section>}
          <CashPanel cash={cash} initialBalance={initialBalance.current} />
          <AuditPanel audit={audit} />
        </aside>
        <div className="col-main">
          {!ticket || !detail ? (
            <Section title="Ticket"><p className="muted">{selected === null ? "Select a ticket." : "Loading ticket…"}</p></Section>
          ) : (
            <>
              <TicketFacts ticket={ticket} inventory={detail.inventory} businessDate={detail.business_date} />
              <Section title="Business outcome">
                {ticket.is_resolved ? (
                  <div data-testid="resolution">
                    <p><strong>{resolutionLabel(ticket.resolution)}</strong>
                      {ticket.resolution?.resolved_by ? ` (by ${ticket.resolution.resolved_by})` : ""}.</p>
                    {ticket.resolution?.basis === "simulated_workflow_complete" ? (
                      <>
                        <p className="small">The person confirmed only that the desk&apos;s simulated workflow is complete.
                          They did <strong>not</strong> confirm the business outcome. Not verified:</p>
                        <ul className="small">
                          {ticket.resolution.unverified_outcomes.map((u) => <li key={u}>{UNVERIFIED_LABEL[u] ?? u}</li>)}
                        </ul>
                      </>
                    ) : (
                      <p className="small">A person confirmed the underlying business issue is resolved (their statement; the system
                        does not verify deliveries, acceptance or messages).</p>
                    )}
                    {ticket.resolution?.summary && <p className="small muted">“{ticket.resolution.summary}”</p>}
                  </div>
                ) : (
                  <div className="run-controls">
                    <button type="button" onClick={() => setDialog({ type: "resolve" })}>Resolve ticket {ticket.id}…</button>
                    <span className="small muted">Only a person can resolve a ticket, and must say what they are confirming: the business
                      outcome, or only that the simulated workflow is complete. A finished agent run does not resolve it.</span>
                  </div>
                )}
              </Section>
              <RunPanel ticket={ticket} tracker={tracker} runId={runId} onRunClick={() => setDialog({ type: "run" })}
                        undecided={undecided} drafts={detail.drafts.length} onResolveClick={() => setDialog({ type: "resolve" })} />
              <ProposalsPanel actions={detail.pending_actions} drafts={detail.drafts} disabled={dialog !== null}
                              onDecide={(action, verb) => setDialog({ type: "decide", action, verb })}
                              onExecute={(action) => setDialog({ type: "execute", action })} />
            </>
          )}
        </div>
      </main>
      <footer className="app-footer small muted">
        Local prototype. Names are recorded for attribution only. Backend: {index?.service ?? "not connected"}.
      </footer>

      {dialog?.type === "run" && ticket && (
        <RunDialog ticket={ticket} liveEnabled={index ? index.run_mode !== null : null} onCancel={() => setDialog(null)}
                   onStarted={(id) => { trackedTicket.current = ticket.id; setRunId(id); setDialog(null);
                                        say({ kind: "info", text: `Run started on ticket ${ticket.id}. Activity appears below as it is recorded.` });
                                        void refreshAll(); }} />
      )}
      {dialog?.type === "decide" && (
        <DecisionDialog action={dialog.action} verb={dialog.verb} defaultName={personName} onDone={decided} onCancel={() => setDialog(null)} />
      )}
      {dialog?.type === "execute" && (
        <ExecutePurchaseDialog action={dialog.action} defaultName={personName} onDone={decided} onCancel={() => setDialog(null)}
                               unitCost={detail?.inventory?.sku === dialog.action.sku ? detail.inventory.unit_cost
                                 : dialog.action.qty ? Math.round((dialog.action.amount / dialog.action.qty) * 100) / 100 : null} />
      )}
      {dialog?.type === "resolve" && ticket && (
        <ResolveDialog ticket={ticket} undecided={undecided} defaultName={personName} onDone={decided} onCancel={() => setDialog(null)} />
      )}
    </div>
  );
}
