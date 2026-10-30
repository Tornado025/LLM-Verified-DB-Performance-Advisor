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

## 2026-10-25 step 08 — plans, HypoPG screening and observation

- Added sandbox-only EXPLAIN with JSON, generic PostgreSQL 16 plans, read-only
  analyzed SELECTs and an opt-in per-job bounded cache. Session PID/options extend
  the base cache key so different hypothetical sessions cannot share stale plans.
  HypoPG create/reset invalidates cache; analyzed plans are never cached.
- Added structural 12-hex SHA-1 fingerprints, literal normalization, sort-column
  canonicalization, OID normalization, and depth-preserving preorder traversal.
  Costs, estimates, actual runtimes, buffers and worker counts do not affect identity.
- Added B-tree DDL normalization and parameter-bound HypoPG planning with reset
  in finally, including on query errors. No hypothetical candidate builds a real index.
- Added kind-specific signatures, fixed-parameter no-plan and same-kind duplicate
  filtering, existing-index checks and normalized stats-column signatures. Existing
  physical indexes take duplicate precedence over generic no-plan to meet the
  reference's explicit existing-index acceptance case. Duplicate owner ID is in reason.
- Added public schema Catalog (columns/types/nullability, indexes, planner row
  estimates, PKs/FKs) and pg_stat_statements observation by total/mean time. System
  and utility SQL is excluded; missing extension is an explicit list-result flag.
  Updated contracts for Catalog/flag and the step's keyword/generic EXPLAIN signature.
- Verified fingerprint stability across changing hypothetical OIDs, cache lifetime,
  cache invalidation, generic $1 planning, analyzed SELECTs, live refusal, real
  invalid expression DDL rejection, plain/partial/expression/covering hypothetical
  indexes, statistics signatures, existing-index duplication, query tags and catalog.
- Commands: focused tests/test_plans.py -m db -s: 11 passed, 5 non-DB cases
  deselected; make test: 90 passed, 41 DB cases deselected; make lint: Ruff
  check/format and mypy (17 modules) pass; git diff --check. Raw integration log
  is /tmp/dpa-step08-plans.log. Cleanup CLI returned 0; no sandbox leaks.
- All 24 step 04 fixtures were screened on a baseline clone at scale 3, seed 1234.
  recent_orders status index was skipped_noplan; delivered_at DATE index was
  rejected_invalid_ddl. Its real clone attempt also rejected it. Two identical
  index definitions with different names were skipped_duplicate on the second.
  INCLUDE(store_id) had a different signature and was retained, per observed plans.
- Fixture observations also differ from hypotheses: hourly_sales composite index
  and open_order_customers partial index did not change their sampled plans here.
  The deliberately wrong hourly-window rewrite also had the same structural plan
  and was dropped. Correct/wrong delivered-day rewrites both survived screening.
  Surviving means proposed only, never selected, equivalent, faster or safe.
- This is plan screening, not the LLM safety allowlist or equivalence engine.
  No LLM/network calls; no qc_live changes; query measurements/guard decisions
  were not fabricated. Step 09 not started. Commit pending user's message.

- Final normalization edge check: preserve case and significant whitespace inside
  quoted identifiers, so differently named columns/tables never collapse together.

## 2026-10-25 step 09 — providers, allowlist and plan-saturation sampling

- Added typed provider/context/result contracts, bounded JSON extraction, prompt
  construction with seeded table/index order variation, OpenRouter and Ollama
  HTTP clients, and deterministic mock sampling with trusted parameter binding.
- HTTP clients implement bounded 429/5xx/transport backoff, optional OpenRouter
  response_format fallback, one parse-reminder retry, usage/model/provider/temperature
  records, output/prompt size caps and optional explicit request budgets. No full
  prompts, keys or provider error bodies are logged. Settings repr hides credentials.
- Pure safety checks reject comments, multiple statements, DML/locking/INTO,
  unknown tables/columns, non-B-tree or unique indexes, oversized indexes, volatile
  index expressions, unsupported functions, system schemas, custom cast types and
  operator classes. CONCURRENTLY is removed before plan checking. Rewrite output
  names/count are checked against an explicitly supplied baseline via qualification.
  Semantic correctness and the three rewrite-class validation remain step 10 work.
- The proposer runs safety before normalization/signature DB calls, records
  rejections/duplicates and usage, enforces per-job index identity, stops on plan
  saturation or should_stop(), and records a cumulative novelty curve. Provider
  failures stop the run rather than falsely implying saturation. Updated contracts
  for provider/proposer signatures, Catalog.original_sql and the result curve.
- Local mock integration is reproducible and saturates on recent_orders, keeping
  multiple distinct signatures. Every step 04 mock fixture is safety-screened;
  deliberately semantically wrong rewrites may pass syntax/scope checks and still
  need equivalence. Invalid DDL and rejected safety evidence are separately tested.
- Initially configured GPT-4.1 Mini with explicit user authorization and $3 budget.
  Live models API confirmed input $0.40/M, output $1.60/M. Real smoke call:
  OpenAI provider, temperature 0.7, 970 prompt / 89 completion tokens,
  reported cost $0.0005304; one index proposal parsed and passed the safety screen.
- Generation used 60 hand-written seed questions (6 categories, 12 sale-specific),
  producing 228 saved questions validated by the final safety checker and read-only
  EXPLAIN on qc_live. 53 are sale-specific, all day labels 1..14 represented.
  SHA256 questions.json: e94ea64fc4af6aae4afc2f925133c4adbd96aac84978cb2323d4a4dc1b38bbd7.
  Completed saved generation responses: 11 requests, reported cost $0.0594336.
- User requested a smaller model while generation was running. Terminated that
  worker and preserved its >=200 accepted items. Metadata explicitly records Mini
  as their source and the user-requested stop. An interrupted in-flight response,
  if billed, is not included in the saved-response subtotal; do not claim account
  billing/remaining credits from this subtotal. No regeneration or further Mini calls.
- Changed ignored .env to openai/gpt-4.1-nano; mock remains default LLM_PROVIDER.
  Live models API confirmed Nano input $0.10/M, output $0.40/M (4x cheaper token
  rates). One Nano smoke call via Azure, temperature 0.7, 970 prompt / 152 completion
  tokens, cost $0.0001578. Its parsed rewrite was cleanly rejected by the allowlist;
  this is not evidence of correctness or a model-quality benchmark.
- Recorded completed-response subtotal: $0.0601218 including both smoke calls.
  Generation capped at 15 HTTP requests; each request at 8192 output tokens and
  50000 encoded prompt bytes. The user's $3 limit was not treated as a spending goal.
- Provider wire/usage verification references: OpenRouter chat reference
  https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request;
  model pricing https://openrouter.ai/openai/gpt-4.1-nano/providers;
  Ollama source docs https://github.com/ollama/ollama/blob/main/docs/api.md.
- Verification: make test 146 passed, 44 DB/real-LLM cases deselected; make lint
  Ruff check/format and mypy (25 modules) pass. Focused mock DB tests passed;
  final focused mock DB run: 2 passed, 2 non-DB cases deselected. Cleanup CLI
  returned 0. Read-only re-audit of all 228 saved queries had zero failures. Real calls were
  explicit, not hidden in unit tests; real API pytest is marked llm and opt-in.
  HTTP retry/fallback/parse tests use MockTransport only. Seed list coverage checked.
- .env is ignored and mode 0600. A scan of every Git-visible file found no API
  credential. No destructive qc_live operations, no research-source changes.
  Step 10 not started. Step 08/09 changes remain uncommitted pending user's message.

## 2026-10-25 step 10 — tiered rewrite equivalence tested

- Implemented syntax/output-name/count/type-family checks with read-only LIMIT 0;
  identical SQL returns unknown. Restricted structural recognizers cover bare
  range predicates, correlated EXISTS/IN/scalar-to-join claims and redundant
  DISTINCT/internal-sort/join removal. Top-level sort removal is always rejected.
- Implemented seeded counterexample rows in cx: PK/unique keys, ancestor tables,
  FK parent selection, duplicate join keys, no-child parents, NULLs, numeric/text
  literal pools and local-midnight boundaries in all configured timezones. Local
  scratch sequences avoid modifying public serial sequences. Cleanup uses finally.
- Each eligible positive runs 50 seeded scratch databases (seed 1234+k), default
  rows_per_table 30, in UTC/Asia-Kolkata/America-Los_Angeles, then symmetric full-data
  EXCEPT ALL, count and ordered-result hash checks under all three zones. Queries
  execute read-only with transaction-local timeout, timezone and search_path.
- Results carry tested/rejected/unknown, class/tier, actual scratch count, bounded
  nonvacuous fraction, weak flag and truncated replayable counterexample evidence
  with seed/case/row counts. Unsupported generators, unhashable JSON, timeout,
  scratch-size excess and unavailable veto fail closed. No claim of proof.
- Added an explicit optional veto adapter; it preserves reported usage in the
  reason and can only downgrade. It is disabled by default and tested with mocks;
  no additional OpenRouter calls or credits were used in this step.
- Bootstrapped backend/data/rewrite_pairs.json with the 10 mandatory traps and
  12 positive cases across all three classes, with human-written explanations.
  This is the step 10 seed catalogue, not step 18's future >=60-pair expansion.
- Initial smoke check exposed EXISTS as a sqlglot Func subclass. Added it as a
  structural predicate to the safety allowlist and added a regression test.
  The unbounded E8 scalar COUNT over 60000 customers timed out without an index;
  bounded both versions to the same three customer IDs rather than loosening
  timeout or masking a failure. It now tests the same scalar-to-join behavior.
- Final measured sweep: all T1–T10 rejected, all E1–E12 tested. Every E case ran
  all 50 scratch databases with nonvacuous_fraction=1.0, weak=False, and completed
  tier 3. T1's first mismatch was Asia/Kolkata. Other targeted tier-2 traps caught
  inclusive/degenerate bounds, dropped subquery filter, invalid DISTINCT removal
  and a filtering join removal. T4/T5/T8/T10 reject structurally at tier 1.
- Prefix-tier false-accept/false-block counts follow. These are planted-set
  survival statistics, not universal error-rate estimates. No candidate can be
  selected for application merely by passing an early tier.

| Through tier | Traps surviving / 10 | Equivalent blocked / 12 |
|---|---:|---:|
| 0 | 10 | 0 |
| 1 | 6 | 0 |
| 2 | 0 | 0 |
| 3 | 0 | 0 |

- Full measured 22-pair sweep took 19.441 seconds at scale
  3. Saved raw verdicts, counterexamples, policy and pair hash in
  experiments/results/equivalence_report.json. Runner: scripts/equivalence_report.py.
- Verification: make test 173 passed, 73 DB/real-LLM cases deselected; focused
  tests/test_equivalence.py -m db: 29 passed, 25 then-current unit cases deselected
  in 28.13s (including the sweep and additional DB edge checks); make lint: Ruff
  check/format and mypy (26 modules) pass; git diff --check passes.
- Additional checks: session timezone/search_path restoration; unchanged public
  sequence state; scratch cleanup after success and injected insertion error;
  timeout returns unknown; nullable JSON reaches unknown at tier 3, NOT NULL JSON
  at tier 2; output type-family mismatch rejected at tier 0; live DB forbidden;
  duplicate/NULL/numeric comparisons; CTE sort recognizer; explicit mock veto.
- Final cleanup CLI returned 0: no sandbox databases remain. No qc_live writes,
  raw research-data changes or model charges. Step 11 not started. Commit pending
  user's chosen message; previous step 08/09 work remains uncommitted.

## 2026-10-25 step 10 — resumed final safety review

- Added a defensive call to safety.check with trusted schema/baseline context before
  executing candidate SQL for output-description checks. Regression confirms a
  system-schema candidate never reaches the description query.
- Re-ran the complete equivalence sweep: all 10 traps rejected, all 12 equivalent
  examples tested after 50 scratch populations and three timezones each. Updated
  equivalence_report.json with this measured run.
- Commands: make test (173 passed, 74 DB/real-LLM cases deselected); focused DB
  equivalence suite (30 passed, 26 non-DB cases deselected, 31.45 seconds);
  make lint and git diff --check passed. Cleanup CLI returned 0.
- No API calls or model charges. Step 10 is verified; step 11 has not started.
  Changes remain uncommitted pending the user's chosen message.

## 2026-10-25 step 11 — runner and BenchBase implementation (acceptance in progress)

- Added deterministic Poisson schedules, profile-specific product skew/polling,
  precomputed order parameters and logical references to earlier scheduled order
  creations. Logical references avoid depending on concurrently allocated sequence
  ids. Seeded status transition budgets prevent exhausted active pools. A/B reuse
  the same schedule and initial pools. Fresh clones per A/A pair avoid state drift.
- Worker connections execute the five trusted transactions; a separate dispatcher
  keeps arrivals open-loop. Finish-target latency includes queueing; OOS/errors,
  dropped arrivals, dashboard samples and dispatcher lag are retained. Dashboard
  samples are excluded from protected transaction cells. Stragglers are bounded.
- Clone preparation fills missing hot stock rows as well as updating existing
  ones: the seed intentionally omits 20% of stock rows, so UPDATE alone cannot
  meet the requested available-hot-stock fixture. Flash preload inserts 2,000
  products with stock for 20 stores and refreshes prices. Flash20 skips explicit
  ANALYZE; autovacuum remains enabled and may analyze asynchronously.
- Actual 20-second calibration extended beyond the original 320 TPS ceiling to
  640/1,280/2,560, because 320 was not saturation. Place-order p95 was 14.513 ms at
  5 TPS, 8.090 ms at 1,280 TPS, then 5,674.199 ms at 2,560 TPS. Last good rate:
  1,280 TPS; uncensored. Local BASE_RATE_TPS=41 gives flash20=820 TPS (64.06%).
  BLOCK_SECONDS=12 covers the 10-second normal dashboard interval and rare types;
  A/A uses longer 20-second normal and 10-second flash5 blocks. Hardware/scale
  changes require recalibration. Raw measurements: backend/data/calibration.json.
- Built pinned CMU BenchBase 33c00473807ebd49304d114a6d769d2d2b2bbb34 using verified
  Temurin 23.0.2+7. README's Java 21 advice differs from pom.xml's Java 23 target;
  Java 27 fails in its formatter plugin. Build command: JAVA_HOME=<jdk23-home>
  ./mvnw clean package -P postgres -DskipTests. Packaging succeeded; upstream
  tests were not run. Source/JDK/distribution/raw logs are ignored; provenance
  and upstream license retained in benchbase/.
- Actual load_ch('qc_ch_golden', 10) succeeded; warehouse count checked and CH
  template closed to ordinary connections. Source confirms composite benchmark
  name tpcc,chbenchmark and work attribute arrival="poisson". Four configs and
  adapter preserve separate CH databases, ms conversion, independent abort/error/
  retry counts, missing-data nulls, and fresh output directories. See benchbase/
  README.md for sources, commands and limitations. No model calls or charges.
- Acceptance still running: A/A raw checkpoints are retained in
  experiments/results/workload_aa.json. No A/A PASS or step completion claimed
  yet. BenchBase 60-second profile executions follow A/A to avoid interference.
  Final acceptance/test numbers will be appended. No commit without the user's
  chosen message; no step 12 work started.

### Step 11 checkpoint interference diagnosis

- Initial A/A attempt: 40 normal pairs, mean logs place_order 0.0152,
  update_status -0.0094, order_status -0.0020, stock_decrement -0.0243,
  picker_queue_read -0.0267. Upper bounds 0.0630/0.0620/0.0347/0.0469/-0.0049:
  first two BLOCK, remaining three PASS. Flash5 stopped after three completed
  pairs. Evidence retained as workload_aa_busy_host.json; this is not acceptance.
- User reported the Mac quiet before a fresh attempt. Spikes persisted (e.g.
  134–171 ms place_order and 149–169 ms update_status, while dispatcher lag
  remained about 5 ms). This rules out treating desktop activity as the complete
  explanation. Interrupted the attempt with SIGINT; contexts cleaned clones.
  Completed pairs retained as workload_aa_wal_log.json, explicitly incomplete.
- PostgreSQL logs repeatedly showed "checkpoints are occurring too frequently
  (1 second apart)" and WAL-triggered checkpoints lasting about 43 seconds during
  blocks. PostgreSQL 16 CREATE DATABASE defaults to WAL_LOG, logging every copied
  template block. Repeated pairs of 348 MB templates created substantial WAL.
- Changed sandbox creation to STRATEGY FILE_COPY: much smaller WAL records and
  forced checkpoints completed before clone creation returns. This changes clone
  preparation, not database tuning knobs or measured transaction definitions.
  Official reference: https://www.postgresql.org/docs/16/sql-createdatabase.html
  (strategy parameter). Record actual clone strategy in metrics/A/A evidence.
  No claim of resolved measurement noise until the corrected A/A run passes.
- make test: 179 passed / 78 deselected. make lint: ruff check+format and mypy
  clean (28 app modules). Focused workload DB tests: 3 passed / 4 deselected in
  8.11s, including actual FILE_COPY cloning/preparation/traffic/error accounting.
- Repeating calibration after this preparation change. Prior calibration raw
  data retained locally in ignored benchbase/results/calibration_wal_log.json.

## 2026-10-25 step 11 — paused at user request

- User asked to continue tomorrow. Sent SIGINT to the active A/A worker; it
  exited 130 and its caffeinate process ended. Verified zero qc_sbx_* databases
  and zero workload connections. No benchmark/download worker started for later.
- Corrected FILE_COPY calibration completed successfully: last good rate 1,280
  TPS, 2,560 TPS exceeded the threshold; BASE_RATE_TPS remains 41 and local
  BLOCK_SECONDS remains 12. Low-load p95 and final overload values are in the
  current backend/data/calibration.json. Current calibration numbers:
  low-load p95=14.241698 ms; overload p95=2298.457598 ms.
- Corrected A/A checkpoint: normal, 13 completed pairs; profile passed=False.
  {'place_order': {'mean_log': -0.000884, 'upper': 0.05705, 'state': 'CONTINUE'}, 'update_status': {'mean_log': 0.000707, 'upper': 0.073528, 'state': 'CONTINUE'}, 'order_status': {'mean_log': 0.009858, 'upper': 0.109348, 'state': 'CONTINUE'}, 'stock_decrement': {'mean_log': -0.007706, 'upper': 0.081369, 'state': 'CONTINUE'}, 'picker_queue_read': {'mean_log': -0.011094, 'upper': 0.024635, 'state': 'PASS'}}
- A/A remains incomplete; do not mark step 11 finished. Saved current raw
  pairs in experiments/results/workload_aa.json. Earlier failed/interrupted
  attempts are workload_aa_busy_host.json and workload_aa_wal_log.json; retain
  them as non-acceptance evidence. Current log: /tmp/dpa-step11-aa.log.
- Resume: read this entry, inspect worker state (do not duplicate), then rerun
  `python backend/scripts/validate_workload_aa.py` on a quiet Mac, preserving
  the interrupted checkpoint separately first. The CLI currently starts a fresh
  experiment rather than appending pairs. Default blocks normal=20s, flash5=10s;
  acceptance is >=12 pairs, all five types PASS and abs(mean log)<0.03 within40.
  Bounds retain alpha=.05, delta=.05, family10, looks35. File-copy strategy is
  recorded per clone. No weakening checks or selectively discarding pairs.
- BenchBase source/JDK/build and ten-warehouse qc_ch_golden load are complete.
  Still run `python backend/scripts/benchbase_smoke.py` after A/A (never concurrently):
  normal/flash5/flash10/flash20 each60s plus flash20 with active warehouse range2.
  It creates qc_ch if absent, uses fresh FILE_COPY CH clones, checkpoints actual
  results into experiments/results/ch_smoke.json and drops temporary clones.
- Remaining verification: slow A/A evidence audit; focused sandbox/workload DB
  tests (including FILE_COPY); make test and make lint after final edits. Last
  completed results:179 unit tests passed,3 focused workload DB tests passed,
  Ruff clean and mypy clean28 modules. Minor subsequent edits were metadata/
  evidence assertions/scripts; rerun final checks. Slow A/A audit intentionally
  fails on the current incomplete checkpoint. All other step08–11 changes remain
  uncommitted, per user's control of commit messages. No step12 work started.

## 2026-10-26 step 11 — resumed at user request

- Read current AGENTS.md, contracts, step 11 and the saved pause entry. No prior
  benchmark workers remained. Docker Desktop was stopped; started it and the
  existing db container without resetting/seeding or changing the named volume.
- Added explicit --resume to the A/A CLI. It rejects changed policies, durations,
  seed/order prefixes, invalid blocks and non-FILE_COPY clones; reconstructs all
  paired observations from raw samples; preserves the original40-pair budget and
  look allocation; and records resumed_at timestamps. Prior completed13 pairs
  were retained, not selectively discarded. Backup before resuming:
  experiments/results/workload_aa_paused_20261025.json.
- Running caffeinate -i python backend/scripts/validate_workload_aa.py --resume;
  current log /tmp/dpa-step11-aa-resume.log. Independent BenchBase runs remain
  queued after A/A; no concurrent heavy experiments or real LLM calls.
- Added a unit control rejecting missing/incompatible resume checkpoints before
  any database work. Final acceptance and verification results pending.

### Step 11 A/A acceptance completed on 2026-10-26
- Resumed experiment completed successfully without changing thresholds or dropping prior completed pairs. Seeds1234+i,16workers, normal20s/flash5 10s; alpha=.05, delta=.05, family10,35looks, max40pairs.
- All below are derived from the saved raw samples, in percent.

| Profile | Pairs | Transaction | Geometric mean change % | Upper bound % | Verdict |
|---|---:|---|---:|---:|---|
| normal | 32 | place_order | 0.0716 | 2.8077 | PASS |
| normal | 32 | update_status | 0.9613 | 4.9412 | PASS |
| normal | 32 | order_status | 0.2005 | 4.4280 | PASS |
| normal | 32 | stock_decrement | 0.2227 | 4.3895 | PASS |
| normal | 32 | picker_queue_read | -0.7089 | 1.1183 | PASS |
| flash5 | 15 | place_order | -0.8836 | 4.2663 | PASS |
| flash5 | 15 | update_status | -0.7935 | 4.4317 | PASS |
| flash5 | 15 | order_status | -1.1079 | 4.4131 | PASS |
| flash5 | 15 | stock_decrement | -2.7790 | 4.3868 | PASS |
| flash5 | 15 | picker_queue_read | -0.3587 | 3.0070 | PASS |

- Every mean log magnitude is below.03, every upper bound is below.05, every block is valid, and no arrivals were dropped. Raw latencies include queueing and failed transactions.
- Failed attempts were retained: normal A/B counts match exactly per type (place5130, update189, status134, stock677, picker0); flash5 counts likewise match (5974,422,628,1005,0). These include out-of-stock attempts and references to failed planned order creations; this is not a claim that all attempted orders succeeded. Dashboard samples are present but excluded from protected cells.
- Current result: experiments/results/workload_aa.json; saved interrupted/failed attempts remain separate and are not used as acceptance passes. Completed normal32pairs and flash5 15pairs.
- Started separate BenchBase smoke runs only after A/A exited0. No real LLM calls. Final functional tests and BenchBase results pending.

### Step 11 final verification — 2026-10-26
- BenchBase completed all five CH-benCHmark/TPC-C-derived smoke scenarios on
  disposable clones: normal, flash5, flash10, flash20, and flash20 with two
  active warehouses. Every run lasted about 60 seconds and included all five
  protected transaction sample types. Results are in
  `experiments/results/ch_smoke.json`; each run records measured throughput and
  successful goodput separately.
- BenchBase throughput/goodput requests per second: normal 36.5/19.9; flash5
  215.1/193.5; flash10 429.9/331.4; flash20 831.9/544.0; flash20 with two
  active warehouses 835.1/361.8. These are local smoke measurements, not
  certified TPC-C results or claims about production capacity.
- The smoke run exposed unexpected SQL errors: 0 normal, 0 flash5, 1,430
  flash10, 11,511 flash20, and 2 flash20/two-warehouse. PostgreSQL logged
  `out of shared memory` and recommended reviewing
  `max_pred_locks_per_transaction`; it is 64 in this environment. BenchBase's
  sample configuration uses SERIALIZABLE transactions. This is recorded as an
  observed limitation, not a clean/error-free benchmark pass. No PostgreSQL
  runtime setting was changed because tuning configuration is outside scope.
- Final checks: `make test` — 180 passed, 78 deselected;
  `make lint` — Ruff check/format and mypy clean across 28 app modules;
  `pytest -m 'db and not slow' tests/test_sandbox.py tests/test_workload.py` —
  8 passed, 6 deselected; and `pytest -m 'db and slow' tests/test_workload.py`
  — 1 passed, 7 deselected (saved A/A evidence audit).
- Calibration remains BASE_RATE_TPS=41, from measured saturation at 1,280 TPS;
  2,560 TPS exceeded the target. Measured low-load p95 was 14.24 ms and
  overload p95 was 2,298.46 ms. Sandbox clones were cleaned up; `qc_live` was
  not used for benchmark writes, and both golden databases remain closed to
  ordinary connections.
- Step 11 acceptance checks are complete. No commit was created; the commit
  checklist remains for the user to supply the commit message. No step 12 work
  started.
- Created `docs/POST_21_FOLLOWUPS.md` to track the BenchBase high-load predicate-
  lock errors and Step 07 host-noise variance test. Both are explicitly deferred
  until after Step 21 and do not gate or alter numbered-step acceptance.

## 2026-10-26 step 12 — guard, race, decide in progress

- Implemented round-based report-query racing, paired write-cost guarding across
  normal/flash5/flash10/flash20, and rewrite/index/block decisions. Added unit
  checks for fail-closed verdicts, preference boundaries, invalid index DDL,
  guard measurement errors, and preserving statistical failure bounds. No Step
  12 commit has been made.
- `make test`: 197 passed, 82 deselected. `make lint`: Ruff check/format clean;
  mypy clean across 31 app modules. Real database tests used disposable clones;
  the final clone count was zero.
- Planted redundant-index case (8 added write indexes): FAIL on normal-profile
  `place_order` with n=16 paired blocks (seed 9876). Measured geometric mean
  slowdown 11.53%, one-sided lower/upper bounds 5.06%/18.40%, elapsed 409.06 s.
  A prior run failed after 8 blocks in 206.25 s, showing run-to-run variation.
- Planted candidate-side order-insert trigger (seed 2468): the guard reported
  `errors_increased` for `place_order` in the first normal-profile block,
  elapsed 27.18 s. The error-rate verdict had n=0 statistical pairs and null
  confidence bounds because it fired before latency testing. The trigger was
  created only in the disposable candidate clone; the baseline and live
  database were untouched.
- Planted HOT-killer `CREATE INDEX ON store_stock(qty)` (seed 4321): BLOCK after
  40 paired blocks in **each** of the four profiles, elapsed 4162.31 s. The
  `stock_decrement` results were:

  | Profile | n | Mean change | Lower bound | Upper bound | Verdict |
  |---|---:|---:|---:|---:|---|
  | normal | 40 | 2.94% | -1.10% | 7.16% | BLOCK |
  | flash5 | 40 | 0.70% | -5.59% | 7.41% | BLOCK |
  | flash10 | 40 | 1.70% | -7.37% | 11.65% | BLOCK |
  | flash20 | 40 | 0.20% | -19.84% | 25.25% | BLOCK |

- Planted harmless `CREATE INDEX ON customers(city)` (seed 1234): BLOCK, not
  PASS, with block counts normal=40, flash5=15, flash10=10, flash20=40;
  elapsed 2736.54 s. This run preceded per-cell console reporting, so its
  detailed bounds are unavailable and are not inferred.
- The harmless case was repeated with per-cell reporting (same candidate,
  seed and settings): BLOCK again, 40 blocks in every profile, elapsed
  4165.40 s. The `stock_decrement` results were:

  | Profile | n | Mean change | Lower bound | Upper bound | Verdict |
  |---|---:|---:|---:|---:|---|
  | normal | 40 | 0.94% | -3.50% | 5.59% | BLOCK |
  | flash5 | 40 | -8.65% | -29.66% | 18.63% | BLOCK |
  | flash10 | 40 | -1.91% | -11.64% | 8.89% | BLOCK |
  | flash20 | 40 | -1.71% | -15.77% | 14.68% | BLOCK |

  This is not evidence that the index harmed stock updates; every reported
  upper bound exceeded 5%. The difference between runs shows
  unstable measurement power, especially at flash load.
- `estimate_guard_seconds` is a **minimum scheduled workload** estimate:
  576 s for four profiles under the current 12 s block/6-run settings. The
  measured HOT-killer run took 4162.31 s because multiple profiles exhausted
  the 40-block cap and workload drain/clone overhead added time. The estimate
  must not be presented as an expected or maximum runtime.
- Acceptance remains **open**: the specified harmless-index PASS and HOT-killer
  FAIL were not reproduced under the calibrated 41 TPS base rate and the
  required 5% bound. Both candidates were rejected safely; relabeling either
  measured BLOCK as PASS or FAIL would violate the evidence and the contract.
  No Step 13 work has started.

### Step 12 measurement investigation — 2026-10-27

- User asked to keep the original Step 12 PASS/FAIL expectations and investigate
  the measurement setup. No threshold or acceptance check was changed.
- Recalculated the saved Step 11 A/A samples with Step 12's stricter family of
  20 cells and 40 planned looks. Normal `update_status` had n=32, upper=5.23%
  (unproven); flash5 `stock_decrement` had n=15, upper=5.08% (unproven).
  Multiplicity accounts for some, but not all, of the Step 12 BLOCKs.
- A controlled flash5 diagnostic compared eight reused-pair blocks with eight
  fresh-pair blocks using identical seeds/schedules and the harmless city index.
  All 16 blocks were valid. `stock_decrement` log-ratio SD was 0.0633 reused
  versus 0.0627 fresh; clone reuse alone did not create large noise in these
  first eight blocks. Evidence: `experiments/results/step12_clone_lifecycle.json`.
- A separate 40-block reused-pair flash5 run kept all blocks valid and queue
  lag stable. At n=40, the unchanged Step 12 bounds from the raw block ratios
  were: `place_order` upper 5.83%, `update_status` 6.69%, `order_status`
  5.12% (all BLOCK); `stock_decrement` 4.96% and `picker_queue_read` 3.01%
  (PASS). At the observed means and SDs, the first three would need roughly
  53, 71 and 42 samples respectively to pass; those projections are not
  guarantees and do not alter the 40-block cap. Evidence:
  `experiments/results/step12_reused_40.json`.
- On disposable clones with the single `store_stock(qty)` index, baseline
  stock updates were 92.42% HOT over the measured interval versus 0% in the
  candidate clone. The short flash5 diagnostic nevertheless measured only
  +0.84% mean change for all stock-decrement attempts, with n=8 and log-ratio
  SD 0.0511. About 23.6% of those stock attempts and 50.4% of place-order
  attempts ended in expected errors on both sides, so their latency averages
  include attempts without the indexed write. Evidence:
  `experiments/results/step12_qty_hot_stats.json`.
- Restricting a separate diagnostic to preloaded in-stock hot products removed
  those errors and yielded 99.8% baseline HOT updates versus 0% with the qty
  index. Eight valid paired blocks still did not show a reliable >5% latency
  regression; one large timing outlier dominated the mean. A timeline-enabled
  run also showed two large second-side timing spikes, so its instrumented
  numbers are diagnostic only. Evidence: `experiments/results/step12_qty_stocked_only.json`
  and `experiments/results/step12_qty_success.json`.
- From the prior full harmless flash20 `stock_decrement` mean/SD, a fixed-effect
  calculation projects about 185 blocks to put its upper bound below 5%, far
  beyond the configured 40. The full HOT-index stock means were below 5% in
  every profile; more samples alone cannot establish a >5% failure if those
  effects remain the same. The physical HOT loss is real, but the specified
  latency FAIL is not supported by these workloads. This remains an open Step
  12 acceptance issue, not a deferred post-Step-21 item. No Step 13 work or
  commit was started. Every diagnostic clone was removed.
- A further eight-block flash5 diagnostic measured both the guard's end-to-end
  latency and PostgreSQL's `pg_stat_statements` execution time on the **same**
  disposable clone pair. All eight pairs were valid and had identical A/B SQL
  call counts and affected-row counts. PostgreSQL groups the identical stock
  update used by `place_order` and `stock_decrement` into one query-statistics
  entry, regardless of their different SQL comments. Across 22,827 calls per
  side, combined stock-update SQL time rose from 375.68 ms (baseline) to
  463.51 ms (qty-index candidate), a 23.38% increase: about 0.01646 to
  0.02030 ms per call. Baseline HOT updates were 16,425/20,377 versus
  0/20,377 on the candidate. Yet mean full `stock_decrement` latency was
  2.092 versus 2.073 ms and the eight-block paired geometric change was
  -0.91% (SD of log ratios 0.0614), far from a demonstrated >5% regression.
  The SQL update itself accounts for under 1% of the measured transaction
  time, so its approximately 0.00385 ms added cost is too small to establish
  the planted FAIL on this workload. This is a diagnostic explanation, not a
  guard verdict, and it does not alter the required 5% threshold. Evidence:
  `experiments/results/step12_qty_statement_cost.json`. The diagnostic took
  209.2 s; Ruff passed; all clones were removed. The Step 12 acceptance issue
  remains open.
- Reviewed Step 12 statistical look accounting. With `MIN_RUNS=6` and
  `MAX_BLOCKS=40`, the guard can first decide at block 6 and can therefore
  spend at most 35 sequential looks, not 40. Updated the guard to allocate
  alpha over those 35 possible looks while retaining the same family of 20
  protected profile/transaction cells and the same 5% non-inferiority rule.
  Recomputing saved Step 11 A/A samples with the corrected look count changed
  flash5 `stock_decrement` at n=15 from upper=5.080% to 4.967% (PASS); normal
  `update_status` at n=32 remained unproven (upper 5.233% to 5.186%). This
  modest correction cannot account for the earlier harmless-index BLOCKs or
  turn the measured below-5% HOT-index point estimates into a FAIL. `make test`
  (using the project `.venv`) passed 197 tests with 82 deselected; Ruff and
  mypy passed. The real planted-case acceptance is still open.
- Started a controlled harmless-index BLOCK-rate pilot after the user requested
  a measured answer. `backend/scripts/run_guard_budget_study.py` runs three
  independent seeds for each of `normal` and `flash5`, each with 80 sequential
  paired blocks on a fresh clone pair. It replays the same observed blocks at
  40 and 80 block budgets using the unchanged 5% delta, 5% alpha, family of
  20 cells, and budget-specific sequential-look correction. Runs are serial to
  avoid host CPU competition; raw checkpoints and verdict counts are under
  `experiments/results/step12_guard_budget_study/`. This is a six-run pilot,
  not an estimate of the finished advisor's overall BLOCK rate. The study is
  running; no result is claimed yet. A quiet thread monitor will report its
  completion or any failure needing attention.
- The user paused the pilot on 2026-10-27. The first normal-profile seed
  finished all 80 valid blocks; the second had saved 70 valid blocks when its
  worker was interrupted. No worker or disposable clone remains, and the
  monitor was removed. `status.json` records `paused_by_user`. No six-run
  BLOCK-rate conclusion is available yet; resume only when the user asks.
- The user resumed the pilot later on 2026-10-27. Preserved the interrupted
  70-block checkpoint as `normal_5201_interrupted.json`, verified no worker or
  sandbox clone was left running, and restarted the orchestrator. It skips the
  completed 80-block `normal_3101.json` run and repeats seed 5201 from fresh
  clones for a fair 80-block comparison. The quiet completion monitor was
  restored. No result is claimed until the six planned runs finish.
- The controlled pilot completed six valid 80-block runs on 2026-10-27. The
  harmless `customers(city)` index had these profile-level guard verdicts when
  the **same** paired observations were replayed with each budget's correct
  sequential-look allocation (5% delta, 5% alpha, family 20, min runs 6):

  | Profile | Independent seeds | 40 blocks | 80 blocks |
  |---|---:|---:|---:|
  | normal | 3 | 3 BLOCK, 0 PASS | 0 BLOCK, 3 PASS |
  | flash5 | 3 | 0 BLOCK, 3 PASS | 0 BLOCK, 3 PASS |

  All six runs had 80/80 valid paired blocks and no FAIL. In normal load, the
  last protected transaction reached PASS between 55 and 71 valid pairs,
  depending on seed; the remaining cells at block 40 were `update_status`,
  `order_status`, `place_order`, or `stock_decrement`. The flash5 runs passed
  within 14–24 pairs. This is a deliberately small pilot of one harmless
  candidate and two profiles. Its observed BLOCK fractions (normal 3/3 at
  40, 0/3 at 80; flash5 0/3 at either budget) are not an estimate of the
  product-wide BLOCK rate, and flash10/flash20 remain untested at 80 blocks.
  Evidence: `experiments/results/step12_guard_budget_study/summary.json`
  and six per-run JSON files. Each run used a fresh clone pair and took about
  34 minutes. Ruff, mypy, and 197 non-DB/non-LLM tests passed. No worker or
  `qc_sbx_*` clone remains. The original Step 12 planted-case acceptance is
  still open, particularly the required HOT-killer FAIL; no commit or Step 13
  work has started.
- On 2026-10-28, the user requested the same harmless-index pilot under 10×
  and 20× flash-sale load. Extended the serial study runner to include
  `flash10` and `flash20`, retained all six completed normal/flash5 runs, and
  started three fresh-clone seeds per new profile. Each run records 80 paired
  blocks, then evaluates the original 5% guard at 40 and 80 block budgets with
  family 20. The study is in progress; no flash10/flash20 result is claimed
  until its checkpoints complete and are verified. A quiet monitor is active.
- The first flash10 attempt found Docker Desktop stopped and exited before
  creating a measurement. Started Docker Desktop and the project's PostgreSQL
  service, verified `qc_golden` and `qc_live` still exist and no sandbox clone
  was left behind, then restarted the serial study. The first flash10 paired
  blocks are valid. No earlier study result was changed.
- During the third flash10 seed, Docker Desktop stopped again after 29 valid
  blocks and the worker exited. The PostgreSQL container showed a clean
  shutdown, with no evidence from its new startup log of a database crash.
  Restarted Docker/PostgreSQL, removed the two disposable clones left by the
  interrupted run, preserved its 29-block checkpoint as
  `flash10_7301_interrupted.json`, and restarted the serial study. The two
  completed flash10 seeds and all earlier profiles were skipped and retained.
  This interruption is not counted as a completed replicate.
- On 2026-10-29, the four-profile harmless-index budget pilot finished. All
  12 planned runs had 80/80 valid paired blocks, three independent seeds per
  profile. Replaying the same observations with the original 5% delta and
  alpha, family 20, and budget-specific sequential looks gave:

  | Profile | 40 blocks: PASS/BLOCK/FAIL | 80 blocks: PASS/BLOCK/FAIL |
  |---|---:|---:|
  | normal | 0/3/0 | 3/0/0 |
  | flash5 | 3/0/0 | 3/0/0 |
  | flash10 | 1/2/0 | 1/2/0 |
  | flash20 | 0/3/0 | 1/2/0 |

  Thus extra blocks resolved all three normal runs and one flash20 run, but
  not the two BLOCK runs at 10x or the other two at 20x. A BLOCK means the
  upper confidence bound did not establish less than 5% slowdown; it is not
  proof of harm. No planted harmless-index run failed. The pilot covers one
  candidate and three seeds per load, so these fractions are not a general
  BLOCK rate. Docker Desktop stopped during the second and third flash20
  seeds; the interrupted partial files were preserved and each seed was
  restarted on fresh clones. Completed runs were retained. Evidence:
  `experiments/results/step12_guard_budget_study/summary.json` and the 12
  complete per-run files. Step 12 remains open because the harmless index
  did not PASS in all flash profiles and the single HOT-killer index has not
  produced the required FAIL. After resuming on 2026-10-30, confirmed all
  12 runs contain 80 valid blocks, no guard worker or disposable clone remains,
  `git diff --check` is clean, `make lint` passes Ruff and mypy, and `make test`
  passes 197 tests (82 DB/LLM tests deselected). No Step 13 work or commit
  was made.
- Follow-up review on 2026-10-30 found that the four 80-block harmless-index
  BLOCK runs had wide upper bounds, especially at 20x. For flash10 seeds
  5201/7301, the `stock_decrement` mean changes were -4.33%/-1.52% but upper
  bounds 11.39%/13.25%; for flash20 seeds 3101/7301 they were +0.15%/+17.46%
  with upper bounds 11.66%/89.52%. The last flash20 seed was unstable:
  its first 40 blocks averaged +49.46% stock latency and the second 40
  averaged -7.70%, so a single run cannot identify a causal index effect.
  The single qty-index statement diagnostic remains the stronger causal
  explanation for the HOT case: SQL stock updates rose 23.38% but by only
  ~0.00385 ms per call, versus ~2.09 ms full `stock_decrement` latency. This
  does not support the planted >5% transaction FAIL. The original acceptance
  expectations were not altered, and no speculative verdict was recorded.
- The user authorized revising Step 12's planted-case expectations on
  2026-10-30 to match measured transaction evidence. Updated
  `docs/steps/12_guard_race_decide.md`: the eight-index write-cost case is the
  proven latency FAIL; the single qty index is a HOT-loss diagnostic whose
  observed full-transaction verdict is BLOCK; the harmless city index may
  PASS only when every profile/cell bound clears 5%, otherwise it BLOCKs.
  The 5% delta and alpha, family correction, and fail-closed guard code were
  not relaxed. Revised the two long database tests to check that the verdict
  agrees with per-cell evidence on short disposable-clone smoke runs, while
  the 12-run study remains the full-profile measured record. `make lint`
  passed and `make test` passed 197 tests with 82 deselected. The two real
  planted harm cases (eight indexes and candidate-side order errors) passed
  together on PostgreSQL. The short harmless/qty smoke checks also passed on
  disposable clones. Final `make lint`, `make test` (197 passed, 82 deselected),
  and `git diff --check` passed; no `qc_sbx_*` clone remains. The revised Step
  12 acceptance is satisfied by these checks and the recorded full-profile
  measurements. The requested Step 12 commit has not yet been made.

## 2026-10-30 step 13 — pipeline and evidence in progress

- Implemented the blocking `run_job` orchestrator for selected report shapes:
  baseline clone/plan/timings, safe mock or configured LLM proposal, plan and
  DDL filtering, tiered rewrite equivalence, racing, guarded decisions, ordered
  events, and JSON Schema 2020-12 evidence cards. The code stores every raw
  proposal's candidate status and every final card in SQLite. A single scratch
  clone validates accepted DDL transactionally before racing; rollback removes
  each trial DDL. Job-local clone tracking cleans only this job's clones.
- Added monotonic per-job/per-shape budgets and cooperative cancellation.
  The pipeline polls the store for cancellation, propagates a stop event to
  `run_block`, and checks it between paired report measurements. Unknown or
  incomplete evidence yields a `block` decision. No code applies changes to
  `qc_live` in this step. The evidence schema and `jsonschema` dependency were
  already present in requirements, so no new package download was needed.
- Real PostgreSQL acceptance checks passed: all eight mock dry-run shapes
  saved schema-valid cards; a reduced-profile three-shape full job completed;
  cancellation after the first proposal, provider-error isolation, and both
  immediate and 20-second budget exhaustion worked. The three-shape measured
  run is saved as `experiments/results/step13_mock_run.json` with three cards
  and 41 ordered events. It used `MIN_RUNS=3`, `MAX_RUNS=6`, `MAX_BLOCKS=12`,
  `BLOCK_SECONDS=2`, `MIN_SAMPLES_PER_BLOCK=1`, `GUARD_PROFILES=normal`, the mock
  provider, seed 1234, and a 1200-second job budget. These are smoke settings,
  not a four-profile safety claim. It took 74.31 seconds:

  | Shape | Observe | Propose | Filter | Equivalence | Race | Guard/decide | Decision |
  |---|---:|---:|---:|---:|---:|---:|---|
  | recent_orders | 0.509 s | 0.011 s | 0.690 s | 0.001 s | 2.953 s | 64.813 s | BLOCK: guard unproven |
  | delivered_yesterday | 0.526 s | 0.020 s | 0.013 s | 1.300 s | 0.704 s | <0.001 s | BLOCK: no proven report gain |
  | payout_report | 0.636 s | 0.016 s | 0.008 s | 1.269 s | 0.752 s | <0.001 s | tested rewrite selected |

  All three saved cards validated against `docs/evidence_card.schema.json`,
  event IDs increased strictly, and no `qc_sbx_*` clone remained. Earlier
  final checks passed Ruff, mypy, 198 non-DB/non-LLM tests, and all six
  PostgreSQL pipeline acceptance tests (dry run, full run, cancellation,
  immediate budget, 20-second budget, and provider-error isolation).
  Step 13 is not committed or pushed.

## 2026-10-30 step 14 — apply, monitor, revert

- Implemented explicit approval records bound to candidate SQL, selected-evidence
  checks, live safety rechecking, weak-rewrite confirmation, and idempotent apply.
  Indexes use deterministic managed names, CREATE INDEX CONCURRENTLY, validity
  checks, a 5-second lock timeout, and a Python cancellation deadline. Failed
  builds attempt cleanup and retain an audit result. Statistics and approved
  rewrites are supported; plain ANALYZE has no undo, and rewrites are stored for
  application adoption without executing their SELECT. Concrete rewrite SQL is
  retained where recovering parameter placeholders would be ambiguous.
- Added window differences over live pg_stat_statements counters, insufficient
  sample handling, reset detection/rebaselining, optional z gating, per-group
  consecutive regression counts, capped window history, and at most one revert
  per interval. A process-local single live traffic generator supports normal
  and flash profiles, stops cooperatively, and reports errors. Baseline fallback
  intentionally writes ordinary order traffic to qc_live; it performs no bulk
  clone preparation. Revert retries three times and verifies managed index removal.
- The natural qty-index/flash10 demo measured no regression in 12 windows and
  therefore did not automatically revert. It safely removed the index during
  final cleanup. Evidence: `experiments/results/step14_natural_demo.json`.
  pg_stat_statements combines identical stock SQL under whichever query comment
  was first recorded, so stock_decrement can have no separately attributable
  counters. Such groups remain insufficient; missing pre-change baselines are
  never silently learned from post-change traffic.
- Added an explicitly labelled controlled-delay demo: a trusted tagged probe
  sleeps 1 ms during the pre-change baseline and 10 ms afterwards, alongside
  flash10 traffic. This tests the real monitoring/revert lifecycle, not index
  causality. Recorded baseline mean was 2.273 ms; measured windows were:

  | Window | Calls | Statement mean | Change vs baseline | Consecutive | Action |
  |---|---:|---:|---:|---:|---|
  | 1 | 151 | 10.419 ms | +358.33% | 1 | watching |
  | 2 | 146 | 10.539 ms | +363.62% | 2 | automatic revert |

  The applied row ended `reverted`, the index was absent, and the live traffic
  thread stopped without error. Evidence: `experiments/results/step14_demo.json`.
  The threshold stayed at 5%; these are statement latency measurements, not
  full transaction latency or proof that the qty index is harmful.
- Validation: 201 non-DB/non-LLM tests passed; all six PostgreSQL monitor tests
  passed, including apply/idempotency/manual revert, safety and evidence rejection,
  invalid-build cleanup, rewrite with no index changes, weak confirmation, and the
  controlled auto-revert demo. Ruff and mypy passed. No commit or push was made;
  Step 13 changes remain uncommitted separately in the same working tree.
