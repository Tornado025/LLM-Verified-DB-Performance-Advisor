"""Validated REST and resumable event streams for the performance advisor."""

import asyncio
import json
import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app import monitor, observe
from app.errors import BadRequest, Conflict, NotFound
from app.pg import admin_connect, connect
from app.shapes import ORDER_TXNS, REPORT_SHAPES
from app.simulate import run_rq2
from app.store import now
from app.workload import load_profiles

api_router = APIRouter()
RESULTS = Path(__file__).resolve().parents[2] / "experiments/results"
EXPERIMENTS = {"rq2_sim", "rq2_real", "rq4_shapes", "rq4_k", "rq1_cost", "rq3_metrics", "rq5_flash"}


class JobRequest(BaseModel):
    """Public job parameters; defaults are filled from the active settings."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"shape_ids": ["recent_orders"], "llm": "mock", "dry_run": True, "budget_s": 300}
            ]
        },
    )
    shape_ids: list[str] = Field(min_length=1)
    k_max: int = Field(default=10, ge=1, le=20)
    patience: int = Field(default=3, ge=1, le=10)
    delta: float = Field(default=0.05, ge=0.01, le=0.5)
    alpha: float = Field(default=0.05, ge=0.001, le=0.2)
    metric: Literal["time", "buffers"] = "time"
    profiles: list[Literal["normal", "flash5", "flash10", "flash20"]] = Field(
        default=["normal", "flash5", "flash10", "flash20"], min_length=1
    )
    budget_s: float = Field(default=1800, ge=60, le=14400)
    prefer_ratio: float = Field(default=0.8, ge=0.5, le=1)
    llm: Literal["mock", "openrouter", "ollama"] = "mock"
    seed: int = Field(default=1234, ge=0)
    workload: Literal["custom", "ch"] = "custom"
    guard_stat: Literal["mean", "p95"] = "mean"
    noise: float = Field(default=0, ge=0, le=0.2)
    dry_run: bool = False

    @field_validator("shape_ids")
    @classmethod
    def known_shapes(cls, value):
        """Reject unknown report identities before queueing work."""
        if any(name not in REPORT_SHAPES for name in value) or len(value) != len(set(value)):
            raise ValueError("Unknown or duplicate shape")
        return value

    @field_validator("profiles")
    @classmethod
    def unique_profiles(cls, value):
        """Ensure each requested profile is measured once."""
        if len(value) != len(set(value)):
            raise ValueError("Duplicate profile")
        return value


class ConfirmRequest(BaseModel):
    """Explicit weak-rewrite confirmation."""

    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


class ReasonRequest(BaseModel):
    """Optional human reason for blocking or reverting."""

    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="human requested", max_length=2000)


class TrafficRequest(BaseModel):
    """Start or stop the single live traffic generator."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["start", "stop"]
    profile: Literal["normal", "flash5", "flash10", "flash20"] = "normal"
    seconds: int = Field(default=60, ge=1, le=3600)


class JobCreated(BaseModel):
    """Identifier of persisted queued work."""

    job_id: str


class EventsResponse(BaseModel):
    """One ordered polling page and its resume cursor."""

    events: list[dict]
    last_id: int


class EvidenceResponse(BaseModel):
    """Schema-validated cards saved by a completed or partial job."""

    cards: list[dict]


@api_router.get("/health")
def health(request: Request) -> dict:
    """Check database and configuration without contacting an LLM provider."""
    s = request.app.state.settings
    result = {
        "status": "degraded",
        "postgres": False,
        "hypopg": False,
        "pg_stat_statements": False,
        "golden_idle": False,
        "llm": {
            "provider": s.llm_provider,
            "model": s.openrouter_model
            if s.llm_provider == "openrouter"
            else s.ollama_model
            if s.llm_provider == "ollama"
            else "mock",
            "ok": s.llm_provider == "mock" or bool(s.openrouter_model and s.openrouter_api_key)
            if s.llm_provider != "ollama"
            else bool(s.ollama_model),
        },
        "version": "0.15.0",
    }
    try:
        with admin_connect(s) as conn:
            result["postgres"] = True
            idle = conn.execute(
                "/* dpa:sys */ SELECT count(*)=0 FROM pg_stat_activity WHERE datname=%s",
                (s.golden_db,),
            ).fetchone()
            result["golden_idle"] = bool(idle and idle[0])
        with connect(s.live_db, settings=s) as conn:
            extensions = {
                r[0] for r in conn.execute("/* dpa:sys */ SELECT extname FROM pg_extension")
            }
            result["hypopg"] = "hypopg" in extensions
            result["pg_stat_statements"] = "pg_stat_statements" in extensions
    except Exception:
        pass
    if all(result[k] for k in ("postgres", "hypopg", "pg_stat_statements", "golden_idle")):
        result["status"] = "ok"
    return result


@api_router.get("/settings")
def settings(request: Request) -> dict:
    """Expose masked settings."""
    return request.app.state.settings.public()


@api_router.get("/observe")
def observations(
    request: Request,
    limit: int = Query(20, ge=1, le=1000),
    order: Literal["total", "mean"] = "total",
) -> dict:
    """Read live query statistics."""
    with connect(request.app.state.settings.live_db) as conn:
        rows = observe.top_statements(conn, limit, order)
    return {"available": not getattr(rows, "extension_missing", False), "statements": rows}


@api_router.get("/shapes")
def shapes() -> dict:
    """List trusted report templates and protected transactions."""
    return {
        "reports": [
            {"id": s.id, "name": s.name, "question": s.question, "sql_template": s.sql}
            for s in REPORT_SHAPES.values()
        ],
        "txns": [
            {"id": t.id, "weight": t.weight, "description": t.description}
            for t in ORDER_TXNS.values()
        ],
    }


@api_router.get("/profiles")
def profiles() -> list[dict]:
    """Return explicit workload profiles."""
    return [p.to_dict() for p in load_profiles().values()]


@api_router.post("/jobs", status_code=202, response_model=JobCreated)
def create_job(body: JobRequest, request: Request) -> dict:
    """Validate provider configuration and enqueue a job."""
    s = request.app.state.settings
    defaults = {
        "k_max": s.k_max,
        "patience": s.patience,
        "delta": s.delta,
        "alpha": s.alpha,
        "metric": s.metric,
        "profiles": s.guard_profiles,
        "prefer_ratio": s.prefer_ratio,
        "llm": s.llm_provider,
        "guard_stat": s.guard_stat,
    }
    params = JobRequest.model_validate(defaults | body.model_dump(exclude_unset=True)).model_dump()
    if params["llm"] == "openrouter" and not (s.openrouter_api_key and s.openrouter_model):
        raise BadRequest("OpenRouter key and model must be configured")
    if params["llm"] == "ollama" and not s.ollama_model:
        raise BadRequest("Ollama model must be configured")
    return {"job_id": request.app.state.runner.submit(params)}


@api_router.get("/jobs")
def jobs(request: Request, limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
    """List newest jobs."""
    return request.app.state.store.list_jobs(limit)


@api_router.get("/jobs/{job_id}")
def job(job_id: str, request: Request) -> dict:
    """Read one job and its candidate summaries."""
    store = request.app.state.store
    return store.get_job(job_id) | {
        "candidates": [c.to_dict() for c in store.get_candidates(job_id)]
    }


@api_router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict:
    """Cancel a queued or running job idempotently."""
    request.app.state.runner.cancel(job_id)
    return {"status": request.app.state.store.get_job(job_id)["status"]}


@api_router.get("/jobs/{job_id}/events.json", response_model=EventsResponse)
def events_json(
    job_id: str,
    request: Request,
    after: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
) -> dict:
    """Read ordered event pages for polling clients."""
    store = request.app.state.store
    store.get_job(job_id)
    events = store.events_after(job_id, after, limit)
    return {"events": events, "last_id": events[-1]["id"] if events else after}


@api_router.get("/jobs/{job_id}/events", response_class=StreamingResponse)
async def events_stream(job_id: str, request: Request, after: int | None = Query(None, ge=0)):
    """Stream resumable ordered events until terminal state and event drain."""
    store = request.app.state.store
    await run_in_threadpool(store.get_job, job_id)
    try:
        cursor = after if after is not None else int(request.headers.get("Last-Event-ID", "0"))
        if cursor < 0:
            raise ValueError()
    except ValueError as exc:
        raise BadRequest("Invalid Last-Event-ID") from exc

    async def generate():
        last, last_sent = cursor, time.monotonic()
        while not await request.is_disconnected():
            rows = await run_in_threadpool(store.events_after, job_id, last)
            for event in rows:
                last = event["id"]
                yield f"id: {last}\nevent: {event['step']}\ndata: {json.dumps(event)}\n\n"
                last_sent = time.monotonic()
            current = await run_in_threadpool(store.get_job, job_id)
            if current["status"] in {"done", "failed", "cancelled"}:
                newer = await run_in_threadpool(store.events_after, job_id, last, 1)
                if not newer:
                    yield "event: end\ndata: " + json.dumps({"status": current["status"]}) + "\n\n"
                    return
            if time.monotonic() - last_sent >= 15:
                yield ": ping\n\n"
                last_sent = time.monotonic()
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@api_router.get("/jobs/{job_id}/evidence", response_model=EvidenceResponse)
def evidence(job_id: str, request: Request) -> dict:
    """Read saved evidence cards."""
    request.app.state.store.get_job(job_id)
    return {"cards": request.app.state.store.get_evidence(job_id)}


@api_router.get("/candidates/{candidate_id}")
def candidate(candidate_id: str, request: Request) -> dict:
    """Read a complete candidate."""
    c = request.app.state.store.get_candidate(candidate_id)
    preview = None
    if c.status in {"selected", "approved", "applied"}:
        preview = monitor._index_sql(c)[1] if c.kind == "index" else c.sql
    return c.to_dict() | {
        "apply_preview": preview,
        "target_database": request.app.state.settings.live_db,
    }


@api_router.post("/candidates/{candidate_id}/approve")
def approve(candidate_id: str, request: Request, body: ConfirmRequest | None = None) -> dict:
    """Record human approval and apply a verified candidate."""
    return monitor.apply(
        request.app.state.store, candidate_id, confirm=body.confirm if body else False
    )


@api_router.post("/candidates/{candidate_id}/block")
def block(candidate_id: str, request: Request, body: ReasonRequest | None = None) -> dict:
    """Record a human rejection before application."""
    store = request.app.state.store
    with monitor._CHANGE_LOCK:
        c = store.get_candidate(candidate_id)
        if c.status in {"applied", "reverted"}:
            raise Conflict("Use revert for an applied candidate")
        c.status, c.reason = "blocked_by_human", body.reason if body else "human requested"
        store.save_candidate(c)
    return c.to_dict()


@api_router.get("/applied")
def applied(request: Request) -> dict:
    """List applied changes and their latest monitoring window."""
    return {
        "applied": [
            r | {"latest_window": r["windows"][-1] if r["windows"] else None}
            for r in request.app.state.store.list_applied()
        ]
    }


@api_router.post("/applied/{applied_id}/revert")
def revert_applied(applied_id: str, request: Request, body: ReasonRequest | None = None) -> dict:
    """Manually revert an approved applied change."""
    return monitor.revert(
        request.app.state.store, applied_id, body.reason if body else "human requested"
    )


@api_router.post("/live/traffic")
def traffic_control(body: TrafficRequest, request: Request) -> dict:
    """Control one process-local live traffic generator."""
    state = request.app.state
    with state.traffic_lock:
        worker = state.traffic
        if body.action == "start":
            if worker and worker.is_alive():
                raise Conflict("Live traffic is already running")
            worker = monitor.LiveTraffic(body.profile, seconds=body.seconds)
            worker.start()
            state.traffic, state.traffic_started = worker, now()
        elif worker:
            worker.stop()
            worker.join(state.settings.stmt_timeout_ms / 1000 + 10)
        return {
            "running": bool(worker and worker.is_alive()),
            "profile": worker.profile if worker else None,
            "started_at": state.traffic_started,
        }


@api_router.get("/live/traffic")
def traffic_status(request: Request) -> dict:
    """Read recent live traffic measurements and errors."""
    worker = request.app.state.traffic
    return {
        "running": bool(worker and worker.is_alive()),
        "profile": worker.profile if worker else None,
        "recent": [row | {"ts": row["taken_at"]} for row in worker.recent] if worker else [],
        "error": worker.error if worker else None,
    }


def _read(name: str) -> dict:
    path = RESULTS / (name + ".json")
    if not path.is_file():
        return {"available": False}
    return json.loads(path.read_text())


@api_router.get("/experiments/rq2")
def rq2(trials: int = Query(400, ge=1, le=2000), recompute: bool = False) -> dict:
    """Read or explicitly recompute the bounded noise simulation."""
    return {
        "simulation": run_rq2(trials=trials) if recompute else _read("rq2_sim"),
        "real": _read("rq2_real"),
    }


@api_router.get("/experiments/rq4")
def rq4() -> dict:
    """Read measured shape and sampling curves."""
    return {"shape_stats": _read("rq4_shapes"), "speedup_vs_k": _read("rq4_k")}


@api_router.get("/experiments/rq5")
def rq5(request: Request) -> dict:
    """Aggregate only observed normal-PASS to flash-FAIL transitions."""
    store = request.app.state.store
    rows = []
    for job in store.list_jobs(100000):
        for card in store.get_evidence(job["id"]):
            for c in card.get("candidates", []):
                profiles = (c.get("guard") or {}).get("profiles", {})
                normal = profiles.get("normal", {}).get("cells", {})
                normal_pass = bool(normal) and all(v["state"] == "PASS" for v in normal.values())
                if not normal_pass:
                    continue
                for p, mult in (("flash5", 5), ("flash10", 10), ("flash20", 20)):
                    cells = profiles.get(p, {}).get("cells", {})
                    if cells:
                        rows.append(
                            {
                                "candidate_id": c["id"],
                                "shape_id": card["shape_id"],
                                "kind": c["kind"],
                                "multiplier": mult,
                                "flash_fail": any(v["state"] == "FAIL" for v in cells.values()),
                                "flash_block": any(v["state"] == "BLOCK" for v in cells.values()),
                            }
                        )
    return {
        "available": bool(rows),
        "observations": rows,
        "by_kind_shape": [
            {
                "kind": kind,
                "shape_id": shape,
                "multiplier": mult,
                "measured": sum(
                    r["kind"] == kind and r["shape_id"] == shape and r["multiplier"] == mult
                    for r in rows
                ),
                "flips": sum(
                    r["kind"] == kind
                    and r["shape_id"] == shape
                    and r["multiplier"] == mult
                    and r["flash_fail"]
                    for r in rows
                ),
            }
            for kind, shape, mult in sorted(
                {(r["kind"], r["shape_id"], r["multiplier"]) for r in rows}
            )
        ],
        "by_multiplier": [
            {
                "multiplier": m,
                "measured": sum(r["multiplier"] == m for r in rows),
                "flips": sum(r["multiplier"] == m and r["flash_fail"] for r in rows),
            }
            for m in (5, 10, 20)
        ],
    }


@api_router.get("/experiments/{name}")
def experiment(name: str) -> dict:
    """Read only explicitly whitelisted experiment names."""
    if name not in EXPERIMENTS:
        raise NotFound("Unknown experiment")
    return _read(name)
