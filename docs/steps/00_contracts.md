# Step 00 — Contracts (shared names, signatures, enums, JSON)

**Goal:** a single reference that every later step obeys. Nothing to build here except what step 03
turns into `app/models.py`, `app/errors.py`, `app/config.py`. If you change anything in this file, update every user in the same commit.

## 0.1 Conventions

| Topic | Rule |
|---|---|
| Language | Python 3.11, type hints everywhere in `app/`, `ruff` line length 100 |
| Units | Latency in **milliseconds** (float). Rates in **transactions per second**. Time spans in **seconds** |
| Relative change | Fraction: `0.05` means +5 % (slower). Negative means faster. Always `ratio − 1` |
| Log ratio | Natural log of `candidate / baseline`. Variable name `d` (per paired run) |
| IDs | `job_id`: `YYYYMMDD-HHMMSS-<4hex>`. `candidate_id`: `<job_id>:<shape_id>:<nn>` (nn zero-padded from 01) |
| Time | Store timestamps as ISO-8601 UTC strings in SQLite. Never naive datetimes |
| RNG | `numpy.random.Generator` created from an explicit integer seed; pass it down; no global RNG |
| Errors | Raise subclasses of `AdvisorError` (see 0.6). API maps them to JSON errors |
| Logging | `logging.getLogger(__name__)`; never log secrets (mask DSN passwords, API keys) |

## 0.2 Environment variables (`.env.example` must contain all of these)

```
# database server (one server, several databases)
PG_HOST=localhost
PG_PORT=5432
PG_USER=advisor
PG_PASSWORD=advisor
GOLDEN_DB=qc_golden
LIVE_DB=qc_live
MAINT_DB=postgres            # used only for CREATE/DROP DATABASE
SANDBOX_PREFIX=qc_sbx_

# metadata store
META_DB_PATH=./data/meta.sqlite

# statistics / policy
DELTA=0.05
ALPHA=0.05
MIN_RUNS=6
MAX_RUNS=60
RACE_BATCH=3
PREFER_RATIO=0.8
METRIC=time                  # time | buffers

# proposer
K_MAX=10
PATIENCE=3

# guard / workload
BASE_RATE_TPS=15
WORKERS=16
BLOCK_SECONDS=8
MAX_BLOCKS=40
MIN_SAMPLES_PER_BLOCK=5
GUARD_PROFILES=normal,flash5,flash10,flash20
STMT_TIMEOUT_MS=60000

# LLM
LLM_PROVIDER=mock            # mock | openrouter | ollama
OPENROUTER_API_KEY=
OPENROUTER_MODEL=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_TEMPERATURE=0.7
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=
LLM_TIMEOUT_S=120

# extra knobs referenced in later steps
MAX_CLONES=4
MAX_GUARD_CANDIDATES=3
GUARD_STAT=mean              # mean | p95
OPENROUTER_JSON_MODE=1
MONITOR_INTERVAL_S=60
MONITOR_MIN_CALLS=30
MONITOR_CONSECUTIVE=2
MIN_AGE_S=120
BASELINE_SECONDS=60
APPLY_TIMEOUT_S=1800
FREE_DISK_GB=                # optional hint for the clone disk check
BENCHBASE_JAR=               # optional path to the built BenchBase jar

# API
API_HOST=0.0.0.0
API_PORT=8000
CORS_ORIGINS=http://localhost:5173
```

## 0.3 Database names and roles

| Name | Purpose | Rules |
|---|---|---|
| `qc_golden` | Seeded template | **No connections** outside `seed.py` while seeding |
| `qc_live` | "Production" | Created `TEMPLATE qc_golden`; receives approved DDL and live traffic |
| `qc_sbx_<token>` | Sandbox clone | Created/dropped by `sandbox.py` only; token = 8 hex chars |
| role `advisor` | Superuser in dev | Needed for `CREATE DATABASE`, extensions |

## 0.4 Enumerations (exact strings)

```python
CandidateKind  = "index" | "stats" | "rewrite"
CandidateStatus = ("proposed" | "rejected_safety" | "rejected_invalid_ddl" | "skipped_duplicate"
                   | "skipped_noplan" | "rejected_equiv" | "dropped_race" | "failed_guard"
                   | "blocked_unproven" | "selected" | "approved" | "applied" | "reverted"
                   | "blocked_by_human")
State          = "CONTINUE" | "PASS" | "FAIL" | "BLOCK"      # sequential test outcome
Metric         = "time" | "buffers"
ProfileName    = "normal" | "flash5" | "flash10" | "flash20"
TxnType        = "place_order" | "stock_decrement" | "update_status" | "order_status" | "picker_queue_read"
Step           = "system" | "observe" | "propose" | "filter" | "equivalence" | "race" | "guard"
               | "decide" | "apply" | "monitor"
Level          = "info" | "warn" | "error" | "result"
JobStatus      = "queued" | "running" | "done" | "failed" | "cancelled"
AppliedState   = "healthy" | "watching" | "reverted" | "revert_failed"
DecisionAction = "apply_rewrite" | "apply_index" | "block"
EquivLabel     = "tested" | "rejected" | "unknown"
```

## 0.5 Dataclasses (`app/models.py`)

```python
@dataclass(frozen=True)
class Bounds:            # one-sided bounds on RELATIVE change (fractions)
    lower: float
    upper: float

@dataclass
class Summary:           # paired-run summary
    n: int
    mean_rel: float      # expm1(mean(d))
    lower: float
    upper: float
    sd_log: float        # std(d, ddof=1)
    state: str           # State

@dataclass
class RawCandidate:      # what an LLM provider returns (untrusted)
    kind: str
    sql: str
    rationale: str

@dataclass
class Candidate:
    id: str
    job_id: str
    shape_id: str
    kind: str                    # CandidateKind
    sql: str
    rationale: str
    sample_idx: int
    status: str = "proposed"     # CandidateStatus
    reason: str | None = None
    signature: str | None = None # plan fingerprint (index/rewrite) or normalised DDL (stats)
    plan_before: dict | None = None
    plan_after: dict | None = None
    equiv: dict | None = None    # EquivResult as dict
    speedup: dict | None = None  # Summary as dict + metric + n_runs
    guard: dict | None = None    # GuardResult as dict

@dataclass
class EquivResult:
    label: str                   # EquivLabel
    rewrite_class: int | None    # 1, 2, 3
    tier_reached: int            # 0..3
    reason: str | None
    counterexample: dict | None  # {"tz": "...", "db": {...}, "orig_rows": [...], "new_rows": [...]}
    cx_dbs: int                  # number of counterexample DBs run
    nonvacuous_fraction: float | None
    weak: bool

@dataclass
class Decision:
    action: str                  # DecisionAction
    candidate_id: str | None
    reason: str

@dataclass
class ProfileSpec:
    name: str                    # ProfileName
    rate_mult: float             # 1, 5, 10, 20
    zipf_s: float                # product skew exponent
    hot_set_size: int            # number of "hot SKUs"
    hot_share: float             # fraction of order lines hitting the hot set
    status_poll_mult: float      # relative rate of order_status reads
    dashboard_refresh_s: float   # background report refresh period
    pre_sale_load: bool          # bulk catalogue load before the block
    analyze_after_load: bool
```

## 0.6 Errors (`app/errors.py`)

```python
class AdvisorError(Exception): http_status = 500; code = "internal"
class NotFound(AdvisorError):        http_status = 404; code = "not_found"
class BadRequest(AdvisorError):      http_status = 400; code = "bad_request"
class Conflict(AdvisorError):        http_status = 409; code = "conflict"
class SafetyRejected(AdvisorError):  http_status = 422; code = "safety_rejected"
class SandboxError(AdvisorError):    http_status = 500; code = "sandbox_error"
class LLMError(AdvisorError):        http_status = 502; code = "llm_error"
class BudgetExceeded(AdvisorError):  http_status = 408; code = "budget_exceeded"
```
API error body: `{"error": {"code": "...", "message": "...", "detail": {...}}}`.

## 0.7 Module interface table (signatures other modules may rely on)

| Module | Public API |
|---|---|
| `config` | `get_settings() -> Settings` (frozen dataclass; cached) |
| `pg` | `connect(dbname, autocommit=True) -> psycopg.Connection`; `admin_connect()`; `render_sql(conn, sql, params: dict) -> str`; `mask_dsn(str)->str` |
| `store` | `Store(path)`; `create_job(params)->job_id`; `set_job_status`; `emit(job_id, step, level, message, data=None)`; `events_after(job_id, last_id)->list`; `save_candidate(c)`; `get_candidates(job_id)`; `save_evidence(job_id, shape_id, card)`; `get_evidence(job_id)`; `create_applied(...)`; `list_applied()`; `update_applied(...)` |
| `shapes` | `REPORT_SHAPES: dict[str, Shape]`, `ORDER_TXNS: dict[str, TxnDef]`, `load_as_of(conn) -> datetime`, `sample_params(shape, conn, rng, as_of) -> dict`, `render(shape, conn, params) -> str` |
| `observe` | `top_statements(conn, limit=20, order="total") -> list[dict]` |
| `llm.base` | `LLMProvider.suggest(shape, ctx: SchemaContext, sample_idx: int) -> SuggestResult(candidates: list[RawCandidate], usage: dict)`; `get_provider(settings)` |
| `safety` | `check(raw: RawCandidate, catalog: Catalog) -> tuple[bool, str]` |
| `propose` | `propose(shape, provider, ctx, signature_fn, k_max, patience, safety_fn) -> ProposeResult(kept: list[Candidate], samples: int, saturated: bool, usage: dict, rejected: list[dict])` |
| `plans` | `explain(conn, sql, analyze=False, buffers=False) -> dict`; `fingerprint(plan: dict) -> str`; `hypo_plan(conn, create_index_sql, query_sql) -> dict`; `signature_for(candidate, original_sql, conn) -> str` |
| `equivalence` | `check_rewrite(conn, original_sql, rewrite_sql, cfg: EquivConfig) -> EquivResult` |
| `sandbox` | `create_clone(name=None) -> str`; `drop_clone(name)`; `clone_pair(candidate, settings) -> ContextManager[ClonePair(a: str, b: str)]`; `apply_ddl(db, sql)`; `analyze(db)` |
| `measure` | `run_once(conn, sql, metric) -> float`; `paired_report(conn_a, sql_a, conn_b, sql_b, *, metric, delta=None, alpha, looks, family, min_n, max_n, batch, mode="noninferiority", budget_s=None, rng=None) -> PairedResult(d: list[float], summary: Summary, seconds: float)` |
| `stats` | `verdict(d, delta, alpha, looks, family, min_n, max_n) -> tuple[State, Bounds | None]`; `summarize(d, delta, alpha, looks, family, min_n, max_n) -> Summary`; `SequentialTest` class |
| `workload` | `build_schedule(profile, seconds, seed, base_rate, pools) -> list[Arrival]`; `run_block(dbname, schedule, workers, stop_event=None) -> BlockResult`; `prepare_clone(dbname, profile, seed)` |
| `guard` | `run_guard(candidate, profiles, settings, progress) -> GuardResult` |
| `race` | `race(candidates, shape, settings, progress) -> RaceResult` |
| `decide` | `decide(survivors, shape, settings, progress) -> Decision` |
| `pipeline` | `run_job(job_id, params, store)` (blocking; call from a thread) |
| `monitor` | `apply(store, candidate_id)`, `revert(store, applied_id, reason)`, `MonitorThread(store).start()` |
| `simulate` | `run_rq2(trials, seed, noise_levels, ...) -> dict` and `python -m app.simulate` |

## 0.8 SQL tagging convention

Every SQL statement the workload or measurement code sends to PostgreSQL starts with a tag comment so
`pg_stat_statements` rows can be grouped without parsing:

```
/* dpa:txn=place_order */ INSERT INTO orders ...
/* dpa:shape=recent_orders */ SELECT ...
```
The advisor's own bookkeeping queries use `/* dpa:sys */`. Tags are comments only; they never change plans.

## 0.9 Frozen clock

Report shapes that reference "now" use a **seeded timestamp** `as_of` stored in table `meta(key text
primary key, value text)` under key `as_of` (ISO UTC). Shapes render it as a literal, e.g.
`'2026-10-21T00:00:00+00:00'::timestamptz`. Order transactions (`place_order`, etc.) use the real `now()`.

## 0.10 Evidence card JSON (also saved as `docs/evidence_card.schema.json` in step 13)

```json
{
  "job_id": "...", "shape_id": "delivered_yesterday",
  "created_at": "2026-10-23T10:00:00Z",
  "settings": {"delta": 0.05, "alpha": 0.05, "metric": "time", "profiles": ["normal","flash5"],
               "llm": {"provider": "openrouter", "model": "…", "temperature": 0.7}, "seed": 1234},
  "baseline": {"sql": "…", "signature": "ab12cd", "plan": {}, "time_ms": {"mean": 412.0, "n": 12}},
  "propose": {"samples": 5, "saturated": true, "usage": {"prompt_tokens": 0, "completion_tokens": 0}},
  "candidates": [ {
      "id": "…", "kind": "rewrite", "sql": "…", "rationale": "…", "status": "selected", "reason": null,
      "signature": "…", "plan_before": {}, "plan_after": {},
      "equiv": {"label": "tested", "rewrite_class": 1, "tier_reached": 3, "cx_dbs": 50,
                "nonvacuous_fraction": 0.62, "weak": false, "reason": null, "counterexample": null},
      "speedup": {"n": 14, "mean_rel": -0.93, "lower": -0.95, "upper": -0.91, "state": "PASS", "metric": "time"},
      "guard": {"passed": true, "profiles": {"normal": {"place_order":
                 {"n": 18, "mean_rel": 0.012, "lower": -0.01, "upper": 0.031, "state": "PASS"}}}}
  } ],
  "decision": {"action": "apply_rewrite", "candidate_id": "…", "reason": "…"},
  "budget": {"seconds": 214.0, "paired_runs": 96, "llm_calls": 5, "clones_created": 3}
}
```

## 0.11 Threading and connections

* One psycopg connection per thread; never share a connection between threads.
* The job runner executes in a worker thread (`ThreadPoolExecutor(max_workers=2)`).
* The SQLite store is opened with `check_same_thread=False` and every method takes a module-level `threading.Lock`.
* Long-running DB statements set `SET statement_timeout = <STMT_TIMEOUT_MS>` per connection.

## 0.12 Acceptance for this step

* [ ] You can list every enum and signature above from memory of the file (re-read before each step).
* [ ] `.env.example` (step 01) contains every variable in 0.2.
