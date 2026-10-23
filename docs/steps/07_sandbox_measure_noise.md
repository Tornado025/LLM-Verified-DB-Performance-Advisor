# Step 07 — Sandbox clones, paired measurement, noise injector

**Goal (end state):** `sandbox.py`, `measure.py`, `noise.py` work against the real database. A clone pair is created in seconds,
a paired A/A measurement of a report shape runs, an **A/A noise report** is written, and the noise injector measurably
increases variance. All clones are cleaned up even on exceptions.

**Prerequisites:** steps 02–05.

## 7.1 `app/sandbox.py`

```python
def create_clone(name: str | None = None, template: str | None = None) -> str
def drop_clone(name: str) -> None
def list_clones() -> list[str]            # names starting with SANDBOX_PREFIX
def drop_all_clones() -> int
def apply_ddl(dbname: str, sql: str) -> None      # runs a single DDL statement with autocommit
def analyze(dbname: str, table: str | None = None) -> None
@contextmanager
def clone_pair(candidate: Candidate, settings) -> Iterator[ClonePair]
@contextmanager
def single_clone(settings) -> Iterator[str]
def db_size_bytes(dbname: str) -> int
```
Rules:
1. Name: `f"{SANDBOX_PREFIX}{uuid4().hex[:8]}"`. Refuse any name that does not start with the prefix (safety against dropping real DBs).
2. `create_clone`: `CREATE DATABASE "<name>" TEMPLATE "<GOLDEN_DB>"` using `admin_connect()`. Wrap errors in `SandboxError`.
   Record elapsed seconds (return via module-level metrics dict or log) — needed for RQ1 clone-cost reporting.
3. Immediately after creation run `ANALYZE` (whole database) via a short connection. (Template copies keep statistics, but this is a defensive rule that also refreshes after any workload.)
4. `drop_clone`: `terminate_connections` then `DROP DATABASE IF EXISTS "<name>"`. Never raises if absent.
5. `clone_pair` creates A (baseline) and B, applies the candidate DDL **to B only** (`apply_ddl`; for `kind == "index"` use plain `CREATE INDEX`, non-concurrent, since the clone is private; for `stats` run `ANALYZE <t>` / `CREATE STATISTICS` then `ANALYZE`), then `ANALYZE` both.
   * If DDL fails (`psycopg.errors.*`), mark candidate `rejected_invalid_ddl` with the PostgreSQL message and raise `SandboxError` subclass `InvalidDDL`.
   * `try/finally` drops both clones.
6. Concurrency guard: a module-level semaphore `MAX_CLONES = 4` prevents disk blow-up; acquire before creating, release in `finally`.
7. Disk check: before cloning, `shutil.disk_usage` of the data dir is not visible from outside Docker; instead query
   `pg_database_size(GOLDEN_DB)` and refuse if `size * 2 > free_space_hint` when `FREE_DISK_GB` env hint is set. (Optional env `FREE_DISK_GB`.)
8. `atexit` hook and a CLI `python -m app.sandbox --cleanup` call `drop_all_clones()` (also used by tests' session teardown).

## 7.2 `app/measure.py`

```python
def run_once(conn, sql: str, metric: str = "time") -> float
def warmup(conn, sql: str, n: int = 2) -> None
def paired_report(conn_a, sql_a, conn_b, sql_b, *, metric, delta=None, alpha, looks, family, min_n, max_n,
                  batch, mode="noninferiority"|"improvement", budget_s=None, rng=None) -> PairedResult
```
`run_once`:
* Executes `EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON) <sql>` (the SQL is already tagged with the shape comment).
* `metric="time"` → `plan["Execution Time"]` (ms). `metric="buffers"` → `Shared Hit Blocks + Shared Read Blocks` of the top plan node
  (+ `Local*` if non-zero). Return `max(value, 1e-3)` to avoid zero in log.
* Wrap in `SET LOCAL statement_timeout` to `STMT_TIMEOUT_MS`; on timeout return the timeout value and flag via exception `QueryTimeout` handled by caller (a timed-out candidate is `FAIL` for speed).
* Use autocommit connections; no side effects: reject non-`SELECT` SQL with `BadRequest`.

`paired_report` loop:
1. `warmup` both sides (2 runs each, discarded). Use `pg_prewarm` is **not** used; warm-up is just repeated runs.
2. Counter-balanced order: run index `i` uses order A,B when `i % 2 == 0`, else B,A.
3. After each batch of `batch` pairs (default 3) append `d_i = ln(b_i / a_i)` to a `SequentialTest` and stop when state ∈ {PASS, FAIL, BLOCK}, or when `budget_s` is exhausted (state stays `CONTINUE` → treated as `BLOCK` by callers).
4. Return `PairedResult` with raw `a_vals`, `b_vals`, `d`, `summary`, elapsed seconds, metric.
5. When `conn_a is conn_b` (rewrite vs original on the same DB) it still works; ordering alternates.

Screening rule (RQ3): provide `screen_with_buffers(...)` that first runs buffers (nearly deterministic) and only runs time for borderline results; log which path was taken.

## 7.3 `app/noise.py`

```python
class NoiseInjector:
    def __init__(self, level: float, cpu_workers: int | None = None, disk: bool = True, seed: int = 0)
    def __enter__(self): ...
    def __exit__(self, *exc): ...
```
* `level` ∈ {0, 0.05, 0.10, …} = target **duty cycle** per worker (fraction of each 100 ms period spent busy-looping).
* CPU workers: `multiprocessing.Process` ×`cpu_workers` (default = half of `os.cpu_count()`), each loops: busy for `level × 100 ms`, sleep the rest, with ±20 % jitter from a seeded RNG.
* Disk (optional): one process writing and `fsync`ing a 64 MB temp file in a loop with the same duty cycle.
* `level == 0` starts nothing. `__exit__` terminates processes with `terminate()` then `join(2)`; always runs in `finally`.
* Log the actual measured CPU load (psutil optional; if unavailable skip).
* **Important:** noise processes run on the **host** that runs the database. With Docker Desktop, noise on the host affects the VM indirectly; document which setup you used.

## 7.4 A/A noise report script (`backend/scripts/noise_report.py`)

1. For each of 3 shapes (one fast, one medium, one slow), create **one** clone `X` and use `conn_a = conn_b` on `X` with the **same SQL**.
2. Run 50 paired A/A measurements for metrics `time` and `buffers`.
3. Compute `sd(d)`, the mean `d`, the min detectable effect at 80 % power (`≈ 2.8 × sd/√n`), and the fraction of runs outside ±5 %.
4. Repeat with `NoiseInjector(0.05)` and `NoiseInjector(0.10)`.
5. Write `experiments/results/noise_report.json` and a Markdown table into `docs/PROGRESS.md`.

## 7.5 Tests (`tests/test_sandbox.py`, `test_measure.py`; `@pytest.mark.db`)

* `test_clone_roundtrip`: create, assert exists, write a row in clone, assert golden unchanged, drop.
* `test_refuse_bad_name`: `drop_clone("qc_live")` raises.
* `test_clone_pair_ddl_only_on_b`: after `CREATE INDEX` in B, `pg_indexes` differs between A and B.
* `test_invalid_ddl_marks_candidate`: `CREATE INDEX ON orders (DATE(delivered_at))` ⇒ `InvalidDDL`, status `rejected_invalid_ddl`.
* `test_cleanup_on_exception`: raise inside context; no `qc_sbx_*` left.
* `test_run_once_time_and_buffers`: both > 0 for `recent_orders`.
* `test_paired_report_aa_passes`: A/A on a stable query returns `PASS` for non-inferiority within `max_n`.
* `test_paired_report_detects_slowdown`: B with a deliberately heavier SQL (`... CROSS JOIN generate_series(1,2)`) returns `FAIL`.
* `test_noise_injector_increases_variance` (slow): sd(d) at 10 % > sd(d) at 0 % (soft assertion with 3 repetitions; mark xfail-able if the host is noisy).

## 7.6 Done checklist
- [ ] Clone cost (seconds, bytes) recorded for your scale in `docs/PROGRESS.md`
- [ ] `noise_report.json` created; chosen default metric justified (time vs buffers)
- [ ] No leaked `qc_sbx_*` databases after the test run (`python -m app.sandbox --cleanup` prints 0)
- [ ] Commit `step 07: sandbox, measure, noise`

## 7.7 Pitfalls
* `CREATE DATABASE ... TEMPLATE` blocks while anything is connected to golden. Keep the API/workers away from golden.
* `EXPLAIN ANALYZE` of DML executes it; only `SELECT` is allowed in `run_once`.
* Warm caches differ between A and B if one clone was just created (cold). That is why warm-up and counter-balanced ordering exist.
