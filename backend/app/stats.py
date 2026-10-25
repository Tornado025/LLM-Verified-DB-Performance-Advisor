"""Paired log-ratio tests for the geometric mean candidate/baseline ratio.

Assumptions: paired log ratios are approximately independent and normally
distributed across runs. exp(mean(d))-1 describes a geometric mean ratio, not
an arithmetic mean latency ratio; these are close under comparable multiplicative
noise. One-sided Student-t bounds use alpha/(looks*family): Bonferroni across
both the planned sequential looks and simultaneous tests avoids peeking inflation
under these assumptions. Conservatism increases BLOCKs under noise; that cost
must be measured and reported in RQ2. Simulation checks are empirical validation,
not a proof for arbitrary workload distributions or dependent measurements.

Callers of the stateless API must honor the declared maximum number of looks.
SequentialTest enforces that budget, rejects samples beyond max_n, and latches
terminal decisions. A look is spent by each nonempty add at or above min_n.
"""

import math
from collections.abc import Iterable, Sequence

import numpy as np
from scipy.stats import t

from app.models import Bounds, Summary

_T_CACHE: dict[tuple[float, int], float] = {}
_SD_FLOOR = 1e-6


def _values(d: Sequence[float] | np.ndarray) -> np.ndarray:
    value = np.asarray(d, dtype=float)
    if value.ndim != 1 or not np.all(np.isfinite(value)):
        raise ValueError("Measurements must be a finite one-dimensional sequence")
    return value


def paired_log_ratios(
    a: Sequence[float] | np.ndarray, b: Sequence[float] | np.ndarray
) -> np.ndarray:
    """Validate positive paired measurements and return log(b)-log(a)."""
    av, bv = _values(a), _values(b)
    if av.shape != bv.shape or np.any(av <= 0) or np.any(bv <= 0):
        raise ValueError("Pairs must have equal length and strictly positive values")
    # Subtraction avoids overflow/underflow of b/a for valid positive inputs.
    return np.log(bv) - np.log(av)


def alpha_per_test(alpha: float, looks: int, family: int) -> float:
    """Allocate one-sided error across all declared looks and tests."""
    if not math.isfinite(alpha) or not 0 < alpha < 0.5:
        raise ValueError("Alpha must be finite and between 0 and 0.5")
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 1 for x in (looks, family)):
        raise ValueError("Looks and family must be positive integers")
    a = alpha / (looks * family)
    if a <= 0:
        raise ValueError("Per-test alpha underflows")
    return a


def bounds(d: np.ndarray, a: float) -> Bounds:
    """Compute one-sided Student-t bounds, flooring zero sample deviation."""
    values = _values(d)
    if not math.isfinite(a) or not 0 < a < 0.5:
        raise ValueError("Per-test alpha must be between 0 and 0.5")
    if len(values) < 2:
        return Bounds(-math.inf, math.inf)
    key = (round(1 - a, 12), len(values) - 1)
    if key not in _T_CACHE:
        # Use the upper edge of the rounded probability bucket. Reusing a cache
        # entry can widen, never narrow, the intended interval (fail closed).
        probability = min(1.0, key[0] + 0.5e-12)
        _T_CACHE[key] = float(t.ppf(probability, key[1]))
    with np.errstate(over="ignore", invalid="ignore"):
        mean = float(np.mean(values))
        sd = float(np.std(values, ddof=1))
        if not math.isfinite(mean) or not math.isfinite(sd):
            return Bounds(-math.inf, math.inf)
        se = max(sd, _SD_FLOOR) / math.sqrt(len(values))
        margin = _T_CACHE[key] * se
        return Bounds(float(np.expm1(mean - margin)), float(np.expm1(mean + margin)))


def _policy(delta: float, alpha: float, looks: int, family: int, min_n: int, max_n: int) -> float:
    if not math.isfinite(delta) or delta <= -1:
        raise ValueError("Relative threshold must be finite and greater than -1")
    if (
        isinstance(min_n, bool)
        or isinstance(max_n, bool)
        or not isinstance(min_n, int)
        or not isinstance(max_n, int)
        or min_n < 3
        or max_n < min_n
    ):
        raise ValueError("Run limits require max_n >= min_n >= 3")
    return alpha_per_test(alpha, looks, family)


def verdict(
    d: Sequence[float] | np.ndarray,
    delta: float = 0.05,
    alpha: float = 0.05,
    looks: int = 10,
    family: int = 1,
    min_n: int = 6,
    max_n: int = 60,
) -> tuple[str, Bounds | None]:
    """Pass only when the upper relative-change bound is strictly below delta."""
    a = _policy(delta, alpha, looks, family, min_n, max_n)
    values = _values(d)
    if len(values) > max_n:
        raise ValueError("Measurement count exceeds the declared run budget")
    interval = bounds(values, a)
    if len(values) < min_n:
        return "CONTINUE", interval
    if interval.upper < delta:
        return "PASS", interval
    if interval.lower > delta:
        return "FAIL", interval
    return ("BLOCK" if len(values) >= max_n else "CONTINUE"), interval


def summarize(
    d: Sequence[float] | np.ndarray,
    delta: float,
    alpha: float,
    looks: int,
    family: int,
    min_n: int,
    max_n: int,
) -> Summary:
    """Summarize paired geometric change and its allocated confidence bounds."""
    values = _values(d)
    state, interval = verdict(values, delta, alpha, looks, family, min_n, max_n)
    assert interval is not None
    with np.errstate(over="ignore", invalid="ignore"):
        mean_rel = float(np.expm1(np.mean(values))) if len(values) else math.nan
        sd = float(np.std(values, ddof=1)) if len(values) >= 2 else math.nan
    return Summary(len(values), mean_rel, interval.lower, interval.upper, sd, state)


def improvement_verdict(
    d: Sequence[float] | np.ndarray,
    alpha: float,
    looks: int,
    family: int,
    min_n: int,
    max_n: int,
    min_gain: float = 0.0,
) -> tuple[str, Bounds | None]:
    """Test whether report time decreases by more than min_gain (fraction)."""
    if not math.isfinite(min_gain) or not 0 <= min_gain < 1:
        raise ValueError("Minimum gain must be between 0 and 1")
    return verdict(d, -min_gain, alpha, looks, family, min_n, max_n)


class SequentialTest:
    """Accumulate validated pairs with a bounded look budget and terminal latch."""

    def __init__(
        self,
        delta: float,
        alpha: float,
        looks: int,
        family: int,
        min_n: int,
        max_n: int,
        mode: str = "noninferiority",
    ) -> None:
        _policy(delta, alpha, looks, family, min_n, max_n)
        if mode not in {"noninferiority", "improvement"}:
            raise ValueError("Unknown sequential test mode")
        self.delta, self.alpha, self.looks, self.family = delta, alpha, looks, family
        self.min_n, self.max_n, self.mode = min_n, max_n, mode
        self._data: list[float] = []
        self._state = "CONTINUE"
        self._looks_used = 0

    def add(self, d_values: Iterable[float]) -> str:
        """Spend a look on a nonempty eligible batch; ignore data after stopping."""
        if self._state != "CONTINUE":
            return self._state
        new = _values(list(d_values))
        if len(self._data) + len(new) > self.max_n:
            raise ValueError("Batch exceeds run budget")
        if not len(new):
            return self._state
        if len(self._data) + len(new) >= self.min_n:
            if self._looks_used >= self.looks:
                self._state = "BLOCK"
                return self._state
            self._looks_used += 1
        self._data.extend(new.tolist())
        threshold = self.delta if self.mode == "noninferiority" else 0.0
        self._state = verdict(
            self.d, threshold, self.alpha, self.looks, self.family, self.min_n, self.max_n
        )[0]
        if self._state == "CONTINUE" and self._looks_used >= self.looks:
            self._state = "BLOCK"
        return self._state

    @property
    def d(self) -> np.ndarray:
        """Return a copy so callers cannot mutate accumulated evidence."""
        return np.array(self._data, dtype=float)

    @property
    def state(self) -> str:
        """Return the latched decision without spending another look."""
        return self._state

    def summary(self) -> Summary:
        """Describe the same evidence and latched decision, without peeking again."""
        threshold = self.delta if self.mode == "noninferiority" else 0.0
        result = summarize(
            self.d, threshold, self.alpha, self.looks, self.family, self.min_n, self.max_n
        )
        result.state = self._state
        return result


def confidently_worse(bounds_c: Bounds, bounds_leader: Bounds) -> bool:
    """Drop only when candidate best case is slower than leader worst case."""
    return bounds_c.lower > bounds_leader.upper


def best_leader(summaries: dict[str, Summary]) -> str | None:
    """Choose the lowest finite upper bound among improvement PASS results."""
    eligible = {
        key: value
        for key, value in summaries.items()
        if value.state == "PASS" and math.isfinite(value.upper) and value.upper < 0
    }
    return min(eligible, key=lambda key: eligible[key].upper) if eligible else None
