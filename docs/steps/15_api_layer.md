# Step 15 — API layer (`app/api.py`, `app/main.py`)

**Goal (end state):** a FastAPI service exposing every endpoint below, with pydantic request/response models, consistent error bodies,
SSE for live job events, CORS for the Vite dev server, a background job runner, and tests using `httpx.AsyncClient`/`TestClient`.
`docker-compose` gains a `backend` service.

**Prerequisites:** steps 03–14.

## 15.1 App wiring (`main.py`)

* `create_app() -> FastAPI` with lifespan: load settings, set up logging, open the `Store`, create `JobRunner` (`ThreadPoolExecutor(max_workers=2)`),
  start `MonitorThread`, on shutdown stop the monitor and `sandbox.drop_all_clones()` for orphans.
* Include router `api_router` with prefix `/api`; CORS from `CORS_ORIGINS`.
* Exception handlers: `AdvisorError` → `{"error": {"code","message","detail"}}` with `http_status`; `RequestValidationError` → 422 with the same envelope; unhandled → 500 (log trace, hide detail).
* `uvicorn app.main:app` (module-level `app = create_app()`).

## 15.2 Endpoint catalogue (exact)

| Method + path | Request | Response | Notes |
|---|---|---|---|
| `GET /api/health` | – | `{status, postgres: bool, hypopg: bool, pg_stat_statements: bool, golden_idle: bool, llm: {provider, model, ok}, version}` | `llm.ok` only checks configuration for non-mock (no network call) |
| `GET /api/settings` | – | `Settings.public()` | |
| `GET /api/observe?limit=&order=` | – | `{available: bool, statements: [...]}` | from `observe.top_statements` on `qc_live` |
| `GET /api/shapes` | – | `{reports: [{id,name,question,sql_template,baseline?}], txns: [{id,weight,description}]}` | |
| `GET /api/profiles` | – | list of `ProfileSpec` | |
| `POST /api/jobs` | `JobRequest` | `{job_id}` 202 | validates params; enqueues |
| `GET /api/jobs?limit=` | – | list of job summaries | newest first |
| `GET /api/jobs/{id}` | – | job + candidate summaries + status | |
| `POST /api/jobs/{id}/cancel` | – | `{status}` | idempotent |
| `GET /api/jobs/{id}/events?after=<id>` | – | `text/event-stream` | SSE, see 15.4 |
| `GET /api/jobs/{id}/events.json?after=&limit=` | – | `{events: [...], last_id}` | polling fallback |
| `GET /api/jobs/{id}/evidence` | – | `{cards: [EvidenceCard]}` | |
| `GET /api/candidates/{id}` | – | candidate full object | |
| `POST /api/candidates/{id}/approve` | `{confirm?: bool}` | applied row | calls `monitor.apply`; 409 on precondition failure |
| `POST /api/candidates/{id}/block` | `{reason?: str}` | candidate | status `blocked_by_human` |
| `GET /api/applied` | – | `{applied: [row + latest window]}` | |
| `POST /api/applied/{id}/revert` | `{reason?: str}` | applied row | |
| `POST /api/live/traffic` | `{action: "start"|"stop", profile?: str, seconds?: int}` | `{running, profile, started_at}` | |
| `GET /api/live/traffic` | – | `{running, profile, recent: [{ts, mean_ms by txn}]}` | |
| `GET /api/experiments/rq2` | `?trials=&recompute=` | JSON from file or recomputed | |
| `GET /api/experiments/rq4` | – | shape stats + speedup-vs-k | |
| `GET /api/experiments/rq5` | – | verdict-flip aggregates | computed from stored evidence |
| `GET /api/experiments/{name}` | – | any file `experiments/results/{name}.json` | whitelist names |

`JobRequest` fields and validation (pydantic v2): `shape_ids` (non-empty, known ids), `k_max` 1–20, `patience` 1–10, `delta` 0.01–0.5,
`alpha` 0.001–0.2, `metric` in enum, `profiles` subset of known, `budget_s` 60–14400, `prefer_ratio` 0.5–1.0, `llm` in enum, `seed` int, `workload` in `custom|ch`,
`noise` 0–0.2, `dry_run` bool. Unknown fields ⇒ 422 (`extra="forbid"`). Reject when `llm=openrouter` but not configured (400 with a clear message).

## 15.3 Job runner

```python
class JobRunner:
    def submit(self, params) -> str       # creates job (queued), submits run_job to the executor
    def cancel(self, job_id) -> None
    def is_cancelled(self, job_id) -> bool
```
* Enforce **one running job at a time** that uses clones/guard (a semaphore) — a second job stays `queued` (visible in the UI).
* On startup, mark stale `running` jobs from a previous process as `failed` with an event "server restarted" and drop orphan clones.
* A job wraps `pipeline.run_job` in `try/except Exception`: log the traceback, emit `error`, status `failed`.

## 15.4 Server-Sent Events

`GET /api/jobs/{id}/events` returns `StreamingResponse(media_type="text/event-stream")` with headers `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
* Generator: `last = int(after or Last-Event-ID or 0)`; loop: fetch `events_after(job_id, last)`; for each yield
  `id: {id}\nevent: {step}\ndata: {json}\n\n`; sleep 0.5 s; every 15 s of silence yield `: ping\n\n`.
* Terminate (yield `event: end\ndata: {"status": "..."}\n\n`) once the job status is terminal **and** no newer events remain.
* Support client disconnects: check `await request.is_disconnected()` each loop (make the generator async, run store calls in the thread pool via `run_in_threadpool`).

## 15.5 Experiments endpoints
* `rq2`: read `experiments/results/rq2_sim.json` (and `rq2_real.json` if present) ⇒ `{simulation, real}`; `recompute=true&trials=N` runs `simulate.run_rq2` in the thread pool (cap `trials ≤ 2000`).
* `rq4`: `{shape_stats: from rq4_shapes.json, speedup_vs_k: from rq4_k.json}`.
* `rq5`: aggregate `evidence` cards: for every candidate with guard results, `normal_pass` and `flash_fail` ⇒ flip table by multiplier (`flash5/10/20`) and by candidate kind/shape.
* Missing files ⇒ `{"available": false}` (not an error).

## 15.6 Docker
`backend/Dockerfile`: `python:3.11-slim`, install `libpq5`, copy requirements, `pip install`, copy app, `CMD uvicorn app.main:app --host 0.0.0.0 --port 8000`.
Compose `backend` service: build `./backend`, `env_file: .env` (override `PG_HOST=db`), `depends_on: db (healthy)`, `volumes: ./backend/data:/app/data, ./experiments:/experiments`, port 8000.
The backend container needs the **Docker socket?** No — it connects to Postgres over the network only. (BenchBase/Java is run on the host or in an extra container; document.)

## 15.7 Tests (`tests/test_api.py`)

* `test_health_ok` (db), `test_settings_masks_key`, `test_shapes_list`, `test_profiles_list`.
* `test_job_validation_errors` (unknown shape, k_max 0, extra field).
* `test_job_lifecycle_mock` (`@db @slow`): POST dry-run job ⇒ poll status to `done` ⇒ `evidence` has a card ⇒ events JSON ordered.
* `test_sse_stream`: consume the stream with `httpx` until `event: end`; event ids strictly increasing; `Last-Event-ID` resume returns only newer events.
* `test_cancel`, `test_approve_conflict`, `test_error_envelope_shape`.
* OpenAPI: `GET /openapi.json` validates; generate TypeScript types in step 16 from it.

## 15.8 Done checklist
- [ ] All endpoints implemented; OpenAPI documented with examples
- [ ] SSE verified with `curl -N http://localhost:8000/api/jobs/<id>/events`
- [ ] `docker compose up backend` serves `/api/health`
- [ ] Commit `step 15: api`
