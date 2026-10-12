// Offline tests for the dashboard. Every response comes from the mocked backend (recorded real responses);
// nothing contacts a server, a model or the network.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { MockBackend, afterRuns, deferred, sample, sampleStatus, serveRecordedRun, type Reply } from "./mockBackend";

let mb: MockBackend;

beforeEach(() => { mb = new MockBackend().install(); });
afterEach(() => { vi.unstubAllGlobals(); });

const renderApp = () => render(<App pollMs={15} refreshMs={60_000} />);
const ticketButton = (id: number) => screen.getByRole("button", { name: new RegExp(`^Ticket ${id}:`) });
const mainBalance = () => screen.getByTestId("cash-balance");
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function openTicket(user: ReturnType<typeof userEvent.setup>, id: number) {
  await user.click(await screen.findByRole("button", { name: new RegExp(`^Ticket ${id}:`) }));
  return screen.findByRole("heading", { name: new RegExp(`^Ticket ${id}:`) });
}

describe("tickets and facts", () => {
  it("lists the three tickets with the business status and balance reported by the backend", async () => {
    renderApp();
    for (const id of [101, 102, 103]) {
      const b = await screen.findByRole("button", { name: new RegExp(`^Ticket ${id}:`) });
      expect(within(b).getByText(/Open/)).toBeInTheDocument();
      expect(within(b).getByText(/Not run yet/)).toBeInTheDocument();
    }
    expect(await screen.findByTestId("header-balance")).toHaveTextContent("$3,400.00");
    expect(mainBalance()).toHaveTextContent("$3,400.00");
    expect(screen.getByTestId("runs-availability")).toHaveTextContent("Disabled on this server");
    expect(mb.posts()).toHaveLength(0);
  });

  it("shows each ticket's underlying issue and its stored financial or inventory facts", async () => {
    const user = userEvent.setup();
    renderApp();
    await screen.findByRole("heading", { name: /^Ticket 101: Bulldog tee/ });   // first ticket is selected on load
    expect(screen.getByTestId("on-hand")).toHaveTextContent("0");
    expect(screen.getByText(/Invoice #501: Bulldog Print Co/)).toBeInTheDocument();
    expect(screen.getByText(/2026-08-28 \(overdue\)/)).toBeInTheDocument();
    expect(screen.getByText(/ships nothing new \(rule R3\)/)).toBeInTheDocument();
    expect(screen.getByText(/were delivered is not recorded/)).toBeInTheDocument();

    await openTicket(user, 103);
    expect(screen.getByTestId("on-hand")).toHaveTextContent("8");
    expect(screen.getByTestId("shortfall")).toHaveTextContent("12");
    expect(screen.getByText("$22.00 / $58.00")).toBeInTheDocument();

    await openTicket(user, 102);
    expect(screen.getByText(/Lease #1: Chapel Street shop/)).toBeInTheDocument();
    expect(screen.getByText("$2,400.00")).toBeInTheDocument();
    expect(screen.queryByTestId("on-hand")).not.toBeInTheDocument();
  });

  it("reports an unreachable backend instead of showing invented data", async () => {
    mb.down = true;
    renderApp();
    expect((await screen.findAllByText(/Cannot reach the backend at http:\/\/localhost:8000/))[0]).toBeInTheDocument();
    expect(screen.getByTestId("header-balance")).toHaveTextContent("—");
  });
});

describe("agent runs", () => {
  it("explains a run refused because live runs are not authorized, and starts nothing", async () => {
    const user = userEvent.setup();
    mb.on("POST", /^\/api\/tickets\/101\/run$/, () => ({ status: sampleStatus("run_refused_not_authorized"), body: sample("run_refused_not_authorized") }));
    renderApp();
    await user.click(await screen.findByRole("button", { name: "Run agents on ticket 101" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/not enabled in this server process/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Start run on ticket 101" }));
    expect(await within(dialog).findByText(/Agent runs are disabled on this server/)).toBeInTheDocument();
    expect(within(dialog).getByText("CC_LIVE_AUTHORIZATION")).toBeInTheDocument();
    expect(mb.posts()).toHaveLength(1);
    expect(mb.posts()[0].headers["X-Human-Decision"]).toBe("confirm");
    expect(mb.count("GET", /^\/api\/runs\//)).toBe(0);
  });

  it("streams a run's recorded events in order without duplicates, then waits for a person without resolving the ticket", async () => {
    const user = userEvent.setup();
    const { runId, eventCount } = serveRecordedRun(mb, 101, { steps: 3, pageSize: 5 });
    renderApp();
    await user.click(await screen.findByRole("button", { name: "Run agents on ticket 101" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Start run on ticket 101" }));

    expect(await screen.findByRole("heading", { name: "Run finished: proposals await your decision" })).toBeInTheDocument();
    const timeline = screen.getByRole("list", { name: "Agent activity" });
    await waitFor(() => expect(within(timeline).getAllByRole("listitem")).toHaveLength(eventCount));
    const seqs = within(timeline).getAllByRole("listitem").map((li) => Number(li.getAttribute("data-seq")));
    expect(seqs).toEqual([...seqs].sort((a, b) => a - b));
    expect(new Set(seqs).size).toBe(seqs.length);
    // which agent acted, what it said, tools and delegation
    expect(within(timeline).getAllByText(/delegated to/).map((n) => n.textContent)).toEqual(["delegated to Inventory", "delegated to Accounting"]);
    expect(within(timeline).getAllByText("check_inventory").length).toBeGreaterThan(0);
    expect(within(timeline).getAllByText("propose_payment").length).toBeGreaterThan(0);
    expect(within(timeline).getByText(/Payment proposal created for invoice #501/)).toBeInTheDocument();
    // follow-up 39: the recorded activity is labelled as historical
    const caption = screen.getByTestId("activity-caption");
    expect(caption).toHaveTextContent(/^Recorded during this run \(started .+, finished .+\)\./);
    expect(caption).toHaveTextContent(/The team's summary and the Activity list below describe the shop as it was at that time and may no\s+longer\s+describe its current condition/);
    // follow-up 41: the caption precedes BOTH the team's summary and the Activity list
    const summaryHeading = screen.getByRole("heading", { name: "The team's summary" });
    const activityHeading = screen.getByRole("heading", { name: /^Activity/ });
    expect(caption.compareDocumentPosition(summaryHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(caption.compareDocumentPosition(activityHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // pagination: more than one events page was requested, with a moving cursor
    const cursors = mb.calls.filter((c) => c.path === "/api/events").map((c) => Number(c.query.get("after_seq")));
    expect(Math.max(...cursors)).toBeGreaterThan(0);
    expect(mb.calls.filter((c) => c.path === "/api/events").every((c) => c.query.get("run_id") === runId)).toBe(true);

    // The run is finished, but the ticket is NOT resolved and nothing was approved or paid.
    expect(screen.getByRole("heading", { name: /^Ticket 101:/ }).parentElement!).toHaveTextContent("Open");
    expect(screen.getByText(/Only a person's confirmation resolves a ticket/)).toBeInTheDocument();
    expect(await screen.findByText(/#1 Payment of invoice #501/)).toBeInTheDocument();
    expect(screen.getAllByText(/Awaiting decision/).length).toBeGreaterThan(0);
    expect(mainBalance()).toHaveTextContent("$3,400.00");
    expect(mb.posts()).toHaveLength(1);                                    // only the explicit "start run"

    // Polling stops at the terminal phase; the history stays on screen.
    const polls = mb.count("GET", /^\/api\/runs\//);
    await sleep(150);
    expect(mb.count("GET", /^\/api\/runs\//)).toBe(polls);
    expect(within(timeline).getAllByRole("listitem")).toHaveLength(eventCount);
  });

  it("presents the human steps when a run finishes, without resolving or deciding anything", async () => {
    const user = userEvent.setup();
    serveRecordedRun(mb, 101, { steps: 2, pageSize: 50 });
    renderApp();
    await user.click(await screen.findByRole("button", { name: "Run agents on ticket 101" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Start run on ticket 101" }));
    const next = await screen.findByRole("region", { name: "Next: your decisions" });
    expect(next).toHaveTextContent("That does not resolve ticket 101.");
    expect(within(next).getByTestId("step-proposals")).toHaveTextContent("Decide 1 proposal(s) awaiting your decision");
    expect(within(next).getByTestId("step-drafts")).toHaveTextContent("No unsent drafts to review.");
    await user.click(within(next).getByRole("button", { name: "Review and confirm resolution…" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Resolve ticket 101: what are you confirming?" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Choose what you are confirming" })).toBeDisabled();   // no basis preselected
    expect(within(dialog).getByText(/1 proposal\(s\) on this ticket still await a decision/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(mb.posts()).toHaveLength(1);                                    // only the explicit "start run"
    expect(ticketButton(101)).toHaveTextContent("Open");
  });

  it("does not offer the resolution step after a failed run or on a resolved ticket", async () => {
    const user = userEvent.setup();
    const failed = sample("run_failed");
    mb.state.details[101].runs = [{ run_id: failed.run_id, mode: "simulated", phase: "failed", outcome: "failed", started_at: failed.started_at, finished_at: failed.finished_at }];
    mb.on("GET", new RegExp(`^/api/runs/${failed.run_id}$`), () => ({ status: 200, body: failed }));
    mb.state.tickets = sample("tickets_after_decisions");
    mb.state.details[102] = sample("ticket_102_resolved");
    renderApp();
    expect(await screen.findByRole("heading", { name: "Run failed" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Next: your decisions" })).not.toBeInTheDocument();
    await openTicket(user, 102);
    expect(await screen.findByText("Resolved: business outcome confirmed")).toBeInTheDocument();   // follow-up 36: basis shown
    expect(screen.queryByText(/fulfilment not verified/)).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Next: your decisions" })).not.toBeInTheDocument();
  });

  it("shows interrupted and failed runs honestly", async () => {
    const user = userEvent.setup();
    const interrupted = sample("run_interrupted");
    const failed = sample("run_failed");
    mb.state.details[101].runs = [{ run_id: interrupted.run_id, mode: "simulated", phase: "interrupted", outcome: "interrupted", started_at: interrupted.started_at, finished_at: null }];
    mb.state.details[103].runs = [{ run_id: failed.run_id, mode: "simulated", phase: "failed", outcome: "failed", started_at: failed.started_at, finished_at: failed.finished_at }];
    mb.on("GET", new RegExp(`^/api/runs/${interrupted.run_id}$`), () => ({ status: 200, body: interrupted }));
    mb.on("GET", new RegExp(`^/api/runs/${failed.run_id}$`), () => ({ status: 200, body: failed }));
    renderApp();
    expect(await screen.findByRole("heading", { name: "Run interrupted" })).toBeInTheDocument();
    expect(screen.getByText(/started but never finished/)).toBeInTheDocument();
    await openTicket(user, 103);
    expect(await screen.findByRole("heading", { name: "Run failed" })).toBeInTheDocument();
    expect(screen.getByText(/model unavailable/)).toBeInTheDocument();
    expect(mb.posts()).toHaveLength(0);
  });
});

describe("human decisions", () => {
  it("shows the pending proposals with the right consequences for each kind", async () => {
    const user = userEvent.setup();
    afterRuns(mb);
    renderApp();
    await openTicket(user, 103);
    expect(await screen.findByText(/Customer quote: 20 × CC-HOOD-NAVY \(M\) at \$45.00 each/)).toBeInTheDocument();
    expect(screen.getByText(/Restock purchase: 12 × CC-HOOD-NAVY \(M\)/)).toBeInTheDocument();
    expect(screen.getByText(/\$264.00 \(estimate, not a vendor quote\)/)).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "Approve…" })[0]);   // ticket 103's first proposal is the quote (#3)
    let dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/does NOT send the quote to the customer/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await user.click(screen.getByRole("button", { name: "Approve (authorize only)…" }));
    dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/this only AUTHORIZES the purchase\. No money moves/)).toBeInTheDocument();
    expect(within(dialog).getByText(/separate step 2, "Execute purchase"/)).toBeInTheDocument();
    expect(within(dialog).getByText(/^Now: Bulldog Print Co has unpaid invoice\(s\) 501 .*execution is refused while that remains true/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await user.click(screen.getAllByRole("button", { name: "Reject…" })[0]);
    expect(within(screen.getByRole("dialog")).getByText(/nothing is executed/)).toBeInTheDocument();
    expect(mb.posts()).toHaveLength(0);                                    // opening and cancelling sends nothing
  });

  it("approves a payment only after a name and an explicit confirmation, once, and shows the balance the backend reports", async () => {
    const user = userEvent.setup();
    afterRuns(mb);
    const hold = deferred<Reply>();
    mb.on("POST", /^\/api\/actions\/1\/approve$/, () => hold.promise);
    renderApp();
    await openTicket(user, 101);
    await user.click(await screen.findByRole("button", { name: "Approve and pay…" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Approve and EXECUTE a payment of $840.00" })).toBeInTheDocument();
    expect(within(dialog).getByText(/checking balance falls by \$840.00/)).toBeInTheDocument();
    expect(within(dialog).getByText(/not proof of identity/)).toBeInTheDocument();
    const submit = within(dialog).getByRole("button", { name: "Approve and pay proposal #1" });
    expect(submit).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Your name"), "Dr. Ryan");
    expect(submit).toBeDisabled();                                        // the confirmation box is still unticked
    await user.click(within(dialog).getByRole("checkbox"));
    expect(submit).toBeEnabled();

    await user.dblClick(submit);
    await user.click(submit);
    expect(mb.posts()).toHaveLength(1);                                   // one request, despite three clicks
    expect(mb.posts()[0].body).toEqual({ decided_by: "Dr. Ryan", confirm: true, note: null });
    expect(mb.posts()[0].headers["X-Human-Decision"]).toBe("confirm");
    expect(within(dialog).getByRole("button", { name: "Sending…" })).toBeDisabled();
    expect(mainBalance()).toHaveTextContent("$3,400.00");                 // no change until the backend reports one

    // The backend executes the payment; its new state is now what GET returns.
    mb.state.cash = sample("cash_after_501");
    mb.state.details[101].pending_actions[0] = { ...mb.state.details[101].pending_actions[0], status: "executed", decided_by: "Dr. Ryan",
                                                 decided_at: "2026-10-10T18:24:54Z", executed_at: "2026-10-10T18:24:54Z", payment_id: 1 };
    hold.resolve({ status: 200, body: sample("approve_payment_501") });
    expect(await screen.findByText(/went from \$3,400.00 to \$2,560.00/)).toBeInTheDocument();
    await waitFor(() => expect(mainBalance()).toHaveTextContent("$2,560.00"));
    expect(screen.getByTestId("balance-change")).toHaveTextContent("-$840.00");
    expect(await screen.findByText(/Executed \(paid\)/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows a backend refusal in the dialog and claims nothing", async () => {
    const user = userEvent.setup();
    afterRuns(mb);
    mb.state.details[103] = sample("ticket_103_purchase_approved");                 // purchase #4 approved (authorized only)
    mb.on("POST", /^\/api\/actions\/4\/execute$/, () => ({ status: 409, body: sample("execute_purchase_refused_r3") }));
    renderApp();
    await openTicket(user, 103);
    await user.click(await screen.findByRole("button", { name: "Execute purchase (spends cash)…" }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Actual amount to pay now/), "264");
    await user.click(within(dialog).getByRole("checkbox", { name: /supplier has agreed/ }));
    await user.click(within(dialog).getByRole("checkbox", { name: /actual amount to pay now/ }));
    await user.type(within(dialog).getByLabelText("Your name"), "Dr. Ryan");
    await user.click(within(dialog).getByRole("button", { name: "Pay $264.00 and execute purchase #4" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("REFUSED_RULE");
    expect(alert).toHaveTextContent("R3: Bulldog Print Co has open unpaid invoice(s) [501]");
    expect(alert).toHaveTextContent("our implementation choice");
    expect(screen.queryByText(/Purchase #4 executed/)).not.toBeInTheDocument();
    expect(mainBalance()).toHaveTextContent("$3,400.00");
    expect(within(dialog).getByRole("button", { name: "Pay $264.00 and execute purchase #4" })).toBeEnabled();   // can retry or cancel
  });

  it("shows an approved purchase as authorized but not paid, and counts it separately from cash", async () => {
    const user = userEvent.setup();
    afterRuns(mb);
    mb.on("POST", /^\/api\/actions\/4\/approve$/, () => {
      mb.state.details[103] = sample("ticket_103_purchase_approved");
      mb.state.cash = sample("cash_with_approved_purchase");
      return { status: 200, body: sample("approve_purchase_blocked_vendor") };
    });
    renderApp();
    await openTicket(user, 103);
    await user.click(await screen.findByRole("button", { name: "Approve (authorize only)…" }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText("Your name"), "Dr. Ryan");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: "Approve proposal #4" }));
    expect(await screen.findByText(/This approval only authorizes the purchase; executing it \(spending cash\) will be refused/)).toBeInTheDocument();
    const state = await screen.findByTestId("purchase-4-authorized");
    expect(state).toHaveTextContent("not paid");
    expect(state).toHaveTextContent("No money has moved");
    expect(screen.getByText(/Authorized, not paid/)).toBeInTheDocument();
    expect(mainBalance()).toHaveTextContent("$3,400.00");                              // actual cash unchanged
    await waitFor(() => expect(screen.getByTestId("committed-purchases")).toHaveTextContent("$264.00"));
    expect(screen.getByTestId("available-for-purchases")).toHaveTextContent("$3,136.00");
  });

  it("executes a purchase only with a confirmed amount, a supplier statement and a name, once, and shows the balance the backend reports", async () => {
    const user = userEvent.setup();
    const executed = sample("ticket_101_purchase_executed");
    // The state just before execution is BUILT FROM the recorded after-state (purchase approved, not yet executed).
    const before = structuredClone(executed);
    const p = before.pending_actions.find((a: { kind: string }) => a.kind === "purchase");
    Object.assign(p, { status: "approved", executed_at: null, payment_id: null });
    mb.state.index = sample("index");
    mb.state.details[101] = before;
    mb.state.cash = sample("cash_after_501");
    const hold = deferred<Reply>();
    mb.on("POST", new RegExp(`^/api/actions/${p.id}/execute$`), () => hold.promise);
    renderApp();
    await openTicket(user, 101);
    await user.click(await screen.findByRole("button", { name: "Execute purchase (spends cash)…" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/this PAYS the vendor now/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Paying is not delivery/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Database cost estimate: 10 × \$8\.00 = \$80\.00/)).toBeInTheDocument();
    const pay = () => within(dialog).getByRole("button", { name: /and execute purchase/ });
    expect(pay()).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/Actual amount to pay now/), "80");
    await user.type(within(dialog).getByLabelText("Your name"), "Dr. Ryan");
    expect(pay()).toBeDisabled();                                                     // supplier and amount not yet confirmed
    await user.click(within(dialog).getByRole("checkbox", { name: /supplier has agreed/ }));
    expect(pay()).toBeDisabled();
    await user.click(within(dialog).getByRole("checkbox", { name: /actual amount to pay now/ }));
    expect(pay()).toBeEnabled();
    await user.dblClick(pay());
    expect(mb.posts()).toHaveLength(1);
    expect(mb.posts()[0].body).toEqual({ decided_by: "Dr. Ryan", confirm: true, amount_basis: "recorded_unit_cost_estimate", amount: 80,
                                         amount_confirmed: true, supplier_confirmed: true, confirmation_note: null, note: null });
    expect(mainBalance()).toHaveTextContent("$2,560.00");                             // unchanged until the backend reports
    mb.state.cash = sample("cash_after_purchase");
    mb.state.details[101] = executed;
    hold.resolve({ status: 200, body: sample("execute_purchase_ok") });
    expect(await screen.findByText(/went from \$2,560.00 to \$2,480.00\. Paying is not delivery/)).toBeInTheDocument();
    await waitFor(() => expect(mainBalance()).toHaveTextContent("$2,480.00"));
    expect(await screen.findByTestId(`purchase-${p.id}-executed`)).toHaveTextContent("receipt of the goods is not recorded");
  });

  it("distinguishes a withdrawn authorization from a plain rejection", async () => {
    const user = userEvent.setup();
    afterRuns(mb);
    mb.state.details[103] = sample("ticket_103_purchase_withdrawn");                   // #4: authorized, then withdrawn
    mb.state.details[101] = sample("ticket_101_purchase_executed");                    // #3: rejected while still proposed
    renderApp();
    await openTicket(user, 103);
    const withdrawn = await screen.findByTestId("purchase-4-withdrawn");
    expect(withdrawn).toHaveTextContent(/Authorized by Dr\. Ryan .*authorization withdrawn by Dr\. Ryan .*"No confirmed hoodie supplier"/);
    expect(withdrawn).toHaveTextContent("It was never executed, and nothing was paid.");
    expect(screen.getByText("Withdrawn (was authorized, never paid)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Execute purchase (spends cash)…" })).not.toBeInTheDocument();
    await openTicket(user, 101);
    const rows = await screen.findAllByText(/Restock purchase: 4 × CC-TEE-WHITE \(S\)/);
    const plain = rows[0].closest(".proposal") as HTMLElement;
    expect(within(plain).getByText("Rejected")).toBeInTheDocument();
    expect(within(plain).queryByText(/Withdrawn/)).not.toBeInTheDocument();
    expect(within(plain).queryByText(/Authorized by/)).not.toBeInTheDocument();
  });

  it("labels stored warnings as proposal-time and shows the current vendor status from current facts", async () => {
    const user = userEvent.setup();
    afterRuns(mb);
    mb.state.details[103] = sample("ticket_103_after_501_paid");                     // proposed while blocked; #501 paid since
    renderApp();
    await openTicket(user, 103);
    const historical = await screen.findByTestId("historical-4");
    expect(historical).toHaveTextContent(/^At proposal time \(business date 2026-08-31, recorded .*\); these may no longer be true:/);
    expect(historical).toHaveTextContent("Vendor blocked by unpaid invoice(s) 501 (R3).");
    expect(historical).toHaveTextContent("R3: Bulldog Print Co has open unpaid invoice(s) [501]");
    const now = screen.getByTestId("current-vendor-4");
    expect(now).toHaveTextContent("Now (business date 2026-08-31): Bulldog Print Co has no unpaid invoices; R3 does not block it today.");
    // Before #501 was paid, the same proposal's current status said blocked (also from current facts).
    mb.state.details[103] = sample("ticket_103_purchase_approved");
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(screen.getByTestId("current-vendor-4")).toHaveTextContent("Bulldog Print Co has unpaid invoice(s) 501"));
    expect(mb.posts()).toHaveLength(0);
  });

  it("requires a confirmation note for a supplier-confirmed price", async () => {
    const user = userEvent.setup();
    afterRuns(mb);
    mb.state.details[103] = sample("ticket_103_purchase_approved");
    renderApp();
    await openTicket(user, 103);
    await user.click(await screen.findByRole("button", { name: "Execute purchase (spends cash)…" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("radio", { name: /Price confirmed by the supplier/ }));
    await user.type(within(dialog).getByLabelText(/Actual amount to pay now/), "250");
    await user.click(within(dialog).getByRole("checkbox", { name: /supplier has agreed/ }));
    await user.click(within(dialog).getByRole("checkbox", { name: /actual amount to pay now/ }));
    await user.type(within(dialog).getByLabelText("Your name"), "Dr. Ryan");
    const pay = within(dialog).getByRole("button", { name: "Pay $250.00 and execute purchase #4" });
    expect(pay).toBeDisabled();                                                       // no confirmation note yet
    await user.type(within(dialog).getByLabelText(/How and when did the supplier confirm/), "ok");
    expect(pay).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/How and when did the supplier confirm/), " by email on 2026-08-31");
    expect(pay).toBeEnabled();
    expect(mb.posts()).toHaveLength(0);
  });

  it("shows a backend error for a second decision on the same proposal", async () => {
    const user = userEvent.setup();
    afterRuns(mb);
    mb.on("POST", /^\/api\/actions\/1\/reject$/, () => ({ status: 409, body: sample("approve_payment_501_again") }));
    renderApp();
    await openTicket(user, 101);
    await user.click(await screen.findByRole("button", { name: "Reject…" }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText("Your name"), "Dr. Ryan");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: "Reject proposal #1" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("NOT_PENDING");
  });

  it("resolves a ticket only with an explicit business confirmation and a written explanation", async () => {
    const user = userEvent.setup();
    afterRuns(mb);
    mb.state.details[102].pending_actions[0] = { ...mb.state.details[102].pending_actions[0], status: "executed", payment_id: 2, decided_by: "Dr. Ryan" };
    mb.on("POST", /^\/api\/tickets\/102\/resolve$/, () => {
      mb.state.tickets = sample("tickets_after_decisions");
      mb.state.details[102] = sample("ticket_102_resolved");
      return { status: 200, body: sample("resolve_102") };
    });
    renderApp();
    await openTicket(user, 102);
    await user.click(await screen.findByRole("button", { name: "Resolve ticket 102…" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/does not verify deliveries, customer acceptance,\s+messages sent/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();   // nothing to confirm until a basis is chosen
    await user.click(within(dialog).getByRole("radio", { name: /^A\. Business outcome confirmed/ }));
    const submit = within(dialog).getByRole("button", { name: "Mark ticket 102 resolved (business outcome confirmed)" });
    await user.type(within(dialog).getByLabelText("Your name"), "Dr. Ryan");
    await user.type(within(dialog).getByLabelText(/How was the issue resolved/), "done");
    await user.click(within(dialog).getByRole("checkbox"));
    expect(submit).toBeDisabled();                                         // explanation too short
    await user.type(within(dialog).getByLabelText(/How was the issue resolved/), ": landlord confirmed receipt of the rent");
    expect(submit).toBeEnabled();
    await user.click(submit);
    expect(mb.posts()[0].body).toMatchObject({ decided_by: "Dr. Ryan", confirm: true, resolution_basis: "business_outcome",
                                               business_outcome_confirmed: true,
                                               resolution_summary: "done: landlord confirmed receipt of the rent" });
    expect(mb.posts()[0].body).not.toHaveProperty("unverified_outcomes");
    expect(await screen.findByText(/Ticket 102 marked resolved by Dr. Ryan/)).toBeInTheDocument();
    await waitFor(() => expect(ticketButton(102)).toHaveTextContent("Resolved"));
    expect(ticketButton(101)).toHaveTextContent("Open");
    expect(screen.getByRole("button", { name: "Run agents on ticket 102" })).toBeDisabled();
  });

  it("warns that undecided proposals block resolution and shows the backend refusal", async () => {
    const user = userEvent.setup();
    afterRuns(mb);
    mb.on("POST", /^\/api\/tickets\/103\/resolve$/, () => ({ status: 409, body: sample("resolve_103_refused_undecided") }));
    renderApp();
    await openTicket(user, 103);
    await user.click(await screen.findByRole("button", { name: "Resolve ticket 103…" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/2 proposal\(s\) on this ticket still await a decision/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("radio", { name: /^A\. Business outcome confirmed/ }));
    await user.type(within(dialog).getByLabelText("Your name"), "Dr. Ryan");
    await user.type(within(dialog).getByLabelText(/How was the issue resolved/), "Customer accepted the quote by email");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: "Mark ticket 103 resolved (business outcome confirmed)" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("still await a decision");
    expect(ticketButton(103)).toHaveTextContent("Open");
  });
});

describe("resolution basis B (follow-up 36)", () => {
  it("resolves as 'workflow complete' only with an explicit choice, named unverified outcomes and a summary", async () => {
    const user = userEvent.setup();
    afterRuns(mb);
    mb.state.details[101].pending_actions[0] = { ...mb.state.details[101].pending_actions[0], status: "executed", payment_id: 1, decided_by: "Dr. Ryan" };
    mb.on("POST", /^\/api\/tickets\/101\/resolve$/, () => {
      mb.state.tickets = sample("tickets_after_simulated_resolution");
      mb.state.details[101] = sample("ticket_101_resolved_simulated");
      return { status: 200, body: sample("resolve_101_simulated") };
    });
    renderApp();
    await openTicket(user, 101);
    await user.click(await screen.findByRole("button", { name: "Resolve ticket 101…" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("radio", { name: /^B\. Simulated workflow complete; fulfilment not verified/ }));
    const submit = within(dialog).getByRole("button", { name: "Mark ticket 101 resolved (workflow complete, fulfilment not verified)" });
    await user.type(within(dialog).getByLabelText("Your name"), "Dr. Ryan");
    await user.type(within(dialog).getByLabelText(/What was done, and what remains unverified/), "Invoice paid; delivery not recorded");
    await user.click(within(dialog).getByRole("checkbox", { name: /simulated workflow for ticket 101 is complete\. I am NOT confirming delivery/ }));
    expect(submit).toBeDisabled();                                         // no unverified outcome named yet
    await user.click(within(dialog).getByRole("checkbox", { name: /Physical production, shipment, delivery/ }));
    await user.click(within(dialog).getByRole("checkbox", { name: /Acceptance by the customer/ }));
    expect(submit).toBeEnabled();
    await user.click(submit);
    expect(mb.posts()[0].body).toEqual({ decided_by: "Dr. Ryan", confirm: true, resolution_basis: "simulated_workflow_complete",
                                         business_outcome_confirmed: false, workflow_complete_confirmed: true,
                                         unverified_outcomes: ["delivery", "customer_acceptance"],
                                         resolution_summary: "Invoice paid; delivery not recorded", note: null });
    expect(await screen.findByText(/simulated workflow complete; fulfilment NOT verified/)).toBeInTheDocument();
    const outcome = await screen.findByTestId("resolution");
    expect(outcome).toHaveTextContent("Resolved: workflow complete; fulfilment not verified");
    expect(outcome).toHaveTextContent(/did not confirm the business outcome/i);
    expect(outcome).toHaveTextContent(/Physical production, shipment, delivery or receipt of goods/);
    expect(outcome).not.toHaveTextContent(/business outcome confirmed/i);
    await waitFor(() => expect(ticketButton(101)).toHaveTextContent("fulfilment not verified"));
  });

  it("switching basis clears the earlier confirmation, so nothing carries over between A and B", async () => {
    const user = userEvent.setup();
    afterRuns(mb);
    renderApp();
    await openTicket(user, 103);
    await user.click(await screen.findByRole("button", { name: "Resolve ticket 103…" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("radio", { name: /^A\. Business outcome confirmed/ }));
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("radio", { name: /^B\. Simulated workflow complete/ }));
    expect(within(dialog).getByRole("checkbox", { name: /simulated workflow for ticket 103 is complete/ })).not.toBeChecked();
    expect(within(dialog).getAllByRole("checkbox").filter((c) => (c as HTMLInputElement).checked)).toHaveLength(0);
    expect(mb.posts()).toHaveLength(0);
  });
});

describe("cash and audit", () => {
  it("keeps actual cash separate from proposals, estimates and quotes", async () => {
    afterRuns(mb);
    renderApp();
    await waitFor(() => expect(mainBalance()).toHaveTextContent("$3,400.00"));
    const cash = mainBalance().closest("section")!;
    expect(within(cash).getByText("Not counted as cash")).toBeInTheDocument();
    expect(within(cash).getByText("$3,240.00")).toBeInTheDocument();       // proposed payments (840 + 2,400), not deducted
    expect(within(cash).getByText("$900.00")).toBeInTheDocument();         // the quote, not revenue
    expect(within(cash).getByText("None.")).toBeInTheDocument();           // no executed payments
    expect(screen.queryByTestId("balance-change")).not.toBeInTheDocument();
  });

  it("shows audit gaps reported by the backend", async () => {
    mb.state.audit = { ...sample("audit_initial"), consistent: false,
                       incomplete: [{ kind: "human_decision", db_outcome: "committed", reason: "audit.db events for this decision exist" }] };
    renderApp();
    expect(await screen.findByText(/1 operation\(s\) need reconciliation/)).toBeInTheDocument();
    expect(screen.getByText(/human_decision: committed/)).toBeInTheDocument();
  });

  it("never sends a POST from loading, polling or browsing", async () => {
    const user = userEvent.setup();
    afterRuns(mb);
    renderApp();
    for (const id of [101, 102, 103]) await openTicket(user, id);
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await sleep(100);
    expect(mb.calls.length).toBeGreaterThan(10);
    expect(mb.posts()).toHaveLength(0);
  });
});
