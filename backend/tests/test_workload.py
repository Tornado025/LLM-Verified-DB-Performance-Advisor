"""Poisson schedule properties and real sandbox workload behavior."""

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import chisquare

from app.config import get_settings
from app.models import Arrival
from app.pg import connect
from app.sandbox import single_clone
from app.shapes import ORDER_TXNS
from app.stats import summarize
from app.workload import (
    Schedule,
    build_schedule,
    load_pools,
    load_profiles,
    prepare_clone,
    run_block,
)


@pytest.fixture
def pools():
    return {
        "customer_ids": list(range(1, 501)),
        "store_ids": list(range(1, 51)),
        "product_ids": list(range(1, 2001)),
        "hot_product_ids": list(range(1, 51)),
        "active_order_ids": list(range(1, 10001)),
        "prices": {p: "10.00" for p in range(1, 2001)},
    }


def test_schedule_deterministic(pools):
    profile = load_profiles()["normal"]
    a = build_schedule(profile, 20, 1234, 20, pools)
    b = build_schedule(profile, 20, 1234, 20, pools)
    assert a == b and a != build_schedule(profile, 20, 1235, 20, pools)
    assert isinstance(a, Schedule) and a.seconds == 20
    assert all(a[i].t < a[i + 1].t for i in range(len(a) - 1))
    for field in ("zipf_s", "rate_mult", "status_poll_mult", "dashboard_refresh_s"):
        with pytest.raises(ValueError):
            build_schedule(replace(profile, **{field: float("nan")}), 20, 1234, 20, pools)


def test_aa_resume_rejects_changed_policy_before_database_work(tmp_path):
    from scripts.validate_workload_aa import run_aa

    checkpoint = tmp_path / "aa.json"
    with pytest.raises(ValueError, match="existing checkpoint"):
        run_aa(41, out=checkpoint, resume=True)
    checkpoint.write_text(json.dumps({"meta": {"seed": 1234, "base_rate": 999}}))
    with pytest.raises(ValueError, match="base_rate"):
        run_aa(41, out=checkpoint, resume=True)


def test_schedule_rate_and_type_frequencies(pools):
    profile = load_profiles()["normal"]
    schedule = build_schedule(profile, 100, 77, 40, pools)
    assert abs(len(schedule) / 4000 - 1) < 0.1
    from app.workload import _weights

    names, weights = _weights(profile)
    counts = np.array([sum(a.txn == name for a in schedule) for name in names])
    assert chisquare(counts, weights * len(schedule)).pvalue > 0.01


def test_hot_share(pools):
    profile = load_profiles()["normal"]
    schedule = build_schedule(profile, 100, 78, 40, pools)
    lines = [line for a in schedule if a.txn == "place_order" for line in a.params["lines"]]
    share = sum(line["p"] <= 50 for line in lines) / len(lines)
    assert abs(share - profile.hot_share) < 0.05
    assert all(2 <= len(a.params["lines"]) <= 4 for a in schedule if a.txn == "place_order")


@pytest.mark.db
def test_run_block_smoke():
    profile = load_profiles()["normal"]
    with single_clone(get_settings()) as name:
        prepare_clone(name, profile, 1234)
        pools = load_pools(name, profile)
        # Choose a seeded smoke schedule containing all five types; Poisson traffic
        # at 5 TPS has a nonzero probability of missing the rare 5% type in 5 sec.
        for seed in range(100):
            schedule = build_schedule(profile, 5, seed, 5, pools)
            if len({arrival.txn for arrival in schedule}) == 5:
                break
        else:
            pytest.fail("No five-type smoke schedule found")
        result = run_block(name, schedule, 4, record_timeline=True)
        assert result.metadata["valid"] and result.dropped == 0, result
        assert all(
            result.latencies[t]
            for t in (
                "place_order",
                "update_status",
                "order_status",
                "stock_decrement",
                "picker_queue_read",
            )
        )
        assert len(result.metadata["timeline"]) == len(schedule)


@pytest.mark.db
def test_errors_counted_not_raised():
    with single_clone(get_settings()) as name:
        result = run_block(name, [Arrival(0.1, "place_order", {})], 2)
        assert result.errors["place_order"] == 1
        assert result.latencies["place_order"]


@pytest.mark.db
def test_prepare_clone_skip_analyze():
    with single_clone(get_settings()) as name, connect(name) as conn:
        before = conn.execute(
            "SELECT last_analyze FROM pg_stat_user_tables WHERE relname='products'"
        ).fetchone()[0]
        evidence = prepare_clone(name, load_profiles()["flash20"], 1234)
        after = conn.execute(
            "SELECT last_analyze FROM pg_stat_user_tables WHERE relname='products'"
        ).fetchone()[0]
        assert before == after and evidence["analyzed"] is False
        assert conn.execute("SELECT count(*) FROM products").fetchone()[0] == 4000
        assert (
            conn.execute("SELECT min(qty) FROM store_stock WHERE product_id<=50").fetchone()[0]
            == 1000000
        )


@pytest.mark.db
@pytest.mark.slow
@pytest.mark.timeout(7200)
def test_aa_validation():
    """Recompute acceptance from measured raw pairs, running it if evidence is absent."""
    path = Path(__file__).resolve().parents[2] / "experiments/results/workload_aa.json"
    if not path.exists():
        from scripts.validate_workload_aa import run_aa

        run_aa(get_settings().base_rate_tps, out=path)
    report = json.loads(path.read_text())
    assert report["passed"], "A/A evidence is incomplete or failed; rerun validation CLI"
    assert report["meta"]["base_rate"] == get_settings().base_rate_tps
    assert report["meta"]["delta"] == 0.05 and report["meta"]["alpha"] == 0.05
    assert report["meta"]["family"] == 10 and report["meta"]["looks"] == 35
    assert set(report["profiles"]) == {"normal", "flash5"}
    for profile in report["profiles"].values():
        assert 12 <= len(profile["blocks"]) <= 40
        for txn in ORDER_TXNS:
            observations = []
            for i, block in enumerate(profile["blocks"]):
                assert block["order"] == (["a", "b"] if i % 2 == 0 else ["b", "a"])
                assert block["a"]["metadata"]["valid"] and block["b"]["metadata"]["valid"]
                assert block["clone_a"]["copy_strategy"] == "FILE_COPY"
                assert block["clone_b"]["copy_strategy"] == "FILE_COPY"
                for side in ("a", "b"):
                    assert block[side]["errors"].get("dashboard", 0) == 0
                    assert block[side]["latencies"]["report:hourly_sales"]
                    assert block[side]["latencies"]["report:stockouts_15m"]
                av, bv = block["a"]["latencies"][txn], block["b"]["latencies"][txn]
                if min(len(av), len(bv)) >= get_settings().min_samples_per_block:
                    observations.append(float(np.log(np.mean(bv) / np.mean(av))))
            meta = report["meta"]
            result = summarize(
                observations, meta["delta"], meta["alpha"], meta["looks"], meta["family"], 6, 40
            )
            assert abs(np.mean(observations)) < 0.03, txn
            assert result.state == "PASS", (txn, result)
            assert result.n == profile["cells"][txn]["n"]
