# Step 06 — RQ2 simulation (`app/simulate.py`)

**Goal (end state):** `python -m app.simulate --trials 400 --seed 1234 --out ../experiments/results/rq2_sim.json`
runs a database-free Monte-Carlo and writes the RQ2 data: false-apply rate, false-block rate and mean runs
per decider at noise 0 / 5 / 10 %. The MARS guard's false-apply stays low and roughly flat while simple
rules climb. `GET /api/experiments/rq2` (step 15) will serve this file.

**Prerequisites:** step 05.

## 6.1 Model of one candidate

* True relative change `θ` of the candidate vs baseline on a protected transaction.
  * **Harmful** (violates δ = 0.05): `θ ∈ {0.07, 0.10, 0.15, 0.25}` (equal probability).
  * **Harmless** (acceptable): `θ ∈ {0.00, 0.01, 0.02}` (equal probability).
  * Optional **borderline** set `θ ∈ {0.04, 0.05, 0.06}` reported separately, never in headline rates.
* Per paired run `i`:
  `a_i = base · exp(e_s + e_a)`, `b_i = base · (1 + θ) · exp(e_s + e_b)` with
  `e_s ~ N(0, (0.5·noise)²)` (shared, cancels in pairs), `e_a, e_b ~ N(0, noise²)` (independent).
  `base = 100` ms. Noise levels `{0.0, 0.05, 0.10}` (plus an optional `0.20` stress level).
* Interleaving effect is already captured by the shared component.

## 6.2 Deciders (each returns `approve: bool` and `runs_used: int`)

| Name | Rule | Runs |
|---|---|---|
| `single_run` | approve iff `b/a − 1 ≤ δ` on one pair | 1 |
| `median5_band` | 5 pairs; median relative change; approve unless median `> +5 %` (Microsoft-style ±5 % band) | 5 |
| `threshold10` | 5 pairs median; approve unless worse than `+10 %` (QUITE-style) | 5 |
| `threshold2x` | 5 pairs median; approve unless worse than `+100 %` (QO-Verify-style 2×) | 5 |
| `ttest_fixed` | 10 pairs; one-sided paired t-test; **block only if significantly worse** (p < 0.05); fails open | 10 |
| `mars_guard` | `SequentialTest` from `stats.py` (min_n 6, max_n 60, batch 3, δ, α, looks computed, family = 1); approve iff state `PASS`; `FAIL` or `BLOCK` ⇒ block | variable |

All deciders must use the **same** underlying random draws per trial where possible (generate 60 pairs per trial
once; each decider consumes the prefix it needs) so comparisons are paired.

## 6.3 Outputs

For each `(noise, decider)`:
* `false_apply` = P(approve | harmful), `false_block` = P(block | harmless), `mean_runs`, `runs_p90`.
* Wilson 95 % confidence intervals for both rates.
* Also store by-θ breakdown: `by_theta[θ] = {approve_rate, mean_runs}` — useful for the sensitivity figure.

JSON shape:
```json
{
  "meta": {"trials": 400, "seed": 1234, "delta": 0.05, "alpha": 0.05, "noise_levels": [0.0,0.05,0.10],
           "harmful": [0.07,0.10,0.15,0.25], "harmless": [0.0,0.01,0.02], "created_at": "…"},
  "results": {"0.05": {"mars_guard": {"false_apply": 0.003, "false_apply_ci": [0.0,0.01],
              "false_block": 0.21, "false_block_ci": [..], "mean_runs": 31.2, "runs_p90": 54,
              "by_theta": {"0.07": {"approve_rate": 0.0, "mean_runs": 28.0}}}}}
}
```

## 6.4 Implementation steps

1. `simulate_pairs(rng, theta, noise, n)` → `(a, b)` arrays.
2. `decide_*` functions for each decider (pure; take arrays).
3. `run_cell(rng, noise, thetas, trials)` loops trials; for each θ draw `trials` experiments.
4. `wilson(k, n)` helper.
5. `run_rq2(trials, seed, noise_levels, ...) -> dict`; seed per cell derived via `np.random.SeedSequence(seed).spawn(...)` so adding a noise level does not change earlier results.
6. CLI with `argparse`: `--trials`, `--seed`, `--out`, `--noise 0,0.05,0.1`, `--delta`, `--alpha`.
7. Print a compact table to stdout; write JSON atomically (write to `*.tmp` then rename).

## 6.5 Tests (`tests/test_simulate.py`)

* `test_reproducible`: same seed ⇒ identical JSON (excluding `created_at`).
* `test_shapes`: JSON keys present for every noise level and decider.
* `test_mars_false_apply_bounded`: at noise 0.10 with 300 trials `mars_guard.false_apply < 0.02`.
* `test_simple_rules_climb`: `single_run.false_apply(0.10) > single_run.false_apply(0.0)`.
* `test_no_noise_sanity`: at noise 0 every decider except `threshold10/2x` rejects θ = 0.25 almost always.
* `test_false_block_cost_reported`: `mars_guard.false_block(0.10) >= mars_guard.false_block(0.0)` (the price of fail-closed).
* `test_runtime`: 400 trials × 3 noise levels finishes in < 60 s (use cached t-quantiles).

## 6.6 Interpreting results (write into `docs/PROGRESS.md`)
* The headline claim is **not** "MARS never errs"; it is: false-apply stays flat as noise rises, at a measured cost in false blocks and runs. Include that cost.
* Report borderline θ separately (no method can reliably decide θ ≈ δ).

## 6.7 Done checklist
- [ ] `experiments/results/rq2_sim.json` created and committed
- [ ] All tests pass
- [ ] A matplotlib or Recharts-ready summary table printed
- [ ] Commit `step 06: rq2 simulation`
