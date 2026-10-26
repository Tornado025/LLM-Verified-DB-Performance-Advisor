# Step 03 — Backend core: config, connections, store, models, errors, logging

**Goal (end state):** `app/config.py`, `app/pg.py`, `app/store.py`, `app/models.py`, `app/errors.py` and
logging setup exist with unit tests; a `Store` can create a job, emit events, and read them back in
order, from several threads, without corruption.

**Prerequisites:** steps 01–02. Contracts: `00_contracts.md` (sections 0.2, 0.4–0.7, 0.11).

## 3.1 `app/config.py`

* Load `.env` with `python-dotenv` (`load_dotenv(find_dotenv())`) once.
* `Settings` is a `@dataclass(frozen=True)` holding **every** variable in contracts 0.2, typed
  (`float`, `int`, `list[str]` for `GUARD_PROFILES`, `bool` where relevant).
* `get_settings()` is `functools.lru_cache`d. Provide `get_settings.cache_clear()` use in tests.
* Validation (raise `BadRequest` or `ValueError` at startup):
  * `0 < DELTA < 1`, `0 < ALPHA < 0.5`, `MIN_RUNS >= 3`, `MAX_RUNS >= MIN_RUNS`, `K_MAX >= 1`, `PATIENCE >= 1`.
  * `LLM_PROVIDER` in `{mock, openrouter, ollama}`. For `openrouter` require non-empty `OPENROUTER_API_KEY`
    and `OPENROUTER_MODEL`. For `ollama` require `OLLAMA_MODEL`.
  * `METRIC` in `{time, buffers}`; each profile in `GUARD_PROFILES` must be a known `ProfileName`.
* Derived properties: `looks` = `ceil((MAX_RUNS - MIN_RUNS) / RACE_BATCH) + 1`.
* `Settings.public()` returns a dict with secrets masked (`OPENROUTER_API_KEY` → `"***"` or `""`).

## 3.2 `app/pg.py`

```python
def dsn(dbname: str, settings=None) -> str                 # postgresql://user:pw@host:port/dbname
def connect(dbname: str, autocommit: bool = True, settings=None) -> psycopg.Connection
def admin_connect(settings=None) -> psycopg.Connection      # to MAINT_DB, autocommit=True
def render_sql(conn, sql: str, params: dict) -> str         # psycopg.ClientCursor(conn).mogrify
def mask_dsn(s: str) -> str                                 # replaces password with ***
def set_session_defaults(conn, settings=None) -> None       # statement_timeout, application_name
```
Rules:
* `connect` sets `application_name='dpa'` and `statement_timeout = STMT_TIMEOUT_MS` via `SET` on the new connection.
* `render_sql` uses `%(name)s` placeholders only. It returns a **text** SQL string with literals inlined and
  must be used **only** for values from our own samplers (never for LLM text).
* Retry `connect` up to 5 times with 1 s sleep on `OperationalError` (database starting).
* Provide `terminate_connections(admin_conn, dbname)`:
  `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()`.

## 3.3 `app/models.py`
Implement exactly the dataclasses from contracts 0.5, plus:

```python
@dataclass
class Arrival: t: float; txn: str; params: dict      # scheduled offset seconds, TxnType, parameters
@dataclass
class BlockResult: seconds: float; latencies: dict[str, list[float]]; errors: dict[str, int]; dropped: int; metadata: dict = field(default_factory=dict)
@dataclass
class PairedResult: d: list[float]; summary: Summary; seconds: float; metric: str; a_vals: list[float]; b_vals: list[float]
@dataclass
class ClonePair: a: str; b: str
@dataclass
class GuardResult: passed: bool; profiles: dict; seconds: float; blocks: dict; failed_cell: tuple | None
@dataclass
class RaceResult: states: dict[str, Summary]; dropped: dict[str, str]; seconds: float; runs: int
@dataclass
class ProposeResult: kept: list; samples: int; saturated: bool; usage: dict; rejected: list
    # Step 09 extension: distinct_plan_curve: list[int] = field(default_factory=list)
```
Add `to_dict()` helpers (`dataclasses.asdict`) and `from_dict` for `Candidate`, `Summary`.

## 3.4 `app/errors.py`
Implement contracts 0.6 verbatim. Add `detail: dict` attribute and `__init__(self, message, detail=None)`.

## 3.5 `app/store.py` (SQLite)

Open with `sqlite3.connect(path, check_same_thread=False, isolation_level=None)`; `PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;`. A module-level `threading.RLock` guards every method.

Tables:
```sql
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
  params TEXT NOT NULL, result TEXT);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
  ts TEXT NOT NULL, step TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL, data TEXT);
CREATE INDEX IF NOT EXISTS events_job_id ON events(job_id, id);
CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), shape_id TEXT NOT NULL,
  kind TEXT NOT NULL, sql TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence (
  job_id TEXT NOT NULL REFERENCES jobs(id), shape_id TEXT NOT NULL, card TEXT NOT NULL,
  PRIMARY KEY (job_id, shape_id));
CREATE TABLE IF NOT EXISTS applied (
  id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, job_id TEXT NOT NULL, kind TEXT NOT NULL,
  ddl TEXT, undo_ddl TEXT, rewrite_sql TEXT, baseline TEXT, state TEXT NOT NULL,
  applied_at TEXT NOT NULL, reverted_at TEXT, revert_reason TEXT, windows TEXT);
CREATE TABLE IF NOT EXISTS experiments (
  name TEXT PRIMARY KEY, updated_at TEXT NOT NULL, payload TEXT NOT NULL);
```
Methods (all JSON-encode `data`/`params`/`card` columns):
`create_job`, `get_job`, `list_jobs(limit)`, `set_job_status(job_id, status, result=None)`,
`emit(job_id, step, level, message, data=None) -> int`, `events_after(job_id, last_id=0, limit=500)`,
`save_candidate(c: Candidate)` (upsert), `get_candidate(id)`, `get_candidates(job_id)`,
`save_evidence`, `get_evidence(job_id)`, `create_applied`, `get_applied`, `list_applied`, `update_applied`,
`put_experiment(name, payload)`, `get_experiment(name)`.
* `emit` also logs through `logging` at the matching level.
* `emit` truncates `message` to 2000 chars and `data` JSON to 200 KB (replace overflow with `{"truncated": true}`).

## 3.6 Logging
`app/logging_conf.py`: `setup_logging(level="INFO")`, format
`%(asctime)s %(levelname)s %(name)s %(message)s`. A `SecretFilter` replaces `OPENROUTER_API_KEY` and DSN passwords with `***`.

## 3.7 Tests (`tests/test_core.py`, no DB needed except where marked)

* `test_settings_defaults`, `test_settings_validation_errors`, `test_settings_public_masks_key`.
* `test_mask_dsn`.
* `test_store_job_roundtrip`, `test_store_events_ordered_and_incremental` (emit 50 events, `events_after(last_id)` returns only newer).
* `test_store_threadsafe`: 8 threads × 100 emits ⇒ 800 events, ids strictly increasing, none lost.
* `test_store_candidate_upsert`, `test_store_applied_lifecycle`.
* `@pytest.mark.db test_connect_live`, `test_render_sql_quotes` (a string with `'` is escaped).

## 3.8 Done checklist
- [x] `make test` green; `@pytest.mark.db` tests green with `make up`
- [x] `mypy app` passes for these modules
- [ ] Commit `step 03: backend core`

Implementation note: committing waits for the user's chosen message.
