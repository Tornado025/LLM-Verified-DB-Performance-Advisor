import { describe, it, expect } from "vitest";
import { clampDomain, relToX, verdictOf, fmtPct } from "./ci";
describe("confidence interval geometry", () => {
  it("uses strict non-inferiority boundaries", () => {
    expect(verdictOf({ lower: 0, upper: 0.049 }, 0.05)).toBe("PASS");
    expect(verdictOf({ lower: 0.051, upper: 0.1 }, 0.05)).toBe("FAIL");
    expect(verdictOf({ lower: 0, upper: 0.05 }, 0.05)).toBe("HOLD");
    expect(verdictOf({ lower: NaN, upper: Infinity }, 0.05)).toBe("HOLD");
  });
  it("includes zero and delta, ignoring infinite bounds", () => {
    const d = clampDomain([{ lower: -Infinity, upper: Infinity }], 0.05);
    expect(d[0]).toBeLessThan(0);
    expect(d[1]).toBeGreaterThan(0.05);
    expect(clampDomain([], 0).every(Number.isFinite)).toBe(true);
  });
  it("maps and clips positions, including equal domains", () => {
    expect(relToX(0.05, [0, 0.1], 100)).toBe(50);
    expect(relToX(1, [0, 0.1], 100)).toBe(100);
    expect(relToX(0, [0, 0], 100)).toBe(50);
  });
  it("formats signed fractions", () => {
    expect(fmtPct(0.0123)).toBe("+1.2%");
    expect(fmtPct(-0.1)).toBe("-10.0%");
    expect(fmtPct(Infinity)).toBe("—");
  });
});
