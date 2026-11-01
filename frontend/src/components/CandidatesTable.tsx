import type { Candidate } from "../types";
import { Badge } from "./Badge";
import { CIBar } from "./CIBar";
export function statusTone(
  status: string,
): "pass" | "fail" | "hold" | "info" | "muted" {
  return status === "selected"
    ? "pass"
    : status.startsWith("rejected") || status === "failed_guard"
      ? "fail"
      : status === "blocked_unproven" || status === "dropped_race"
        ? "hold"
        : status.startsWith("skipped")
          ? "muted"
          : "info";
}
export function CandidatesTable({
  candidates,
  onSelect,
  selected,
  delta,
}: {
  candidates: Candidate[];
  onSelect: (c: Candidate) => void;
  selected?: string;
  delta: number;
}) {
  const ranked = [...candidates].sort(
    (a, b) =>
      (a.status === "selected" ? -1 : b.status === "selected" ? 1 : 0) ||
      (a.speedup?.upper ?? Infinity) - (b.speedup?.upper ?? Infinity),
  );
  return (
    <div className="table-scroll">
      <table>
        <caption className="sr-only">
          Candidate changes and verification results
        </caption>
        <thead>
          <tr>
            {[
              "Kind / candidate",
              "Status",
              "Report change",
              "Equivalence",
              "Guard",
            ].map((c) => (
              <th scope="col" key={c}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ranked.map((c) => (
            <tr key={c.id} className={selected === c.id ? "selected-row" : ""}>
              <td>
                <Badge tone="info">{c.kind}</Badge>
                <button
                  className="text-button"
                  onClick={() => onSelect(c)}
                  aria-pressed={selected === c.id}
                >
                  {c.sql.length > 65 ? c.sql.slice(0, 65) + "…" : c.sql}
                </button>
                <details>
                  <summary>Full SQL</summary>
                  <pre>{c.sql}</pre>
                </details>
              </td>
              <td title={c.reason ?? undefined}>
                <Badge tone={statusTone(c.status)}>
                  {c.status.replaceAll("_", " ")}
                </Badge>
                {c.reason && <small className="reason">{c.reason}</small>}
              </td>
              <td>
                {c.speedup ? (
                  <CIBar
                    lower={c.speedup.lower}
                    upper={c.speedup.upper}
                    mean={c.speedup.mean_rel}
                    delta={0}
                    state={c.speedup.state}
                    n={c.speedup.n}
                    metric={c.speedup.metric}
                    label="Report change"
                  />
                ) : (
                  "—"
                )}
              </td>
              <td>
                {c.equiv
                  ? `${c.equiv.label} · tier ${c.equiv.tier_reached}${c.equiv.weak ? " · weak" : ""}`
                  : "—"}
              </td>
              <td>
                {c.guard ? (
                  <Badge
                    tone={
                      c.guard.passed
                        ? "pass"
                        : c.guard.failed_cell
                          ? "fail"
                          : "hold"
                    }
                  >
                    {c.guard.passed
                      ? "Passed"
                      : c.guard.failed_cell
                        ? "Failed"
                        : "Not proven"}
                  </Badge>
                ) : (
                  "—"
                )}
                <span className="sr-only">Slowdown limit {delta * 100}%</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
