# Step 05 — Statistics engine (`app/stats.py`)

**Goal (end state):** `stats.py` implements the paired log-ratio analysis, one-sided non-inferiority and
improvement tests, the sequential decision (`CONTINUE/PASS/FAIL/BLOCK`), alpha allocation across looks and
tests, and summaries. A property-style test proves type-I error ≤ α at the δ boundary and acceptable power.

**Prerequisites:** steps 01–03. No database needed.

## 5.1 Definitions

For paired runs `i = 1..n` with baseline value `a_i` and candidate value `b_i` (both > 0):

* Log ratio `d_i = ln(b_i / a_i)`. Relative change of the **geometric mean** ratio: `r = exp(mean(d)) − 1`.
  Document that this bounds the *geometric* mean ratio, which approximates the ratio of means when noise is multiplicative.
* Standard error `se = std(d, ddof=1) / sqrt(n)`.
* One-sided bounds at confidence `1 − a` (a = per-look, per-test alpha):
  `upper = expm1(mean(d) + t_{1−a, n−1} · se)`, `lower = expm1(mean(d) − t_{1−a, n−1} · se)`.
* **Non-inferiority (guard) hypothesis:** H0: true relative change ≥ δ. **PASS** iff `upper < δ`.
* **Proven inferior:** `lower > δ` ⇒ **FAIL**.
* **Not proven** after `max_n` runs ⇒ **BLOCK** (fail closed). Before `min_n` runs ⇒ **CONTINUE**.
* **Improvement test** (for report speedups): `improved` iff `upper < −min_gain` (default `min_gain = 0.0`).

## 5.2 Alpha allocation (document in code comments and in the paper)

* `looks` = maximum number of sequential looks = `ceil((max_n − min_n) / batch) + 1`.
* `family` = number of simultaneously guarded tests (guard: `n_txn_types × n_profiles`; report race: number of candidates).
* Per-test, per-look alpha: `a = alpha / (looks × family)` (Bonferroni twice; conservative on purpose).
* Edge: if `n < 2` return `CONTINUE` with `Bounds(-inf, +inf)`. If `std == 0` (identical pairs) use a floor
  `sd_floor = 1e-6` so `se > 0`.

## 5.3 API (match contracts 0.7)

```python
def paired_log_ratios(a: Sequence[float], b: Sequence[float]) -> np.ndarray       # validates > 0, equal length
def alpha_per_test(alpha: float, looks: int, family: int) -> float
def bounds(d: np.ndarray, a: float) -> Bounds                                     # one-sided, t-based
def verdict(d, delta=0.05, alpha=0.05, looks=10, family=1, min_n=6, max_n=60) -> tuple[str, Bounds | None]
def summarize(d, delta, alpha, looks, family, min_n, max_n) -> Summary
def improvement_verdict(d, alpha, looks, family, min_n, max_n, min_gain=0.0) -> tuple[str, Bounds | None]

class SequentialTest:
    """Accumulates paired log ratios and reports the current state."""
    def __init__(self, delta, alpha, looks, family, min_n, max_n, mode="noninferiority")
    def add(self, d_values: Iterable[float]) -> str
    @property
    def d(self) -> np.ndarray
    @property
    def state(self) -> str
    def summary(self) -> Summary
```
Cache `scipy.stats.t.ppf` results in a dict keyed by `(round(1 - a, 12), df)` for speed (simulation calls it millions of times).

## 5.4 Racing helpers (also in `stats.py`)

```python
def confidently_worse(bounds_c: Bounds, bounds_leader: Bounds) -> bool
    # candidate best case (lower) is slower than leader worst case (upper)
    return bounds_c.lower > bounds_leader.upper
def best_leader(summaries: dict[str, Summary]) -> str | None
    # smallest upper bound among candidates that are state "PASS" on improvement; else None
```
Relative changes here are on **report time** vs baseline (negative = faster).

## 5.5 Behaviour tests (`tests/test_stats.py`)

Use `numpy.random.default_rng(seed)`; all deterministic.

| Test | Setup | Assertion |
|---|---|---|
| `test_log_ratio_basic` | a=[1,2], b=[2,4] | d ≈ ln 2 each |
| `test_invalid_inputs` | zero/negative/len mismatch | raises `ValueError` |
| `test_pass_when_clearly_fast` | 30 pairs, θ = −0.5, noise 5 % | `PASS` |
| `test_fail_when_clearly_slow` | θ = +0.30, noise 5 %, n = 12 | `FAIL` |
| `test_continue_before_min_n` | n = 4 | `CONTINUE` |
| `test_block_when_undetermined_at_max` | θ = 0.05, noise 10 %, n = max_n | `BLOCK` (not PASS) |
| `test_zero_variance` | identical pairs, θ = 0 | `PASS` after min_n (no divide-by-zero) |
| `test_alpha_split` | alpha_per_test(0.05, 10, 20) | = 0.05/200 |
| `test_typeI_at_boundary` | θ = δ exactly, noise 5 %, 2000 sequential trials from n=6..60 (batch 3) | pass rate ≤ α (0.05); expect ≪ α |
| `test_power_reasonable` | θ = 0, noise 5 %, 400 trials | PASS rate > 0.9 |
| `test_sequential_monotone_states` | once PASS/FAIL returned by `SequentialTest`, adding more data does not flip back in the same instance (state latches) |
| `test_improvement_verdict` | θ = −0.3 | `PASS` (improved) |
| `test_confidently_worse` | synthetic bounds | True/False cases |

## 5.6 Performance requirement
`verdict` on n = 60 in < 0.2 ms (use cached t-quantiles). Add a micro-benchmark test with a generous 5 ms ceiling.

## 5.7 Documentation block (top of `stats.py`)
State clearly: assumptions (paired differences of log ratios approximately normal), why Bonferroni across looks (simple, conservative,
no peeking inflation), limitation (conservative ⇒ more BLOCKs at high noise; this cost is reported honestly in RQ2).

## 5.8 Done checklist
- [ ] All tests above pass; `mypy app/stats.py` clean
- [ ] Type-I-error and power numbers recorded in `docs/PROGRESS.md`
- [ ] Commit `step 05: stats engine`
