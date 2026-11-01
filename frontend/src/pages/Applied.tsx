import { useState } from "react";
import { listApplied, revert, getTraffic, traffic } from "../api";
import { useApi } from "../hooks/useApi";
import { usePolling } from "../hooks/usePolling";
import { PageTitle, Button, EmptyState, Modal } from "../components/UI";
import { Badge } from "../components/Badge";
import { ErrorBanner } from "../components/ErrorBanner";
import { DataChart } from "../components/DataChart";
import { useToast } from "../components/Toast";
import type { Applied as AppliedRow } from "../types";
export default function Applied() {
  const changes = useApi(listApplied),
    live = useApi(getTraffic);
  usePolling(async () => {
    changes.reload();
    live.reload();
  }, 5000);
  const [selected, setSelected] = useState("");
  const [profile, setProfile] = useState("normal");
  const [confirm, setConfirm] = useState<AppliedRow>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error>();
  const toast = useToast();
  const row = changes.data?.applied.find((r) => r.id === selected);
  const [group, setGroup] = useState("");
  const groups = row?.baseline?.groups ?? {};
  const key = group in groups ? group : Object.keys(groups)[0];
  const baseline = groups[key]?.mean_ms;
  const chart =
    row?.windows.map((w, i) => {
      const cell = (
        w.groups as Record<string, { mean_ms?: number | null }> | undefined
      )?.[key];
      return {
        window: i + 1,
        "Current statement mean (ms)": cell?.mean_ms ?? null,
        "Baseline mean (ms)": baseline ?? null,
      };
    }) ?? [];
  async function control() {
    setBusy(true);
    try {
      await traffic({
        action: live.data?.running ? "stop" : "start",
        profile,
        seconds: 60,
      });
      live.reload();
      toast(
        live.data?.running ? "Live traffic stopped." : "Live traffic started.",
      );
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        title="Applied changes"
        description="Approved changes, live statement monitoring, and reversible actions."
      />
      <section className="panel panel-body">
        <h2>Live traffic</h2>
        {live.data?.running && (
          <div className="notice">
            Live traffic is running on qc_live. Applying a change while it runs
            affects baseline measurements.
          </div>
        )}
        <div className="actions">
          <label>
            Profile{" "}
            <select
              value={profile}
              onChange={(e) => setProfile(e.target.value)}
              disabled={live.data?.running}
            >
              {["normal", "flash5", "flash10", "flash20"].map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </label>
          <Button
            variant={live.data?.running ? "danger" : "secondary"}
            loading={busy}
            onClick={() => void control()}
          >
            {live.data?.running ? "Stop live traffic" : "Start live traffic"}
          </Button>
          <Badge tone={live.data?.running ? "hold" : "muted"}>
            {live.data?.running ? "Running" : "Stopped"}
          </Badge>
        </div>
        <p>These controls generate ordinary order transactions on qc_live.</p>
      </section>
      {(error || changes.error || live.error) && (
        <ErrorBanner error={error ?? changes.error ?? live.error} />
      )}
      <section className="panel table-scroll spaced">
        {!changes.data?.applied.length ? (
          <EmptyState
            title="No approved changes yet"
            body="A selected candidate appears here after you approve it from its evidence card."
          />
        ) : (
          <table>
            <thead>
              <tr>
                {[
                  "Change",
                  "Applied at",
                  "State",
                  "Latest window",
                  "Action",
                ].map((t) => (
                  <th scope="col" key={t}>
                    {t}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {changes.data.applied.map((r) => (
                <tr key={r.id}>
                  <td>
                    <button
                      className="text-button"
                      onClick={() => setSelected(r.id)}
                    >
                      {r.kind} · {r.candidate_id}
                    </button>
                    <details>
                      <summary>Approved SQL</summary>
                      <pre>{r.ddl ?? r.rewrite_sql}</pre>
                    </details>
                  </td>
                  <td>{new Date(r.applied_at).toLocaleString()}</td>
                  <td>
                    <Badge
                      tone={
                        r.state === "healthy"
                          ? "pass"
                          : r.state === "revert_failed"
                            ? "fail"
                            : "hold"
                      }
                    >
                      {r.state.replaceAll("_", " ")}
                    </Badge>
                  </td>
                  <td>
                    {r.latest_window
                      ? String(r.latest_window.state)
                      : "No monitoring window"}
                  </td>
                  <td>
                    <Button
                      variant="secondary"
                      disabled={
                        r.state === "reverted" ||
                        (r.kind === "stats" &&
                          !r.ddl?.toUpperCase().startsWith("CREATE STATISTICS"))
                      }
                      onClick={() => setConfirm(r)}
                    >
                      Revert change
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      {row && (
        <section className="panel panel-body spaced">
          <div className="actions">
            <h2>Statement monitoring</h2>
            <Button variant="secondary" onClick={() => setSelected("")}>
              Close detail
            </Button>
          </div>
          <p>
            Statement means differ from the full transaction latency used by the
            guard. Rewrites are stored for application adoption and have no
            database monitor.
          </p>
          {Object.keys(groups).length > 0 ? (
            <>
              <label>
                Statement group{" "}
                <select value={key} onChange={(e) => setGroup(e.target.value)}>
                  {Object.keys(groups).map((k) => (
                    <option key={k}>{k}</option>
                  ))}
                </select>
              </label>
              {chart.length ? (
                <DataChart
                  title="Statement latency (ms)"
                  data={chart}
                  x="window"
                  series={["Current statement mean (ms)", "Baseline mean (ms)"]}
                  limit={baseline == null ? undefined : baseline * 1.05}
                />
              ) : (
                <p>No monitoring windows are available.</p>
              )}
            </>
          ) : (
            <p>No database change was applied for this rewrite.</p>
          )}
        </section>
      )}
      {confirm && (
        <Modal
          title="Revert approved change"
          onClose={() => setConfirm(undefined)}
        >
          <p>
            {confirm.kind === "rewrite"
              ? "Withdraw this approved rewrite from the advisor. Application adoption must be managed separately."
              : "Remove this managed database change from qc_live."}
          </p>
          <pre>{confirm.ddl ?? confirm.rewrite_sql}</pre>
          <div className="actions">
            <Button variant="secondary" onClick={() => setConfirm(undefined)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await revert(confirm.id, "human requested from UI");
                  setConfirm(undefined);
                  changes.reload();
                  toast("Change reverted.");
                } catch (e) {
                  setError(e as Error);
                } finally {
                  setBusy(false);
                }
              }}
            >
              Confirm revert
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}
