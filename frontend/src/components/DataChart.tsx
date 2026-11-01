import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
  ReferenceLine,
} from "recharts";
import { Button } from "./UI";
export type ChartRow = Record<string, string | number | null>;
export function DataChart({
  title,
  data,
  x,
  series,
  bar = false,
  limit,
  saturation,
}: {
  title: string;
  data: ChartRow[];
  x: string;
  series: string[];
  bar?: boolean;
  limit?: number;
  saturation?: number;
}) {
  function download() {
    const keys = [x, ...series];
    const csv = [keys, ...data.map((row) => keys.map((k) => row[k] ?? ""))]
      .map((row) =>
        row.map((v) => `"${String(v).replaceAll('"', '""')}"`).join(","),
      )
      .join("\n");
    const url = URL.createObjectURL(
      new Blob([csv], { type: "text/csv;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = title.toLowerCase().replace(/\W+/g, "_") + ".csv";
    link.click();
    URL.revokeObjectURL(url);
  }
  const colors = [
    "var(--accent)",
    "var(--hold)",
    "var(--pass)",
    "var(--fail)",
    "var(--ink-2)",
  ];
  return (
    <section className="panel chart-panel">
      <div className="panel-head">
        <h2>{title}</h2>
        <Button variant="secondary" onClick={download}>
          Download CSV
        </Button>
      </div>
      <div
        className="chart"
        role="img"
        aria-label={`${title}. Data table follows the chart.`}
      >
        <ResponsiveContainer width="100%" height={280}>
          {bar ? (
            <BarChart data={data}>
              <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
              <XAxis dataKey={x} />
              <YAxis />
              <Tooltip />
              <Legend />
              {series.map((key, i) => (
                <Bar key={key} dataKey={key} fill={colors[i % colors.length]} />
              ))}
            </BarChart>
          ) : (
            <LineChart data={data}>
              <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
              <XAxis dataKey={x} />
              <YAxis />
              <Tooltip />
              <Legend />
              {saturation != null && (
                <ReferenceLine
                  x={saturation}
                  stroke="var(--accent)"
                  strokeDasharray="4 4"
                  label="Plan saturation"
                />
              )}
              {limit != null && (
                <ReferenceLine
                  y={limit}
                  stroke="var(--hold)"
                  strokeDasharray="4 4"
                  label="5% limit"
                />
              )}
              {series.map((key, i) => (
                <Line
                  key={key}
                  type="linear"
                  dataKey={key}
                  stroke={colors[i % colors.length]}
                  strokeWidth={key === "DB Performance Advisor" ? 3 : 1.5}
                  dot={true}
                  connectNulls={false}
                />
              ))}
            </LineChart>
          )}
        </ResponsiveContainer>
      </div>
      <details>
        <summary>Text table alternative</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {[x, ...series].map((k) => (
                  <th key={k} scope="col">
                    {k}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.map((row, i) => (
                <tr key={i}>
                  {[x, ...series].map((k) => (
                    <td key={k}>{row[k] ?? "—"}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}
