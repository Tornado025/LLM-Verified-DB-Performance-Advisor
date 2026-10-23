# Step 13 — Pipeline orchestration and evidence cards (`app/pipeline.py`)

**Goal (end state):** `run_job(job_id, params, store)` executes the whole pipeline for the selected shapes using the mock LLM,
emits ordered events, saves candidates and one **evidence card per shape**, respects the time budget, handles errors and
cancellation, and always cleans up sandboxes. `docs/evidence_card.schema.json` validates every saved card.

**Prerequisites:** steps 03–12.

## 13.1 Job parameters (`params`, validated by the API in step 15)

```json
{"shape_ids": ["recent_orders"], "k_max": 10, "patience": 3, "delta": 0.05, "alpha": 0.05, "metric": "time",
 "profiles": ["normal","flash5","flash10","flash20"], "budget_s": 1800, "prefer_ratio": 0.8,
 "llm": "mock|openrouter|ollama", "seed": 1234, "workload": "custom", "guard_stat": "mean",
 "noise": 0.0, "dry_run": false}
```
Defaults come from `Settings`. `dry_run=true` stops after Filter + Equivalence (no race/guard) — useful for fast UI demos.

## 13.2 Per-job flow

```
run_job:
  set status running; emit system "job started"
  budget = Budget(params.budget_s)            # monotonic clock; shared across shapes (split equally unless one shape given)
  for shape_id in shape_ids:
      if cancelled(job_id): break
      run_shape(shape_id)
  set status done|failed|cancelled; emit result summary
  finally: sandbox cleanup of every clone created by this job
```

`run_shape` steps, each wrapped in `try/except AdvisorError` that emits an `error` event and continues to the next shape:

1. **observe** — pick params (`sample_params` with `Generator(seed)`), render concrete SQL on the baseline clone; create baseline clone `A`; `ANALYZE`; baseline plan, signature, 5 timing runs (`measure.run_once`), `catalog`; emit `observe` event with plan summary and baseline mean.
2. **propose** — `propose.propose(...)` (step 09). Save every raw candidate (incl. rejected) via `store.save_candidate`.
3. **filter** — `plans.filter_candidates` ⇒ statuses `skipped_noplan` / `skipped_duplicate`; also sandbox-DDL validity: for each index/stats candidate attempt `apply_ddl` on a throwaway clone **only when HypoPG accepted it**; invalid ⇒ `rejected_invalid_ddl`. (Do this check lazily inside `race` clone creation to avoid extra clones; whichever path is used, the status must be set before race starts for index candidates — implement as a dedicated `validate_ddl_batch` using a **single** scratch clone: apply each DDL in its own savepoint-free transaction, rollback by dropping the index afterwards.)
4. **equivalence** — rewrites only: `equivalence.check_rewrite` on `A`; `rejected_equiv` or keep with `equiv`.
5. If `dry_run`: build the card with `decision.reason="dry run"` and return.
6. **race** — `race.race(...)` ⇒ speedups; dropped statuses.
7. **guard + decide** — `decide.decide(...)`; sets final statuses (`selected`, `failed_guard`, `blocked_unproven`).
8. **evidence** — assemble the card (contracts 0.10), validate against the JSON schema, `store.save_evidence`, emit `result` event with the decision.

## 13.3 Events (exact shapes)

`emit(job_id, step, level, message, data)` where `data` is JSON. Required events (the UI stepper depends on them):

| step | when | data keys |
|---|---|---|
| `system` | start/end/cancel/error | `shape_ids`, `settings` (public) |
| `observe` | after baseline | `shape_id`, `baseline_ms`, `plan_signature`, `top_node` |
| `propose` | after each LLM sample | `sample`, `raw`, `accepted`, `new_plans`, `cumulative_distinct`, `saturated` |
| `filter` | after filtering | `kept`, `skipped_noplan`, `skipped_duplicate`, `invalid_ddl` |
| `equivalence` | per rewrite | `candidate_id`, `label`, `tier_reached`, `reason`, `rewrite_class` |
| `race` | per round | `round`, `alive`, `leader`, `bounds`: `{cid: [lower, upper]}`, `dropped_now` |
| `guard` | per block | `candidate_id`, `profile`, `block`, `worst_upper`, `states` |
| `decide` | decision | `action`, `candidate_id`, `reason`, `ranking` |
| `apply` / `monitor` | step 14 | — |

Step names must be exactly the contract `Step` enum; levels `result` are used for final decisions.

## 13.4 Budget and cancellation

* `Budget(total_s)` with `remaining()`, `slice(fraction)`. Pass `budget_s` slices to `race` (default 40 % of the shape budget) and `guard` (60 %).
* Cancellation: `store.set_job_status(job_id, "cancelled")` by the API; the pipeline checks `store.is_cancelled(job_id)` between steps and inside `run_block`'s stop event (workers check `stop_event` each arrival) and in `paired_report` loops. Cleanup still runs.
* On `BudgetExceeded`: finish the shape with decision `block` ("budget exhausted") unless a candidate already passed the guard.

## 13.5 Sandbox hygiene
* Every clone created via `clone_pair`/`single_clone` is released in `finally`.
* The job records `clones_created` and total clone seconds (RQ1).
* At job end call `sandbox.drop_all_clones()` **only** for names registered by this job (keep a per-job registry; never drop another running job's clones).

## 13.6 Evidence card assembly details
* `settings.llm`: provider, model, temperature (public), plus `usage` totals and cost if known.
* `candidates[*]`: full per-candidate objects (status, reason, signature, plan before/after JSON trimmed to node-type tree, `equiv`, `speedup`, `guard`).
* `budget`: seconds, paired runs, llm calls, clones, guard blocks.
* `baseline.time_ms`: mean/sd/n from the five timing runs.
* Truncate any plan JSON > 50 KB to its node-type tree.
* Write `docs/evidence_card.schema.json` (JSON Schema draft 2020-12) matching contracts 0.10; test validates every card with the `jsonschema` package (add to requirements).

## 13.7 Tests (`tests/test_pipeline_mock.py`, `@db`, `@slow`)
* `test_dry_run_all_shapes`: mock LLM, `dry_run=true`, all 8 shapes finish; every card validates; statuses are valid enum values.
* `test_invariants_full_run` (`recent_orders`, `delivered_yesterday`, `payout_report` with reduced profiles `["normal"]`, `MAX_BLOCKS=12`):
  * wrong-time-zone rewrite never `selected`; invalid-DDL candidate never `selected`/`applied`;
  * every `selected` index candidate has `guard.passed == true`; every `selected` rewrite has `equiv.label == "tested"`;
  * events are strictly increasing in id, contain a `decide` event per shape;
  * no `qc_sbx_*` databases remain.
* `test_cancel_midway`: start, cancel after the first `propose` event ⇒ status `cancelled`, no leaked clones.
* `test_budget_exhaustion`: `budget_s=20` ⇒ decision `block` with budget reason.
* `test_llm_error_isolated`: provider raising `LLMError` for one shape ⇒ that shape errors, next shape continues.

## 13.8 Done checklist
- [ ] A mock-LLM job on three shapes completes; cards saved and schema-valid
- [ ] Time per stage recorded in `PROGRESS.md`
- [ ] Commit `step 13: pipeline and evidence`
