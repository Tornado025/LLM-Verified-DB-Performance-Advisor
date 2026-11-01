export function relToX(rel: number, domain: [number, number], width: number) {
  if (!Number.isFinite(width) || width <= 0) return 0;
  const [a, b] = domain;
  if (!Number.isFinite(a) || !Number.isFinite(b) || b <= a) return width / 2;
  if (Number.isNaN(rel)) return width / 2;
  return Math.max(0, Math.min(width, ((rel - a) / (b - a)) * width));
}
export function clampDomain(
  bounds: { lower: number; upper: number }[],
  delta: number,
): [number, number] {
  const values = [
    0,
    Number.isFinite(delta) ? delta : 0.05,
    ...bounds.flatMap((b) => [b.lower, b.upper]).filter(Number.isFinite),
  ];
  let lo = Math.min(...values),
    hi = Math.max(...values);
  if (lo === hi) {
    lo -= 0.05;
    hi += 0.05;
  }
  const pad = (hi - lo) * 0.1;
  return [lo - pad, hi + pad];
}
export function verdictOf(
  b: { lower: number; upper: number },
  delta: number,
): "PASS" | "FAIL" | "HOLD" {
  if (
    !Number.isFinite(b.lower) ||
    !Number.isFinite(b.upper) ||
    b.lower > b.upper ||
    !Number.isFinite(delta)
  )
    return "HOLD";
  return b.upper < delta ? "PASS" : b.lower > delta ? "FAIL" : "HOLD";
}
export function fmtPct(value: number, digits = 1) {
  return Number.isFinite(value)
    ? `${value >= 0 ? "+" : ""}${(value * 100).toFixed(digits)}%`
    : "—";
}
