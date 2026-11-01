import type { Guard } from "../types";
import { CIBar } from "./CIBar";
import { clampDomain, fmtPct } from "../lib/ci";
export function GuardGrid({
  guard,
  delta,
}: {
  guard: Guard | null | undefined;
  delta: number;
}) {
  if (!guard) return <p>No workload guard was run for this candidate.</p>;
  const profiles = ["normal", "flash5", "flash10", "flash20"];
  const txns = Array.from(
    new Set(Object.values(guard.profiles).flatMap((p) => Object.keys(p.cells))),
  );
  const cells = Object.entries(guard.profiles).flatMap(([p, v]) =>
    Object.entries(v.cells).map(([t, c]) => ({ profile: p, txn: t, ...c })),
  );
  const domain = clampDomain(
    cells.map((c) => ({
      lower: c.lower ?? -Infinity,
      upper: c.upper ?? Infinity,
    })),
    delta,
  );
  const normal = guard.profiles.normal?.cells;
  const normalPass =
    !!normal && Object.values(normal).every((c) => c.state === "PASS");
  const flash = cells.filter((c) => c.profile !== "normal");
  const worst = cells
    .filter((c) => c.upper != null && Number.isFinite(c.upper))
    .sort((a, b) => b.upper! - a.upper!)[0];
  return (
    <>
      {normalPass &&
        flash.some((c) => c.state === "FAIL" || c.state === "BLOCK") && (
          <div className="notice">
            {flash.some((c) => c.state === "FAIL")
              ? "Passes on a normal day, fails under flash-sale load"
              : "Passes on a normal day; not proven under flash-sale load"}
          </div>
        )}
      <div className="table-scroll">
        <table className="guard-grid">
          <caption className="sr-only">
            Protected transaction latency by load profile
          </caption>
          <thead>
            <tr>
              <th scope="col">Transaction</th>
              {profiles.map((p, i) => (
                <th scope="col" key={p}>
                  {p} · {[1, 5, 10, 20][i]}× orders
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {txns.map((txn) => (
              <tr key={txn}>
                <th scope="row">{txn}</th>
                {profiles.map((p) => {
                  const c = guard.profiles[p]?.cells[txn];
                  return (
                    <td key={p}>
                      {c ? (
                        <>
                          <CIBar
                            lower={c.lower}
                            upper={c.upper}
                            mean={c.mean_rel}
                            delta={delta}
                            domain={domain}
                            state={c.state}
                            n={c.n}
                            label={`${txn}, ${p}`}
                          />
                          <small>
                            {fmtPct(c.mean_rel ?? NaN)} ·{" "}
                            {["BLOCK", "CONTINUE"].includes(c.state)
                              ? `not proven (n=${c.n})`
                              : c.state}
                          </small>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {worst && (
        <p className="mono">
          Worst bound: {worst.txn} @ {worst.profile} = {fmtPct(worst.upper!)}{" "}
          (limit {Math.round(delta * 100)}%)
        </p>
      )}
    </>
  );
}
