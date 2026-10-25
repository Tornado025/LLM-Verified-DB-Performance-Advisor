"""Real read-only measurements, counterbalancing, budgets and timeout handling."""

from dataclasses import replace

import numpy as np
import pytest

from app.config import get_settings
from app.errors import BadRequest
from app.measure import QueryTimeout, paired_report, run_once, screen_with_buffers
from app.noise import NoiseInjector
from app.pg import connect
from app.sandbox import single_clone

POLICY = dict(
    metric="buffers", delta=0.05, alpha=0.05, looks=19, family=1, min_n=6, max_n=60, batch=3
)
QUERY = "/* dpa:shape=recent_orders */ SELECT order_id FROM orders WHERE customer_id=1 LIMIT 20"


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM orders",
        "SELECT 1; DELETE FROM orders",
        "WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x",
        "SELECT * INTO accidental FROM orders",
        "SELECT * FROM orders FOR UPDATE",
    ],
)
def test_reject_non_select(query):
    from app.measure import _select_only

    with pytest.raises(BadRequest):
        _select_only(query)


@pytest.mark.db
def test_run_once_time_and_buffers(live_conn):
    assert run_once(live_conn, QUERY, "time") > 0
    assert run_once(live_conn, QUERY, "buffers") > 0


@pytest.mark.db
def test_paired_report_aa_passes(live_conn):
    stable = "SELECT count(*) FROM stores"
    result = paired_report(live_conn, stable, live_conn, stable, **POLICY)
    assert result.summary.state == "PASS"
    assert len(result.a_vals) == len(result.b_vals) == len(result.d)
    assert result.summary.mean_rel == pytest.approx(0)
    assert screen_with_buffers(live_conn, stable, live_conn, stable, **POLICY).metric == "buffers"


@pytest.mark.db
def test_paired_report_detects_slowdown(live_conn):
    # Count all rows, twice, instead of a selective LIMIT query; buffer metric
    # keeps this planted slowdown test independent of incidental host scheduling.
    heavy = "SELECT count(*) FROM orders CROSS JOIN generate_series(1,2)"
    assert paired_report(live_conn, QUERY, live_conn, heavy, **POLICY).summary.state == "FAIL"


@pytest.mark.db
def test_timeout_is_not_successful_sample(live_conn, monkeypatch):
    monkeypatch.setattr(
        "app.measure.get_settings", lambda: replace(get_settings(), stmt_timeout_ms=10)
    )
    with pytest.raises(QueryTimeout) as exc:
        run_once(live_conn, "SELECT pg_sleep(0.1)")
    assert exc.value.timeout_ms == 10
    assert live_conn.execute("SELECT 1").fetchone()[0] == 1


@pytest.mark.db
def test_expired_budget_returns_uncertain(live_conn):
    result = paired_report(live_conn, QUERY, live_conn, QUERY, **POLICY, budget_s=1e-9)
    assert result.summary.state == "CONTINUE" and not result.d


def test_zero_noise_and_validation():
    with NoiseInjector(0) as injector:
        assert not injector.processes
    with pytest.raises(ValueError):
        NoiseInjector(-1)


@pytest.mark.db
@pytest.mark.slow
def test_noise_injector_increases_variance():
    # Host duty cycle is indirect interference on Docker Desktop. Explicitly
    # report an inconclusive host experiment as xfail, never force a pass.
    query = "SELECT sum(total) FROM orders"
    deviations = {0.0: [], 0.1: []}
    with single_clone(get_settings()) as name, connect(name) as conn:
        for repetition in range(3):
            for level in (0.0, 0.1):
                with NoiseInjector(level, cpu_workers=2, seed=700 + repetition):
                    from time import sleep

                    sleep(1)
                    a, b = [], []
                    for i in range(20):
                        values = {
                            side: run_once(conn, query)
                            for side in (("a", "b") if i % 2 == 0 else ("b", "a"))
                        }
                        a.append(values["a"])
                        b.append(values["b"])
                    deviations[level].append(float(np.std(np.log(np.array(b) / a), ddof=1)))
    if np.mean(deviations[0.1]) <= np.mean(deviations[0.0]):
        pytest.xfail(f"Host duty did not measurably increase paired variance: {deviations}")
    assert np.mean(deviations[0.1]) > np.mean(deviations[0.0])


def test_counterbalance_and_warmup_excluded(monkeypatch):
    calls = []
    a, b = object(), object()

    def fake(conn, query, metric="time"):
        calls.append("a" if conn is a else "b")
        return 10.0

    monkeypatch.setattr("app.measure.run_once", fake)
    result = paired_report(a, "SELECT 1", b, "SELECT 1", **POLICY)
    assert calls[:4] == ["a", "a", "b", "b"]
    assert calls[4:] == ["a", "b", "b", "a"] * 3
    assert result.a_vals == result.b_vals == [10.0] * 6


def test_timeout_side_propagates(monkeypatch):
    a, b = object(), object()

    def fake(conn, query, metric="time"):
        if conn is b:
            raise QueryTimeout(100)
        return 10.0

    monkeypatch.setattr("app.measure.run_once", fake)
    with pytest.raises(QueryTimeout) as exc:
        paired_report(a, "SELECT 1", b, "SELECT 1", **POLICY)
    assert exc.value.side == "b"
