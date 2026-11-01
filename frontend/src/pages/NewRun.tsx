import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { createJob, getShapes, getSettings } from "../api";
import { useApi } from "../hooks/useApi";
import { PageTitle, Field, Button } from "../components/UI";
import { ErrorBanner } from "../components/ErrorBanner";
import type { JobRequest } from "../types";
export default function NewRun() {
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const reports = useApi(getShapes),
    settings = useApi(getSettings);
  const [selected, setSelected] = useState<string[]>(
    search.get("shape") ? [search.get("shape")!] : [],
  );
  const [profiles, setProfiles] = useState([
    "normal",
    "flash5",
    "flash10",
    "flash20",
  ]);
  const [delta, setDelta] = useState(5);
  const [alpha, setAlpha] = useState(0.05);
  const [metric, setMetric] = useState<"time" | "buffers">("time");
  const [minutes, setMinutes] = useState(30);
  const [llm, setLlm] = useState<"mock" | "openrouter" | "ollama">("mock");
  const [k, setK] = useState(10);
  const [patience, setPatience] = useState(3);
  const [ratio, setRatio] = useState(0.8);
  const [seed, setSeed] = useState(1234);
  const [noise, setNoise] = useState(0);
  const [workload, setWorkload] = useState<"custom" | "ch">("custom");
  const [dry, setDry] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error>();
  const [invalid, setInvalid] = useState(false);
  const toggle = (value: string, old: string[], set: (v: string[]) => void) =>
    set(old.includes(value) ? old.filter((v) => v !== value) : [...old, value]);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!selected.length || !profiles.length) {
      setInvalid(true);
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const result = await createJob({
        shape_ids: selected,
        profiles: profiles as JobRequest["profiles"],
        delta: delta / 100,
        alpha,
        metric,
        budget_s: minutes * 60,
        llm,
        k_max: k,
        patience,
        prefer_ratio: ratio,
        seed,
        noise,
        workload,
        dry_run: dry,
        guard_stat: "mean",
      });
      navigate(`/runs/${result.job_id}`);
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        title="New run"
        description="Set the verification policy. Every proposed change must earn its evidence."
      />
      <form onSubmit={(e) => void submit(e)} className="run-form">
        <fieldset>
          <legend>1 · Report shapes</legend>
          <label className="inline-check">
            <input
              type="checkbox"
              checked={
                !!reports.data?.reports.length &&
                selected.length === reports.data.reports.length
              }
              onChange={(e) =>
                setSelected(
                  e.target.checked
                    ? (reports.data?.reports.map((s) => s.id) ?? [])
                    : [],
                )
              }
            />
            Select all reports
          </label>
          <div className="shape-checks">
            {reports.data?.reports.map((s) => (
              <label key={s.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(s.id)}
                  onChange={() => toggle(s.id, selected, setSelected)}
                />
                <span>
                  <strong>{s.name}</strong>
                  <small>{s.question}</small>
                </span>
              </label>
            ))}
          </div>
          {invalid && !selected.length && (
            <p role="alert" className="field-error">
              Select at least one report.
            </p>
          )}
          {reports.error && <ErrorBanner error={reports.error} />}
        </fieldset>
        <fieldset>
          <legend>2 · Verification policy</legend>
          <div className="form-grid">
            <Field
              label="Maximum slowdown δ (%)"
              hint="A change passes only when its upper bound is below this limit."
            >
              {(id, hint) => (
                <input
                  id={id}
                  aria-describedby={hint}
                  type="number"
                  min={1}
                  max={20}
                  step={0.5}
                  value={delta}
                  onChange={(e) => setDelta(Number(e.target.value))}
                />
              )}
            </Field>
            <label className="range-label">
              Adjust slowdown limit
              <input
                aria-label="Slowdown limit slider"
                type="range"
                min={1}
                max={20}
                step={0.5}
                value={delta}
                onChange={(e) => setDelta(Number(e.target.value))}
              />
            </label>
            <Field
              label="Error probability α"
              hint="Family and repeated-look corrections are applied by the verifier."
            >
              {(id, hint) => (
                <input
                  id={id}
                  aria-describedby={hint}
                  type="number"
                  min={0.001}
                  max={0.2}
                  step={0.001}
                  value={alpha}
                  onChange={(e) => setAlpha(Number(e.target.value))}
                />
              )}
            </Field>
            <Field label="Report measurement metric">
              {(id) => (
                <select
                  id={id}
                  value={metric}
                  onChange={(e) => setMetric(e.target.value as typeof metric)}
                >
                  <option value="time">Execution time (ms)</option>
                  <option value="buffers">Buffer reads</option>
                </select>
              )}
            </Field>
          </div>
        </fieldset>
        <fieldset>
          <legend>3 · Load profiles</legend>
          <div className="actions">
            {["normal", "flash5", "flash10", "flash20"].map((p, i) => (
              <label className="inline-check" key={p}>
                <input
                  type="checkbox"
                  checked={profiles.includes(p)}
                  onChange={() => toggle(p, profiles, setProfiles)}
                />
                {p} · {[1, 5, 10, 20][i]}× orders
              </label>
            ))}
          </div>
          {invalid && !profiles.length && (
            <p className="field-error">Select at least one profile.</p>
          )}
          <p>
            Minimum scheduled guard traffic per index candidate ≈{" "}
            {(
              (2 *
                Number(settings.data?.block_seconds ?? 8) *
                Number(settings.data?.min_runs ?? 6) *
                profiles.length) /
              60
            ).toFixed(1)}{" "}
            min, plus setup and extra measurements. This is not a maximum
            runtime.
          </p>
        </fieldset>
        <fieldset>
          <legend>4 · Budget and model</legend>
          <div className="form-grid">
            <Field label="Job budget (minutes)">
              {(id) => (
                <input
                  id={id}
                  type="number"
                  min={1}
                  max={240}
                  value={minutes}
                  onChange={(e) => setMinutes(Number(e.target.value))}
                />
              )}
            </Field>
            <Field label="LLM provider">
              {(id) => (
                <select
                  id={id}
                  value={llm}
                  onChange={(e) => setLlm(e.target.value as typeof llm)}
                >
                  <option value="mock">Mock · no API cost</option>
                  <option value="openrouter">OpenRouter</option>
                  <option value="ollama">Ollama</option>
                </select>
              )}
            </Field>
            <Field label="Configured model">
              {(id) => (
                <input
                  id={id}
                  readOnly
                  value={
                    llm === "mock"
                      ? "mock"
                      : String(
                          settings.data?.[
                            llm === "openrouter"
                              ? "openrouter_model"
                              : "ollama_model"
                          ] ?? "Not configured",
                        )
                  }
                />
              )}
            </Field>
          </div>
        </fieldset>
        <details>
          <summary>Advanced settings</summary>
          <div className="form-grid details-body">
            {[
              ["Maximum samples", k, setK, 1, 20, 1],
              ["Saturation patience", patience, setPatience, 1, 10, 1],
              ["Rewrite preference ratio", ratio, setRatio, 0.5, 1, 0.01],
              ["Seed", seed, setSeed, 0, 2147483647, 1],
              ["Noise duty", noise, setNoise, 0, 0.2, 0.01],
            ].map(([label, value, set, min, max, step]) => (
              <Field key={String(label)} label={String(label)}>
                {(id) => (
                  <input
                    id={id}
                    type="number"
                    value={Number(value)}
                    min={Number(min)}
                    max={Number(max)}
                    step={Number(step)}
                    onChange={(e) =>
                      (set as (n: number) => void)(Number(e.target.value))
                    }
                  />
                )}
              </Field>
            ))}
            <Field label="Workload runner">
              {(id) => (
                <select
                  id={id}
                  value={workload}
                  onChange={(e) =>
                    setWorkload(e.target.value as typeof workload)
                  }
                >
                  <option value="custom">Quick-commerce</option>
                  <option value="ch">
                    CH-benCHmark (requires Java runtime)
                  </option>
                </select>
              )}
            </Field>
            <label className="inline-check">
              <input
                type="checkbox"
                checked={dry}
                onChange={(e) => setDry(e.target.checked)}
              />
              Dry run · stop after filtering and equivalence
            </label>
          </div>
        </details>
        {error && <ErrorBanner error={error} />}
        <div className="submit-bar">
          <p>
            Starting a run verifies suggestions on isolated clones. Applying any
            change requires a separate human approval.
          </p>
          <Button loading={busy} type="submit">
            Start verification →
          </Button>
        </div>
      </form>
    </>
  );
}
