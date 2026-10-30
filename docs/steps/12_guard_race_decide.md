# Step 12 — Guard, racing, decision (`app/guard.py`, `race.py`, `decide.py`)

**Goal (end state):** given verified candidates, `race` ranks them by statistically supported report speedup and drops losers early;
`guard` returns pass/fail per (profile × transaction) with confidence bounds; `decide` picks verified rewrite → guarded index → block.
Planted cases demonstrate the fail-closed rule: a clearly harmful multi-index change fails,
an uncertain candidate blocks, and only a candidate proven safe across every profile passes.

**Prerequisites:** steps 05, 07, 08, 10, 11.

## 12.1 `race.py` — safe racing on report speed

```python
def race(candidates, shape, rendered_sql, settings, *, budget_s, progress=None, rng=None) -> RaceResult
```
Setup:
* Baseline clone `A`. For **rewrites**: candidate runs on `A` against the original SQL. For **index/stats**: one clone `B_c` per candidate (limit concurrent clones to `MAX_CLONES`; process candidates in batches if more).
* Each candidate has an improvement `SequentialTest(mode="improvement", family = len(candidates))`.

Rounds (until budget, or every candidate resolved, or one survivor resolved):
1. For each alive candidate run `RACE_BATCH` more paired runs (`measure.paired_report` single batch mode: reuse connections; do not re-warm after the first round).
2. Update bounds. Determine `leader` = candidate with state `PASS` (improved) and the smallest upper bound (fastest).
3. **Drop rules** (record `dropped[c] = reason`, status `dropped_race`):
   * `no_improvement`: after `MAX_RUNS` pairs the improvement test is not `PASS` ⇒ drop.
   * `confidently_worse_than_leader`: `bounds_c.lower > bounds_leader.upper` ⇒ drop.
   * `slower_than_baseline`: `bounds_c.lower > +δ` ⇒ drop (it is clearly slower than baseline on the report itself).
4. Borderline candidates stay and keep receiving runs.
5. Early exit: when only one candidate remains unresolved and every other is dropped.

Outputs per candidate: `speedup = Summary(...)` (relative **time** change vs baseline; negative = faster), `n_runs`.
Budget accounting: seconds, total paired runs, clones created. If budget is exhausted candidates still `CONTINUE` are treated as `dropped_race` ("budget").

Metric: `settings.metric`; if `buffers` the speedup is in buffer reads. Optional **screening**: run buffers first (cheap); only candidates whose buffers improve proceed to timing (log it).

## 12.2 `guard.py` — non-inferiority under normal and flash-sale profiles

```python
def run_guard(candidate, profiles: list[str], settings, *, seed, progress=None, workload="custom") -> GuardResult
```
For each profile in `settings.GUARD_PROFILES` (or the argument list):
1. `with sandbox.clone_pair(candidate, settings) as pair:` (fresh A and B **per profile**; DDL on B; ANALYZE both).
2. `workload.prepare_clone(A, profile, seed)` and `prepare_clone(B, profile, seed)` (identical work).
3. `family = len(TXN_TYPES) × len(profiles)`, `looks` from settings; one `SequentialTest(mode="noninferiority", delta, alpha, looks, family, min_n, max_n)` per transaction type.
4. For block index `i = 0..MAX_BLOCKS-1`:
   * `schedule = build_schedule(profile, BLOCK_SECONDS, seed + i, base_rate, pools)` — **same schedule** for A and B in this block.
   * Order: A then B if `i` even else B then A. Between sides sleep 1 s (let background work settle). Run `run_block` on each.
   * For each type with ≥ `MIN_SAMPLES_PER_BLOCK` samples on **both** sides: `d = ln(mean_B / mean_A)` (metric `mean`; also store `p95_B/p95_A` for reporting; `GUARD_STAT=p95` option uses p95 instead).
   * Error asymmetry: if the error rate for a transaction type on B exceeds A's by more than 1 percentage point in a block, set that type's state to `FAIL` immediately with reason `errors_increased` (a candidate that causes errors is unsafe).
   * Update tests. Evaluate states:
     * any `FAIL` ⇒ stop **this and all remaining profiles** (candidate is unsafe), `passed=False`, `failed_cell=(profile, type)`.
     * all types `PASS` ⇒ stop this profile.
     * all types decided or `i == MAX_BLOCKS-1`: types still `CONTINUE` ⇒ `BLOCK` ⇒ `passed=False` (unproven).
5. Record per cell `{n, mean_rel, lower, upper, state}` + `block_seconds`, `base_rate`, `prepare` info, `workload`.
6. `passed = all cells PASS across all profiles`.
7. Types lacking enough samples after `MAX_BLOCKS` ⇒ `BLOCK` with reason `insufficient_samples` (never silently pass).
8. Emit store events per block (progress callback) with the current worst upper bound so the UI can show it live.

Time budget note: minimum cost per profile ≈ `2 × BLOCK_SECONDS × MIN_RUNS` seconds (e.g. 96 s) plus clone time; four profiles ≈ 7 min per candidate. Provide `estimate_guard_seconds(settings)` used by the API to warn users.

CH-benCHmark mode (`workload="ch"`): blocks are BenchBase runs (`benchbase.run_profile`); the same statistics apply; types = NewOrder, Payment, OrderStatus, Delivery, StockLevel.

## 12.3 `decide.py`

```python
def decide(survivors: list[Candidate], race: RaceResult, shape, settings, *, progress=None) -> Decision
```
1. `ranked = survivors sorted by speedup.upper ascending` (most confidently fast first). If none ⇒ `Decision("block", None, "no candidate improves the report")`.
2. `best = ranked[0]`.
3. **Rewrites first:** let `rw` = best rewrite with `equiv.label == "tested"` and speedup `PASS`. If `rw` exists and
   `(−rw.speedup.mean_rel) ≥ PREFER_RATIO × (−best.speedup.mean_rel)` (i.e. its speedup is at least `PREFER_RATIO` of the best, both measured as time reduction) ⇒ `Decision("apply_rewrite", rw.id, "verified rewrite within <ratio> of the best option; zero write cost")`. (Weak rewrites ⇒ decision `apply_rewrite` but flagged `requires_confirmation`.)
4. **Guarded indexes:** for `c` in `ranked` where `c.kind in {"index","stats"}`: `g = run_guard(c, …)`; `c.guard = g.to_dict()`.
   * `g.passed` ⇒ status `selected`; return `Decision("apply_index", c.id, "passed non-inferiority under <profiles>")`.
   * else status `failed_guard` (if a cell FAIL) or `blocked_unproven` (if BLOCK); continue to the next candidate.
   Limit guard attempts to `MAX_GUARD_CANDIDATES` (default 3) per shape.
5. If a rewrite exists but was not preferred (ratio) **and** no index passes ⇒ fall back to that rewrite (`apply_rewrite`) with reason "no index proven safe; verified rewrite is the best safe option".
6. Otherwise `Decision("block", None, "no candidate proven safe within budget")` and a list of per-candidate reasons.

All thresholds from settings; log the full ranking into the evidence card.

## 12.4 Planted-case tests (`tests/test_guard.py`, `@db`, `@slow`)

Use real clones with a **synthetic candidate** whose effect is controlled:
| Case | Candidate | Expected guard |
|---|---|---|
| harmless | `CREATE INDEX ON customers (city)` (not touched by order txns) | `PASS` only where every protected cell's upper bound is below 5%; otherwise `BLOCK`. Record both outcomes by profile and budget. |
| heavy write cost | 8 redundant indexes on `orders` (`(customer_id,created_at)`, `(store_id,created_at)`, `(status)`, `(delivered_at)`, …) | `FAIL` at the first flash profile at the latest; usually `normal` already shows `place_order` slowdown |
| HOT loss diagnostic | `CREATE INDEX ON store_stock (qty)` | Record HOT-update loss, SQL statement cost, and full-transaction guard bounds. Do not force a `FAIL` when the 5% full-transaction slowdown is unproven; `BLOCK` is the observed verdict on this fixture. |
| undecidable | tiny effect with noise injected at 10 % and `MAX_BLOCKS = 8` | `BLOCK` (not PASS) |
| errors | a trigger that raises on 20 % of inserts (create in B only) | `FAIL` with `errors_increased` |
Race tests: three candidates with known ordering (large, medium, none) — `race` ends with the best as leader and the `none` dropped `no_improvement`.
Decide tests (unit, with fake guard/race results): preference ratio boundaries (0.79 vs 0.81), rewrite fallback, block when nothing passes.

## 12.5 Done checklist
- [x] Planted-case results recorded (verdict, n, bounds, seconds) in `PROGRESS.md`
- [x] Guard time per candidate measured and compared with `estimate_guard_seconds`
- [ ] Commit `step 12: guard, race, decide`

The planted expectations above were revised after controlled PostgreSQL runs (see
`docs/PROGRESS.md`, 2026-10-26 through 2026-10-30). The single qty index removed HOT
updates but added only about 0.00385 ms per stock SQL call, below 1% of the measured
full transaction. Treating its `BLOCK` as a 5% latency `FAIL` would invent evidence.
The 40/80-block harmless-index pilot also showed genuine `BLOCK` verdicts under noisy
10x/20x load. The guard's delta, alpha, family correction, and fail-closed policy are unchanged.
