import { useEffect, useState } from "react";
import { getEvents, getJob } from "../api";
import type { JobEvent } from "../types";
import { terminal } from "../types";
const steps = [
  "system",
  "observe",
  "propose",
  "filter",
  "equivalence",
  "race",
  "guard",
  "decide",
  "apply",
  "monitor",
];
export function useEventStream(jobId: string) {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [status, setStatus] = useState("connecting");
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<Error>();
  useEffect(() => {
    setEvents([]);
    setError(undefined);
    setConnected(false);
    setStatus(jobId ? "connecting" : "idle");
    if (!jobId) return;
    let closed = false;
    let failures = 0;
    let polling = false;
    let last = 0;
    let timer: ReturnType<typeof setTimeout>;
    const abort = new AbortController();
    const accept = (incoming: JobEvent[]) => {
      if (closed) return;
      for (const e of incoming) last = Math.max(last, e.id);
      setEvents((old) =>
        Array.from(
          new Map([...old, ...incoming].map((e) => [e.id, e])).values(),
        )
          .sort((a, b) => a.id - b.id)
          .slice(-2000),
      );
    };
    const source = new EventSource(
      `/api/jobs/${encodeURIComponent(jobId)}/events`,
    );
    const poll = async () => {
      if (closed) return;
      try {
        const page = await getEvents(jobId, last, abort.signal);
        accept(page.events);
        const job = await getJob(jobId, abort.signal);
        if (closed) return;
        setError(undefined);
        if (terminal(job.status) && page.events.length < 500) {
          setStatus(job.status);
          closed = true;
          return;
        }
      } catch (reason) {
        if (!closed)
          setError(
            reason instanceof Error ? reason : new Error(String(reason)),
          );
      }
      if (!closed) timer = setTimeout(poll, 1000);
    };
    source.onopen = () => {
      if (!closed) {
        setConnected(true);
        setStatus("streaming");
        setError(undefined);
      }
    };
    const receive = (message: Event) => {
      try {
        const parsed = JSON.parse((message as MessageEvent).data) as JobEvent;
        if (Number.isSafeInteger(parsed.id)) accept([parsed]);
      } catch {
        setError(new Error("Invalid event received"));
      }
    };
    for (const step of steps) source.addEventListener(step, receive);
    source.addEventListener("end", (message: Event) => {
      source.close();
      if (closed) return;
      try {
        setStatus(JSON.parse((message as MessageEvent).data).status);
      } catch {
        setStatus("done");
      }
      setConnected(false);
      closed = true;
    });
    source.onerror = () => {
      if (closed || polling) return;
      setConnected(false);
      failures++;
      setStatus("reconnecting");
      if (failures >= 3) {
        polling = true;
        source.close();
        setStatus("polling");
        void poll();
      }
    };
    return () => {
      closed = true;
      source.close();
      abort.abort();
      clearTimeout(timer);
    };
  }, [jobId]);
  return { events, status, connected, error };
}
