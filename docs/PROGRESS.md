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

## 2026-10-25 step 04 — reports and protected transactions

- Implemented eight exact report templates (whitespace wrapping only), seeded
  parameter samplers, per-database cached clock/pools, and explicit cache invalidation
  after reseeding. Pool block sampling uses REPEATABLE(1234), ordered IDs and a
  fallback for small databases so initial cache population is reproducible.
- Five protected transactions have specified mix weights and statement tags.
  A runner requires an idle connection, commits one transaction, locks status rows
  before advancing exactly one state, and rolls back the whole order on out of
  stock. It raises OutOfStock(code=oos) for step 11 to count and time. Positive
  quantities and 2–4 order lines are required; stock locks use stable product order.
- Candidate fixture expectations remain hypotheses, never measured verdicts or
  authorization. All supplied candidates are present. Added deliberate bad rewrites
  for hourly_sales/top_products_store to satisfy the per-shape bad-candidate check.
- Resolved reference ambiguity: payout ±2-day jitter around as_of minus 8 days
  otherwise allows a window ending one day in the future. Clamp its start at
  as_of minus 7 days so every complete seven-day window stays inside the past 30
  days. Documented this rather than weakening the bounded-window check.
- Counterexample SQL checks cover duplicate-producing joins, dropped payout status,
  and UTC calendar-day mismatch. Correct rewrites agree on planted rows in UTC,
  Asia/Kolkata and America/New_York. Explicit ::timestamp casts are necessary for
  the deliberately UTC-bound rewrite to actually use UTC midnight; confirmed by
  PostgreSQL experiments. These are fixture checks, not the later equivalence engine.
- Verification: make test: 33 passed; make lint: Ruff check/format and mypy (9
  modules) pass. make test-db: 19 passed, 33 unit tests deselected. Database suite
  includes all seed/core tests and step 04 checks.
  Transaction writes and invalid index experiment use session-local temporary
  tables shadowing public names; seeded commerce records were not changed.
- No LLM or external dataset access. Step 05 not started. Commit waits for user's
  chosen message, per their standing instruction.

### Single-run report baseline (scale 3)

Measured using EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON), seed 1234, UTC,
as_of 2026-10-25T12:00:00+00:00. Each report measured once after acceptance queries;
these are warm/mixed-cache observations, not confidence estimates. Shared block
counts are the plan-root inclusive totals (not sums of child nodes). No temp blocks
were read or written. Under-50 ms reports are fast controls at this scale; do not
claim their hypothetical index speedups are established.

| Shape | Execution ms | Top node | Shared hits | Shared reads | Role at this scale |
|---|---:|---|---:|---:|---|
| recent_orders | 16.571 | Limit | 6551 | 3751 | fast control |
| delivered_yesterday | 29.779 | Aggregate | 6645 | 3655 | fast control |
| stockouts_15m | 2.955 | Sort | 1782 | 0 | fast control |
| hourly_sales | 17.815 | Aggregate | 6757 | 3559 | fast control |
| open_order_customers | 16.311 | Sort | 7332 | 3463 | fast control |
| payout_report | 70.759 | Sort | 6963 | 3367 | tuning target |
| top_products_store | 86.388 | Limit | 10227 | 19943 | tuning target |
| picker_queue | 16.09 | Limit | 7127 | 3175 | fast control |

Measured sampler inputs and row counts:

```json
{
  "seed": 1234,
  "as_of": "2026-10-25T12:00:00+00:00",
  "scale": "3",
  "timezone": "UTC",
  "shapes": [
    {
      "id": "recent_orders",
      "params": {
        "as_of": "2026-10-25T12:00:00+00:00",
        "customer_id": 1
      },
      "execution_ms": 16.571,
      "node": "Limit",
      "shared_hit_blocks": 6551,
      "shared_read_blocks": 3751,
      "temp_read_blocks": 0,
      "temp_written_blocks": 0,
      "rows": 20
    },
    {
      "id": "delivered_yesterday",
      "params": {
        "as_of": "2026-10-25T12:00:00+00:00"
      },
      "execution_ms": 29.779,
      "node": "Aggregate",
      "shared_hit_blocks": 6645,
      "shared_read_blocks": 3655,
      "temp_read_blocks": 0,
      "temp_written_blocks": 0,
      "rows": 50
    },
    {
      "id": "stockouts_15m",
      "params": {
        "as_of": "2026-10-25T12:00:00+00:00"
      },
      "execution_ms": 2.955,
      "node": "Sort",
      "shared_hit_blocks": 1782,
      "shared_read_blocks": 0,
      "temp_read_blocks": 0,
      "temp_written_blocks": 0,
      "rows": 423
    },
    {
      "id": "hourly_sales",
      "params": {
        "as_of": "2026-10-25T12:00:00+00:00"
      },
      "execution_ms": 17.815,
      "node": "Aggregate",
      "shared_hit_blocks": 6757,
      "shared_read_blocks": 3559,
      "temp_read_blocks": 0,
      "temp_written_blocks": 0,
      "rows": 1200
    },
    {
      "id": "open_order_customers",
      "params": {
        "city": "Pune"
      },
      "execution_ms": 16.311,
      "node": "Sort",
      "shared_hit_blocks": 7332,
      "shared_read_blocks": 3463,
      "temp_read_blocks": 0,
      "temp_written_blocks": 0,
      "rows": 367
    },
    {
      "id": "payout_report",
      "params": {
        "start": "2026-10-18T12:00:00+00:00",
        "end": "2026-10-25T12:00:00+00:00"
      },
      "execution_ms": 70.759,
      "node": "Sort",
      "shared_hit_blocks": 6963,
      "shared_read_blocks": 3367,
      "temp_read_blocks": 0,
      "temp_written_blocks": 0,
      "rows": 50
    },
    {
      "id": "top_products_store",
      "params": {
        "as_of": "2026-10-25T12:00:00+00:00",
        "store_id": 20
      },
      "execution_ms": 86.388,
      "node": "Limit",
      "shared_hit_blocks": 10227,
      "shared_read_blocks": 19943,
      "temp_read_blocks": 0,
      "temp_written_blocks": 0,
      "rows": 20
    },
    {
      "id": "picker_queue",
      "params": {
        "as_of": "2026-10-25T12:00:00+00:00",
        "store_id": 9
      },
      "execution_ms": 16.09,
      "node": "Limit",
      "shared_hit_blocks": 7127,
      "shared_read_blocks": 3175,
      "temp_read_blocks": 0,
      "temp_written_blocks": 0,
      "rows": 37
    }
  ]
}
```

## 2026-10-25 step 05 — statistics engine verified

- Implemented positive paired log ratios, geometric-mean relative change, one-sided
  Student-t bounds, alpha allocation across looks and tests, strict non-inferiority
  and improvement decisions, summaries, racing helpers and SequentialTest.
- Fail-closed edges: finite one-dimensional inputs only, validated budgets, infinite
  bounds with fewer than two pairs, sample deviation floor 1e-6, terminal-state
  latch, and no extra looks beyond the declared budget. Missing mean/SD estimates
  in empty/one-pair summaries are NaN, never invented zeros. Overflowing moment
  calculations yield unbounded intervals and cannot authorize PASS.
- The t-quantile cache uses rounded probability/df keys and the conservative upper
  probability-bucket edge. Ratios use log(b)-log(a) to avoid intermediate overflow.
- Seed 5001: 2000 sequential boundary trials, true geometric change +5%, log SD
  0.05, delta/alpha 0.05, family 1, min_n 6, max_n 60, batch 3, looks 19:
  PASS 34 (1.7%), FAIL 34 (1.7%), BLOCK 1932 (96.6%). False-accept rate is below
  alpha in this simulation. Boundary is unsafe for strict non-inferiority.
- Seed 5002: 400 trials, true geometric change 0%, same noise/policy: PASS 400
  (100%), FAIL 0, BLOCK 0. This is observed power, not a universal guarantee.
- Cached 60-pair verdict benchmark: mean 0.007694 ms over 1000 calls, seed 1234;
  below the 0.2 ms target and 5 ms test ceiling. Raw run log:
  /tmp/dpa-step05-stats.log (local temporary artifact).
- Commands: pytest tests/test_stats.py -s -q; make test (66 passed, 19 DB tests
  deselected); make lint (Ruff check/format and mypy, 10 modules); git diff --check.
  All pass. Step 05 adds 33 unit cases and requires no DB/network/LLM calls.
- Added paper method notes on assumptions, geometric vs arithmetic ratios,
  Bonferroni allocation, optional peeking, and conservative false-block cost.
- No database records changed. Step 06 has not started. Commit pending user's
  message; step 04's uncommitted work remains intact.

## 2026-10-25 step 06 — RQ2 noise simulation

- Implemented database-free paired multiplicative-noise trials, six pure decision
  rules, common random pairs across methods, Wilson 95% intervals, per-theta rates,
  error counts/denominators, measurement costs and atomic strict-JSON CLI output.
- Uses advisor_guard / DB Performance Advisor rather than the retired name, per
  user instruction. The fixed t-test is a one-sided paired test of raw differences
  b-(1+delta)*a; non-significance approves (deliberate fail-open comparator).
- Command (from backend): python -m app.simulate --trials 400 --seed 1234
  --out ../experiments/results/rq2_sim.json --borderline.
- 400 trials per theta per noise, four harmful and three harmless thetas: 1600
  harmful and 1200 harmless cases per noise. Headline mean/p90 costs pool these
  2800 cases. Borderline 0.04/0.05/0.06 are separate, using independent streams,
  and never enter headline error denominators. SeedSequence child streams preserve
  existing results when levels are appended; enabling borderline leaves them intact.
- Results are measured, not preset to a desired curve. Advisor false apply rises
  from 0/1600 to 1/1600 at 10% noise; zero observed errors do not establish zero risk.
  Wilson confidence intervals and counts are in the result file. At 10% noise the
  safe-case false-block cost is 65.75% and mean cost 42.121 pairs, p90 60.
- Wide fixed-threshold rules already accept some harmful cases without noise:
  threshold10 accepts 50%, threshold2x 100%. Their curves need not monotonically
  climb; the meaningful comparison includes that baseline permissiveness.
- Commands passed: focused simulation tests (9); make test (75 passed, 19 DB
  deselected); make lint (Ruff check/format and mypy, 11 modules). Full headline
  400-trial/3-noise run measured 1.318 seconds in the runtime test (<60 seconds).
- Corrected Wilson endpoint floating-point cancellation to return exact zero/one
  at k=0/n. No database, network or LLM calls. Step 07 not started. Commit pending
  the user's message, with steps 04–05 changes preserved.

### Measured RQ2 simulation summary

| Noise | Method | False apply % | False block % | Mean pairs | P90 pairs |
|---:|---|---:|---:|---:|---:|
| 0% | single_run | 0.0000 | 0.0000 | 1.000 | 1.0 |
| 0% | median5_band | 0.0000 | 0.0000 | 5.000 | 5.0 |
| 0% | threshold10 | 50.0000 | 0.0000 | 5.000 | 5.0 |
| 0% | threshold2x | 100.0000 | 0.0000 | 5.000 | 5.0 |
| 0% | ttest_fixed | 0.0000 | 0.0000 | 10.000 | 10.0 |
| 0% | advisor_guard | 0.0000 | 0.0000 | 6.000 | 6.0 |
| 5% | single_run | 18.8750 | 26.8333 | 1.000 | 1.0 |
| 5% | median5_band | 9.8125 | 15.5833 | 5.000 | 5.0 |
| 5% | threshold10 | 33.7500 | 1.5000 | 5.000 | 5.0 |
| 5% | threshold2x | 100.0000 | 0.0000 | 5.000 | 5.0 |
| 5% | ttest_fixed | 30.2500 | 0.2500 | 10.000 | 10.0 |
| 5% | advisor_guard | 0.0000 | 13.9167 | 26.639 | 60.0 |
| 10% | single_run | 27.6250 | 40.5000 | 1.000 | 1.0 |
| 10% | median5_band | 18.3125 | 30.0833 | 5.000 | 5.0 |
| 10% | threshold10 | 36.0625 | 11.9167 | 5.000 | 5.0 |
| 10% | threshold2x | 100.0000 | 0.0000 | 5.000 | 5.0 |
| 10% | ttest_fixed | 51.6250 | 0.9167 | 10.000 | 10.0 |
| 10% | advisor_guard | 0.0625 | 65.7500 | 42.121 | 60.0 |

## 2026-10-25 step 07 — disposable clones and real paired measurements

- Implemented sandbox creation, defensive ANALYZE, safe identifier validation,
  maintenance-only template access, clone size/timing metrics, bounded local
  semaphore slots, free-disk hint checks, pair/single contexts and cleanup CLI.
  Both pair slots are reserved together to avoid concurrent pair deadlocks.
- Trusted fixture DDL is limited to single nonconcurrent index/statistics commands
  on sandbox names. Candidate PostgreSQL errors mark rejected_invalid_ddl and raise
  InvalidDDL; both clones are cleaned. LLM safety filtering remains a step 09 task.
- Implemented read-only SELECT-only EXPLAIN ANALYZE measurements, local timeout,
  time/buffer extraction, warmups, alternating order, raw paired results, sequential
  stopping and wall-time budget handling. Unknown function calls are rejected;
  pg_sleep is permitted for timeout controls. This does not replace LLM safety.
- Timeout interpretation clarified: QueryTimeout carries timeout_ms and side.
  Candidate timeout must FAIL; baseline timeout must BLOCK in downstream callers.
  Censored timeout values are never inserted as valid buffer/timing samples.
- Updated the contracts' stale paired_report(..., cfg) signature to match step 07's
  explicit keyword policy. No existing code used the old cfg signature.
- Added seeded spawn-based CPU/disk interference with jitter, 64 MiB temporary
  disk file, measured host CPU utilization, and reliable terminate/join cleanup.
  Noise runs on macOS; PostgreSQL runs inside Docker Desktop, so interference is
  indirect. Requested duty cycles are NOT measured latency-noise percentages.
- Atexit cleans only clones owned by this process (avoids terminating another
  worker's clones); explicit --cleanup removes all matching project clones.
- Verification: make test 84 passed, 30 DB cases deselected; make lint Ruff
  check/format and mypy (14 modules) passed. Database suite: 29 passed, 1 xfailed,
  82 then-current unit cases deselected. Added two later unit-only controls for
  counterbalanced ordering and timeout-side propagation; they pass in the 84 run.
- Inconclusive test is the permitted host-noise variance soft assertion: 3 repeats
  with 2 CPU workers did not consistently increase paired variance. It is reported
  xfailed, not presented as a pass. The full noise report below shows mixed effects.
- Initial A/A query with a parallel LIMIT had unstable buffer counts. Replaced
  that stability-control query with SELECT count(*) FROM stores (not a relaxed
  bound); real report measurements retain their observed variability.
- Clone tests checked writes remain local, B-only indexes, golden idle, failed DDL
  cleanup, user-exception cleanup, insufficient disk rejection, and slot reuse.
  Measurement tests cover read-only/DML rejection, positive metrics, stable A/A
  PASS, planted heavier-query FAIL, timeout recovery and exhausted-budget uncertainty.
- noise_report.py ran seed 1234, 50 pairs/metric/cell, three report shapes and duties
  0/0.05/0.10. No sequential early stops in this fixed-sample report. Raw A/B values,
  log ratios, mean/SD, 80% log-scale detectable effect approximation and ±5% tail
  fraction are in experiments/results/noise_report.json. Baseline levels were
  measured first, so temporal host load is an additional experiment limitation.
- Created/analyzed clone times (seconds): [0.887266, 0.679138, 0.866873].
  Each source template size was 348404759 bytes at scale 3. No golden connections.
- Default remains time: latency safety requires elapsed-time evidence. Buffers are
  steadier here but cannot establish transaction latency safety; screen results
  retain their metric and only uncertain buffer results trigger timing fallback.
- Cleanup command: python -m app.sandbox --cleanup returned 0 after report and
  test contexts cleaned themselves. No leaked clones or noise workers/temp files.
- No qc_live destructive SQL, no raw research data changes. Step 08 not started.
  Commit deferred until user supplies a message; earlier uncommitted steps retained.

### Fixed-sample A/A noise report

SD and mean are on paired log ratios. MDE is approximately 2.8 SD / sqrt(50),
not an independently calibrated power guarantee. Outside is fraction with absolute
relative change above 5%; CPU is measured whole-host utilization, not worker duty.

| Shape | Duty | Metric | Mean log | SD log | MDE log (80%) | Outside ±5% | Host CPU % |
|---|---:|---|---:|---:|---:|---:|---:|
| stockouts_15m | 0.0 | time | -0.026242 | 0.200089 | 0.079231 | 0.600 | 23.3 |
| stockouts_15m | 0.0 | buffers | 0.000000 | 0.000000 | 0.000000 | 0.000 | 23.3 |
| stockouts_15m | 0.05 | time | 0.045611 | 0.215056 | 0.085158 | 0.640 | 49.3 |
| stockouts_15m | 0.05 | buffers | 0.000000 | 0.000000 | 0.000000 | 0.000 | 49.3 |
| stockouts_15m | 0.1 | time | 0.006162 | 0.226389 | 0.089645 | 0.700 | 48.5 |
| stockouts_15m | 0.1 | buffers | 0.000000 | 0.000000 | 0.000000 | 0.000 | 48.5 |
| payout_report | 0.0 | time | -0.017113 | 0.132059 | 0.052293 | 0.140 | 42.0 |
| payout_report | 0.0 | buffers | -0.000019 | 0.000137 | 0.000054 | 0.000 | 42.0 |
| payout_report | 0.05 | time | -0.002059 | 0.033960 | 0.013447 | 0.140 | 38.0 |
| payout_report | 0.05 | buffers | 0.000000 | 0.000000 | 0.000000 | 0.000 | 38.0 |
| payout_report | 0.1 | time | -0.004099 | 0.059286 | 0.023476 | 0.180 | 39.5 |
| payout_report | 0.1 | buffers | 0.000000 | 0.000000 | 0.000000 | 0.000 | 39.5 |
| top_products_store | 0.0 | time | -0.030112 | 0.108920 | 0.043130 | 0.260 | 41.1 |
| top_products_store | 0.0 | buffers | 0.000000 | 0.000000 | 0.000000 | 0.000 | 41.1 |
| top_products_store | 0.05 | time | -0.001906 | 0.081190 | 0.032149 | 0.260 | 50.5 |
| top_products_store | 0.05 | buffers | 0.000000 | 0.000000 | 0.000000 | 0.000 | 50.5 |
| top_products_store | 0.1 | time | 0.020911 | 0.180813 | 0.071598 | 0.360 | 54.7 |
| top_products_store | 0.1 | buffers | 0.000000 | 0.000000 | 0.000000 | 0.000 | 54.7 |
