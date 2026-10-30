"""Run one advisory job, preserving every safety decision as an evidence card."""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
from jsonschema import Draft202012Validator

from app.cancel import JobCancelled, cancellation, check_cancelled
from app.catalog import get_catalog
from app.config import Settings, get_settings
from app.decide import decide
from app.equivalence import EquivConfig, check_rewrite
from app.errors import AdvisorError, BadRequest, BudgetExceeded, LLMError
from app.llm.base import SchemaContext, get_provider
from app.llm.mock import MockProvider
from app.measure import run_once
from app.models import Candidate, Decision, RaceResult
from app.noise import NoiseInjector
from app.pg import connect
from app.plans import explain, filter_candidates, fingerprint, hypo_plan, signature_for
from app.propose import propose
from app.race import race
from app.safety import check as safety_check
from app.sandbox import drop_clone, single_clone, track_clones
from app.shapes import REPORT_SHAPES, load_as_of, render, sample_params
from app.store import Store

_LOG = logging.getLogger(__name__)
_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs/evidence_card.schema.json"
_PARAM_KEYS = {
    "shape_ids",
    "k_max",
    "patience",
    "delta",
    "alpha",
    "metric",
    "profiles",
    "budget_s",
    "prefer_ratio",
    "llm",
    "seed",
    "workload",
    "guard_stat",
    "noise",
    "dry_run",
}


class Budget:
    """One monotonic deadline shared by the shapes in a job."""

    def __init__(self, total_s: float) -> None:
        if not isinstance(total_s, (int, float)) or not math.isfinite(total_s) or total_s <= 0:
            raise BadRequest("Budget must be a positive finite number of seconds")
        self.total_s = float(total_s)
        self.started = time.perf_counter()

    def remaining(self) -> float:
        """Return nonnegative seconds left."""
        return max(0.0, self.total_s - (time.perf_counter() - self.started))

    def slice(self, fraction: float) -> float:
        """Reserve a fraction of the current remaining time for a stage."""
        if not 0 < fraction <= 1:
            raise BadRequest("Budget fraction must be in (0, 1]")
        return self.remaining() * fraction

    def check(self) -> None:
        """Refuse to start another stage after the deadline."""
        if self.remaining() <= 0:
            raise BudgetExceeded("budget exhausted")


def _options(params: dict, base: Settings) -> tuple[Settings, dict]:
    if not isinstance(params, dict) or set(params) - _PARAM_KEYS:
        raise BadRequest("Unknown job parameter")
    ids = params.get("shape_ids", list(REPORT_SHAPES))
    profiles = params.get("profiles", base.guard_profiles)
    if (
        not isinstance(ids, list)
        or not ids
        or not all(isinstance(x, str) and x in REPORT_SHAPES for x in ids)
        or len(ids) != len(set(ids))
        or not isinstance(profiles, list)
        or not profiles
        or not all(
            isinstance(x, str) and x in {"normal", "flash5", "flash10", "flash20"} for x in profiles
        )
        or len(profiles) != len(set(profiles))
    ):
        raise BadRequest("Invalid shapes or profiles")
    seed = params.get("seed", 1234)
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise BadRequest("Seed must be a nonnegative integer")
    noise = params.get("noise", 0.0)
    if not isinstance(noise, (int, float)) or not math.isfinite(noise) or not 0 <= noise <= 1:
        raise BadRequest("Noise must be between 0 and 1")
    if not isinstance(params.get("dry_run", False), bool):
        raise BadRequest("dry_run must be boolean")
    workload = params.get("workload", "custom")
    if workload not in {"custom", "ch"}:
        raise BadRequest("Unknown workload")
    try:
        settings = replace(
            base,
            k_max=params.get("k_max", base.k_max),
            patience=params.get("patience", base.patience),
            delta=params.get("delta", base.delta),
            alpha=params.get("alpha", base.alpha),
            metric=params.get("metric", base.metric),
            guard_profiles=profiles,
            prefer_ratio=params.get("prefer_ratio", base.prefer_ratio),
            llm_provider=params.get("llm", base.llm_provider),
            guard_stat=params.get("guard_stat", base.guard_stat),
        )
    except (TypeError, ValueError) as exc:
        raise BadRequest("Invalid job settings") from exc
    Budget(params.get("budget_s", 1800))
    return settings, {
        "shape_ids": ids,
        "seed": seed,
        "noise": float(noise),
        "dry_run": params.get("dry_run", False),
        "workload": workload,
        "budget_s": float(params.get("budget_s", 1800)),
    }


def _json_safe(value: Any) -> Any:
    """Replace nonfinite measured numbers with explicit nulls."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _small_plan(plan: dict | None) -> dict | None:
    if plan is None or len(json.dumps(plan, default=str).encode()) <= 50_000:
        return plan

    def node(tree: dict) -> dict:
        return {
            "Node Type": tree.get("Node Type"),
            "Plans": [node(child) for child in tree.get("Plans", [])],
        }

    return {"Plan": node(plan.get("Plan", {})), "truncated": True}


def _card(
    job_id: str,
    shape_id: str,
    settings: Settings,
    options: dict,
    baseline: dict,
    proposal: dict,
    candidates: list[Candidate],
    decision: Decision,
    budget: dict,
) -> dict:
    model = (
        "mock"
        if settings.llm_provider == "mock"
        else settings.openrouter_model
        if settings.llm_provider == "openrouter"
        else settings.ollama_model
    )
    card = {
        "job_id": job_id,
        "shape_id": shape_id,
        "created_at": datetime.now(UTC).isoformat(),
        "settings": {
            "delta": settings.delta,
            "alpha": settings.alpha,
            "metric": settings.metric,
            "profiles": settings.guard_profiles,
            "guard_stat": settings.guard_stat,
            "seed": options["seed"],
            "workload": options["workload"],
            "llm": {
                "provider": settings.llm_provider,
                "model": model,
                "temperature": settings.openrouter_temperature,
                "usage": proposal.get("usage", {}),
            },
        },
        "baseline": baseline,
        "propose": proposal,
        "candidates": [
            candidate.to_dict()
            | {
                "plan_before": _small_plan(candidate.plan_before),
                "plan_after": _small_plan(candidate.plan_after),
            }
            for candidate in candidates
        ],
        "decision": decision.to_dict(),
        "budget": budget,
    }
    card = _json_safe(card)
    Draft202012Validator(json.loads(_SCHEMA_PATH.read_text())).validate(card)
    json.dumps(card, allow_nan=False)
    return card


def _validate_ddl_batch(candidates: list[Candidate], settings: Settings) -> list[Candidate]:
    """Check safe DDL on one scratch clone, rolling each transaction back."""
    ddl = [c for c in candidates if c.kind in {"index", "stats"}]
    if not ddl:
        return candidates
    with single_clone(settings) as dbname, connect(dbname, autocommit=False) as conn:
        for candidate in ddl:
            try:
                conn.execute(candidate.sql)
            except psycopg.Error as exc:
                candidate.status = "rejected_invalid_ddl"
                candidate.reason = exc.diag.message_primary or type(exc).__name__
            finally:
                conn.rollback()
    return [c for c in candidates if c.status == "proposed"]


def _empty_baseline() -> dict:
    return {"sql": "", "signature": None, "plan": {}, "time_ms": {"mean": None, "sd": None, "n": 0}}


def _run_shape(
    job_id: str,
    shape_id: str,
    options: dict,
    settings: Settings,
    store: Store,
    budget: Budget,
    tracker,
) -> dict:
    shape = REPORT_SHAPES[shape_id]
    started = time.perf_counter()
    stage_times: dict[str, float] = {}
    baseline = _empty_baseline()
    proposal: dict = {
        "samples": 0,
        "saturated": False,
        "usage": {},
        "rejected": [],
        "distinct_plan_curve": [],
    }
    candidates: list[Candidate] = []
    race_result = RaceResult({}, {}, 0.0, 0, 0)
    decision = Decision("block", None, "no candidate proven safe")
    clone_start = tracker.created
    clone_seconds_start = tracker.seconds

    def stage(name: str, began: float) -> None:
        stage_times[name] = time.perf_counter() - began

    def ensure_active() -> None:
        if store.is_cancelled(job_id):
            raise JobCancelled()
        check_cancelled()

    try:
        budget.check()
        ensure_active()
        began = time.perf_counter()
        with single_clone(settings) as dbname, connect(dbname) as conn:
            rng = np.random.default_rng(options["seed"])
            sampled = sample_params(shape, conn, rng, load_as_of(conn))
            sql = render(shape, conn, sampled)
            plan = explain(conn, sql)
            signature = fingerprint(plan)
            timings = [run_once(conn, sql, "time") for _ in range(5)]
            baseline = {
                "sql": sql,
                "signature": signature,
                "plan": _small_plan(plan),
                "time_ms": {
                    "mean": float(np.mean(timings)),
                    "sd": float(np.std(timings, ddof=1)),
                    "n": len(timings),
                },
            }
            catalog = replace(get_catalog(conn), original_sql=sql)
            stage("observe", began)
            store.emit(
                job_id,
                "observe",
                "info",
                "Baseline report measured",
                {
                    "shape_id": shape_id,
                    "baseline_ms": baseline["time_ms"]["mean"],
                    "plan_signature": signature,
                    "top_node": plan.get("Plan", {}).get("Node Type"),
                },
            )
            budget.check()
            ensure_active()

            began = time.perf_counter()
            provider = (
                MockProvider(options["seed"], sampled)
                if settings.llm_provider == "mock"
                else get_provider(settings)
            )
            result = propose(
                shape,
                sql,
                provider,
                lambda i: SchemaContext(
                    catalog.tables, catalog.indexes, catalog.row_counts, json.dumps(plan), i
                ),
                lambda c: signature_for(c, sql, conn),
                lambda raw: safety_check(raw, catalog),
                k_max=settings.k_max,
                patience=settings.patience,
                job_id=job_id,
                store=store,
                should_stop=lambda: store.is_cancelled(job_id) or budget.remaining() <= 0,
            )
            proposal = {
                "samples": result.samples,
                "saturated": result.saturated,
                "usage": result.usage,
                "rejected": result.rejected,
                "distinct_plan_curve": result.distinct_plan_curve,
            }
            candidate_map = {
                c.id: c for c in store.get_candidates(job_id) if c.shape_id == shape_id
            }
            candidate_map.update({c.id: c for c in result.kept})
            candidates = [candidate_map[key] for key in sorted(candidate_map)]
            if (
                result.rejected
                and not result.kept
                and any(row.get("status") == "provider_error" for row in result.rejected)
            ):
                raise LLMError("Proposal provider failed")
            stage("propose", began)
            budget.check()
            ensure_active()

            began = time.perf_counter()
            kept, _ = filter_candidates(result.kept, sql, conn)
            kept = _validate_ddl_batch(kept, settings)
            for c in kept:
                c.plan_before = plan
                if c.kind == "index":
                    c.plan_after = hypo_plan(conn, c.sql, sql)
                elif c.kind == "rewrite":
                    c.plan_after = explain(conn, c.sql)
            for c in candidates:
                store.save_candidate(c)
            stage("filter", began)
            store.emit(
                job_id,
                "filter",
                "info",
                "Candidate plans screened",
                {
                    "shape_id": shape_id,
                    "kept": len(kept),
                    "skipped_noplan": sum(c.status == "skipped_noplan" for c in candidates),
                    "skipped_duplicate": sum(c.status == "skipped_duplicate" for c in candidates),
                    "invalid_ddl": sum(c.status == "rejected_invalid_ddl" for c in candidates),
                },
            )
            budget.check()
            ensure_active()

            began = time.perf_counter()
            survivors: list[Candidate] = []
            for candidate in kept:
                if candidate.kind == "rewrite":
                    equiv = check_rewrite(
                        conn, sql, candidate.sql, EquivConfig(seed=options["seed"])
                    )
                    candidate.equiv = equiv.to_dict()
                    if equiv.label != "tested":
                        candidate.status, candidate.reason = "rejected_equiv", equiv.reason
                    store.emit(
                        job_id,
                        "equivalence",
                        "info",
                        "Rewrite checked",
                        {
                            "shape_id": shape_id,
                            "candidate_id": candidate.id,
                            "label": equiv.label,
                            "tier_reached": equiv.tier_reached,
                            "reason": equiv.reason,
                            "rewrite_class": equiv.rewrite_class,
                        },
                    )
                if candidate.status == "proposed":
                    survivors.append(candidate)
                store.save_candidate(candidate)
            stage("equivalence", began)
            budget.check()
            ensure_active()

        if options["dry_run"]:
            decision = Decision("block", None, "dry run")
        elif survivors:
            began = time.perf_counter()

            def race_progress(event: dict) -> None:
                budget.check()
                ensure_active()
                store.emit(
                    job_id,
                    "race",
                    "info",
                    "Race measurement",
                    {
                        "shape_id": shape_id,
                        "round": event["round"],
                        "alive": event["alive"],
                        "leader": event["leader"],
                        "bounds": event["bounds"],
                        "dropped_now": event["dropped_now"],
                    },
                )

            race_result = race(
                survivors,
                shape,
                sql,
                settings,
                budget_s=max(0.001, budget.slice(0.4)),
                progress=race_progress,
                rng=np.random.default_rng(options["seed"]),
            )
            stage("race", began)
            budget.check()
            ensure_active()
            began = time.perf_counter()
            current_guard: str | None = None

            def decision_progress(event: dict) -> None:
                nonlocal current_guard
                budget.check()
                ensure_active()
                if event.get("candidate_id"):
                    current_guard = event["candidate_id"]
                if event.get("profile"):
                    store.emit(
                        job_id,
                        "guard",
                        "info",
                        "Protected workload measured",
                        {
                            "shape_id": shape_id,
                            "candidate_id": current_guard,
                            "profile": event["profile"],
                            "block": event["block"],
                            "worst_upper": event.get("worst_upper"),
                            "states": event.get("states", {}),
                        },
                    )

            decision = decide(survivors, race_result, shape, settings, progress=decision_progress)
            stage("guard_decide", began)
        else:
            decision = Decision("block", None, "no candidate survived verification")
    except BudgetExceeded:
        decision = Decision("block", None, "budget exhausted")
        for c in candidates:
            if c.status == "proposed":
                c.status, c.reason = "blocked_unproven", "budget_exhausted"
    except AdvisorError as exc:
        decision = Decision("block", None, f"{exc.code}: {type(exc).__name__}")
        store.emit(
            job_id, "system", "error", "Shape failed", {"shape_id": shape_id, "error": exc.code}
        )
    except JobCancelled:
        raise
    except Exception as exc:
        _LOG.exception("Shape %s failed in job %s", shape_id, job_id)
        decision = Decision("block", None, f"internal: {type(exc).__name__}")
        store.emit(
            job_id,
            "system",
            "error",
            "Shape failed",
            {"shape_id": shape_id, "error": type(exc).__name__},
        )
    finally:
        for candidate in candidates:
            store.save_candidate(candidate)

    store.emit(
        job_id,
        "decide",
        "result",
        "Advisory decision recorded",
        {
            "shape_id": shape_id,
            "action": decision.action,
            "candidate_id": decision.candidate_id,
            "reason": decision.reason,
            "ranking": [c.id for c in candidates if c.speedup is not None],
        },
    )
    card = _card(
        job_id,
        shape_id,
        settings,
        options,
        baseline,
        proposal,
        candidates,
        decision,
        {
            "seconds": time.perf_counter() - started,
            "stage_seconds": stage_times,
            "paired_runs": race_result.runs,
            "llm_calls": len(proposal.get("usage", {}).get("calls", [])),
            "clones_created": tracker.created - clone_start,
            "clone_seconds": tracker.seconds - clone_seconds_start,
            "guard_blocks": sum(
                sum(c.guard.get("blocks", {}).values()) for c in candidates if c.guard
            ),
        },
    )
    store.save_evidence(job_id, shape_id, card)
    store.emit(
        job_id,
        "system",
        "result",
        "Shape evidence saved",
        {"shape_id": shape_id, "decision": decision.action},
    )
    return {"shape_id": shape_id, "decision": decision.to_dict(), "stage_seconds": stage_times}


def run_job(job_id: str, params: dict, store: Store) -> dict:
    """Run selected shapes synchronously; the API may call this from a worker thread."""
    settings, options = _options(params, get_settings())
    budget = Budget(options["budget_s"])
    public = {
        "delta": settings.delta,
        "alpha": settings.alpha,
        "metric": settings.metric,
        "profiles": settings.guard_profiles,
        "llm": settings.llm_provider,
        "seed": options["seed"],
    }
    store.set_job_status(job_id, "running")
    store.emit(
        job_id,
        "system",
        "info",
        "Job started",
        {"shape_ids": options["shape_ids"], "settings": public},
    )
    stop = threading.Event()
    watcher_done = threading.Event()

    def watch_cancel() -> None:
        while not watcher_done.wait(0.1):
            if store.is_cancelled(job_id):
                stop.set()
                return

    watcher = threading.Thread(target=watch_cancel, daemon=True)
    watcher.start()
    results: list[dict] = []
    failed = False
    try:
        with cancellation(stop), track_clones() as tracker:
            noise = (
                NoiseInjector(options["noise"], seed=options["seed"])
                if options["noise"]
                else nullcontext()
            )
            try:
                with noise:
                    for shape_id in options["shape_ids"]:
                        if store.is_cancelled(job_id):
                            stop.set()
                        check_cancelled()
                        shape_budget = Budget(
                            max(
                                1e-6,
                                min(budget.remaining(), budget.total_s / len(options["shape_ids"])),
                            )
                        )
                        row = _run_shape(
                            job_id, shape_id, options, settings, store, shape_budget, tracker
                        )
                        results.append(row)
                        failed = failed or row["decision"]["reason"].startswith(
                            ("llm_error:", "sandbox_error:", "internal:")
                        )
            finally:
                for name in list(tracker.names):
                    drop_clone(name)
        status = "failed" if failed and len(results) == len(options["shape_ids"]) else "done"
        if store.is_cancelled(job_id):
            status = "cancelled"
        summary = {
            "shapes": results,
            "status": status,
            "seconds": budget.total_s - budget.remaining(),
        }
        store.set_job_status(job_id, status, summary)
        store.emit(
            job_id,
            "system",
            "result",
            "Job finished",
            {"shape_ids": options["shape_ids"], "settings": public, "status": status},
        )
        return summary
    except JobCancelled:
        summary = {
            "shapes": results,
            "status": "cancelled",
            "seconds": budget.total_s - budget.remaining(),
        }
        store.set_job_status(job_id, "cancelled", summary)
        store.emit(
            job_id,
            "system",
            "result",
            "Job cancelled",
            {"shape_ids": options["shape_ids"], "settings": public},
        )
        return summary
    except Exception as exc:
        _LOG.exception("Job %s failed", job_id)
        summary = {
            "shapes": results,
            "status": "failed",
            "seconds": budget.total_s - budget.remaining(),
        }
        store.set_job_status(job_id, "failed", summary)
        store.emit(
            job_id,
            "system",
            "error",
            "Job failed",
            {
                "shape_ids": options["shape_ids"],
                "settings": public,
                "error_type": type(exc).__name__,
            },
        )
        return summary
    finally:
        watcher_done.set()
        watcher.join(1)
