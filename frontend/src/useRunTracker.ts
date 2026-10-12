// Follows one agent run: GET /api/runs/{id}, then GET /api/events?run_id=..&after_seq=<cursor> (all pages while
// has_more), every `pollMs` until the run reaches a terminal phase. Events are kept once per seq, in seq order.
// READ-ONLY: it calls only GET helpers, so polling can never approve, pay, resolve or start anything.

import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";
import { TERMINAL_PHASES, type EventView, type RunView } from "./types";

const MAX_PAGES_PER_TICK = 50;

export interface RunTracker {
  view: RunView | null;
  events: EventView[];
  polling: boolean;
  error: string | null;
}

export function useRunTracker(runId: string | null, pollMs: number, onFinished?: () => void): RunTracker {
  const [state, setState] = useState<RunTracker>({ view: null, events: [], polling: false, error: null });
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;

  useEffect(() => {
    if (!runId) {
      setState({ view: null, events: [], polling: false, error: null });
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cursor = 0;
    let sawUnfinished = false;
    const seen = new Map<number, EventView>();
    setState({ view: null, events: [], polling: true, error: null });

    const pullEvents = async () => {
      for (let page = 0; page < MAX_PAGES_PER_TICK; page++) {
        const res = await api.events(runId, cursor);
        for (const e of res.events) if (!seen.has(e.seq)) seen.set(e.seq, e);
        cursor = Math.max(cursor, res.next_after_seq);
        if (!res.has_more) break;
      }
    };

    const tick = async () => {
      try {
        const view = await api.run(runId);
        await pullEvents();                          // after the view, so a terminal view's events are all included
        if (cancelled) return;
        const events = [...seen.values()].sort((a, b) => a.seq - b.seq);
        const terminal = TERMINAL_PHASES.has(view.phase);
        setState({ view, events, polling: !terminal, error: null });
        if (terminal) {
          if (sawUnfinished) finishedRef.current?.();   // refresh tickets/cash once, when a run we watched ends
          return;                                       // stop polling; the recorded history stays on screen
        }
        sawUnfinished = true;
      } catch (e) {
        if (cancelled) return;
        const err = e instanceof ApiError ? e : null;
        if (err?.status === 404) {
          setState((s) => ({ ...s, polling: false, error: `Run ${runId} is not known to the backend.` }));
          return;
        }
        setState((s) => ({ ...s, error: `${err?.message ?? "Connection problem."} Retrying…` }));
      }
      timer = setTimeout(tick, pollMs);
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [runId, pollMs]);

  return state;
}
