import { useState } from "react";
import { Link } from "react-router-dom";
import { listJobs } from "../api";
import { useApi } from "../hooks/useApi";
import { usePolling } from "../hooks/usePolling";
import { PageTitle, EmptyState, Skeleton } from "../components/UI";
import { Badge } from "../components/Badge";
import { ErrorBanner } from "../components/ErrorBanner";
export default function Runs() {
  const jobs = useApi(listJobs);
  usePolling(async () => jobs.reload(), 5000);
  const [filter, setFilter] = useState("all");
  return (
    <>
      <PageTitle
        title="Runs"
        description="Follow each verification from proposal to decision."
        action={
          <Link className="button" to="/new">
            New run →
          </Link>
        }
      />
      <label className="filter-label">
        Status{" "}
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          {["all", "queued", "running", "done", "failed", "cancelled"].map(
            (s) => (
              <option key={s}>{s}</option>
            ),
          )}
        </select>
      </label>
      {jobs.error ? (
        <ErrorBanner error={jobs.error} />
      ) : jobs.loading && !jobs.data ? (
        <Skeleton />
      ) : !jobs.data?.length ? (
        <EmptyState
          title="No runs yet"
          body="Choose a report to start collecting verification evidence."
          action={
            <Link to="/new" className="button">
              Start a run
            </Link>
          }
        />
      ) : (
        <section className="panel table-scroll">
          <table>
            <thead>
              <tr>
                {["Run", "Status", "Reports", "Started", "Decision"].map(
                  (t) => (
                    <th scope="col" key={t}>
                      {t}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {jobs.data
                .filter((j) => filter === "all" || j.status === filter)
                .map((j) => (
                  <tr key={j.id}>
                    <td>
                      <Link className="mono" to={`/runs/${j.id}`}>
                        {j.id}
                      </Link>
                    </td>
                    <td>
                      <Badge
                        tone={
                          j.status === "done"
                            ? "pass"
                            : j.status === "failed"
                              ? "fail"
                              : "hold"
                        }
                      >
                        {j.status}
                      </Badge>
                    </td>
                    <td>{j.params.shape_ids.join(", ")}</td>
                    <td>{new Date(j.created_at).toLocaleString()}</td>
                    <td>
                      {j.result?.seconds != null
                        ? `${Number(j.result.seconds).toFixed(1)} s`
                        : "Pending"}{" "}
                      ·{" "}
                      {j.result?.status
                        ? String(j.result.status)
                        : "Awaiting evidence"}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}
