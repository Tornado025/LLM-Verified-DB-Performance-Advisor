# Running the API

Run `docker compose up -d --build backend` from the repository root. PostgreSQL
must already be seeded (`make seed`). The service is available at
http://localhost:8000/api/health; interactive OpenAPI documentation is at
http://localhost:8000/docs and its schema is at `/openapi.json`.

For host development, activate the project virtual environment and run `make api`.
Run one backend instance: its startup recovers stale jobs and removes orphan
sandboxes. The runner serializes database jobs; additional requests stay queued.
Shutdown cancels jobs, stops live traffic and monitoring, waits for cleanup, and
then closes metadata storage.

Example dry run:

```sh
curl -X POST http://localhost:8000/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"shape_ids":["recent_orders"],"llm":"mock","dry_run":true,"budget_s":300}'
curl -N http://localhost:8000/api/jobs/JOB_ID/events
```

SSE uses the pipeline step as the event name and emits a final `end` event.
Reconnect with `Last-Event-ID` or `?after=`. Polling clients can use
`events.json?after=ID`. The mock provider makes no external LLM call.

Approval endpoints apply changes to `qc_live` and can generate normal baseline
traffic. `/api/live/traffic` explicitly starts or stops live order traffic. Monitor
windows describe statement latency; guard evidence describes full transaction
latency. Missing research results return `available: false`.

The backend container uses the PostgreSQL network connection and has no Docker
socket mount. BenchBase requires Java and its built jar on the host or in a
separate runtime; the slim backend image does not include Java. Use the custom
workload for container jobs until that runtime is configured.

Raw research datasets are excluded from the image build context. Metadata uses
the mounted `backend/data/meta.sqlite` file; experiment JSON and the evidence
schema are mounted from the repository. The API is a local development service.
