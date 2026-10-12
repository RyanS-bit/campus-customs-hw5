import { useEffect, useId, useRef, type ReactNode } from "react";
import { RUN_PHASE_LABEL, actionStatusLabel } from "../labels";
import type { ActionStatus, RunPhase, TicketStatus } from "../types";

// ---------------------------------------------------------------- the three status vocabularies

/** Business status of a ticket (open / resolved). Only a person's confirmation changes it. */
export function TicketBadge({ status }: { status: TicketStatus }) {
  const resolved = status === "resolved";
  return (
    <span className={`badge badge-ticket ${resolved ? "is-resolved" : "is-open"}`}>
      <span className="badge-kind">Ticket</span> {resolved ? "Resolved" : status === "open" ? "Open" : status}
    </span>
  );
}

/** Status of an agent run (queued … interrupted). Says nothing about whether the ticket is resolved. */
export function RunBadge({ phase }: { phase: RunPhase | null }) {
  if (!phase) return <span className="badge badge-run is-none"><span className="badge-kind">Agent run</span> Not run yet</span>;
  return (
    <span className={`badge badge-run phase-${phase}`}>
      <span className="badge-kind">Agent run</span> {RUN_PHASE_LABEL[phase] ?? phase}
    </span>
  );
}

/** Status of one proposal, exactly as the backend reports it (worded by kind: an approved purchase is not a payment). */
export function ActionBadge({ status, kind, withdrawn }: { status: ActionStatus; kind?: string; withdrawn?: boolean }) {
  return (
    <span className={`badge badge-action status-${status}${withdrawn ? " withdrawn" : ""}`}>
      <span className="badge-kind">Proposal</span> {actionStatusLabel(status, kind, withdrawn)}
    </span>
  );
}

// ---------------------------------------------------------------- modal dialog

export function Modal({ title, onClose, busy, children }: {
  title: string; onClose: () => void; busy: boolean; children: ReactNode;
}) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input, textarea, button")?.focus();
    return () => opener?.focus?.();
  }, []);
  return (
    <div className="modal-backdrop">
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref}
           onKeyDown={(e) => { if (e.key === "Escape" && !busy) onClose(); }}>
        <h2 id={titleId}>{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function Notice({ kind, children, onDismiss }: { kind: "ok" | "error" | "info" | "warn"; children: ReactNode; onDismiss?: () => void }) {
  return (
    <div className={`notice notice-${kind}`} role={kind === "error" ? "alert" : "status"}>
      <div>{children}</div>
      {onDismiss && <button type="button" className="link-button" onClick={onDismiss} aria-label="Dismiss message">Dismiss</button>}
    </div>
  );
}

export function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="panel">
      <header className="panel-head"><h2>{title}</h2>{aside}</header>
      {children}
    </section>
  );
}
