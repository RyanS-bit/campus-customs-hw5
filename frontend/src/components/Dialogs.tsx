import { useRef, useState, type FormEvent } from "react";
import { ApiError, api } from "../api";
import { UNVERIFIED_LABEL, actionTitle, amountLabel, decisionConsequence, money } from "../labels";
import type { Action, DecisionResponse, PurchaseAmountBasis, ResolutionBasis, TicketRecord, UnverifiedOutcome } from "../types";
import { Modal } from "./common";

const NAME_HINT = "Recorded for attribution only. This local prototype has no login, so the name is not proof of identity.";

function useSingleFlight() {
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<void>) => {
    if (inFlight.current) return;                   // a second click while a request is pending does nothing
    inFlight.current = true;
    setBusy(true);
    try {
      await fn();
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  return { busy, run };
}

function ErrorBox({ error }: { error: ApiError | null }) {
  if (!error) return null;
  return (
    <div className="notice notice-error" role="alert">
      <strong>The backend did not accept this ({error.code}).</strong> {error.message}
      {error.problems && error.problems.length > 0 && (
        <ul>{error.problems.map((p) => <li key={p.field}>{p.field}: {p.message}</li>)}</ul>
      )}
    </div>
  );
}

const asApiError = (e: unknown) => (e instanceof ApiError ? e : new ApiError(0, "UNEXPECTED", String(e)));

// ---------------------------------------------------------------- approve / reject a proposal

export function DecisionDialog({ action, verb, defaultName, onDone, onCancel }: {
  action: Action; verb: "approve" | "reject"; defaultName: string;
  onDone: (res: DecisionResponse, name: string) => void; onCancel: () => void;
}) {
  const [name, setName] = useState(defaultName);
  const [note, setNote] = useState("");
  const [understood, setUnderstood] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const { busy, run } = useSingleFlight();
  const c = decisionConsequence(action, verb);
  const valid = name.trim().length >= 2 && name.trim().length <= 80 && understood && note.length <= 500;
  const label = verb === "approve" ? (action.kind === "payment" ? "Approve and pay" : "Approve") : "Reject";

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    void run(async () => {
      setError(null);
      try {
        const body = { decided_by: name.trim(), confirm: true as const, note: note.trim() || null };
        const res = verb === "approve" ? await api.approve(action.id, body) : await api.reject(action.id, body);
        onDone(res, name.trim());
      } catch (err) {
        setError(asApiError(err));
      }
    });
  };

  return (
    <Modal title={c.headline} onClose={onCancel} busy={busy}>
      <p className="muted">{actionTitle(action)} · {amountLabel(action)} · proposed by {action.proposed_by}</p>
      <ul className="consequences">{c.points.map((p) => <li key={p}>{p}</li>)}</ul>
      <form onSubmit={submit}>
        <label>Your name
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} autoComplete="name" required />
        </label>
        <p className="hint">{NAME_HINT}</p>
        <label>Note (optional)
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
        </label>
        <label className="check">
          <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} />
          I have read the consequences above and want to {verb} proposal #{action.id}.
        </label>
        <ErrorBox error={error} />
        <div className="dialog-buttons">
          <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>
          <button type="submit" className={verb === "approve" ? "primary" : "danger"} disabled={!valid || busy}>
            {busy ? "Sending…" : `${label} proposal #${action.id}`}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------- execute an approved purchase (Problem 9, F3)

export function ExecutePurchaseDialog({ action, unitCost, defaultName, onDone, onCancel }: {
  action: Action; unitCost: number | null; defaultName: string;
  onDone: (res: DecisionResponse, name: string) => void; onCancel: () => void;
}) {
  const estimate = unitCost !== null && action.qty !== null ? Math.round(action.qty * unitCost * 100) / 100 : null;
  const [basis, setBasis] = useState<PurchaseAmountBasis>("recorded_unit_cost_estimate");
  const [amountText, setAmountText] = useState("");
  const [confirmationNote, setConfirmationNote] = useState("");
  const [supplierConfirmed, setSupplierConfirmed] = useState(false);
  const [amountConfirmed, setAmountConfirmed] = useState(false);
  const [name, setName] = useState(defaultName);
  const [note, setNote] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const { busy, run } = useSingleFlight();
  const amount = Number(amountText);
  const amountOk = amountText.trim() !== "" && Number.isFinite(amount) && amount > 0 && Math.round(amount * 100) / 100 === amount;
  const noteOk = basis === "recorded_unit_cost_estimate" || confirmationNote.trim().length >= 10;
  const valid = name.trim().length >= 2 && amountOk && noteOk && supplierConfirmed && amountConfirmed;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    void run(async () => {
      setError(null);
      try {
        const res = await api.executePurchase(action.id, {
          decided_by: name.trim(), confirm: true, amount_basis: basis, amount, amount_confirmed: true, supplier_confirmed: true,
          confirmation_note: confirmationNote.trim() || null, note: note.trim() || null,
        });
        onDone(res, name.trim());
      } catch (err) {
        setError(asApiError(err));
      }
    });
  };

  return (
    <Modal title={`Execute purchase #${action.id}: spend cash`} onClose={onCancel} busy={busy}>
      <p className="muted">{actionTitle(action)} · authorized by {action.decided_by} · estimate {money(action.amount)}</p>
      <ul className="consequences">
        <li>Step 2 of 2: this PAYS the vendor now. If the backend accepts it, the checking balance falls by the amount you confirm.</li>
        <li>Paying is not delivery: inventory does not change, and receipt of the goods is not recorded.</li>
        <li>The backend refuses it if the vendor still has an unpaid invoice, or if cash available for purchases is too low.</li>
        <li>The system cannot contact suppliers (R7). The supplier's agreement and any supplier price are recorded as your statement.</li>
      </ul>
      <form onSubmit={submit}>
        <fieldset className="basis">
          <legend>Basis for the amount</legend>
          <label className="check">
            <input type="radio" name="basis" checked={basis === "recorded_unit_cost_estimate"}
                   onChange={() => { setBasis("recorded_unit_cost_estimate"); }} />
            Database cost estimate: {action.qty} × {money(unitCost)} = {money(estimate)} (not a supplier quote)
          </label>
          <label className="check">
            <input type="radio" name="basis" checked={basis === "supplier_confirmed_price"}
                   onChange={() => setBasis("supplier_confirmed_price")} />
            Price confirmed by the supplier (outside this system)
          </label>
        </fieldset>
        <label>Actual amount to pay now (USD)
          <input inputMode="decimal" value={amountText} onChange={(e) => setAmountText(e.target.value)}
                 placeholder={basis === "recorded_unit_cost_estimate" && estimate !== null ? String(estimate) : ""} />
        </label>
        {basis === "recorded_unit_cost_estimate" && <p className="hint">Must equal the database estimate, {money(estimate)}.</p>}
        {basis === "supplier_confirmed_price" && (
          <label>How and when did the supplier confirm this price? (10–500 characters)
            <textarea value={confirmationNote} onChange={(e) => setConfirmationNote(e.target.value)} maxLength={500} rows={2} />
          </label>
        )}
        <label className="check">
          <input type="checkbox" checked={supplierConfirmed} onChange={(e) => setSupplierConfirmed(e.target.checked)} />
          I confirm the supplier has agreed to supply this item and quantity (a recorded vendor specialty is not enough).
        </label>
        <label className="check">
          <input type="checkbox" checked={amountConfirmed} onChange={(e) => setAmountConfirmed(e.target.checked)} />
          I confirm {amountOk ? money(amount) : "this amount"} is the actual amount to pay now, and that cash will fall by it.
        </label>
        <label>Your name
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} autoComplete="name" required />
        </label>
        <p className="hint">{NAME_HINT}</p>
        <label>Note (optional)
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
        </label>
        <ErrorBox error={error} />
        <div className="dialog-buttons">
          <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>
          <button type="submit" className="primary" disabled={!valid || busy}>
            {busy ? "Sending…" : `Pay ${amountOk ? money(amount) : ""} and execute purchase #${action.id}`}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------- resolve a ticket (two bases, follow-up 36)

const UNVERIFIED_ORDER: UnverifiedOutcome[] = ["delivery", "customer_acceptance", "message_sent", "outside_arrangement"];

export function ResolveDialog({ ticket, undecided, defaultName, onDone, onCancel }: {
  ticket: TicketRecord; undecided: number; defaultName: string;
  onDone: (res: DecisionResponse, name: string) => void; onCancel: () => void;
}) {
  const [name, setName] = useState(defaultName);
  const [basis, setBasis] = useState<ResolutionBasis | null>(null);          // an explicit choice; nothing preselected
  const [summary, setSummary] = useState("");
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [unverified, setUnverified] = useState<UnverifiedOutcome[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const { busy, run } = useSingleFlight();
  const len = summary.trim().length;
  const simulated = basis === "simulated_workflow_complete";
  const valid = name.trim().length >= 2 && basis !== null && confirmed && len >= 10 && len <= 1000
    && (!simulated || unverified.length > 0);

  const choose = (b: ResolutionBasis) => { setBasis(b); setConfirmed(false); setUnverified([]); };
  const toggle = (u: UnverifiedOutcome) =>
    setUnverified((cur) => (cur.includes(u) ? cur.filter((x) => x !== u) : [...cur, u]));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!valid || basis === null) return;
    void run(async () => {
      setError(null);
      try {
        const common = { decided_by: name.trim(), confirm: true as const, resolution_summary: summary.trim(), note: note.trim() || null };
        const res = await api.resolve(ticket.id, simulated
          ? { ...common, resolution_basis: "simulated_workflow_complete", business_outcome_confirmed: false,
              workflow_complete_confirmed: true, unverified_outcomes: UNVERIFIED_ORDER.filter((u) => unverified.includes(u)) }
          : { ...common, resolution_basis: "business_outcome", business_outcome_confirmed: true });
        onDone(res, name.trim());
      } catch (err) {
        setError(asApiError(err));
      }
    });
  };

  return (
    <Modal title={`Resolve ticket ${ticket.id}: what are you confirming?`} onClose={onCancel} busy={busy}>
      <p>
        Finishing an agent run or deciding proposals does <strong>not</strong> resolve a ticket by itself. Choose exactly
        what you are confirming about <strong>{ticket.requester}</strong> ("{ticket.subject}").
      </p>
      <p className="hint">
        Either way, the system records this as your statement. It does not verify deliveries, customer acceptance,
        messages sent, or payments made outside this system.
      </p>
      {undecided > 0 && (
        <div className="notice notice-warn" role="status">
          {undecided} proposal(s) on this ticket still await a decision; the backend will refuse until they are decided.
        </div>
      )}
      <form onSubmit={submit}>
        <fieldset className="basis">
          <legend>What are you confirming? (choose one)</legend>
          <label className="check">
            <input type="radio" name="basis" checked={basis === "business_outcome"} onChange={() => choose("business_outcome")} />
            <span><strong>A. Business outcome confirmed.</strong> The underlying issue is actually solved (for example, a
              payment the counterparty needed has been made).</span>
          </label>
          <label className="check">
            <input type="radio" name="basis" checked={simulated} onChange={() => choose("simulated_workflow_complete")} />
            <span><strong>B. Simulated workflow complete; fulfilment not verified.</strong> The desk&apos;s work in this
              simulation is done (agents ran, every proposal is decided, messages are drafted). You are <em>not</em> confirming
              that goods arrived or that the customer accepted.</span>
          </label>
        </fieldset>
        {simulated && (
          <fieldset className="unverified">
            <legend>What remains unverified? (select at least one)</legend>
            {UNVERIFIED_ORDER.map((u) => (
              <label className="check" key={u}>
                <input type="checkbox" checked={unverified.includes(u)} onChange={() => toggle(u)} /> {UNVERIFIED_LABEL[u]}
              </label>
            ))}
          </fieldset>
        )}
        <label>Your name
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} autoComplete="name" required />
        </label>
        <p className="hint">{NAME_HINT}</p>
        <label>{simulated ? "What was done, and what remains unverified? (your own words, 10–1000 characters)"
                          : "How was the issue resolved? (your own words, 10–1000 characters)"}
          <textarea value={summary} onChange={(e) => setSummary(e.target.value)} maxLength={1000} rows={4} required />
        </label>
        <p className="hint" aria-live="polite">{len} / 1000 characters{len > 0 && len < 10 ? " (at least 10 needed)" : ""}</p>
        <label>Note (optional)
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
        </label>
        {basis !== null && (
          <label className="check">
            <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
            {simulated
              ? `I confirm the desk's simulated workflow for ticket ${ticket.id} is complete. I am NOT confirming delivery or customer acceptance.`
              : `I confirm the underlying business issue for ticket ${ticket.id} is resolved (not only that the agents finished).`}
          </label>
        )}
        <ErrorBox error={error} />
        <div className="dialog-buttons">
          <button type="button" onClick={onCancel} disabled={busy}>Cancel</button>
          <button type="submit" className="primary" disabled={!valid || busy}>
            {busy ? "Sending…" : basis === null ? "Choose what you are confirming"
              : simulated ? `Mark ticket ${ticket.id} resolved (workflow complete, fulfilment not verified)`
              : `Mark ticket ${ticket.id} resolved (business outcome confirmed)`}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------- start a run

export function RunDialog({ ticket, liveEnabled, onStarted, onCancel }: {
  ticket: TicketRecord; liveEnabled: boolean | null;
  onStarted: (runId: string) => void; onCancel: () => void;
}) {
  const [error, setError] = useState<ApiError | null>(null);
  const { busy, run } = useSingleFlight();
  const start = () => void run(async () => {
    setError(null);
    try {
      const res = await api.startRun(ticket.id);
      onStarted(res.run_id);
    } catch (err) {
      setError(asApiError(err));
    }
  });
  const notAuthorized = error?.code === "RUNS_NOT_AUTHORIZED";
  return (
    <Modal title={`Run the agent team on ticket ${ticket.id}?`} onClose={onCancel} busy={busy}>
      <ul className="consequences">
        <li>The Boss agent reads the ticket and delegates to the specialists.</li>
        <li>The agents only investigate, propose and draft. They approve nothing, pay nothing, send nothing, and do not resolve the ticket.</li>
        <li>Live runs call the gpt-6-luna model through Portkey and cost money.</li>
      </ul>
      {liveEnabled === false && !error && (
        <div className="notice notice-warn" role="status">
          The backend reports that agent runs are not enabled in this server process. Starting will most likely be refused.
        </div>
      )}
      {notAuthorized ? (
        <div className="notice notice-error" role="alert">
          <strong>Agent runs are disabled on this server.</strong> Live runs cost money, so the backend starts them only
          when the person launching it sets <code>CC_LIVE_AUTHORIZATION</code> (planned for Problem 9). Nothing was started.
          <details><summary>Backend message</summary>{error?.message}</details>
        </div>
      ) : <ErrorBox error={error} />}
      <div className="dialog-buttons">
        <button type="button" onClick={onCancel} disabled={busy}>{error ? "Close" : "Cancel"}</button>
        {!notAuthorized && (
          <button type="button" className="primary" onClick={start} disabled={busy}>
            {busy ? "Starting…" : `Start run on ticket ${ticket.id}`}
          </button>
        )}
      </div>
    </Modal>
  );
}
