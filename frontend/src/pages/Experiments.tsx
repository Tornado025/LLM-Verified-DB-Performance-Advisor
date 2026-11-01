import { useState } from "react";
import { getRq2, getRq4, getRq5 } from "../api";
import { useApi } from "../hooks/useApi";
import { PageTitle, EmptyState } from "../components/UI";
import { ErrorBanner } from "../components/ErrorBanner";
import { DataChart } from "../components/DataChart";
import type { ChartRow } from "../components/DataChart";
const record = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : {};
function rq2Rows(data: unknown, metric: string): ChartRow[] {
  return Object.entries(record(record(data).results))
    .map(([noise, deciders]) => {
      const row: ChartRow = { "Noise (%)": Number(noise) * 100 };
      for (const [name, value] of Object.entries(record(deciders))) {
        const n = record(value)[metric];
        row[name === "advisor_guard" ? "DB Performance Advisor" : name] =
          typeof n === "number" ? n * (metric === "mean_runs" ? 1 : 100) : null;
      }
      return row;
    })
    .sort((a, b) => Number(a["Noise (%)"]) - Number(b["Noise (%)"]));
}
export default function Experiments() {
  const [tab, setTab] = useState("RQ2");
  const [source, setSource] = useState("simulation");
  const rq2 = useApi(getRq2),
    rq4 = useApi(getRq4),
    rq5 = useApi(getRq5);
  const simulation = rq2.data?.[source];
  const points = rq2Rows(simulation, "false_apply");
  const series = points.length
    ? Object.keys(points[0]).filter((k) => k !== "Noise (%)")
    : [];
  const flips = (
    Array.isArray(rq5.data?.by_multiplier) ? rq5.data.by_multiplier : []
  ) as { multiplier: number; measured: number; flips: number }[];
  const measured = flips
    .filter((r) => r.measured > 0)
    .map((r) => ({
      "Order multiplier": r.multiplier,
      "Verdict flips (%)": (r.flips / r.measured) * 100,
    }));
  const shapeData = record(rq4.data?.shape_stats);
  const curveData = record(rq4.data?.speedup_vs_k);
  function extractRows(v: Record<string, unknown>): ChartRow[] {
    const entries = v.rows ?? v.data ?? v.results;
    return Array.isArray(entries)
      ? entries
          .filter((x) => x && typeof x === "object")
          .map(
            (x) =>
              Object.fromEntries(
                Object.entries(x).filter(
                  ([, n]) =>
                    typeof n === "number" ||
                    typeof n === "string" ||
                    n === null,
                ),
              ) as ChartRow,
          )
      : [];
  }
  const equivalence = extractRows(record(record(rq2.data?.real).equivalence));
  const shapes = extractRows(shapeData),
    curve = extractRows(curveData);
  return (
    <>
      <PageTitle
        title="Experiments"
        description="Measured safety, validation cost, and behavior under flash-sale load."
      />
      <div className="tabs" aria-label="Research question">
        {["RQ2", "RQ5", "RQ4", "Equivalence"].map((t) => (
          <button
            key={t}
            className={tab === t ? "active" : ""}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>
      {(rq2.error || rq4.error || rq5.error) && (
        <ErrorBanner error={rq2.error ?? rq4.error ?? rq5.error} />
      )}
      {tab === "RQ2" && (
        <>
          <label>
            Evidence source{" "}
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              <option value="simulation">Simulation</option>
              <option value="real">Real measurements</option>
            </select>
          </label>
          <p>
            Simulation injects noise into synthetic paired observations. It is
            separate from real database measurements.
          </p>
          {points.length ? (
            <>
              <DataChart
                title="False apply rate (%)"
                data={points}
                x="Noise (%)"
                series={series}
              />
              <DataChart
                title="False block rate (%)"
                data={rq2Rows(simulation, "false_block")}
                x="Noise (%)"
                series={series}
              />
              <DataChart
                title="Mean paired runs"
                data={rq2Rows(simulation, "mean_runs")}
                x="Noise (%)"
                series={series}
              />
            </>
          ) : (
            <EmptyState
              title="No noise results available"
              body={
                source === "real"
                  ? "Real database experiment results will be produced by make experiments in Step 19."
                  : "Generate the simulation with python -m app.simulate from backend/."
              }
            />
          )}
        </>
      )}
      {tab === "RQ5" && (
        <>
          {measured.length ? (
            <DataChart
              title="Normal-to-flash verdict flips (%)"
              data={measured}
              x="Order multiplier"
              series={["Verdict flips (%)"]}
              bar
            />
          ) : (
            <EmptyState
              title="No comparable guard verdicts yet"
              body="Run verification with normal and flash-sale profiles. Only measured normal PASS to flash FAIL transitions count as flips; BLOCK stays uncertain."
            />
          )}
          {Array.isArray(rq5.data?.by_kind_shape) && (
            <div className="table-scroll panel">
              <table>
                <thead>
                  <tr>
                    {["Kind", "Shape", "Multiplier", "Measured", "Flips"].map(
                      (t) => (
                        <th scope="col" key={t}>
                          {t}
                        </th>
                      ),
                    )}
                  </tr>
                </thead>
                <tbody>
                  {(rq5.data.by_kind_shape as Record<string, unknown>[]).map(
                    (r, i) => (
                      <tr key={i}>
                        {[
                          "kind",
                          "shape_id",
                          "multiplier",
                          "measured",
                          "flips",
                        ].map((k) => (
                          <td key={k}>{String(r[k])}</td>
                        ))}
                      </tr>
                    ),
                  )}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      {tab === "RQ4" && (
        <>
          {shapes.length ? (
            <DataChart
              title="Query shapes over time"
              data={shapes}
              x={Object.keys(shapes[0])[0]}
              series={Object.keys(shapes[0]).slice(1)}
            />
          ) : (
            <EmptyState
              title="Shape study not generated"
              body="Step 18 trace analysis and Step 19 experiments generate rq4_shapes.json. No shape counts are inferred from missing data."
            />
          )}
          {curve.length ? (
            <DataChart
              title="Verified speedup versus samples"
              data={curve}
              saturation={
                typeof curveData.saturation_k === "number"
                  ? curveData.saturation_k
                  : undefined
              }
              x={Object.keys(curve[0])[0]}
              series={Object.keys(curve[0]).slice(1)}
            />
          ) : (
            <EmptyState
              title="Sampling curve not generated"
              body="The Step 19 experiment runner produces rq4_k.json, including plan-saturation measurements."
            />
          )}
        </>
      )}
      {tab === "Equivalence" &&
        (equivalence.length ? (
          <DataChart
            title="False accept fraction per equivalence tier"
            data={equivalence}
            x="tier"
            series={["false_accept"]}
            bar
          />
        ) : (
          <EmptyState
            title="Equivalence accuracy study pending"
            body="The labelled rewrite-set experiments in Step 19 produce per-tier false-accept measurements. Individual rewrite evidence is already available in each run's evidence card."
          />
        ))}
    </>
  );
}
