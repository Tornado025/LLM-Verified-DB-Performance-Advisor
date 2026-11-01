import type { JobEvent } from "../types";
export const stages = [
  "observe",
  "propose",
  "filter",
  "equivalence",
  "race",
  "guard",
  "decide",
];
export function stepStates(events: JobEvent[], terminal = false) {
  const latest = events
    .filter((e) => stages.includes(e.step) || e.step === "apply")
    .at(-1);
  const active = latest ? stages.indexOf(latest.step) : -1;
  return stages.map((step, index) => ({
    step,
    state: events.some((e) => e.step === step && e.level === "error")
      ? "error"
      : index === active && !terminal
        ? "running"
        : events.some((e) => e.step === step)
          ? "done"
          : index < active || terminal
            ? "skipped"
            : "pending",
  }));
}
export function Stepper({
  events,
  done = false,
}: {
  events: JobEvent[];
  done?: boolean;
}) {
  const latest = events.at(-1);
  const data = latest?.data;
  return (
    <section aria-label="Verification progress" className="stepper">
      <ol>
        {stepStates(events, done).map(({ step, state }, i) => (
          <li key={step} data-state={state}>
            <span aria-hidden="true">
              {state === "done" ? "✓" : state === "error" ? "✕" : i + 1}
            </span>
            <strong>{step}</strong>
            <small>{state}</small>
          </li>
        ))}
        {events.some((e) => e.step === "apply") && (
          <li data-state="done">
            <span>✓</span>
            <strong>apply</strong>
            <small>approved</small>
          </li>
        )}
      </ol>
      <p className="mono">
        {latest?.message ?? "Waiting for the first event"}
        {data?.sample != null ? ` · sample ${data.sample}` : ""}
        {data?.round != null ? ` · round ${data.round}` : ""}
        {data?.profile ? ` · ${data.profile} block ${data.block}` : ""}
      </p>
    </section>
  );
}
