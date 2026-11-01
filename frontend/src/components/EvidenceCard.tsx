import { useState } from "react";
import { Link } from "react-router-dom";
import type { Candidate, Evidence } from "../types";
import { approve, blockCandidate, getCandidate } from "../api";
import { Button, Modal } from "./UI";
import { ErrorBanner } from "./ErrorBanner";
import { GuardGrid } from "./GuardGrid";
import { PlanDiff, LineDiff } from "./PlanDiff";
import { CIBar } from "./CIBar";
import { useToast } from "./Toast";
export function EvidenceCard({
  card,
  candidate,
  onChanged,
}: {
  card: Evidence;
  candidate?: Candidate;
  onChanged?: () => void;
}) {
  const c =
    candidate ??
    card.candidates.find((c) => c.id === card.decision.candidate_id) ??
    card.candidates[0];
  const [preview, setPreview] = useState<Candidate>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error>();
  const [confirmed, setConfirmed] = useState(false);
  const toast = useToast();
  const canApprove =
    !!c &&
    c.id === card.decision.candidate_id &&
    ["selected", "approved"].includes(c.status);
  const weak = !!c?.equiv?.weak || !!card.decision.requires_confirmation;
  async function prepare() {
    if (!c) return;
    setLoading(true);
    setError(undefined);
    try {
      setPreview(await getCandidate(c.id));
    } catch (e) {
      setError(e as Error);
    } finally {
      setLoading(false);
    }
  }
  async function apply() {
    if (!c) return;
    setLoading(true);
    try {
      await approve(c.id, confirmed);
      setPreview(undefined);
      toast("Approved change recorded. View it under Applied.");
      onChanged?.();
    } catch (e) {
      setError(e as Error);
    } finally {
      setLoading(false);
    }
  }
  async function block() {
    if (!c) return;
    setLoading(true);
    try {
      await blockCandidate(c.id);
      toast("Change blocked by human review.");
      onChanged?.();
    } catch (e) {
      setError(e as Error);
    } finally {
      setLoading(false);
    }
  }
  return (
    <article className="panel evidence-card">
      <div className="panel-body">
        <div className="eyebrow">Evidence card · {card.shape_id}</div>
        <h2>
          {card.decision.action === "block"
            ? "No candidate proven safe"
            : card.decision.action === "apply_rewrite"
              ? "Tested rewrite recommended"
              : "Guarded index recommended"}
        </h2>
        <p>{card.decision.reason}</p>
        <div className="actions">
          <Button
            disabled={!canApprove}
            loading={loading}
            onClick={() => void prepare()}
          >
            Approve and apply
          </Button>
          {c &&
            !["applied", "reverted", "blocked_by_human"].includes(c.status) && (
              <Button
                variant="secondary"
                disabled={loading}
                onClick={() => void block()}
              >
                Block this change
              </Button>
            )}
          <Link to="/applied">View applied changes →</Link>
        </div>
        {weak && (
          <label className="inline-check">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            I reviewed the weak equivalence evidence and confirm this rewrite.
          </label>
        )}
        {error && <ErrorBanner error={error} />}
      </div>
      {c && (
        <>
          <details open>
            <summary>What changed</summary>
            <div className="details-body">
              <p>{c.rationale}</p>
              <div className="sql-pair">
                <div>
                  <h3>Original SQL</h3>
                  <LineDiff text={card.baseline.sql} other={c.sql} />
                </div>
                <div>
                  <h3>Candidate SQL</h3>
                  <LineDiff text={c.sql} other={card.baseline.sql} />
                </div>
              </div>
              <Button
                variant="secondary"
                onClick={() => {
                  navigator.clipboard
                    .writeText(c.sql)
                    .then(() => toast("SQL copied."))
                    .catch(() =>
                      toast("Could not copy SQL. Select it manually."),
                    );
                }}
              >
                Copy candidate SQL
              </Button>
            </div>
          </details>
          <details open>
            <summary>Plan</summary>
            <div className="details-body">
              <PlanDiff
                before={c.plan_before ?? card.baseline.plan}
                after={c.plan_after}
                signature={c.signature}
              />
            </div>
          </details>
          <details open>
            <summary>Equivalence</summary>
            <div className="details-body">
              {c.equiv ? (
                <>
                  <p>
                    {c.equiv.label} · rewrite class{" "}
                    {c.equiv.rewrite_class ?? "unknown"} · {c.equiv.cx_dbs}{" "}
                    counterexample databases · non-vacuous{" "}
                    {c.equiv.nonvacuous_fraction == null
                      ? "unknown"
                      : `${(c.equiv.nonvacuous_fraction * 100).toFixed(1)}%`}
                  </p>
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th scope="col">Tier</th>
                          <th scope="col">Reached</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[
                          "Parse / schema",
                          "Output / class",
                          "Counterexamples / time zones",
                          "Full comparison",
                        ].map((t, i) => (
                          <tr key={t}>
                            <th scope="row">
                              {i} · {t}
                            </th>
                            <td>
                              {i <= c.equiv!.tier_reached ? "✓ reached" : "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {c.equiv.reason && <p>{c.equiv.reason}</p>}
                  {c.equiv.counterexample && (
                    <details>
                      <summary>Counterexample: time zone and rows</summary>
                      <pre>
                        {JSON.stringify(c.equiv.counterexample, null, 2)}
                      </pre>
                    </details>
                  )}
                </>
              ) : (
                <p>Not required for an index or statistics change.</p>
              )}
            </div>
          </details>
          <details open>
            <summary>Report speed</summary>
            <div className="details-body">
              {c.speedup ? (
                <>
                  <CIBar
                    lower={c.speedup.lower}
                    upper={c.speedup.upper}
                    mean={c.speedup.mean_rel}
                    delta={0}
                    n={c.speedup.n}
                    state={c.speedup.state}
                    metric={c.speedup.metric}
                    showAxis
                    label="Report speed change"
                  />
                  <p>
                    {c.speedup.n} pairs ·{" "}
                    {c.speedup.metric ?? card.settings.metric} metric · negative
                    is faster.
                  </p>
                </>
              ) : (
                <p>No report-speed measurement saved.</p>
              )}
            </div>
          </details>
          <details open>
            <summary>Order-processing guard</summary>
            <div className="details-body">
              <GuardGrid guard={c.guard} delta={card.settings.delta} />
            </div>
          </details>
        </>
      )}
      <details open>
        <summary>Validation budget</summary>
        <dl className="metric-grid">
          <div>
            <dt>Elapsed</dt>
            <dd>{card.budget.seconds.toFixed(1)} s</dd>
          </div>
          <div>
            <dt>Paired runs</dt>
            <dd>{card.budget.paired_runs}</dd>
          </div>
          <div>
            <dt>LLM calls</dt>
            <dd>{card.budget.llm_calls}</dd>
          </div>
          <div>
            <dt>Clones</dt>
            <dd>{card.budget.clones_created}</dd>
          </div>
          <div>
            <dt>Reported LLM cost</dt>
            <dd>
              {card.propose?.usage?.cost == null
                ? "Unknown"
                : `$${card.propose.usage.cost.toFixed(6)}`}
            </dd>
          </div>
        </dl>
      </details>
      {preview && (
        <Modal
          title={
            c?.kind === "rewrite"
              ? "Approve rewrite"
              : "Approve live database change"
          }
          onClose={() => setPreview(undefined)}
        >
          <p>
            {c?.kind === "rewrite"
              ? "This stores the approved rewrite for adoption in the application; it does not execute the SELECT."
              : `This exact statement will run on ${preview.target_database ?? "qc_live"}. Live baseline traffic may run before the change.`}
          </p>
          <pre>{preview.apply_preview ?? "Preview unavailable"}</pre>
          {weak && (
            <p>
              Weak equivalence requires the confirmation checkbox outside this
              dialog.
            </p>
          )}
          <div className="actions">
            <Button variant="secondary" onClick={() => setPreview(undefined)}>
              Cancel
            </Button>
            <Button
              loading={loading}
              disabled={!preview.apply_preview || (weak && !confirmed)}
              onClick={() => void apply()}
            >
              Confirm approval{c?.kind === "rewrite" ? "" : " and apply"}
            </Button>
          </div>
        </Modal>
      )}
    </article>
  );
}
