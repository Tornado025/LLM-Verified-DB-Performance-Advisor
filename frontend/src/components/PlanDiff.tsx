import type { Plan } from "../types";
import { Badge } from "./Badge";
function tree(plan: Plan | undefined, depth = 0): string {
  if (!plan) return "No plan captured";
  const node = plan.Plan ?? plan;
  return `${"  ".repeat(depth)}${node["Node Type"] ?? "Unknown"}${node["Relation Name"] ? " · " + node["Relation Name"] : ""}${node["Index Name"] ? " · " + node["Index Name"] : ""}${node["Plan Rows"] != null ? " · estimated rows " + node["Plan Rows"] : ""}\n${(node.Plans ?? []).map((child) => tree(child, depth + 1)).join("")}`;
}
export function LineDiff({ text, other }: { text: string; other: string }) {
  const lines = new Set(other.split("\n"));
  return (
    <pre>
      {text.split("\n").map((line, i) => (
        <span key={i} className={lines.has(line) ? "" : "diff-line"}>
          {line}
          {"\n"}
        </span>
      ))}
    </pre>
  );
}
export function PlanDiff({
  before,
  after,
  signature,
}: {
  before?: Plan | null;
  after?: Plan | null;
  signature?: string | null;
}) {
  const a = tree(before ?? undefined),
    b = tree(after ?? undefined);
  return (
    <>
      <div className="section-meta">
        <Badge tone={a === b ? "muted" : "info"}>
          {a === b ? "Same plan" : "Different plan"}
        </Badge>
        <code>{signature ?? "No fingerprint"}</code>
      </div>
      <div className="sql-pair">
        <div>
          <h3>Original plan</h3>
          <LineDiff text={a} other={b} />
        </div>
        <div className={a !== b ? "changed" : ""}>
          <h3>Candidate plan</h3>
          <LineDiff text={b} other={a} />
        </div>
      </div>
    </>
  );
}
