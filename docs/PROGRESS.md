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

## 2026-10-23 step 01 — independent document recheck

- Compared the newly attached original step 01 document against the repository.
  Only the previously documented MAINT_DB clarification differs. User's chosen
  commit message `Initial scaffold` overrides the suggested step message; commit
  caa0b5b exists. The earlier pending-commit entry is historical.
- Re-ran make test lint, pip check, database health and extension checks,
  hypopg_reset, preload settings, and git diff --check: all pass. The test suite
  is intentionally empty. Confirmed shared_buffers=256MB, fsync=on, and
  synchronous_commit=on. Docker reports 10 CPUs and 8,319,504,384 bytes of memory.
- Verified all contract environment variables, installed dependency pins,
  Python/lint configuration, required directories, package files, and license.
- Fixed a fresh-clone gap: added backend/data/.gitkeep so Git preserves the data
  directory while external datasets and metadata files remain ignored.
- settings/live_conn fixtures depend on step 03 modules; they are deferred and
  are not exercised by the empty step 01 suite. No application behavior exists yet.
- Recheck changes are uncommitted pending the user's next commit message.

## 2026-10-23 step 02 — synthetic fixture verified; real-data direction adopted

- Implemented the specified schema, deterministic SQL seeder, verification report,
  live-only reset, and eight database tests. make seed, make reset-live,
  make test-db, make test, make lint, and --no-drop refusal checks passed.
- Initial scale 1 / seed 1234 run: 300000 orders, 901274 items, 80020 stock rows,
  20000 customers. Status counts: delivered 289842, cancelled 9027,
  out_for_delivery 381, packing 390, placed 360. Active placed/packing fraction
  0.0025; top 1% customer share 0.1233933333; top 10% product share 0.4636314817;
  recent stockouts 423; delivered yesterday 9612. All report checks passed.
- Golden size 126337551 bytes, below 268435456-byte shared_buffers; seed duration
  7.675 seconds. No larger-scale run was performed before the direction changed.
- Repeated full seed and live-only reset reproduced SHA-256 table-copy digests
  across every table, excluding the intentionally changing meta.seeded_at value.
  Raw local logs/checksums are under /tmp/dpa-step02-* and are not Git artifacts.
- Corrected sample SQL issues: materialized one item-count draw per order;
  derived delivery timestamps from the classified order status. Golden direct
  connections are disabled after seeding, while template cloning still works.
- User explicitly chose real commerce records for the main database, with missing
  operational fields clearly marked generated. This overrides step 02's synthetic
  evaluation data and step 18's calibration-only restriction for commerce datasets.
- User will supply download details in a later prompt. No external commerce data
  has been downloaded. Pause dataset selection/import work until then. Existing
  synthetic data is a development fixture only; step 02's revised real-data goal
  remains incomplete. No commit made; user supplies commit messages.

## 2026-10-25 step 02 — original generated-data plan restored and verified

- User restored the original generated quick-commerce dataset plan. This supersedes
  the October 23 pending real-commerce requirement. Research datasets remain
  separate supporting inputs for later experiments; no external records imported.
- Project name is DB Performance Advisor. Removed the retired acronym from the
  README, project guidance and step documents, as explicitly requested.
- Docker Desktop was stopped; started it. Initial sandbox access checks failed;
  all acceptance commands below were rerun successfully with local service access.
- Commands passed: make up; make seed (scale 1); make test-db (8 passed);
  make lint (Ruff check/format and mypy); make seed SCALE=3;
  make test-db (8 passed); make reset-live; make test (8 DB tests deselected,
  no unit tests exist yet). All measured seed/reset invariants passed.
- Scale 1 remains the fast default. Left golden/live at scale 3 so the dataset
  exceeds 256 MiB shared_buffers. Final experiments may require a larger scale
  than Docker RAM; scale 3 is not asserted to meet that later research target.
- Full measured reports follow. Step 02 implementation/verification is complete;
  commit remains pending the user's chosen message. Step 03 has not started.

### Scale 1 verification

```json
{
  "active_all_fraction": 0.00377,
  "active_fraction": 0.0025,
  "checks": {
    "active_all_fraction": true,
    "active_fraction": true,
    "as_of_utc_noon": true,
    "cancelled_fraction": true,
    "count_categories": true,
    "count_customers": true,
    "count_meta": true,
    "count_order_items": true,
    "count_orders": true,
    "count_products": true,
    "count_riders": true,
    "count_store_stock": true,
    "count_stores": true,
    "customer_skew": true,
    "delivered_fraction": true,
    "delivered_yesterday": true,
    "extensions": true,
    "golden_idle": true,
    "items_per_order": true,
    "no_secondary_indexes": true,
    "product_skew": true,
    "scale": true,
    "seed_present": true,
    "seeded_at_utc": true,
    "statuses": true,
    "stockouts": true,
    "time_spread": true,
    "valid_orders": true
  },
  "counts": {
    "categories": 40,
    "customers": 20000,
    "meta": 4,
    "order_items": 901274,
    "orders": 300000,
    "products": 2000,
    "riders": 500,
    "store_stock": 80020,
    "stores": 50
  },
  "customer_top_1pct_share": 0.12339333333333333,
  "delivered_yesterday": 9612,
  "duration_seconds": 8.229,
  "extensions": [
    "hypopg",
    "pg_stat_statements",
    "plpgsql"
  ],
  "golden_idle": true,
  "golden_size_bytes": 126475287,
  "indexes": [
    "categories_pkey",
    "customers_pkey",
    "meta_pkey",
    "order_items_pkey",
    "orders_pkey",
    "products_pkey",
    "products_sku_key",
    "riders_pkey",
    "store_stock_pkey",
    "stores_pkey"
  ],
  "invalid_orders": 0,
  "items_per_order": {
    "max": 5,
    "min": 1,
    "orders": 300000
  },
  "larger_than_shared_buffers": false,
  "metadata": {
    "as_of": "2026-10-25T12:00:00+00:00",
    "scale": "1",
    "seed": "1234",
    "seeded_at": "2026-10-25T03:41:11.271981+00:00"
  },
  "mode": "full",
  "product_top_10pct_share": 0.4636314816581861,
  "recent_stockouts": 423,
  "shared_buffers_bytes": 268435456,
  "statuses": {
    "cancelled": 9027,
    "delivered": 289842,
    "out_for_delivery": 381,
    "packing": 390,
    "placed": 360
  },
  "time_max": "2026-10-25T11:59:42.879906+00:00",
  "time_min": "2026-09-25T12:00:01.085752+00:00"
}
```

### Scale 3 verification

```json
{
  "active_all_fraction": 0.0037522222222222223,
  "active_fraction": 0.0025044444444444446,
  "checks": {
    "active_all_fraction": true,
    "active_fraction": true,
    "as_of_utc_noon": true,
    "cancelled_fraction": true,
    "count_categories": true,
    "count_customers": true,
    "count_meta": true,
    "count_order_items": true,
    "count_orders": true,
    "count_products": true,
    "count_riders": true,
    "count_store_stock": true,
    "count_stores": true,
    "customer_skew": true,
    "delivered_fraction": true,
    "delivered_yesterday": true,
    "extensions": true,
    "golden_idle": true,
    "items_per_order": true,
    "no_secondary_indexes": true,
    "product_skew": true,
    "scale": true,
    "seed_present": true,
    "seeded_at_utc": true,
    "statuses": true,
    "stockouts": true,
    "time_spread": true,
    "valid_orders": true
  },
  "counts": {
    "categories": 40,
    "customers": 60000,
    "meta": 4,
    "order_items": 2698210,
    "orders": 900000,
    "products": 2000,
    "riders": 500,
    "store_stock": 80020,
    "stores": 50
  },
  "customer_top_1pct_share": 0.12327555555555555,
  "delivered_yesterday": 28893,
  "duration_seconds": 23.538,
  "extensions": [
    "hypopg",
    "pg_stat_statements",
    "plpgsql"
  ],
  "golden_idle": true,
  "golden_size_bytes": 348396567,
  "indexes": [
    "categories_pkey",
    "customers_pkey",
    "meta_pkey",
    "order_items_pkey",
    "orders_pkey",
    "products_pkey",
    "products_sku_key",
    "riders_pkey",
    "store_stock_pkey",
    "stores_pkey"
  ],
  "invalid_orders": 0,
  "items_per_order": {
    "max": 5,
    "min": 1,
    "orders": 900000
  },
  "larger_than_shared_buffers": true,
  "metadata": {
    "as_of": "2026-10-25T12:00:00+00:00",
    "scale": "3",
    "seed": "1234",
    "seeded_at": "2026-10-25T03:41:34.088836+00:00"
  },
  "mode": "full",
  "product_top_10pct_share": 0.4646836235874895,
  "recent_stockouts": 423,
  "shared_buffers_bytes": 268435456,
  "statuses": {
    "cancelled": 26848,
    "delivered": 869775,
    "out_for_delivery": 1123,
    "packing": 1142,
    "placed": 1112
  },
  "time_max": "2026-10-25T11:59:59.750556+00:00",
  "time_min": "2026-09-25T12:00:01.710667+00:00"
}
```

### Live-only reset verification

```json
{
  "active_all_fraction": 0.0037522222222222223,
  "active_fraction": 0.0025044444444444446,
  "checks": {
    "active_all_fraction": true,
    "active_fraction": true,
    "as_of_utc_noon": true,
    "cancelled_fraction": true,
    "count_categories": true,
    "count_customers": true,
    "count_meta": true,
    "count_order_items": true,
    "count_orders": true,
    "count_products": true,
    "count_riders": true,
    "count_store_stock": true,
    "count_stores": true,
    "customer_skew": true,
    "delivered_fraction": true,
    "delivered_yesterday": true,
    "extensions": true,
    "golden_idle": true,
    "items_per_order": true,
    "no_secondary_indexes": true,
    "product_skew": true,
    "scale": true,
    "seed_present": true,
    "seeded_at_utc": true,
    "statuses": true,
    "stockouts": true,
    "time_spread": true,
    "valid_orders": true
  },
  "counts": {
    "categories": 40,
    "customers": 60000,
    "meta": 4,
    "order_items": 2698210,
    "orders": 900000,
    "products": 2000,
    "riders": 500,
    "store_stock": 80020,
    "stores": 50
  },
  "customer_top_1pct_share": 0.12327555555555555,
  "delivered_yesterday": 28893,
  "duration_seconds": 1.454,
  "extensions": [
    "hypopg",
    "pg_stat_statements",
    "plpgsql"
  ],
  "golden_idle": true,
  "golden_size_bytes": 348396567,
  "indexes": [
    "categories_pkey",
    "customers_pkey",
    "meta_pkey",
    "order_items_pkey",
    "orders_pkey",
    "products_pkey",
    "products_sku_key",
    "riders_pkey",
    "store_stock_pkey",
    "stores_pkey"
  ],
  "invalid_orders": 0,
  "items_per_order": {
    "max": 5,
    "min": 1,
    "orders": 900000
  },
  "larger_than_shared_buffers": true,
  "metadata": {
    "as_of": "2026-10-25T12:00:00+00:00",
    "scale": "3",
    "seed": "1234",
    "seeded_at": "2026-10-25T03:41:34.088836+00:00"
  },
  "mode": "live_only",
  "product_top_10pct_share": 0.4646836235874895,
  "recent_stockouts": 423,
  "shared_buffers_bytes": 268435456,
  "statuses": {
    "cancelled": 26848,
    "delivered": 869775,
    "out_for_delivery": 1123,
    "packing": 1142,
    "placed": 1112
  },
  "time_max": "2026-10-25T11:59:59.750556+00:00",
  "time_min": "2026-09-25T12:00:01.710667+00:00"
}
```

## 2026-10-25 step 03 — backend core implemented and verified

- Added frozen environment Settings covering every contract variable, cached loading,
  validation, sequential-look calculation and masked public configuration.
- Added PostgreSQL URI escaping, credential masking, maintenance/live connections,
  five-attempt startup retries, session defaults, trusted-value rendering and
  connection termination. Direct golden connections are rejected. Defaults are set
  before switching off autocommit to avoid starting a caller's transaction early.
- Implemented contract models/enumeration aliases, structured errors, serialization,
  central logging and message/argument/exception credential filtering.
- Added SQLite WAL metadata storage for jobs/events/candidates/evidence/applied
  changes/experiments, using a module-level RLock and bound values. Applied records
  are metadata only: no DDL is executed and no approval/apply pipeline exists yet.
- Verification: make test: 31 passed, 10 DB tests deselected; make up and make
  test-db: 10 passed, 29 then-current unit cases deselected; make lint: Ruff check,
  format and mypy (8 modules) pass; git diff --check passes. Later added unit-only
  contract coverage and cross-connection WAL tests also pass in the 31-test run.
- Concurrency checks: 8 threads x 100 emits, all 800 ordered unique events retained;
  two independent Store connections also retain all 200 test events. Checked JSON
  round trips, reopen persistence, incremental reads, overflow handling, FK rejection,
  status lifecycle, retry exhaustion, credentials and actual live SQL quote escaping.
- No hidden network/LLM calls in unit tests. PostgreSQL tests explicitly marked db.
  No changes to commerce records or research downloads. Step 04 has not started.
- Implementation and checks complete; commit deferred until user supplies message.
