"""Approval, live index lifecycle and conservative monitoring acceptance."""

import pytest

import app.monitor as monitor
from app.config import get_settings
from app.errors import Conflict, SafetyRejected, SandboxError
from app.models import Candidate
from app.pg import connect
from app.store import Store


def _snapshot(calls=100, mean=1.0, sd=0.1):
    return {
        "taken_at": "2026-10-30T00:00:00+00:00",
        "groups": {
            "txn:stock_decrement": {
                "calls": calls,
                "total_ms": calls * mean,
                "sum_sq_ms": calls * (sd**2 + mean**2),
                "mean_ms": mean,
            }
        },
    }


def _row():
    return {"baseline": _snapshot(), "windows": []}


def test_clean_insufficient_and_reset_windows():
    for current, expected in [
        (_snapshot(150), "clean"),
        (_snapshot(101), "insufficient"),
        (_snapshot(1), "reset"),
    ]:
        result = monitor.evaluate_window(_row(), current, delta=0.05, min_calls=30, consecutive=2)
        assert result["groups"]["txn:stock_decrement"]["state"] == expected
        assert not result["revert"]


def test_two_regressed_windows_and_clean_reset_streak():
    row = _row()
    first = monitor.evaluate_window(
        row, _snapshot(200, 1.2), delta=0.05, min_calls=30, consecutive=2
    )
    assert first["state"] == "watching" and not first["revert"]
    row["windows"] = [first]
    second = monitor.evaluate_window(
        row, _snapshot(300, 1.3), delta=0.05, min_calls=30, consecutive=2
    )
    assert second["revert"]
    clean = monitor.evaluate_window(
        row, _snapshot(300, 340 / 300), delta=0.05, min_calls=30, consecutive=2
    )
    assert not clean["revert"] and clean["streak"] == 0


def test_z_gate_requires_variance_evidence():
    result = monitor.evaluate_window(
        _row(), _snapshot(200, 1.2, 100), delta=0.05, min_calls=30, consecutive=1, z_gate=True
    )
    assert not result["revert"]


def _fixture(store, kind="index", statement="CREATE INDEX ON store_stock(qty)"):
    job = store.create_job({"test_fixture": True})
    candidate = Candidate(
        job + ":recent_orders:01",
        job,
        "recent_orders",
        kind,
        statement,
        "Explicit planted test fixture; not an advisor recommendation",
        0,
        status="selected",
        guard={"passed": True, "test_fixture": True},
        equiv={"label": "tested", "weak": False},
    )
    store.save_candidate(candidate)
    store.save_evidence(
        job,
        "recent_orders",
        {
            "shape_id": "recent_orders",
            "baseline": {"sql": "SELECT qty FROM store_stock"},
            "candidates": [candidate.to_dict()],
            "decision": {"candidate_id": candidate.id},
        },
    )
    return candidate


@pytest.mark.db
def test_apply_idempotent_and_manual_revert(tmp_path, monkeypatch):
    store = Store(tmp_path / "meta.sqlite")
    candidate = _fixture(store)
    monkeypatch.setattr(monitor, "capture_baseline", lambda _: _snapshot())
    applied = monitor.apply(store, candidate.id)
    try:
        assert store.get_approval(candidate.id)
        assert "CONCURRENTLY" in applied["ddl"]
        assert monitor.apply(store, candidate.id)["id"] == applied["id"]
        name = monitor._index_sql(candidate)[0]
        assert monitor._index_state(name) is True
    finally:
        monitor.revert(store, applied["id"], "test cleanup")
    assert monitor._index_state(name) is None
    assert store.get_candidate(candidate.id).status == "reverted"
    store.close()


@pytest.mark.db
def test_apply_rejects_missing_evidence_and_rechecks_safety(tmp_path):
    store = Store(tmp_path / "meta.sqlite")
    candidate = _fixture(store, statement="DROP TABLE orders")
    with pytest.raises(SafetyRejected):
        monitor.apply(store, candidate.id)
    assert store.get_approval(candidate.id) is None
    store.save_evidence(
        candidate.job_id,
        candidate.shape_id,
        {"shape_id": candidate.shape_id, "decision": {"candidate_id": None}},
    )
    with pytest.raises(Conflict):
        monitor.apply(store, candidate.id)
    store.close()


@pytest.mark.db
def test_invalid_index_cleanup(tmp_path, monkeypatch):
    store = Store(tmp_path / "meta.sqlite")
    candidate = _fixture(store)
    monkeypatch.setattr(monitor, "capture_baseline", lambda _: _snapshot())
    real_state = monitor._index_state
    calls = 0

    def invalid_after_build(name):
        nonlocal calls
        calls += 1
        return False if calls == 2 else real_state(name)

    monkeypatch.setattr(monitor, "_index_state", invalid_after_build)
    with pytest.raises(SandboxError):
        monitor.apply(store, candidate.id)
    assert real_state(monitor._index_sql(candidate)[0]) is None
    assert store.list_applied()[0]["state"] == "reverted"
    store.close()


@pytest.mark.db
def test_rewrite_apply_no_db_change(tmp_path):
    store = Store(tmp_path / "meta.sqlite")
    candidate = _fixture(store, "rewrite", "SELECT qty FROM store_stock")
    with connect(get_settings().live_db) as conn:
        before = conn.execute("SELECT indexname FROM pg_indexes ORDER BY indexname").fetchall()
    applied = monitor.apply(store, candidate.id)
    assert applied["state"] == "healthy" and applied["windows"] == []
    with connect(get_settings().live_db) as conn:
        assert (
            conn.execute("SELECT indexname FROM pg_indexes ORDER BY indexname").fetchall() == before
        )
    monitor.revert(store, applied["id"], "withdraw rewrite")
    store.close()


@pytest.mark.db
def test_weak_rewrite_requires_explicit_confirmation(tmp_path):
    store = Store(tmp_path / "meta.sqlite")
    candidate = _fixture(store, "rewrite", "SELECT qty FROM store_stock")
    candidate.equiv["weak"] = True
    store.save_candidate(candidate)
    with pytest.raises(Conflict):
        monitor.apply(store, candidate.id)
    assert store.get_approval(candidate.id) is None
    applied = monitor.apply(store, candidate.id, confirm=True)
    assert store.get_approval(candidate.id)["confirmed"] == 1
    monitor.revert(store, applied["id"], "test cleanup")
    store.close()


@pytest.mark.db
@pytest.mark.slow
@pytest.mark.timeout(120)
def test_controlled_demo_reverts(tmp_path, monkeypatch):
    import importlib.util
    from dataclasses import replace
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "demo_revert", Path(__file__).parents[1] / "scripts/demo_revert.py"
    )
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    settings = replace(get_settings(), baseline_seconds=3)
    monkeypatch.setattr(monitor, "get_settings", lambda: settings)
    store = Store(tmp_path / "meta.sqlite")
    result = demo.run_demo(store, interval_s=2, max_windows=10, controlled_delay=True)
    assert result["automatic_revert"]
    assert result["index_absent"]
    assert result["final_state"] == "reverted"
    assert result["traffic_error"] is None
    store.close()
