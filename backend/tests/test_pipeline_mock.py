"""Mock-provider pipeline acceptance and fail-closed job behavior."""

import json
import time
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import app.pipeline as pipeline
from app.config import get_settings
from app.errors import BadRequest, LLMError
from app.llm.mock import MockProvider
from app.sandbox import list_clones
from app.shapes import REPORT_SHAPES
from app.store import Store


def _store(tmp_path: Path) -> Store:
    return Store(tmp_path / "pipeline.sqlite")


def _settings():
    return replace(
        get_settings(),
        llm_provider="mock",
        min_runs=3,
        max_runs=6,
        max_blocks=12,
        block_seconds=2,
        min_samples_per_block=1,
        guard_profiles=["normal"],
    )


def test_budget_and_option_validation():
    budget = pipeline.Budget(10)
    assert 0 < budget.remaining() <= 10
    assert 0 < budget.slice(0.4) <= 4
    with pytest.raises(BadRequest):
        pipeline.Budget(0)
    with pytest.raises(BadRequest):
        pipeline._options({"shape_ids": [["recent_orders"]]}, _settings())
    with pytest.raises(BadRequest):
        pipeline._options({"profiles": [["normal"]]}, _settings())


@pytest.mark.db
@pytest.mark.slow
@pytest.mark.timeout(1800)
def test_dry_run_all_shapes(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline, "get_settings", _settings)
    store = _store(tmp_path)
    params = {
        "shape_ids": list(REPORT_SHAPES),
        "llm": "mock",
        "dry_run": True,
        "k_max": 2,
        "patience": 1,
        "budget_s": 2400,
        "seed": 1234,
    }
    job = store.create_job(params)
    result = pipeline.run_job(job, params, store)
    cards = store.get_evidence(job)
    validator = Draft202012Validator(json.loads(pipeline._SCHEMA_PATH.read_text()))
    assert result["status"] == "done"
    assert len(cards) == len(REPORT_SHAPES)
    assert all(validator.validate(card) is None for card in cards)
    assert all(card["decision"]["reason"] == "dry run" for card in cards)
    assert all(
        candidate["status"]
        in {
            "proposed",
            "rejected_safety",
            "rejected_invalid_ddl",
            "skipped_duplicate",
            "skipped_noplan",
            "rejected_equiv",
            "dropped_race",
            "failed_guard",
            "blocked_unproven",
            "selected",
            "approved",
            "applied",
            "reverted",
            "blocked_by_human",
        }
        for card in cards
        for candidate in card["candidates"]
    )
    assert list_clones() == []
    store.close()


@pytest.mark.db
@pytest.mark.slow
@pytest.mark.timeout(1800)
def test_invariants_full_run(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline, "get_settings", _settings)
    store = _store(tmp_path)
    params = {
        "shape_ids": ["recent_orders", "delivered_yesterday", "payout_report"],
        "llm": "mock",
        "profiles": ["normal"],
        "k_max": 2,
        "patience": 1,
        "budget_s": 1200,
        "seed": 1234,
    }
    job = store.create_job(params)
    result = pipeline.run_job(job, params, store)
    cards = store.get_evidence(job)
    events = store.events_after(job)
    assert result["status"] == "done"
    assert len(cards) == 3
    assert [event["id"] for event in events] == sorted(event["id"] for event in events)
    assert len([event for event in events if event["step"] == "decide"]) == 3
    for card in cards:
        for candidate in card["candidates"]:
            if candidate["status"] == "selected" and candidate["kind"] in {"index", "stats"}:
                assert candidate["guard"] and candidate["guard"]["passed"]
            if candidate["status"] == "selected" and candidate["kind"] == "rewrite":
                assert candidate["equiv"] and candidate["equiv"]["label"] == "tested"
            if candidate["status"] in {"rejected_invalid_ddl", "rejected_equiv"}:
                assert candidate["id"] != card["decision"]["candidate_id"]
    assert list_clones() == []
    store.close()


@pytest.mark.db
@pytest.mark.slow
@pytest.mark.timeout(600)
def test_cancel_midway(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline, "get_settings", _settings)
    store = _store(tmp_path)
    original = store.emit

    def emit_and_cancel(job_id, step, level, message, data=None):
        result = original(job_id, step, level, message, data)
        if step == "propose" and level == "info":
            store.set_job_status(job_id, "cancelled")
        return result

    monkeypatch.setattr(store, "emit", emit_and_cancel)
    params = {
        "shape_ids": ["recent_orders", "payout_report"],
        "llm": "mock",
        "dry_run": True,
        "k_max": 2,
        "patience": 1,
        "budget_s": 600,
    }
    job = store.create_job(params)
    assert pipeline.run_job(job, params, store)["status"] == "cancelled"
    assert list_clones() == []
    store.close()


@pytest.mark.db
@pytest.mark.slow
@pytest.mark.timeout(600)
def test_budget_exhaustion(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline, "get_settings", _settings)
    store = _store(tmp_path)
    params = {
        "shape_ids": ["recent_orders"],
        "llm": "mock",
        "k_max": 2,
        "patience": 1,
        "budget_s": 0.001,
    }
    job = store.create_job(params)
    result = pipeline.run_job(job, params, store)
    assert result["status"] == "done"
    assert store.get_evidence(job)[0]["decision"]["reason"] == "budget exhausted"
    assert list_clones() == []
    store.close()


@pytest.mark.db
@pytest.mark.slow
@pytest.mark.timeout(120)
def test_twenty_second_budget_exhaustion(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline, "get_settings", _settings)
    measured = pipeline.run_once

    def slow_measurement(conn, sql, metric):
        value = measured(conn, sql, metric)
        time.sleep(4.1)
        return value

    monkeypatch.setattr(pipeline, "run_once", slow_measurement)
    store = _store(tmp_path)
    params = {
        "shape_ids": ["recent_orders"],
        "llm": "mock",
        "k_max": 2,
        "patience": 1,
        "budget_s": 20,
    }
    job = store.create_job(params)
    result = pipeline.run_job(job, params, store)
    assert result["status"] == "done"
    assert store.get_evidence(job)[0]["decision"]["reason"] == "budget exhausted"
    assert list_clones() == []
    store.close()


@pytest.mark.db
@pytest.mark.slow
@pytest.mark.timeout(600)
def test_llm_error_isolated(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(pipeline, "get_settings", _settings)
    original = MockProvider.suggest

    def fail_one(self, shape, ctx, sample_idx, rendered_sql):
        if shape.id == "recent_orders":
            raise LLMError("fixture provider unavailable")
        return original(self, shape, ctx, sample_idx, rendered_sql)

    monkeypatch.setattr(MockProvider, "suggest", fail_one)
    store = _store(tmp_path)
    params = {
        "shape_ids": ["recent_orders", "payout_report"],
        "llm": "mock",
        "dry_run": True,
        "k_max": 2,
        "patience": 1,
        "budget_s": 600,
    }
    job = store.create_job(params)
    result = pipeline.run_job(job, params, store)
    cards = {card["shape_id"]: card for card in store.get_evidence(job)}
    assert len(cards) == 2
    assert cards["recent_orders"]["decision"]["reason"].startswith("llm_error:")
    assert cards["payout_report"]["decision"]["reason"] == "dry run"
    assert result["status"] == "failed"
    assert list_clones() == []
    store.close()
