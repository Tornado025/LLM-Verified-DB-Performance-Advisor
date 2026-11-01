import { Link } from "react-router-dom";
import { getObserve, getShapes } from "../api";
import { useApi } from "../hooks/useApi";
import { PageTitle, Skeleton, EmptyState } from "../components/UI";
import { ErrorBanner } from "../components/ErrorBanner";
import { Badge } from "../components/Badge";
export default function Overview() {
  const observed = useApi(getObserve),
    shapes = useApi(getShapes);
  return (
    <>
      <PageTitle
        title="Overview"
        description="Recurring reports, measured workload, and evidence for your next decision."
        action={
          <Link className="button" to="/new">
            Start verification →
          </Link>
        }
      />
      <section className="panel">
        <div className="panel-head">
          <h2>Observed slow statements</h2>
          <Badge tone="info">Live PostgreSQL</Badge>
        </div>
        {observed.error ? (
          <ErrorBanner error={observed.error} />
        ) : observed.loading ? (
          <Skeleton />
        ) : !observed.data?.available ? (
          <EmptyState
            title="Query statistics unavailable"
            body="Enable pg_stat_statements and reload this page."
          />
        ) : !observed.data.statements.length ? (
          <EmptyState
            title="No statements recorded"
            body="Run an application query or start live traffic to collect observations."
          />
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {["Statement", "Calls", "Mean latency", "Total latency"].map(
                    (t) => (
                      <th scope="col" key={t}>
                        {t}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {observed.data.statements.slice(0, 12).map((s, i) => (
                  <tr key={String(s.queryid ?? i)}>
                    <td>
                      <Badge tone="muted">{String(s.tag ?? "untagged")}</Badge>
                      <details>
                        <summary>{String(s.query).slice(0, 95)}…</summary>
                        <pre>{String(s.query)}</pre>
                      </details>
                    </td>
                    <td>{Number(s.calls).toLocaleString()}</td>
                    <td>{Number(s.mean_ms).toFixed(2)} ms</td>
                    <td>{Number(s.total_ms).toFixed(1)} ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <div className="section-heading">
        <h2>Report shapes</h2>
        <p>Choose the recurring question you want to tune.</p>
      </div>
      {shapes.error && <ErrorBanner error={shapes.error} />}
      <div className="shape-grid">
        {shapes.data?.reports.map((s) => (
          <article className="panel shape-card" key={s.id}>
            <div className="eyebrow">{s.id.replaceAll("_", " ")}</div>
            <h3>{s.name}</h3>
            <p>{s.question}</p>
            <Link
              className="button secondary"
              to={`/new?shape=${encodeURIComponent(s.id)}`}
            >
              Tune this query <span className="sr-only">{s.name}</span> →
            </Link>
          </article>
        ))}
      </div>
    </>
  );
}
