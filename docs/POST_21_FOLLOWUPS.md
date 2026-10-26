# Deferred follow-ups (after Step 21)

This file tracks unresolved investigations that are intentionally deferred until
the acceptance checks for numbered steps 01–21 are complete.

## How to use this list

- These items do **not** change the numbered implementation sequence or block
  work on the next step.
- Do not start, expand, or resolve an item here while implementing a numbered
  step unless that step's own acceptance check requires it.
- After Step 21, review the list with the user and agree which items to pursue.
- Keep measurements and observed behavior intact; do not relabel an errored run
  as a successful performance result.

## Open investigations

### 1. BenchBase high-load PostgreSQL errors

**Status:** Deferred; investigation not started.

**Observed:** The Step 11 CH-benCHmark/TPC-C-derived smoke runs completed and
included all protected transaction sample types. Unexpected SQL errors were
counted by scenario:

| Scenario | Unexpected SQL errors |
|---|---:|
| normal | 0 |
| flash5 | 0 |
| flash10 | 1,430 |
| flash20 | 11,511 |
| flash20 with two active warehouses | 2 |

The PostgreSQL log reported `out of shared memory` and suggested reviewing
`max_pred_locks_per_transaction`; its observed value was 64. The BenchBase sample
configuration uses SERIALIZABLE transactions. The cause has **not** yet been
confirmed, so SERIALIZABLE isolation is a lead to examine, not a proven cause.

**Later investigation:** Check the exact transaction isolation and predicate
lock behavior for the failing procedures; determine whether the benchmark setup
matches the isolation behavior intended for the application; then, if needed,
run a separately recorded experiment that changes one variable at a time.
PostgreSQL setting changes are outside the numbered-step scope unless the user
later approves a dedicated follow-up experiment.

**Evidence:** `experiments/results/ch_smoke.json`,
`docs/PROGRESS.md` (Step 11 final verification, 2026-10-26), and the ignored raw
BenchBase logs under `benchbase/results/` on the machine that ran the tests.

**Do not claim:** The flash10/flash20 BenchBase runs were error-free, passed the
advisor's guard, or represent certified TPC-C results.

### 2. Host-noise variance test was inconclusive

**Status:** Deferred; the step's soft assertion is xfailed as allowed by its
acceptance instructions.

**Observed:** Three repetitions with two CPU interference workers did not
consistently increase paired measurement variance. The injector runs on macOS
while PostgreSQL runs inside Docker Desktop, so host interference is indirect.
The result does not establish that the injector reliably models a chosen level
of database latency noise.

**Later investigation:** On a controlled, repeatable environment, assess whether
the injector creates a measurable and repeatable change in paired variance. If
it does not, redesign or relabel the noise experiment and update its documented
interpretation. Do not turn the xfailed result into a pass or claim a monotonic
noise effect.

**Evidence:** `backend/tests/test_measure.py`,
`docs/steps/07_sandbox_measure_noise.md`,
`experiments/results/noise_report.json`, and `docs/PROGRESS.md` (Step 07 entry,
2026-10-24).

## Resolved issues retained for context

- Step 11 A/A timings were previously disturbed by template-copy checkpoints
  during measurement. Clone creation now uses PostgreSQL `STRATEGY FILE_COPY`,
  and the completed normal/flash5 A/A evidence passes. Do not reopen this unless
  new measurements show the same problem.
- BenchBase Java compatibility was resolved by using the pinned Java 23 runtime
  required by the selected upstream revision. The failed Java 27 build was a
  toolchain mismatch, not an open application defect.
