# Step 14 — Apply, monitor, auto-revert (`app/monitor.py`)

**Goal (end state):** after human approval a selected index is created on `qc_live` with `CREATE INDEX CONCURRENTLY`, a rewrite is stored
for the application team, a baseline of `pg_stat_statements` counters is captured, a monitor thread evaluates windows every
`MONITOR_INTERVAL_S` and **auto-reverts** (`DROP INDEX CONCURRENTLY`) on confirmed regression. A scripted demo (planted bad index +
live traffic) shows detection and revert; manual revert works.

**Prerequisites:** steps 03, 11, 13.

## 14.1 Approval records and preconditions

`monitor.apply(store, candidate_id, approver="human")`:
1. Load candidate; require `status == "selected"` (or `approved`). Else `Conflict`.
2. Require the evidence card decision to reference this candidate (`decision.candidate_id`). Else `Conflict`.
3. For weak rewrites require `confirm=True` in the request.
4. Idempotency: if an `applied` row for the candidate exists and is not `reverted`, return it.
5. Re-run `safety.check` on the candidate SQL against the **live** catalog (defence in depth). Fail ⇒ `SafetyRejected`.
6. Check the live database still lacks an identical index (name/definition) ⇒ else `Conflict`.

## 14.2 Applying changes

**Index:**
* Choose a deterministic name: `dpa_<shape_id>_<8hex of sha1(ddl)>` (≤ 63 chars). Rewrite the DDL to include the name and `CONCURRENTLY`: `CREATE INDEX CONCURRENTLY dpa_… ON …`.
* Use a dedicated **autocommit** connection to `qc_live` (CONCURRENTLY cannot run in a transaction block). Set `lock_timeout = '5s'`, `statement_timeout = 0` (long builds allowed) but enforce a Python-side timeout of `APPLY_TIMEOUT_S` (default 1800).
* After success verify validity: `SELECT indisvalid FROM pg_index WHERE indexrelid = '<name>'::regclass`. If invalid ⇒ drop it, mark `revert_failed`/error, raise.
* `undo_ddl = DROP INDEX CONCURRENTLY IF EXISTS <name>`.

**Stats:** run `ANALYZE <table>` / `CREATE STATISTICS` (+ `ANALYZE`); `undo_ddl = DROP STATISTICS …` (or `NULL` for plain `ANALYZE`, which has no undo — state this in the UI).

**Rewrite:** nothing runs on the database. Store `rewrite_sql` (the parameterised rewrite template with placeholders restored where possible) and expose it in the API/UI as "approved rewrite — adopt in the application". Mark `state = "healthy"` immediately with `windows = []` and no auto-revert (no database change). Optionally the UI offers "copy SQL".

## 14.3 Baseline snapshot (before DDL)

For each **watched statement group** capture cumulative counters from `pg_stat_statements`:
* Groups: the shape tag `shape:<id>` (statements whose text contains `dpa:shape=<id>`) and each protected transaction tag `txn:<type>`.
* For each group store `{calls, total_exec_time}` summed over matching rows **and** a pre-change **baseline window mean** computed from the last window if available; if the live DB has little traffic, run `workload.run_block` on `qc_live` for `BASELINE_SECONDS` (default 60) at the `normal` profile before applying (document that this writes to live).
* `baseline = {"groups": {"txn:place_order": {"calls": n, "total_ms": t, "mean_ms": t/n}, …}, "taken_at": iso}`.

## 14.4 Monitor thread

```python
class MonitorThread(threading.Thread):
    def __init__(self, store, interval_s=60, min_calls=30, consecutive=2): ...
    def run(self): loop until stopped; every interval: for each applied row with state in {healthy, watching}: evaluate()
```
Window evaluation for one applied change:
1. Read current cumulative counters per group; window = difference vs the previous snapshot stored in `windows[-1].cumulative` (initialise with the baseline snapshot).
2. If `Δcalls < min_calls` for a group ⇒ mark the group `insufficient` (listed for human review; **never** reverts on insufficient data).
3. Otherwise `window_mean = Δtotal_ms / Δcalls`; `rel = window_mean / baseline_mean − 1`.
4. A group is **regressed in this window** if `rel > DELTA` (and optionally `rel > 0` with a significance check using a z-test on the window mean when per-call variance is available: `pg_stat_statements.stddev_exec_time`; use `z = (window_mean − baseline_mean) / (stddev/√Δcalls)`; require `z > 2`).
5. State machine per applied change: `healthy` → on first regressed window `watching` → on `consecutive` (default 2) consecutive regressed windows ⇒ **revert**; any clean window returns to `healthy`.
6. Append the window result to `applied.windows` (cap 500).
7. Statement groups that improved are reported but never trigger actions.

## 14.5 Revert

`monitor.revert(store, applied_id, reason, actor="monitor"|"human")`:
* Index: run `undo_ddl` on `qc_live` (autocommit, `lock_timeout 5s`); on failure retry 3× (1 s backoff), else `revert_failed` and an `error` event.
* Verify the index no longer exists. Update `applied` (`state=reverted`, `reverted_at`, `revert_reason`) and the candidate status `reverted`.
* Emit a `monitor` event with `level=warn` and the window evidence; if the job still exists add an event on that job.
* Auto-revert must itself be **safe**: never revert more than 1 change per interval; never revert a change younger than `MIN_AGE_S` (default 2 windows) unless two consecutive windows regress.

## 14.6 Live traffic generator (for demos and tests)

`POST /api/live/traffic` (step 15) starts a background thread running `workload.run_block` repeatedly against `qc_live`
with the chosen profile until stopped; keeps at most one generator running; stores the latest `BlockResult` summary per minute
so the UI can plot it. Guard against running traffic and `apply` concurrently without notice: the UI warns.

## 14.7 Demo script for the regression + auto-revert (scripts/demo_revert.py)

1. `workload.prepare_clone`-like preparation on `qc_live` is **not** used (do not mutate production-like data heavily); instead start the `normal` generator for 60 s to build the baseline.
2. Insert a planted candidate (`CREATE INDEX ON store_stock (qty)`, status forced to `selected` through a test-only helper) and apply it.
3. Switch the generator to `flash10`. Within `consecutive × interval` the monitor reverts the index; script prints the window table and the final state.
4. Verify afterwards: index absent; applied row `reverted`; events present.

## 14.8 Tests (`tests/test_monitor.py`)

* Unit (no DB): window evaluation with synthetic counters — clean windows keep `healthy`; two regressed windows ⇒ revert decision; insufficient calls ⇒ no action; z-test gate.
* `@db`: `apply` creates a valid index; `indexdef` contains the name; applying twice is idempotent; `revert` drops it; `apply` on a candidate whose evidence card does not list it ⇒ `Conflict`.
* `@db @slow`: the demo script ends in `reverted` within the expected number of windows (use a short interval, e.g. 5 s, and low `min_calls` in the test).
* `test_rewrite_apply_no_db_change`: apply a rewrite ⇒ `pg_indexes` unchanged.
* `test_invalid_index_cleanup`: simulate `indisvalid=false` ⇒ index dropped and error raised.

## 14.9 Done checklist
- [ ] Approve → apply → monitor → revert demonstrated; window table saved in `PROGRESS.md`
- [ ] Safety re-check on apply verified by test
- [ ] Commit `step 14: apply, monitor, revert`

## 14.10 Pitfalls
* `CREATE INDEX CONCURRENTLY` can leave an **invalid** index on failure — always check `indisvalid`.
* `pg_stat_statements` counters are cumulative since reset/restart; use differences only. If counters decrease (reset), re-baseline and emit a warning.
* Statement-level means are not transaction latency; say so in the UI ("statement latency").
