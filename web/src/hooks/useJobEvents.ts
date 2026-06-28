import { useEffect, useRef, useState } from "react";
import type { JobOut, StepOut } from "../api/types";

/** WS events share StepOut's shape so a snapshot/delta maps straight to JobOut. */
interface SnapshotEvent {
  type: "snapshot";
  job: { id: number; status: string };
  steps: StepOut[];
}
interface DeltaEvent {
  type: "delta";
  job?: { id: number; status: string };
  steps: StepOut[];
}
type JobEvent = SnapshotEvent | DeltaEvent;

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
const RECONNECT_MS = 1000;

export interface JobEventsState {
  connected: boolean;
  data: JobOut | null;
}

function wsUrl(jobId: number): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/jobs/${jobId}/events`;
}

/**
 * Subscribe to per-job realtime progress over WebSocket.
 *
 * Returns `{ connected, data }` where `data` is a `JobOut` (or null until the
 * first snapshot). On disconnect it sets `connected=false` and reconnects after
 * `RECONNECT_MS`; on a terminal job status it closes the socket. Callers should
 * fall back to REST polling when `connected` is false / `data` is null.
 */
export function useJobEvents(jobId: number | null): JobEventsState {
  const [state, setState] = useState<JobEventsState>({ connected: false, data: null });
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (jobId == null) return;
    let closed = false;

    const connect = () => {
      const ws = new WebSocket(wsUrl(jobId));
      ws.onopen = () => {
        if (!closed) setState((s) => ({ ...s, connected: true }));
      };
      ws.onmessage = (e) => {
        if (closed) return;
        const evt = JSON.parse(e.data) as JobEvent;
        if (evt.type === "snapshot") {
          setState({ connected: true, data: { id: jobId, status: evt.job.status, steps: evt.steps } });
        } else {
          setState((s) => {
            if (!s.data) return s;
            const byId = new Map(s.data.steps.map((st) => [st.id, st]));
            for (const st of evt.steps) byId.set(st.id, st);
            const status = evt.job?.status ?? s.data.status;
            return { connected: true, data: { ...s.data, status, steps: [...byId.values()] } };
          });
          if (evt.job?.status && TERMINAL.has(evt.job.status)) ws.close();
        }
      };
      ws.onclose = () => {
        if (closed) return;
        setState((s) => ({ ...s, connected: false }));
        timerRef.current = setTimeout(connect, RECONNECT_MS);
      };
      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      closed = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [jobId]);

  return state;
}
