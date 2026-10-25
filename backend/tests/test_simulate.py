"""Simulation contracts, reproducibility, calibration and CLI checks."""

import json
import subprocess
import sys
from time import perf_counter

import numpy as np
import pytest

from app.simulate import (
    DECIDERS,
    decide_advisor_guard,
    decide_ttest_fixed,
    run_rq2,
    simulate_pairs,
    wilson,
)


@pytest.fixture(scope="module")
def measured():
    started = perf_counter()
    result = run_rq2(400, 1234)
    return result, perf_counter() - started


def test_reproducible():
    a, b = run_rq2(5, 19), run_rq2(5, 19)
    a["meta"].pop("created_at")
    b["meta"].pop("created_at")
    assert a == b
    extended = run_rq2(5, 19, (0, 0.05, 0.1, 0.2), borderline=True)
    assert all(extended["results"][k] == v for k, v in a["results"].items())
    assert "borderline" not in a
    assert set(extended["borderline"]["0.0"]["advisor_guard"]) == {"0.04", "0.05", "0.06"}


def test_shapes(measured):
    result, _ = measured
    assert set(result["results"]) == {"0.0", "0.05", "0.1"}
    for cell in result["results"].values():
        assert set(cell) == set(DECIDERS)
        for row in cell.values():
            for key in ("false_apply", "false_block"):
                assert 0 <= row[key] <= 1
                assert row[key + "_ci"][0] <= row[key] <= row[key + "_ci"][1]
            assert row["harmful_trials"] == 1600
            assert row["harmless_trials"] == 1200
            assert len(row["by_theta"]) == 7
            assert 1 <= row["mean_runs"] <= 60


def test_advisor_false_apply_bounded():
    result = run_rq2(300, 1234, (0.1,))
    assert result["results"]["0.1"]["advisor_guard"]["false_apply"] < 0.02


def test_simple_rules_climb(measured):
    r = measured[0]["results"]
    assert r["0.1"]["single_run"]["false_apply"] > r["0.0"]["single_run"]["false_apply"]


def test_no_noise_sanity(measured):
    for name, row in measured[0]["results"]["0.0"].items():
        # +25% is rejected even by threshold10; only the 2x rule accepts it.
        expected = 1 if name == "threshold2x" else 0
        assert row["by_theta"]["0.25"]["approve_rate"] == expected


def test_false_block_cost_reported(measured):
    r = measured[0]["results"]
    assert r["0.1"]["advisor_guard"]["false_block"] >= r["0.0"]["advisor_guard"]["false_block"]


def test_runtime(measured):
    assert measured[1] < 60
    print(f"simulation_400_trials_seconds={measured[1]:.3f}")


def test_noise_and_decider_controls():
    a, b = simulate_pairs(np.random.default_rng(7), 0.1, 0, 60)
    assert b / a == pytest.approx(np.full(60, 1.1))
    assert decide_advisor_guard(a, b) == (False, 6)
    assert decide_ttest_fixed(a, b) == (False, 10)
    # Non-significance is accepted by the deliberately fail-open comparator.
    assert decide_ttest_fixed(a, a) == (True, 10)
    assert wilson(0, 100)[0] == 0
    assert wilson(100, 100)[1] == pytest.approx(1)
    with pytest.raises(ValueError):
        wilson(1, 0)
    with pytest.raises(ValueError):
        run_rq2(1, 1, delta=0.1)
    with pytest.raises(ValueError):
        run_rq2(1, 1, noise_levels=[-1])


def test_cli_atomic_output(tmp_path):
    output = tmp_path / "result.json"
    run = subprocess.run(
        [sys.executable, "-m", "app.simulate", "--trials", "2", "--out", str(output)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "noise,decider,false_apply" in run.stdout
    result = json.loads(output.read_text())
    assert result["meta"]["trials"] == 2
    assert not list(tmp_path.glob("*.tmp"))
