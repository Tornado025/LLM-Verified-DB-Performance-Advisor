"""Unit decisions for rewrite preference, guard fallback and fail-closed results."""

from dataclasses import replace

from app.config import get_settings
from app.decide import decide
from app.models import Candidate, Decision, GuardResult, RaceResult, Summary


def _candidate(name, kind, mean, *, weak=False):
    return Candidate(
        id=name,
        job_id="job",
        shape_id="recent_orders",
        kind=kind,
        sql="SELECT 1",
        rationale="test",
        sample_idx=1,
        equiv={"label": "tested", "weak": weak} if kind == "rewrite" else None,
    )


def _summary(mean, upper):
    return Summary(n=12, mean_rel=mean, lower=mean - 0.01, upper=upper, sd_log=0.02, state="PASS")


def _settings(**changes):
    return replace(get_settings(), **changes)


def test_rewrite_at_preference_boundary_is_selected_and_flags_weakness(monkeypatch):
    rewrite = _candidate("rewrite", "rewrite", -0.16, weak=True)
    index = _candidate("index", "index", -0.20)
    race_result = RaceResult(
        {"rewrite": _summary(-0.16, -0.14), "index": _summary(-0.20, -0.18)},
        {},
        1.0,
        24,
    )
    monkeypatch.setattr(
        "app.decide.run_guard",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("guard should not run")),
    )
    decision = decide([rewrite, index], race_result, "recent_orders", _settings(prefer_ratio=0.8))
    assert decision == Decision(
        "apply_rewrite",
        "rewrite",
        "verified rewrite within 0.80 of the best option; zero write cost",
        True,
    )


def test_rewrite_at_81_percent_is_preferred(monkeypatch):
    rewrite = _candidate("rewrite", "rewrite", -0.162)
    index = _candidate("index", "index", -0.20)
    race_result = RaceResult(
        {"rewrite": _summary(-0.162, -0.14), "index": _summary(-0.20, -0.18)},
        {},
        1.0,
        24,
    )
    monkeypatch.setattr(
        "app.decide.run_guard",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("guard should not run")),
    )
    decision = decide([rewrite, index], race_result, "recent_orders", _settings())
    assert decision.action == "apply_rewrite"


def test_index_is_selected_when_rewrite_misses_preference_ratio(monkeypatch):
    rewrite = _candidate("rewrite", "rewrite", -0.158)
    index = _candidate("index", "index", -0.20)
    result = RaceResult(
        {"rewrite": _summary(-0.158, -0.14), "index": _summary(-0.20, -0.18)},
        {},
        1.0,
        24,
    )
    monkeypatch.setattr(
        "app.decide.run_guard",
        lambda *_args, **_kwargs: GuardResult(True, {}, 1.0, {}, None),
    )
    decision = decide([rewrite, index], result, "recent_orders", _settings())
    assert decision.action == "apply_index" and decision.candidate_id == "index"


def test_verified_rewrite_is_fallback_if_no_index_passes(monkeypatch):
    rewrite = _candidate("rewrite", "rewrite", -0.10)
    index = _candidate("index", "index", -0.20)
    result = RaceResult(
        {"rewrite": _summary(-0.10, -0.08), "index": _summary(-0.20, -0.18)},
        {},
        1.0,
        24,
    )
    monkeypatch.setattr(
        "app.decide.run_guard",
        lambda *_args, **_kwargs: GuardResult(False, {}, 1.0, {}, ("flash5", "place_order")),
    )
    decision = decide([rewrite, index], result, "recent_orders", _settings())
    assert decision.action == "apply_rewrite"
    assert rewrite.status == "selected"


def test_blocks_when_guard_does_not_prove_any_index(monkeypatch):
    index = _candidate("index", "index", -0.20)
    result = RaceResult({"index": _summary(-0.20, -0.18)}, {}, 1.0, 12)
    monkeypatch.setattr(
        "app.decide.run_guard",
        lambda *_args, **_kwargs: GuardResult(False, {}, 1.0, {}, None),
    )
    decision = decide([index], result, "recent_orders", _settings())
    assert decision.action == "block"
    assert index.status == "blocked_unproven"


def test_guard_measurement_error_blocks_the_decision(monkeypatch):
    index = _candidate("index", "index", -0.20)
    result = RaceResult({"index": _summary(-0.20, -0.18)}, {}, 1.0, 12)
    monkeypatch.setattr(
        "app.decide.run_guard",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("measurement stopped")),
    )
    decision = decide([index], result, "recent_orders", _settings())
    assert decision.action == "block"
    assert index.status == "blocked_unproven"
    assert index.reason == "guard_error:RuntimeError"
