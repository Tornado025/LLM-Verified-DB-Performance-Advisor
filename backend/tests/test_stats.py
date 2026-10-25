"""Deterministic statistical calibration, edge cases and sequential safeguards."""

import json
import math
from time import perf_counter

import numpy as np
import pytest
from scipy.stats import t

from app.models import Bounds, Summary
from app.stats import (
    SequentialTest,
    alpha_per_test,
    best_leader,
    bounds,
    confidently_worse,
    improvement_verdict,
    paired_log_ratios,
    summarize,
    verdict,
)


def draws(theta, n, seed=1234, noise=0.05):
    return np.random.default_rng(seed).normal(np.log1p(theta), noise, n)


def test_log_ratio_basic():
    assert paired_log_ratios([1, 2], [2, 4]) == pytest.approx([math.log(2)] * 2)
    assert np.isfinite(paired_log_ratios([1e-300], [1e300])).all()


@pytest.mark.parametrize(
    "a,b",
    [([0], [1]), ([-1], [1]), ([1], []), ([1], [math.nan]), ([math.inf], [1]), ([[1]], [[1]])],
)
def test_invalid_inputs(a, b):
    with pytest.raises(ValueError):
        paired_log_ratios(a, b)


def test_pass_when_clearly_fast():
    assert verdict(draws(-0.5, 30))[0] == "PASS"


def test_fail_when_clearly_slow():
    assert verdict(draws(0.30, 12))[0] == "FAIL"


def test_continue_before_min_n():
    assert verdict(draws(-0.5, 4))[0] == "CONTINUE"
    assert verdict([]) == ("CONTINUE", Bounds(-math.inf, math.inf))
    assert verdict([0])[0] == "CONTINUE"


def test_block_when_undetermined_at_max():
    # A symmetric constructed sample has exactly the boundary mean, avoiding
    # accidentally selecting a random draw that correctly excludes the boundary.
    deviations = np.random.default_rng(12).normal(0, 0.1, 30)
    d = np.log1p(0.05) + np.concatenate([deviations, -deviations])
    assert verdict(d)[0] == "BLOCK"


def test_zero_variance():
    state, interval = verdict(np.zeros(6))
    assert state == "PASS" and interval.lower < 0 < interval.upper
    assert verdict(np.full(60, math.log1p(0.05)))[0] == "BLOCK"


def test_alpha_split():
    assert alpha_per_test(0.05, 10, 20) == 0.05 / 200


def test_bounds_match_student_t():
    d = draws(0, 20)
    a = 0.05 / 19
    margin = t.ppf(1 - a, len(d) - 1) * np.std(d, ddof=1) / np.sqrt(len(d))
    b = bounds(d, a)
    assert b.lower == pytest.approx(np.expm1(d.mean() - margin), abs=1e-9)
    assert b.upper == pytest.approx(np.expm1(d.mean() + margin), abs=1e-9)


def run_trials(theta, trials, seed, family=1):
    rng = np.random.default_rng(seed)
    counts = dict.fromkeys(["PASS", "FAIL", "BLOCK"], 0)
    for _ in range(trials):
        values = rng.normal(np.log1p(theta), 0.05, 60)
        test = SequentialTest(0.05, 0.05, 19, family, 6, 60)
        test.add(values[:6])
        for i in range(6, 60, 3):
            if test.state != "CONTINUE":
                break
            test.add(values[i : i + 3])
        counts[test.state] += 1
    return counts


def test_typeI_at_boundary():
    counts = run_trials(0.05, 2000, 5001)
    print("boundary_trials=" + json.dumps({"seed": 5001, "trials": 2000, **counts}))
    assert counts["PASS"] / 2000 <= 0.05


def test_power_reasonable():
    counts = run_trials(0, 400, 5002)
    print("power_trials=" + json.dumps({"seed": 5002, "trials": 400, **counts}))
    assert counts["PASS"] / 400 > 0.9


@pytest.mark.parametrize("theta,expected", [(-0.5, "PASS"), (0.3, "FAIL")])
def test_sequential_monotone_states(theta, expected):
    test = SequentialTest(0.05, 0.05, 19, 1, 6, 60)
    assert test.add(draws(theta, 12)) == expected
    original = test.d
    assert test.add(draws(-theta, 48)) == expected
    assert test.d == pytest.approx(original)
    copy = test.d
    copy[:] = 1
    assert test.d == pytest.approx(original)
    assert test.summary().state == expected


def test_improvement_verdict():
    assert improvement_verdict(draws(-0.3, 30), 0.05, 19, 1, 6, 60)[0] == "PASS"
    assert improvement_verdict(draws(-0.1, 30), 0.05, 19, 1, 6, 60, min_gain=0.3)[0] == "FAIL"
    test = SequentialTest(0.05, 0.05, 19, 1, 6, 60, mode="improvement")
    assert test.add(draws(-0.3, 30)) == "PASS"


def test_confidently_worse():
    assert confidently_worse(Bounds(0.1, 0.2), Bounds(-0.2, 0))
    assert not confidently_worse(Bounds(0, 0.2), Bounds(-0.2, 0))
    assert not confidently_worse(Bounds(-math.inf, math.inf), Bounds(-0.2, 0))


def test_best_leader_and_summary():
    summaries = {
        "a": Summary(10, -0.3, -0.4, -0.2, 0.1, "PASS"),
        "b": Summary(10, -0.4, -0.5, -0.3, 0.1, "PASS"),
        "c": Summary(10, -0.8, -0.9, -0.7, 0.1, "CONTINUE"),
    }
    assert best_leader(summaries) == "b"
    assert best_leader({"c": summaries["c"]}) is None
    result = summarize(np.full(6, math.log(0.5)), 0.05, 0.05, 19, 1, 6, 60)
    assert result.mean_rel == pytest.approx(-0.5) and result.sd_log == 0


def test_sequential_budgets_and_block_latch():
    test = SequentialTest(0.05, 0.05, 1, 1, 6, 60)
    d = np.log1p(0.05) + np.array([-0.1, 0.1] * 3)
    assert test.add(d) == "BLOCK"
    assert test.add([-1] * 54) == "BLOCK"
    assert test.add([]) == "BLOCK"
    test = SequentialTest(0.05, 0.05, 19, 1, 6, 60)
    with pytest.raises(ValueError):
        test.add([0] * 61)
    assert len(test.d) == 0
    with pytest.raises(ValueError):
        test.add([math.nan])
    assert test.state == "CONTINUE"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"alpha": 0},
        {"alpha": float("nan")},
        {"looks": 0},
        {"family": -1},
        {"looks": 1.5},
        {"min_n": 2},
        {"max_n": 5},
        {"delta": float("inf")},
        {"delta": -1},
    ],
)
def test_policy_validation(kwargs):
    with pytest.raises(ValueError):
        verdict([0] * 6, **kwargs)


def test_nonfinite_or_excess_samples_never_pass():
    for d in ([math.nan] * 6, [math.inf] * 6, [[0] * 6], [0] * 61):
        with pytest.raises(ValueError):
            verdict(d)
    # Extreme, finite values cannot cause an overflowing confidence calculation to pass.
    assert verdict([1e308, -1e308] * 30)[0] == "BLOCK"
    with pytest.raises(ValueError):
        improvement_verdict([0] * 6, 0.05, 19, 1, 6, 60, min_gain=1)


def test_verdict_microbenchmark():
    d = draws(0, 60)
    verdict(d)  # warm quantile cache
    started = perf_counter()
    for _ in range(1000):
        verdict(d)
    milliseconds = (perf_counter() - started) * 1000 / 1000
    print(f"cached_verdict_mean_ms={milliseconds:.6f}")
    assert milliseconds < 5
