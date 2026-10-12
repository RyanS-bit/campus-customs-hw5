// The ONLY module that talks to the backend. GET helpers never change anything. The POST helpers (start a run,
// approve, reject, execute a purchase, resolve) are called only from explicit human confirmation dialogs, never from polling or
// rendering, and they send the X-Human-Decision header the backend requires. There is deliberately no reset
// helper: resetting the working database stays a deliberate operator step outside the dashboard (design.md).

import type {
  ActionsResponse, AuditStatus, CashResponse, DecisionRequest, DecisionResponse, EventsResponse, ExecutePurchaseRequest, ResolveRequest,
  RunAccepted, RunView, ServiceIndex, TicketDetail, TicketsResponse,
} from "./types";

export const API_BASE: string = (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string,
              public problems?: { field: string; message: string }[]) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, init);
  } catch {
    throw new ApiError(0, "BACKEND_UNREACHABLE",
      `Cannot reach the backend at ${API_BASE}. Start it from hw5/backend with: uvicorn main:app --reload --port 8000`);
  }
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    const err = (body as { error?: { code?: string; message?: string; problems?: { field: string; message: string }[] } } | null)?.error;
    throw new ApiError(res.status, err?.code ?? `HTTP_${res.status}`, err?.message ?? `The backend answered ${res.status}.`,
                       err?.problems);
  }
  return body as T;
}

function humanPost<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Human-Decision": "confirm" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const api = {
  index: () => request<ServiceIndex>("/"),
  tickets: () => request<TicketsResponse>("/api/tickets"),
  ticket: (id: number) => request<TicketDetail>(`/api/tickets/${id}`),
  cash: () => request<CashResponse>("/api/cash"),
  audit: () => request<AuditStatus>("/api/audit"),
  pending: () => request<ActionsResponse>("/api/actions/pending"),
  run: (runId: string) => request<RunView>(`/api/runs/${encodeURIComponent(runId)}`),
  events: (runId: string, afterSeq: number, limit = 200) =>
    request<EventsResponse>(`/api/events?run_id=${encodeURIComponent(runId)}&after_seq=${afterSeq}&limit=${limit}`),

  // Human-initiated only (see the note at the top of this file).
  startRun: (ticketId: number) => humanPost<RunAccepted>(`/api/tickets/${ticketId}/run`),
  approve: (actionId: number, body: DecisionRequest) => humanPost<DecisionResponse>(`/api/actions/${actionId}/approve`, body),
  reject: (actionId: number, body: DecisionRequest) => humanPost<DecisionResponse>(`/api/actions/${actionId}/reject`, body),
  executePurchase: (actionId: number, body: ExecutePurchaseRequest) =>
    humanPost<DecisionResponse>(`/api/actions/${actionId}/execute`, body),
  resolve: (ticketId: number, body: ResolveRequest) => humanPost<DecisionResponse>(`/api/tickets/${ticketId}/resolve`, body),
};
