import { useEffect, useRef, useState } from "react";
import type { JobEvent } from "../types";
export function EventLog({ events }: { events: JobEvent[] }) {
  const [filter, setFilter] = useState("all");
  const [paused, setPaused] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!paused) end.current?.scrollIntoView?.({ block: "nearest" });
  }, [events, paused]);
  const important = events
    .filter((e) => ["warn", "error", "result"].includes(e.level))
    .at(-1);
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Event log</h2>
        <label>
          Show{" "}
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="all">All</option>
            <option value="warn">Warn + error</option>
            <option value="result">Results</option>
          </select>
        </label>
      </div>
      <label className="inline-check">
        <input
          type="checkbox"
          checked={paused}
          onChange={(e) => setPaused(e.target.checked)}
        />
        Pause autoscroll
      </label>
      <div className="event-log" role="log" aria-live="off">
        {events
          .filter(
            (e) =>
              filter === "all" ||
              (filter === "warn"
                ? ["warn", "error"].includes(e.level)
                : e.level === "result"),
          )
          .slice(-300)
          .map((e) => (
            <div key={e.id} data-level={e.level}>
              <time dateTime={e.ts}>{new Date(e.ts).toLocaleTimeString()}</time>
              <span>
                {e.level === "error"
                  ? "✕"
                  : e.level === "warn"
                    ? "◐"
                    : e.level === "result"
                      ? "✓"
                      : "·"}{" "}
                {e.step}
              </span>
              <p>{e.message}</p>
            </div>
          ))}
        <div ref={end} />
      </div>
      <div className="sr-only" aria-live="polite">
        {important ? `${important.step}: ${important.message}` : ""}
      </div>
    </section>
  );
}
