"""Safe-racing rounds, limits and loser classification."""

from contextlib import contextmanager
from dataclasses import replace

import numpy as np
import pytest

from app.config import get_settings
from app.errors import BadRequest
from app.models import Candidate, PairedResult
from app.race import race


def _candidate(name):
    return Candidate(
        id=name,
        job_id="job",
        shape_id="recent_orders",
        kind="rewrite",
        sql=f"SELECT 1 /* {name} */",
        rationale="test",
        sample_idx=1,
        equiv={"label": "tested", "weak": False},
    )


def _settings():
    return replace(
        get_settings(),
        min_runs=3,
        max_runs=6,
        race_batch=3,
        max_blocks=6,
        max_clones=3,
    )


def _wire_race(monkeypatch, effects):
    import app.race as module

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def close(self):
            return None

    @contextmanager
    def single_clone(_settings):
        yield "baseline"

    monkeypatch.setattr(module, "single_clone", single_clone)
    monkeypatch.setattr(module, "connect", lambda _dbname: FakeConnection())

    def fake_report(
        _conn_a,
        _sql_a,
        _conn_b,
        sql_b,
        *,
        sequential_test,
        max_pairs,
        metric,
        warmup_first,
        **_kwargs,
    ):
        effect = effects[sql_b]
        values = effect if isinstance(effect, list) else [effect]
        d = [values[i % len(values)] for i in range(max_pairs)]
        sequential_test.add(d)
        return PairedResult(
            d=sequential_test.d.tolist(),
            summary=sequential_test.summary(),
            seconds=0.01,
            metric=metric,
            a_vals=[10.0] * max_pairs,
            b_vals=[10.0 * float(np.exp(d[i])) for i in range(max_pairs)],
        )

    monkeypatch.setattr(module, "paired_report", fake_report)


def test_race_keeps_supported_winners_and_drops_no_improvement(monkeypatch):
    candidates = [_candidate(name) for name in ("large", "medium", "none")]
    effects = {
        candidate.sql: value
        for candidate, value in zip(candidates, (-0.3, -0.15, [0.6, -0.6]), strict=True)
    }
    _wire_race(monkeypatch, effects)
    result = race(candidates, "recent_orders", "SELECT 1", _settings(), budget_s=10)
    assert result.states["large"].state == "PASS"
    assert result.states["medium"].state == "PASS"
    assert result.dropped["none"] == "no_improvement"
    assert result.runs >= 9
    assert result.clones_created == 1


def test_race_rejects_unverified_rewrite_before_cloning(monkeypatch):
    candidate = _candidate("unverified")
    candidate.equiv = {"label": "unknown"}
    result = race([candidate], "recent_orders", "SELECT 1", _settings(), budget_s=1)
    assert result.dropped[candidate.id] == "rewrite_not_tested"
    assert result.clones_created == 0


def test_invalid_index_ddl_drops_only_that_candidate(monkeypatch):
    import app.race as module

    index = _candidate("bad-index")
    index.kind = "index"
    index.sql = "CREATE INDEX broken ON missing_table(col)"
    rewrite = _candidate("rewrite")
    _wire_race(monkeypatch, {rewrite.sql: -0.3})
    dropped_clones = []
    monkeypatch.setattr(module, "create_clone", lambda **_kwargs: "candidate-db")
    monkeypatch.setattr(
        module, "apply_ddl", lambda *_args: (_ for _ in ()).throw(BadRequest("bad DDL"))
    )
    monkeypatch.setattr(module, "drop_clone", dropped_clones.append)

    result = race([index, rewrite], "recent_orders", "SELECT 1", _settings(), budget_s=10)
    assert result.dropped[index.id] == "invalid_ddl"
    assert result.states[rewrite.id].state == "PASS"
    assert dropped_clones == ["candidate-db"]


def test_race_requires_a_positive_finite_budget():
    with pytest.raises(BadRequest):
        race([], "shape", "SELECT 1", _settings(), budget_s=0)
