"""Guard policy and fail-closed behavior with controlled paired blocks."""

import json
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.errors import BadRequest
from app.guard import estimate_guard_seconds, run_guard
from app.models import BlockResult, Candidate
from app.shapes import ORDER_TXNS


def _settings():
    return replace(
        get_settings(),
        min_runs=3,
        max_runs=3,
        race_batch=1,
        max_blocks=3,
        max_clones=2,
        min_samples_per_block=5,
        block_seconds=1,
        guard_profiles=["normal"],
    )


def _candidate():
    return Candidate(
        id="candidate-1",
        job_id="job",
        shape_id="recent_orders",
        kind="index",
        sql="CREATE INDEX city_guard_idx ON customers(city)",
        rationale="test",
        sample_idx=1,
    )


def _block(*, valid=True, place_order_errors=0, latency=10.0):
    return BlockResult(
        seconds=1,
        latencies={txn: [latency] * 5 for txn in ORDER_TXNS},
        errors={txn: (place_order_errors if txn == "place_order" else 0) for txn in ORDER_TXNS},
        dropped=0,
        metadata={
            "valid": valid,
            "oos": {txn: 0 for txn in ORDER_TXNS},
            "actual_seconds": 1,
        },
    )


def _db_progress(event):
    """Show bounded progress for long real-database guard checks."""
    block = event.get("block", 0)
    if block == 1 or block % 5 == 0 or event.get("state") in {"FAIL", "BLOCK"}:
        print("guard progress " + json.dumps(event, sort_keys=True), flush=True)


def _db_result(label, result):
    """Show the measured verdict and confidence bounds for the acceptance record."""
    print(
        "guard result "
        + json.dumps(
            {
                "case": label,
                "passed": result.passed,
                "failed_cell": result.failed_cell,
                "seconds": result.seconds,
                "blocks": result.blocks,
                "cells": {profile: data["cells"] for profile, data in result.profiles.items()},
            },
            sort_keys=True,
        ),
        flush=True,
    )


@contextmanager
def _clone_pair(*_args, **_kwargs):
    yield SimpleNamespace(a="clone-a", b="clone-b")


def _wire_guard(monkeypatch, *, invalid=False, candidate_errors=0, candidate_ratio=1.0):
    import app.guard as module

    monkeypatch.setattr(module, "clone_pair", _clone_pair)
    monkeypatch.setattr(module, "prepare_clone", lambda *args: {"analyzed": True})
    monkeypatch.setattr(module, "load_pools", lambda *args: {})
    monkeypatch.setattr(
        module,
        "build_schedule",
        lambda *args: [SimpleNamespace(txn=txn) for txn in ORDER_TXNS for _ in range(5)],
    )

    def run_block(*args):
        is_b = args[0] == "clone-b"
        return _block(
            valid=not invalid,
            place_order_errors=candidate_errors if is_b else 0,
            latency=10.0 * candidate_ratio if is_b else 10.0,
        )

    monkeypatch.setattr(module, "run_block", run_block)
    monkeypatch.setattr(module.time, "sleep", lambda *_args: None)


def test_guard_passes_when_all_paired_cells_are_noninferior(monkeypatch):
    _wire_guard(monkeypatch)
    result = run_guard(_candidate(), ["normal"], _settings(), seed=1234)
    assert result.passed
    assert result.failed_cell is None
    assert result.blocks == {"normal": 3}
    assert set(result.profiles["normal"]["cells"]) == set(ORDER_TXNS)
    assert all(c["state"] == "PASS" for c in result.profiles["normal"]["cells"].values())


def test_guard_fails_immediately_when_candidate_adds_errors(monkeypatch):
    _wire_guard(monkeypatch, candidate_errors=1)
    events = []
    result = run_guard(_candidate(), ["normal"], _settings(), seed=1234, progress=events.append)
    assert not result.passed
    assert result.failed_cell == ("normal", "place_order")
    assert result.profiles["normal"]["cells"]["place_order"]["reason"] == "errors_increased"
    assert result.blocks == {"normal": 1}
    assert events[-1]["state"] == "FAIL"
    assert events[-1]["failed_cell"] == ("normal", "place_order")


def test_statistical_failure_keeps_its_confidence_bounds(monkeypatch):
    _wire_guard(monkeypatch, candidate_ratio=1.5)
    result = run_guard(_candidate(), ["normal"], _settings(), seed=1234)
    assert not result.passed
    assert result.failed_cell is not None
    failed = result.profiles["normal"]["cells"][result.failed_cell[1]]
    assert failed["state"] == "FAIL"
    assert failed["reason"] == "slowdown_exceeds_delta"
    assert failed["n"] == 3
    assert failed["lower"] > 0.05
    assert failed["upper"] >= failed["lower"]


def test_invalid_measurement_blocks_instead_of_passing(monkeypatch):
    _wire_guard(monkeypatch, invalid=True)
    result = run_guard(_candidate(), ["normal"], _settings(), seed=1234)
    assert not result.passed
    assert result.failed_cell is None
    assert all(c["state"] == "BLOCK" for c in result.profiles["normal"]["cells"].values())


def test_guard_estimate_and_profile_validation():
    settings = _settings()
    assert estimate_guard_seconds(settings, ["normal", "flash5"]) == 12
    with pytest.raises(BadRequest):
        estimate_guard_seconds(settings, ["normal", "normal"])


def test_guard_blocks_noisy_undecidable_cells(monkeypatch):
    settings = replace(_settings(), max_runs=8, max_blocks=8)
    import app.guard as module

    _wire_guard(monkeypatch)
    calls = 0

    def noisy_block(*_args):
        nonlocal calls
        block = calls // 2
        first_side = calls % 2 == 0
        calls += 1
        reverse = block % 2 == 1
        is_b = first_side == reverse
        ratio = 1.1 if block % 2 == 0 else 1 / 1.1
        return BlockResult(
            seconds=1,
            latencies={txn: [10.0 * (ratio if is_b else 1.0)] * 5 for txn in ORDER_TXNS},
            errors={txn: 0 for txn in ORDER_TXNS},
            dropped=0,
            metadata={"valid": True, "oos": {txn: 0 for txn in ORDER_TXNS}},
        )

    monkeypatch.setattr(module, "run_block", noisy_block)
    result = run_guard(_candidate(), ["normal"], settings, seed=1234)
    assert not result.passed
    assert all(cell["state"] == "BLOCK" for cell in result.profiles["normal"]["cells"].values())


@pytest.mark.db
@pytest.mark.slow
@pytest.mark.timeout(900)
def test_guard_harmless_index_verdict_matches_transaction_evidence():
    # The full four-profile, 40/80-block study is recorded under experiments/results.
    settings = replace(get_settings(), guard_profiles=["normal"], max_blocks=8)
    candidate = _candidate()
    result = run_guard(
        candidate, settings.guard_profiles, settings, seed=1234, progress=_db_progress
    )
    _db_result("harmless", result)
    all_pass = all(
        cell["state"] == "PASS"
        for profile in result.profiles.values()
        for cell in profile["cells"].values()
    )
    assert result.passed == all_pass
    if result.failed_cell is not None:
        profile, txn = result.failed_cell
        assert result.profiles[profile]["cells"][txn]["state"] == "FAIL"
    elif not result.passed:
        assert any(
            cell["state"] == "BLOCK"
            for profile in result.profiles.values()
            for cell in profile["cells"].values()
        )


@pytest.mark.db
@pytest.mark.slow
@pytest.mark.timeout(900)
def test_guard_hot_stock_index_verdict_matches_transaction_evidence():
    settings = replace(get_settings(), guard_profiles=["flash5"], max_blocks=8)
    candidate = _candidate()
    candidate.sql = "CREATE INDEX guard_hot_qty_idx ON store_stock(qty)"
    result = run_guard(
        candidate, settings.guard_profiles, settings, seed=4321, progress=_db_progress
    )
    _db_result("hot_stock", result)
    all_pass = all(
        cell["state"] == "PASS"
        for profile in result.profiles.values()
        for cell in profile["cells"].values()
    )
    assert result.passed == all_pass
    if result.failed_cell is not None:
        profile, txn = result.failed_cell
        assert result.profiles[profile]["cells"][txn]["state"] == "FAIL"
    elif not result.passed:
        assert any(
            cell["state"] == "BLOCK"
            for profile in result.profiles.values()
            for cell in profile["cells"].values()
        )


@pytest.mark.db
@pytest.mark.slow
@pytest.mark.timeout(7200)
def test_guard_redundant_write_indexes_fail(monkeypatch):
    from app import sandbox
    from app.pg import connect

    settings = replace(get_settings(), guard_profiles=["normal", "flash5"])
    candidate = _candidate()
    candidate.sql = "CREATE INDEX guard_heavy_1 ON orders(customer_id, created_at)"
    original_apply = sandbox.apply_ddl
    extras = (
        "CREATE INDEX guard_heavy_2 ON orders(store_id, created_at)",
        "CREATE INDEX guard_heavy_3 ON orders(status)",
        "CREATE INDEX guard_heavy_4 ON orders(delivered_at)",
        "CREATE INDEX guard_heavy_5 ON orders(customer_id)",
        "CREATE INDEX guard_heavy_6 ON orders(store_id)",
        "CREATE INDEX guard_heavy_7 ON order_items(product_id)",
        "CREATE INDEX guard_heavy_8 ON store_stock(qty)",
    )

    def apply_with_extra_indexes(dbname, statement):
        original_apply(dbname, statement)
        if statement == candidate.sql:
            with connect(dbname) as conn:
                for ddl in extras:
                    conn.execute(ddl)

    monkeypatch.setattr(sandbox, "apply_ddl", apply_with_extra_indexes)
    result = run_guard(
        candidate, settings.guard_profiles, settings, seed=9876, progress=_db_progress
    )
    _db_result("redundant_indexes", result)
    assert not result.passed
    assert result.failed_cell is not None
    assert result.failed_cell[0] in {"normal", "flash5"}


@pytest.mark.db
@pytest.mark.slow
@pytest.mark.timeout(300)
def test_guard_rejects_planted_order_errors(monkeypatch):
    from app import sandbox
    from app.pg import connect

    settings = replace(get_settings(), guard_profiles=["normal"])
    candidate = _candidate()
    original_apply = sandbox.apply_ddl

    def apply_with_error_trigger(dbname, statement):
        original_apply(dbname, statement)
        if statement == candidate.sql:
            with connect(dbname) as conn:
                conn.execute(
                    "CREATE FUNCTION guard_reject_some_orders() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ BEGIN "
                    "IF NEW.order_id % 5 = 0 THEN RAISE EXCEPTION 'planted order rejection'; "
                    "END IF; RETURN NEW; END $$"
                )
                conn.execute(
                    "CREATE TRIGGER guard_reject_some_orders "
                    "BEFORE INSERT ON orders FOR EACH ROW "
                    "EXECUTE FUNCTION guard_reject_some_orders()"
                )

    monkeypatch.setattr(sandbox, "apply_ddl", apply_with_error_trigger)
    result = run_guard(candidate, settings.guard_profiles, settings, seed=2468)
    _db_result("planted_order_errors", result)
    assert not result.passed
    assert result.failed_cell == ("normal", "place_order")
    assert result.blocks == {"normal": 1}
    assert result.profiles["normal"]["cells"]["place_order"]["reason"] == "errors_increased"
