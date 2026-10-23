# Progress log

## 2026-10-23 step 01 — scaffold (acceptance pending Docker)

- Done: imported AGENTS.md and steps 00–21; created directory skeleton, MIT
  license, environment template, PostgreSQL Docker setup, Python configuration,
  deterministic shared fixtures, Make targets, and README. Installed Python
  3.11.17, created .venv, installed dependencies, and pinned pip freeze output.
- Checks passed: Python syntax parsing; exact match of .env.example against
  contract section 0.2; Compose YAML structure validation; make target dry run;
  make test (exit 0, no tests yet); make lint (ruff check/format and mypy pass);
  pip check (no broken requirements); git diff --check.
- Commands: python3.11 -m venv .venv; pip install -r backend/requirements.txt;
  pip freeze; ruff format backend; make test lint with .venv/bin on PATH;
  python -m pip check; make -n up test lint; git diff --check.
- Environment: original system Python was 3.9.6; Node is v24.21.0. No Git remote
  is configured in this local checkout. No source has been pushed.
- Docker installation: brew install --cask docker downloaded Docker Desktop but
  installation failed while creating a system symlink because sudo requires an
  administrator password. Homebrew rolled back the Docker app installation.
  User must finish Docker Desktop installation and start its engine locally.
- Pending: make up; database health, extension availability, hypopg_reset, and
  shared_preload_libraries checks. Step 01 is not complete; no step-completion
  commit has been made. Step 02 has not started.
- Decision: initialize extensions and run health/acceptance checks against
  MAINT_DB, not qc_golden, to obey the golden connection invariant. Updated
  step 01 examples accordingly. Settings/live fixtures import their step 03
  implementations lazily. Empty-suite success only handles pytest exit code 5;
  collection errors and actual test failures retain failure exit codes.

## 2026-10-23 step 01 — acceptance complete

- Docker Desktop is installed. Launched it and confirmed Docker Engine 29.9.2.
- Commands: make up; docker compose up -d --wait db; docker compose ps;
  psql against MAINT_DB with ON_ERROR_STOP=1: \dx, SELECT * FROM hypopg_reset(),
  SHOW shared_preload_libraries, SHOW server_version; make test lint.
- Results: db healthy on port 5432; PostgreSQL 16.15; HypoPG 1.4.3;
  pg_stat_statements 1.10 installed and preloaded; hypopg_reset succeeded.
  Empty test suite exits 0; Ruff check/format pass; mypy passes (2 source files).
- All step 01 acceptance checks pass, using MAINT_DB per the documented golden
  connection rule. Database remains running. Step 02 has not started.
- Commit pending: user will provide the commit message. Stage only project
  source, configuration, documentation, and placeholders for empty directories.
  Exclude local secrets, environments, caches, and generated data. No Git remote
  configured and no push performed.
