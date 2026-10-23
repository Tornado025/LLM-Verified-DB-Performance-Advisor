# Step 19 — Experiments RQ1–RQ5, baselines, reproduction command

**Goal (end state):** `python backend/scripts/run_experiments.py --all` (or `--rq N`) produces every result file under
`experiments/results/` with metadata (seed, git commit, environment report), and the frontend Experiments page renders them.
Baselines (Dexter, Supabase `index_advisor`, ungated LLM, literature decision rules) are re-run in your environment.
A short `experiments/README.md` explains how to reproduce each table and figure.

**Prerequisites:** steps 06, 07, 10–14, 18. Scale and noise settings final (record in `PROGRESS.md`).

## 19.1 Common protocol (applies to all experiments)

1. **Environment report** (`scripts/env_report.py`): CPU model/cores, RAM, OS, Docker version, PostgreSQL version and key settings (`shared_buffers`, etc.), dataset scale and DB size, git commit, Python package versions, LLM provider/model/temperature, seeds. Saved as `experiments/results/env.json` and embedded in each result's `meta`.
2. **Quiet machine:** close other apps; fixed CPU governor if possible; run on the same host for all experiments; note thermal throttling.
3. **Seeds:** top-level `--seed`; derive per-experiment seeds with `SeedSequence.spawn`.
4. **Repetitions:** ≥ 3 independent repetitions for experiments with real timing (report mean and range).
5. **Logging:** every run writes raw observations (paired values) to `experiments/results/raw/<exp>_<ts>.jsonl`; aggregates computed from raw by separate functions (so analysis can be re-run).
6. **Honest reporting:** report null and negative results; include false-block costs next to false-apply gains.

## 19.2 Ground truth for candidates (shared by RQ2/RQ3)

Create a **planted-candidate set** with known severity on the protected transactions:
* **Dial A — redundant write indexes:** add `k ∈ {0, 1, 2, 4, 8}` redundant indexes on `orders` (distinct column combinations including at least one on `status`/`delivered_at`) → graded write slowdown.
* **Dial B — HOT killer:** `CREATE INDEX ON store_stock (qty)`; `CREATE INDEX ON orders (status)`.
* **Dial C — harmless:** index on `customers(city)`, `riders(store_id)`.
For each planted candidate compute the **reference verdict**: 50+ paired blocks on a quiet machine on full clones under `normal` and `flash10`; `θ_ref = expm1(mean d)` with 95 % CI; label **harmful** if `θ_ref > δ` with CI lower bound > δ, **harmless** if `θ_ref < δ` with CI upper bound < δ, **borderline** otherwise (excluded from headline rates, reported separately). Save `ground_truth.json`.

## 19.3 RQ2 — guard accuracy under noise (primary result)

* **Simulation:** already produced by step 06 (`rq2_sim.json`). Re-run with final seeds and 1000 trials.
* **Real:** for noise ∈ {0, 5 %, 10 %} (`NoiseInjector`) and each planted candidate, run each decider on **fresh** measurements:
  `single_run`, `median5_band`, `threshold10`, `threshold2x`, `ttest_fixed`, `mars_guard` (all using the same raw paired blocks where possible: collect up to `MAX_BLOCKS` blocks once per (candidate, noise, repetition) and let each decider consume the prefix it needs).
* Metrics: false-apply (approved & harmful), false-block (blocked & harmless), runs/seconds used, borderline reported separately. Wilson 95 % CIs.
* Output `rq2_real.json` with the same shape as the simulation plus `n_candidates`, `repetitions`.
* **Equivalence sub-study:** run `check_rewrite` on all of `rewrite_pairs.json` with tiers truncated to {0–1}, {0–2}, {0–3}; compute false-accept rate (accepted & expected=rejected) and false-block (rejected & expected=equivalent) per tier and combined; also the same with an **LLM-judge-only** baseline (provider asked "equivalent?") and a **result-comparison-only** baseline (tier 3 only). Output `rq2_equivalence.json`.

## 19.4 RQ1 — rewrite vs index; validation cost

* **Rewrite vs index (Part 3 RQ1):** for each of the 8 shapes run the pipeline (mock **and** one real OpenRouter model) 3× and record: best verified rewrite speedup, best guarded index speedup, which was chosen, the order-processing cost saved when the rewrite wins (tpmC-like throughput, p95 `place_order`, lost HOT updates `n_tup_hot_upd/n_tup_upd` from `pg_stat_user_tables`, index size `pg_relation_size`), and how often `decide` chose rewrite vs index vs block. Output `rq1_rewrite_vs_index.json`.
* **Validation cost (Part 1 RQ1):** compare minutes per verdict for (a) exhaustive replay (no filter, no early stopping, `MAX_BLOCKS` blocks every profile on a full clone) vs (b) the pipeline (filter + dedupe + early stopping + racing); also **verdict agreement** between (a) and (b). Record clone create seconds and bytes. Output `rq1_cost.json`.

## 19.5 RQ3 — metric stability
* For 3 shapes × 20 repetitions measure the verdict (`PASS/FAIL/BLOCK`) using `time` vs `buffers` (and `time` with buffer screening) at noise 0/5/10 %. Report the **flip rate** (verdict differs across repetitions) and runs needed. Output `rq3_metrics.json`.

## 19.6 RQ4 — workload shift and plan saturation
* **Shapes:** group `questions.json` queries into shapes (sqlglot literal normalisation); report shape count, per-day counts, day-over-day coverage (`day N+1` queries whose shape appeared on days ≤ N); compare with Redset statistics from step 18. Output `rq4_shapes.json`.
* **Speedup vs k:** for each shape run the proposer with `k_max=15`, `patience` disabled, 5 seeds; after each sample compute the best verified speedup so far (reuse cached race results by candidate signature); mark where `patience=3` saturation would have stopped. Output `rq4_k.json` (per k: median/min/max best speedup, distinct plans).

## 19.7 RQ5 — flash-sale validity (headline)
* **Verdict flip:** for every candidate of every shape (real LLM proposals + planted ones) run the guard under all four profiles **independently** (no early stop across profiles) and compute: `normal_pass`, and for each flash profile `pass/fail/block`. Flip rate = P(flash fail or block | normal pass) per multiplier and per change type (index on updated column vs insert-only column vs rewrite/stat).
* **Spike-size sweep:** for 3 flip-prone candidates run multipliers {2, 5, 10, 20, 30} (custom workload only) and plot `upper` bound vs multiplier (find the multiplier where the bound crosses δ).
* **Ablation:** "normal-day-only validation" vs "normal + flash": how many unsafe changes would each let through.
* **Stale statistics:** `flash20` with and without `ANALYZE` after the pre-sale load; report plan changes and latency differences.
* **CH-benCHmark confirmation:** repeat the planted candidates through BenchBase profiles (`--workload ch`) and report agreement with the custom workload.
Output `rq5_flash.json`, `rq5_sweep.json`, `rq5_ablation.json`.

## 19.8 Baselines
* **Dexter:** install per its README; run against `qc_live` with the same workload statements (feed via `pg_stat_statements` or query log) → record its index suggestions, then evaluate them with the **same guard** (to measure how often a classic advisor's suggestions would have violated δ under flash load).
* **Supabase `index_advisor`:** create the extension in a clone; call it on each shape's SQL; evaluate suggestions with the guard.
* **Ungated LLM:** apply the LLM's top candidate per shape (no gate); measure report speedup and order-transaction regressions (the harm the gate prevents).
* **Rule-only rewrites:** apply a small hand-written rule set (the 3 classes) without LLM; compare coverage.
* **Decision rules from the literature:** implemented in `simulate.py` and reused on real raw blocks (19.3).
* Fair comparison: start from a sensibly configured database (e.g. FK indexes missing is the intended tuning opportunity — document it) and the same hardware.
Outputs `baselines.json` (per method: suggestions, speedups, guard outcomes, harm counts).

## 19.9 Headline metrics table (build `experiments/results/summary.json` + `summary.md`)
Report speedup, order throughput (tps / tpmC-like), p95/p99 latency of `place_order` and `order_status`, write overhead avoided (HOT ratio, index MB), wrong-result rate (equivalence), false-apply / false-block (RQ2), verdict-flip rate (RQ5), validation minutes per verdict (RQ1), LLM tokens and cost, clone cost.

## 19.10 `scripts/run_experiments.py`
CLI: `--all`, `--rq {1,2,3,4,5}`, `--sim-only`, `--scale-check`, `--repetitions N`, `--seed`, `--llm mock|openrouter`, `--workload custom|ch`, `--quick` (small trials for smoke testing).
Each experiment function writes its JSON atomically, prints a console table, appends a line to `docs/PROGRESS.md` (optional flag), and stores a copy in the SQLite `experiments` table for the API.
`--quick` must finish in < 15 minutes and is used by CI/smoke tests with reduced blocks.

## 19.11 Tests (`tests/test_experiments_smoke.py`, `@slow`)
* `--quick --rq 2 --sim-only` runs and writes valid JSON with expected keys.
* Each result writer validates against a small JSON schema (keys, types, ranges).
* `ground_truth` classification function unit-tested on synthetic data (harmful/harmless/borderline).

## 19.12 Done checklist
- [ ] All result files produced from a clean checkout with one command (document runtime)
- [ ] Each figure has a source JSON + script; tables in `summary.md`
- [ ] Baseline comparison table complete
- [ ] Commit `step 19: experiments`
