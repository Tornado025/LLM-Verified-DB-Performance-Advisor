# Step 01 — Repository scaffold and environment

**Goal (end state):** a fresh clone can run `make up` (Postgres 16 with `pg_stat_statements` + `hypopg`
healthy), `make test` (green, zero real tests yet), and `make lint`. Git history has one commit
`step 01: scaffold`. `docs/PROGRESS.md` exists.

**Prerequisites:** Docker + Docker Compose v2, Git, Python 3.11, Node 20+ (for step 16), `make`.

## 1.1 Create the repository

1. `mkdir db-performance-advisor && cd db-performance-advisor && git init -b main`
2. Copy `AGENTS.md` to the root and the `docs/steps/` folder to `docs/steps/`.
3. Create the directory skeleton exactly as in `AGENTS.md` section 6 (empty folders get a `.gitkeep`).
4. `LICENSE`: choose Apache-2.0 or MIT (state your choice in README later).

## 1.2 `.gitignore`

```
.env
__pycache__/
*.pyc
.venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
node_modules/
dist/
backend/data/meta.sqlite*
backend/data/external/
benchbase/results/
experiments/results/*.tmp
*.log
.DS_Store
```
Commit `experiments/results/` JSON files only when they are final outputs (not `*.tmp`).

## 1.3 Docker

`docker/postgres.Dockerfile`
```dockerfile
FROM postgres:16
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-hypopg \
 && rm -rf /var/lib/apt/lists/*
```
If `postgresql-16-hypopg` is not found in the image's apt sources, add the PGDG repository (the official
`postgres` image already ships it) or build HypoPG from source (`make && make install` after installing
`postgresql-server-dev-16`). Verify the extension file exists:
`docker compose exec db ls $(pg_config --sharedir)/extension | grep hypopg`.

`docker/postgres.conf`
```
listen_addresses = '*'
max_connections = 200
shared_buffers = 256MB          # MUST stay smaller than the dataset (otherwise index effects vanish)
effective_cache_size = 1GB
work_mem = 16MB
maintenance_work_mem = 256MB
shared_preload_libraries = 'pg_stat_statements'
pg_stat_statements.track = all
pg_stat_statements.max = 10000
track_io_timing = on
autovacuum = on
fsync = on                      # leave ON for honest timings; do not disable for experiments
synchronous_commit = on
log_min_duration_statement = -1
```

`docker/init/01-extensions.sql`
```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS hypopg;
```
This only runs for `POSTGRES_DB`. `seed.py` (step 02) also runs it inside `qc_golden` so every clone
inherits both extensions.

`docker-compose.yml` (db service now; backend/frontend services are added in steps 15/16)
```yaml
services:
  db:
    build: { context: ., dockerfile: docker/postgres.Dockerfile }
    environment:
      POSTGRES_USER: ${PG_USER:-advisor}
      POSTGRES_PASSWORD: ${PG_PASSWORD:-advisor}
      POSTGRES_DB: ${MAINT_DB:-postgres}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./docker/init:/docker-entrypoint-initdb.d:ro
      - ./docker/postgres.conf:/etc/postgresql/postgresql.conf:ro
    command: ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]
    shm_size: 1g
    ports: ["${PG_PORT:-5432}:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${PG_USER:-advisor} -d ${MAINT_DB:-postgres}"]
      interval: 5s
      timeout: 3s
      retries: 20
volumes:
  pgdata: {}
```
Notes: the init scripts run only on an **empty** data directory. After changing them run
`docker compose down -v` (this deletes data; re-seed afterwards).

## 1.4 Python project

`backend/requirements.txt` (pin exact versions after the first successful install with `pip freeze`):
```
fastapi
uvicorn[standard]
psycopg[binary]
sqlglot
numpy
scipy
httpx
pydantic>=2
python-dotenv
pytest
pytest-timeout
ruff
mypy
pytest-cov
jsonschema
psutil        # optional: noise load measurement
pandas        # analysis scripts only
pyarrow       # reading Redset parquet
```
`backend/pyproject.toml`
```toml
[tool.ruff]
line-length = 100
target-version = "py311"
[tool.ruff.lint]
select = ["E","F","I","UP","B","SIM"]
[tool.mypy]
python_version = "3.11"
ignore_missing_imports = true
warn_unused_ignores = true
```
`backend/pytest.ini`
```
[pytest]
testpaths = tests
markers =
    db: needs the PostgreSQL service from docker compose
    llm: calls a real LLM API (skipped unless RUN_LLM_TESTS=1)
    slow: takes more than 30 seconds
timeout = 300
addopts = -q
```
`backend/tests/conftest.py`: skip `db` tests when `connect()` to `MAINT_DB` fails; skip `llm` tests unless `RUN_LLM_TESTS=1`; provide fixtures `settings`, `rng` (`numpy.random.default_rng(1234)`), `live_conn` (connection to `qc_live`, autocommit).

## 1.5 Makefile

```make
.PHONY: up down seed test test-db lint api web experiments clean
up:        ; docker compose up -d db
down:      ; docker compose down
seed:      ; cd backend && python scripts/seed.py --scale 1
test:      ; cd backend && pytest -m "not db and not llm"
test-db:   ; cd backend && pytest -m db
lint:      ; cd backend && ruff check . && ruff format --check . && mypy app
api:       ; cd backend && uvicorn app.main:app --reload --port 8000
web:       ; cd frontend && npm run dev
experiments: ; cd backend && python scripts/run_experiments.py --all
clean:     ; docker compose down -v
```

## 1.6 `.env.example`
Copy the block in `00_contracts.md` section 0.2 verbatim. Then `cp .env.example .env`.

## 1.7 `docs/PROGRESS.md` template
```
# Progress log
## YYYY-MM-DD step NN — title
- Done: …
- Commands run: …
- Results / numbers: …
- Problems and decisions: …
```

## 1.8 Python environment

```
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
```
Create empty package files: `backend/app/__init__.py`, `backend/app/llm/__init__.py`, `backend/tests/__init__.py`.

## 1.9 Acceptance checks

| Check | Command | Expected |
|---|---|---|
| Postgres healthy | `docker compose ps` | `db` is `healthy` |
| Extensions present | `docker compose exec db psql -U advisor -d postgres -c "\dx"` | shows `pg_stat_statements` and `hypopg` |
| HypoPG usable | `docker compose exec db psql -U advisor -d postgres -c "SELECT * FROM hypopg_reset();"` | no error |
| Settings loaded | `docker compose exec db psql -U advisor -d postgres -c "SHOW shared_preload_libraries;"` | contains `pg_stat_statements` |
| Tests | `make test` | exit 0 ("no tests ran" is acceptable here) |
| Lint | `make lint` | exit 0 |

## 1.10 Done checklist
- [ ] Repo skeleton, `.gitignore`, `LICENSE`, `Makefile`, `.env.example`, compose file committed
- [ ] `docs/PROGRESS.md` has the first entry
- [ ] Commit `step 01: scaffold`

## 1.11 Pitfalls
* Compose `command` replaces the entrypoint's default args; keep `postgres` first.
* If `hypopg` cannot be installed from apt, do not skip it — step 08 depends on it.
* Docker Desktop on Windows/macOS: allocate at least 6 CPUs and 8 GB RAM, and ≥ 60 GB disk image for later steps.

## Local clarification

Initialize and validate extensions in `MAINT_DB` during scaffolding. Step 02 creates
`qc_golden` during seeding. This keeps health and acceptance checks consistent with
the golden database connection prohibition in AGENTS.md and step 00.
