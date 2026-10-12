// A mocked backend for the offline dashboard tests. It serves RESPONSES RECORDED FROM THE REAL BACKEND
// (fixtures/backend_samples.json, made by scripts/make_frontend_fixtures.py on a temporary database with scripted
// offline agents) through a stubbed fetch, and records every call. No server, model or network is involved.
// Where a test needs an in-between state that was not recorded (e.g. a run still in progress), it is built from a
// recorded sample and labelled as such in the test.

import { vi } from "vitest";
import samples from "./fixtures/backend_samples.json";

type Sample = { status: number; body: unknown };
const SAMPLES = samples as unknown as Record<string, Sample>;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const sample = <T = any>(name: string): T => {
  if (!(name in SAMPLES)) throw new Error(`no recorded sample ${name}`);
  return structuredClone(SAMPLES[name].body) as T;
};
export const sampleStatus = (name: string): number => SAMPLES[name].status;

export interface Call { method: string; path: string; query: URLSearchParams; headers: Record<string, string>; body: unknown }
export interface Reply { status: number; body: unknown }
type Handler = (call: Call, match: RegExpMatchArray) => Reply | Promise<Reply>;

const notFound = (what: string): Reply => ({ status: 404, body: { error: { code: "NOT_FOUND", message: `Unknown ${what}` } } });

export class MockBackend {
  calls: Call[] = [];
  down = false;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  state: { index: any; tickets: any; details: Record<number, any>; cash: any; audit: any; pending: any } = {
    index: sample("index_runs_disabled"),
    tickets: sample("tickets_initial"),
    details: { 101: sample("ticket_101_initial"), 102: sample("ticket_102_initial"), 103: sample("ticket_103_initial") },
    cash: sample("cash_initial"),
    audit: sample("audit_initial"),
    pending: sample("pending_initial"),
  };
  private routes: { method: string; re: RegExp; handler: Handler }[] = [];

  /** Override one route (most recent first). */
  on(method: string, re: RegExp, handler: Handler) {
    this.routes.unshift({ method, re, handler });
    return this;
  }

  posts() { return this.calls.filter((c) => c.method === "POST"); }
  count(method: string, re: RegExp) { return this.calls.filter((c) => c.method === method && re.test(c.path)).length; }

  fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    if (this.down) throw new TypeError("Failed to fetch");
    const url = new URL(String(input));
    const call: Call = {
      method: (init?.method ?? "GET").toUpperCase(), path: url.pathname, query: url.searchParams,
      headers: (init?.headers as Record<string, string>) ?? {},
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
    };
    this.calls.push(call);
    let reply: Reply | undefined;
    for (const r of this.routes) {
      const m = call.path.match(r.re);
      if (r.method === call.method && m) { reply = await r.handler(call, m); break; }
    }
    reply ??= this.defaultReply(call);
    return new Response(JSON.stringify(reply.body), { status: reply.status, headers: { "Content-Type": "application/json" } });
  });

  private defaultReply(call: Call): Reply {
    const s = this.state;
    if (call.method !== "GET") return { status: 405, body: { error: { code: "UNEXPECTED_POST", message: `No mock for ${call.method} ${call.path}` } } };
    if (call.path === "/") return { status: 200, body: s.index };
    if (call.path === "/api/tickets") return { status: 200, body: s.tickets };
    const t = call.path.match(/^\/api\/tickets\/(\d+)$/);
    if (t) return s.details[Number(t[1])] ? { status: 200, body: s.details[Number(t[1])] } : notFound(`ticket ${t[1]}`);
    if (call.path === "/api/cash") return { status: 200, body: s.cash };
    if (call.path === "/api/audit") return { status: 200, body: s.audit };
    if (call.path === "/api/actions/pending") return { status: 200, body: s.pending };
    if (call.path === "/api/events") return { status: 200, body: { events: [], next_after_seq: 0, has_more: false, trail_events: 0, trail_valid: true, source: "mock" } };
    if (call.path.startsWith("/api/runs/")) return notFound("run");
    return notFound(call.path);
  }

  install() {
    vi.stubGlobal("fetch", this.fetch);
    return this;
  }
}

/** Serve one recorded run: the POST is accepted, the run is "active" for `steps` polls while its recorded events
 *  become visible a slice at a time, then the recorded completed view is returned and the ticket, its proposals and
 *  the pending list switch to the recorded after-run samples. Events are paged like the backend (after_seq, has_more). */
export function serveRecordedRun(mb: MockBackend, ticketId: number, { steps = 3, pageSize = 5 } = {}) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const completed = sample<any>(`run_${ticketId}_completed`);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const all = sample<any>(`events_${ticketId}`).events as { seq: number }[];
  let polls = 0;
  const visible = () => (polls >= steps ? all.length : Math.ceil((all.length * polls) / steps));
  mb.state.index = sample("index");                                   // recorded from a server with runs enabled (simulated)
  mb.on("POST", new RegExp(`^/api/tickets/${ticketId}/run$`), () => ({ status: 202, body: sample(`run_${ticketId}_accepted`) }));
  mb.on("GET", /^\/api\/runs\/(.+)$/, (_c, m) => {
    if (m[1] !== completed.run_id) return notFound("run");
    polls += 1;
    if (polls < steps) {
      // An in-progress view, BUILT FROM the recorded completed view (an in-between state is not recorded).
      return { status: 200, body: { ...completed, phase: "active", outcome: "in_progress", finished_at: null, usage: null,
                                    awaiting_human_action: false, proposals_awaiting_decision: [], agent_report: null,
                                    agent_report_source: null } };
    }
    mb.state.details[ticketId] = sample(`ticket_${ticketId}_after_run`);
    const after = sample("tickets_after_runs");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    mb.state.tickets.tickets = mb.state.tickets.tickets.map((t: any) => (t.id === ticketId ? after.tickets.find((x: any) => x.id === ticketId) : t));
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const pend = sample<any>("pending_after_runs");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    pend.actions = pend.actions.filter((a: any) => a.ticket_id === ticketId);
    pend.count = pend.actions.length;
    mb.state.pending = pend;
    return { status: 200, body: completed };
  });
  mb.on("GET", /^\/api\/events$/, (c) => {
    const after = Number(c.query.get("after_seq") ?? 0);
    const limit = Math.min(Number(c.query.get("limit") ?? 200), pageSize);
    const shown = all.slice(0, visible());
    const rest = shown.filter((e) => e.seq > after);
    const page = rest.slice(0, limit);
    const hasMore = rest.length > limit;
    const last = shown.length ? shown[shown.length - 1].seq : after;
    return { status: 200, body: { events: page, has_more: hasMore, next_after_seq: hasMore ? page[page.length - 1].seq : Math.max(last, after),
                                  trail_events: shown.length, trail_valid: true, source: "mock of output/audit_trail.json" } };
  });
  return { runId: completed.run_id as string, eventCount: all.length };
}

/** Start from the recorded state after all three runs (proposals waiting), for decision tests. */
export function afterRuns(mb: MockBackend) {
  mb.state.index = sample("index");
  mb.state.tickets = sample("tickets_after_runs");
  for (const t of [101, 102, 103]) mb.state.details[t] = sample(`ticket_${t}_after_run`);
  mb.state.pending = sample("pending_after_runs");
  mb.state.cash = sample("cash_after_runs");
  // The recorded runs: their views and events are served as recorded.
  const eventsByRun: Record<string, { seq: number }[]> = {};
  for (const t of [101, 102, 103]) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const v = sample<any>(`run_${t}_completed`);
    eventsByRun[v.run_id] = sample(`events_${t}`).events;
    mb.on("GET", new RegExp(`^/api/runs/${v.run_id}$`), () => ({ status: 200, body: sample(`run_${t}_completed`) }));
  }
  mb.on("GET", /^\/api\/events$/, (c) => {
    const evs = (eventsByRun[c.query.get("run_id") ?? ""] ?? []).filter((e) => e.seq > Number(c.query.get("after_seq") ?? 0));
    const last = evs.length ? evs[evs.length - 1].seq : Number(c.query.get("after_seq") ?? 0);
    return { status: 200, body: { events: evs, next_after_seq: last, has_more: false, trail_events: evs.length, trail_valid: true, source: "mock" } };
  });
}

/** A promise the test resolves by hand (to hold a request open). */
export function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}
