"""Round-based safe racing for report-query candidates."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

import psycopg
import sqlglot

from app.config import Settings
from app.errors import BadRequest
from app.measure import QueryTimeout, paired_report
from app.models import Bounds, Candidate, RaceResult, Summary
from app.pg import connect
from app.sandbox import InvalidDDL, analyze, apply_ddl, create_clone, drop_clone, single_clone
from app.stats import SequentialTest, best_leader, confidently_worse


def _save_speedup(candidate: Candidate, summary: Summary, metric: str) -> None:
    """Attach the current report-speed result to its candidate."""
    candidate.speedup = summary.to_dict() | {"metric": metric, "n_runs": summary.n}


def race(
    candidates: list[Candidate],
    shape,
    rendered_sql: str,
    settings: Settings,
    *,
    budget_s: float,
    progress: Callable[[dict], None] | None = None,
    rng=None,
) -> RaceResult:
    """Compare candidates in bounded paired rounds and discard clear losers."""
    del shape  # Kept in the interface for pipeline/event context compatibility.
    if not math.isfinite(budget_s) or budget_s <= 0:
        raise BadRequest("Race budget must be positive and finite")
    ids = [candidate.id for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise BadRequest("Candidate IDs must be unique within a race")
    if settings.max_clones < 2:
        raise BadRequest("Racing requires at least two sandbox clone slots")
    if not rendered_sql.strip():
        raise BadRequest("Race baseline SQL is required")
    if not candidates:
        return RaceResult({}, {}, 0.0, 0, 0)

    start = time.perf_counter()
    dropped: dict[str, str] = {}
    states: dict[str, Summary] = {}
    tests: dict[str, SequentialTest] = {}
    eligible: list[Candidate] = []
    for candidate in candidates:
        if candidate.kind not in {"index", "stats", "rewrite"}:
            candidate.status, candidate.reason = "rejected_safety", "unknown_candidate_kind"
            dropped[candidate.id] = "unknown_candidate_kind"
        elif candidate.kind == "rewrite" and (candidate.equiv or {}).get("label") != "tested":
            candidate.status, candidate.reason = "rejected_equiv", "rewrite_not_tested"
            dropped[candidate.id] = "rewrite_not_tested"
        else:
            eligible.append(candidate)
            tests[candidate.id] = SequentialTest(
                settings.delta,
                settings.alpha,
                settings.looks,
                len(candidates),
                settings.min_runs,
                settings.max_runs,
                mode="improvement",
            )
    if not eligible:
        return RaceResult(states, dropped, time.perf_counter() - start, 0, 0)

    runs = 0
    clone_count = 0
    round_index = 0
    stop_all = False
    batch_size = max(1, settings.max_clones - 1)
    with single_clone(settings) as baseline_db:
        clone_count += 1
        with connect(baseline_db) as conn_a:
            # Keep the baseline fixed; candidate clones are created in batches
            # so live clone count never exceeds MAX_CLONES.
            for offset in range(0, len(eligible), batch_size):
                group = eligible[offset : offset + batch_size]
                candidate_dbs: dict[str, str] = {}
                candidate_conns = {}
                warmed: set[str] = set()
                try:
                    for candidate in group:
                        if candidate.kind == "rewrite":
                            candidate_conns[candidate.id] = conn_a
                            continue
                        db = create_clone(settings=settings)
                        clone_count += 1
                        candidate_dbs[candidate.id] = db
                        try:
                            apply_ddl(db, candidate.sql)
                            analyze(db)
                            candidate_conns[candidate.id] = connect(db)
                        except (BadRequest, InvalidDDL, psycopg.Error, sqlglot.errors.SqlglotError):
                            candidate.status = "rejected_invalid_ddl"
                            candidate.reason = candidate.reason or "invalid_candidate_ddl"
                            dropped[candidate.id] = "invalid_ddl"
                            # Keep ownership until the group's finally block cleans up.

                    while time.perf_counter() - start < budget_s and not stop_all:
                        dropped_before = set(dropped)
                        pending = [
                            candidate
                            for candidate in group
                            if candidate.id not in dropped
                            and tests[candidate.id].state == "CONTINUE"
                        ]
                        if not pending:
                            break
                        progressed = False
                        for candidate in pending:
                            remaining = budget_s - (time.perf_counter() - start)
                            if remaining <= 0:
                                break
                            test = tests[candidate.id]
                            sql_b = candidate.sql if candidate.kind == "rewrite" else rendered_sql
                            try:
                                result = paired_report(
                                    conn_a,
                                    rendered_sql,
                                    candidate_conns[candidate.id],
                                    sql_b,
                                    metric=settings.metric,
                                    delta=settings.delta,
                                    alpha=settings.alpha,
                                    looks=settings.looks,
                                    family=len(candidates),
                                    min_n=settings.min_runs,
                                    max_n=settings.max_runs,
                                    batch=settings.race_batch,
                                    mode="improvement",
                                    budget_s=remaining,
                                    rng=rng,
                                    sequential_test=test,
                                    max_pairs=settings.race_batch,
                                    warmup_first=candidate.id not in warmed,
                                )
                            except QueryTimeout as exc:
                                if exc.side == "a":
                                    stop_all = True
                                    for unresolved in eligible:
                                        if unresolved.id not in dropped:
                                            dropped[unresolved.id] = "baseline_timeout"
                                            unresolved.status, unresolved.reason = (
                                                "dropped_race",
                                                "baseline_timeout",
                                            )
                                    break
                                dropped[candidate.id] = "candidate_timeout"
                                candidate.status, candidate.reason = (
                                    "dropped_race",
                                    "candidate_timeout",
                                )
                                continue
                            except Exception as exc:
                                dropped[candidate.id] = "candidate_measurement_error"
                                candidate.status, candidate.reason = (
                                    "dropped_race",
                                    type(exc).__name__,
                                )
                                continue
                            warmed.add(candidate.id)
                            n_new = len(result.a_vals)
                            runs += n_new
                            progressed = progressed or n_new > 0
                            if test.d.size:
                                summary = test.summary()
                                states[candidate.id] = summary
                                _save_speedup(candidate, summary, settings.metric)
                            if test.state in {"FAIL", "BLOCK"}:
                                reason = (
                                    "slower_than_baseline"
                                    if test.summary().lower > settings.delta
                                    else "no_improvement"
                                )
                                dropped[candidate.id] = reason
                                candidate.status, candidate.reason = "dropped_race", reason
                        leader_id = best_leader(
                            {key: value for key, value in states.items() if key not in dropped}
                        )
                        if leader_id is not None:
                            leader_bounds = Bounds(states[leader_id].lower, states[leader_id].upper)
                            for candidate in pending:
                                if candidate.id == leader_id or candidate.id in dropped:
                                    continue
                                candidate_summary = states.get(candidate.id)
                                if candidate_summary is None or candidate_summary.state not in {
                                    "CONTINUE",
                                    "PASS",
                                }:
                                    continue
                                leader_drop_reason: str | None = None
                                if (
                                    math.isfinite(candidate_summary.lower)
                                    and math.isfinite(leader_bounds.upper)
                                    and confidently_worse(
                                        Bounds(candidate_summary.lower, candidate_summary.upper),
                                        leader_bounds,
                                    )
                                ):
                                    leader_drop_reason = "confidently_worse_than_leader"
                                if (
                                    leader_drop_reason is None
                                    and candidate_summary.lower > settings.delta
                                ):
                                    leader_drop_reason = "slower_than_baseline"
                                if leader_drop_reason:
                                    dropped[candidate.id] = leader_drop_reason
                                    candidate.status, candidate.reason = (
                                        "dropped_race",
                                        leader_drop_reason,
                                    )
                        round_index += 1
                        if progress is not None:
                            progress(
                                {
                                    "round": round_index,
                                    "alive": [c.id for c in eligible if c.id not in dropped],
                                    "leader": leader_id,
                                    "bounds": {
                                        cid: [summary.lower, summary.upper]
                                        for cid, summary in states.items()
                                    },
                                    "dropped_now": sorted(set(dropped) - dropped_before),
                                    "seconds": time.perf_counter() - start,
                                }
                            )
                        if not progressed or stop_all:
                            break
                finally:
                    for conn in candidate_conns.values():
                        if conn is not conn_a:
                            conn.close()
                    for db in candidate_dbs.values():
                        drop_clone(db)
                if stop_all or time.perf_counter() - start >= budget_s:
                    break

    for candidate in eligible:
        if candidate.id not in dropped and tests[candidate.id].state == "CONTINUE":
            dropped[candidate.id] = "budget"
            candidate.status, candidate.reason = "dropped_race", "budget"
    return RaceResult(states, dropped, time.perf_counter() - start, runs, clone_count)
