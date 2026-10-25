"""Database-free RQ2 comparison using paired synthetic multiplicative noise.

Baseline rules are simplified rules specified by the project, not reproductions
of named external systems. All methods consume prefixes of the same trial pairs.
"""

import argparse
import json
import math
import os
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy.stats import t

from app.stats import SequentialTest, paired_log_ratios

HARMFUL = (0.07, 0.10, 0.15, 0.25)
HARMLESS = (0.0, 0.01, 0.02)
BORDERLINE = (0.04, 0.05, 0.06)
DECIDERS = (
    "single_run",
    "median5_band",
    "threshold10",
    "threshold2x",
    "ttest_fixed",
    "advisor_guard",
)


def simulate_pairs(
    rng: np.random.Generator, theta: float, noise: float, n: int
) -> tuple[np.ndarray, np.ndarray]:
    """Generate baseline/candidate latencies in ms from the specified noise model."""
    if not math.isfinite(theta) or theta <= -1 or not math.isfinite(noise) or noise < 0:
        raise ValueError("Invalid relative change or noise")
    if n < 1:
        raise ValueError("Pair count must be positive")
    shared = rng.normal(0, noise * 0.5, n)
    a = 100 * np.exp(shared + rng.normal(0, noise, n))
    b = 100 * (1 + theta) * np.exp(shared + rng.normal(0, noise, n))
    paired_log_ratios(a, b)
    return a, b


def _prefix(a: Sequence[float], b: Sequence[float], n: int) -> tuple[np.ndarray, np.ndarray]:
    av, bv = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    paired_log_ratios(av, bv)
    if len(av) < n:
        raise ValueError("Insufficient pairs for decider")
    return av[:n], bv[:n]


def decide_single_run(
    a: Sequence[float], b: Sequence[float], delta: float = 0.05
) -> tuple[bool, int]:
    """Approve based on one observed relative change."""
    av, bv = _prefix(a, b, 1)
    return bool(bv[0] / av[0] <= 1 + delta), 1


def _median(a: Sequence[float], b: Sequence[float], threshold: float) -> tuple[bool, int]:
    av, bv = _prefix(a, b, 5)
    return bool(np.median(bv / av) <= 1 + threshold), 5


def decide_median5_band(
    a: Sequence[float], b: Sequence[float], delta: float = 0.05
) -> tuple[bool, int]:
    """Approve unless the five-pair median exceeds the permitted band."""
    return _median(a, b, delta)


def decide_threshold10(a: Sequence[float], b: Sequence[float]) -> tuple[bool, int]:
    """Apply the fixed +10% five-pair median rule."""
    return _median(a, b, 0.10)


def decide_threshold2x(a: Sequence[float], b: Sequence[float]) -> tuple[bool, int]:
    """Apply the fixed two-times five-pair median rule."""
    return _median(a, b, 1.0)


def decide_ttest_fixed(
    a: Sequence[float], b: Sequence[float], delta: float = 0.05, alpha: float = 0.05
) -> tuple[bool, int]:
    """Fail-open paired t-test of b-(1+delta)*a > 0 on ten latency pairs."""
    av, bv = _prefix(a, b, 10)
    differences = bv - (1 + delta) * av
    mean, sd = float(differences.mean()), float(differences.std(ddof=1))
    p = (0.0 if mean > 0 else 1.0) if sd == 0 else float(t.sf(mean / (sd / math.sqrt(10)), 9))
    return not (p < alpha), 10


def decide_advisor_guard(
    a: Sequence[float], b: Sequence[float], delta: float = 0.05, alpha: float = 0.05
) -> tuple[bool, int]:
    """Approve only a sequential non-inferiority PASS within 60 pairs."""
    av, bv = _prefix(a, b, 60)
    d = paired_log_ratios(av, bv)
    test = SequentialTest(delta, alpha, 19, 1, 6, 60)
    test.add(d[:6])
    used = 6
    while test.state == "CONTINUE" and used < 60:
        test.add(d[used : used + 3])
        used += 3
    return test.state == "PASS", used


def wilson(k: int, n: int) -> list[float]:
    """Return the two-sided Wilson 95% binomial confidence interval."""
    if n <= 0 or k < 0 or k > n:
        raise ValueError("Wilson counts require 0 <= k <= n and n > 0")
    z = 1.959963984540054
    p, denom = k / n, 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [
        0.0 if k == 0 else max(0.0, center - radius),
        1.0 if k == n else min(1.0, center + radius),
    ]


def run_cell(
    rng: np.random.Generator,
    noise: float,
    thetas: Sequence[float],
    trials: int,
    delta: float = 0.05,
    alpha: float = 0.05,
) -> dict:
    """Collect equally weighted per-theta trials with common pairs for all methods."""
    if trials < 1 or not thetas:
        raise ValueError("At least one theta and trial are required")
    methods: dict[str, Callable] = {
        "single_run": lambda a, b: decide_single_run(a, b, delta),
        "median5_band": lambda a, b: decide_median5_band(a, b, delta),
        "threshold10": decide_threshold10,
        "threshold2x": decide_threshold2x,
        "ttest_fixed": lambda a, b: decide_ttest_fixed(a, b, delta, alpha),
        "advisor_guard": lambda a, b: decide_advisor_guard(a, b, delta, alpha),
    }
    observations: dict[str, dict[str, list]] = {name: {} for name in methods}
    for theta in thetas:
        key = str(float(theta))
        for name in methods:
            observations[name][key] = []
        for _ in range(trials):
            a, b = simulate_pairs(rng, theta, noise, 60)
            for name, method in methods.items():
                observations[name][key].append(method(a, b))
    return observations


def _aggregate(cells: dict, harmful: Sequence[float], harmless: Sequence[float]) -> dict:
    results = {}
    for name, by_theta in cells.items():
        bad = [obs for theta in harmful for obs in by_theta[str(float(theta))]]
        good = [obs for theta in harmless for obs in by_theta[str(float(theta))]]
        all_obs = bad + good
        false_apply = sum(approve for approve, _ in bad)
        false_block = sum(not approve for approve, _ in good)
        runs = [n for _, n in all_obs]
        results[name] = {
            "false_apply": false_apply / len(bad),
            "false_apply_ci": wilson(false_apply, len(bad)),
            "false_block": false_block / len(good),
            "false_block_ci": wilson(false_block, len(good)),
            "harmful_trials": len(bad),
            "harmless_trials": len(good),
            "false_apply_count": false_apply,
            "false_block_count": false_block,
            "mean_runs": float(np.mean(runs)),
            "runs_p90": float(np.quantile(runs, 0.9)),
            "by_theta": {
                theta: {
                    "approve_rate": sum(x[0] for x in obs) / len(obs),
                    "mean_runs": float(np.mean([x[1] for x in obs])),
                    "trials": len(obs),
                }
                for theta, obs in by_theta.items()
            },
        }
    return results


def run_rq2(
    trials: int = 400,
    seed: int = 1234,
    noise_levels: Sequence[float] = (0.0, 0.05, 0.10),
    delta: float = 0.05,
    alpha: float = 0.05,
    borderline: bool = False,
) -> dict:
    """Run all noise cells, keeping optional borderline cases out of headline rates."""
    if trials < 1 or seed < 0 or not 0 < delta < 1 or not 0 < alpha < 0.5:
        raise ValueError("Invalid simulation policy")
    if not noise_levels or len(set(noise_levels)) != len(noise_levels):
        raise ValueError("Noise levels must be nonempty and unique")
    if any(not math.isfinite(x) or x < 0 for x in noise_levels):
        raise ValueError("Noise must be finite and nonnegative")
    # Keep predefined categories honest when CLI delta changes.
    if any(x <= delta for x in HARMFUL) or any(x >= delta for x in HARMLESS):
        raise ValueError("Delta must keep fixed harmless/harmful sets on their declared sides")
    seeds = np.random.SeedSequence(seed).spawn(len(noise_levels))
    result: dict = {
        "meta": {
            "trials": trials,
            "seed": seed,
            "delta": delta,
            "alpha": alpha,
            "noise_levels": list(noise_levels),
            "harmful": list(HARMFUL),
            "harmless": list(HARMLESS),
            "created_at": datetime.now(UTC).isoformat(),
            "trials_unit": "per theta per noise",
            "guard": {"min_n": 6, "max_n": 60, "batch": 3, "looks": 19, "family": 1},
            "noise_model": "shared log SD=noise/2; independent log SD=noise; base=100 ms",
            "decider_labels": {"advisor_guard": "DB Performance Advisor"},
            "ttest_fixed": "one-sided paired latency difference b-(1+delta)*a; fail open",
            "seed_scheme": "SeedSequence(seed).spawn(noise_count); append preserves earlier cells",
        },
        "results": {},
    }
    if borderline:
        result["borderline"] = {}
    for noise, cell_seed in zip(noise_levels, seeds, strict=True):
        # Split headline and borderline streams so enabling the latter never shifts headlines.
        main_seed, border_seed = cell_seed.spawn(2)
        cells = run_cell(
            np.random.default_rng(main_seed), noise, HARMFUL + HARMLESS, trials, delta, alpha
        )
        key = str(float(noise))
        result["results"][key] = _aggregate(cells, HARMFUL, HARMLESS)
        if borderline:
            border = run_cell(
                np.random.default_rng(border_seed), noise, BORDERLINE, trials, delta, alpha
            )
            result["borderline"][key] = {
                name: {
                    theta: {
                        "approve_rate": sum(x[0] for x in obs) / len(obs),
                        "mean_runs": float(np.mean([x[1] for x in obs])),
                        "trials": len(obs),
                    }
                    for theta, obs in values.items()
                }
                for name, values in border.items()
            }
    return result


def main() -> None:
    """Write measured JSON atomically and print a compact chart-ready table."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=400)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--noise", default="0,0.05,0.1")
    parser.add_argument("--delta", type=float, default=0.05)
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--borderline", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("../experiments/results/rq2_sim.json"))
    args = parser.parse_args()
    try:
        result = run_rq2(
            args.trials,
            args.seed,
            [float(x) for x in args.noise.split(",")],
            args.delta,
            args.alpha,
            args.borderline,
        )
    except ValueError as exc:
        parser.error(str(exc))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=args.out.parent, prefix=args.out.name + ".", suffix=".tmp", delete=False
        ) as handle:
            temporary = handle.name
            json.dump(result, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, args.out)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    print("noise,decider,false_apply,false_block,mean_runs,runs_p90")
    for noise, methods in result["results"].items():
        for name, row in methods.items():
            print(
                f"{noise},{name},{row['false_apply']:.6f},{row['false_block']:.6f},"
                f"{row['mean_runs']:.3f},{row['runs_p90']:.1f}"
            )


if __name__ == "__main__":
    main()
