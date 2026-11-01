import { clampDomain, fmtPct, relToX, verdictOf } from "../lib/ci";
export interface CIProps {
  lower: number | null;
  upper: number | null;
  mean: number | null;
  delta: number;
  domain?: [number, number];
  label?: string;
  state?: string;
  showAxis?: boolean;
  n?: number;
  metric?: string;
}
export function CIBar({
  lower,
  upper,
  mean,
  delta,
  domain,
  label = "Measured change",
  state,
  showAxis = false,
  n,
  metric = "time",
}: CIProps) {
  const lo = lower ?? -Infinity,
    hi = upper ?? Infinity;
  const d = domain ?? clampDomain([{ lower: lo, upper: hi }], delta);
  const x = (v: number) => relToX(v, d, 280) + 8;
  const verdict =
    state === "BLOCK" || state === "CONTINUE"
      ? "HOLD"
      : state === "FAIL"
        ? "FAIL"
        : verdictOf({ lower: lo, upper: hi }, delta);
  const color =
    verdict === "PASS"
      ? "var(--pass)"
      : verdict === "FAIL"
        ? "var(--fail)"
        : "var(--hold)";
  const text = `${label}: ${mean == null ? "unknown mean" : fmtPct(mean)}; bounds ${fmtPct(lo)} to ${fmtPct(hi)}; ${verdict === "PASS" ? "within" : verdict === "FAIL" ? "exceeds" : "not proven within"} δ ${Math.round(delta * 100)}%; n=${n ?? "unknown"}, metric ${metric}`;
  return (
    <svg
      className="ci-bar"
      viewBox={`0 0 316 ${showAxis ? 44 : 28}`}
      width="100%"
      role="img"
      aria-label={text}
      tabIndex={0}
    >
      <title>{text}</title>
      <line x1={8} x2={288} y1={14} y2={14} stroke="var(--line)" />
      <line x1={x(0)} x2={x(0)} y1={2} y2={26} stroke="var(--ink-2)" />
      <line
        x1={x(delta)}
        x2={x(delta)}
        y1={0}
        y2={28}
        stroke="var(--hold)"
        strokeDasharray="3 3"
      />
      <line
        x1={x(lo)}
        x2={x(hi)}
        y1={14}
        y2={14}
        stroke={color}
        strokeWidth={6}
        strokeLinecap="round"
      />
      {!Number.isFinite(lo) && (
        <path d="M 15 7 L 8 14 L 15 21" fill="none" stroke={color} />
      )}
      {!Number.isFinite(hi) && (
        <path d="M 281 7 L 288 14 L 281 21" fill="none" stroke={color} />
      )}
      {mean != null && Number.isFinite(mean) && (
        <circle
          cx={x(mean)}
          cy={14}
          r={4}
          fill="var(--surface)"
          stroke={color}
          strokeWidth={2}
        />
      )}
      <text x={300} y={18} fill={color} fontSize={12}>
        {verdict === "PASS" ? "✓" : verdict === "FAIL" ? "✕" : "◐"}
      </text>
      {showAxis && (
        <text
          x={Math.min(250, x(delta) + 4)}
          y={40}
          fill="var(--ink-2)"
          fontSize={10}
        >
          δ {Math.round(delta * 100)}%
        </text>
      )}
    </svg>
  );
}
