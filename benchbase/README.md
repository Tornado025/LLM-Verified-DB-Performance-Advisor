# CH-benCHmark setup

This is a separate CH-benCHmark / TPC-C-derived workload, not a certified TPC-C
result. It combines five order transactions with 22 analytical queries on shared
tables. The custom quick-commerce runner supplies the hot-stock contention case.

## Pinned build

The source is [CMU BenchBase at 33c0047](https://github.com/cmu-db/benchbase/tree/33c00473807ebd49304d114a6d769d2d2b2bbb34).
See `runtime_manifest.json` for the exact revision, JDK package/checksum and built
distribution checksum. The upstream README mentions Java 21, but this revision's
`pom.xml` targets Java 23. Temurin 23.0.2+7 built it successfully; Java 27 failed
in the pinned formatter plugin. The included upstream license covers the copied
sample configuration.

Keep source, JDK, Maven cache and build outputs in ignored `.runtime/`. Build from
the pinned source with:

```sh
JAVA_HOME=<jdk23-home> ./mvnw clean package -P postgres -DskipTests
```

Unpack the PostgreSQL distribution into `.runtime/dist/` and set `BENCHBASE_JAR`
in the ignored root `.env`. The adapter currently expects the recorded macOS ARM
Temurin path under `.runtime/jdk23/`. Packaging skips upstream tests; project
adapter tests and actual benchmark executions are recorded separately.

## Database and runs

From the project root, with the virtual environment active:

```sh
PYTHONPATH=backend python -c "from app.benchbase import load_ch; load_ch('qc_ch_golden', 10)"
python backend/scripts/benchbase_smoke.py
```

Loading refuses an existing database. It creates ten warehouses, runs ANALYZE and
closes the CH template to ordinary connections. The smoke script creates `qc_ch`
if absent, then executes each scenario on a fresh `qc_sbx_*` clone of
`qc_ch_golden`; clones are dropped in `finally`. Quick-commerce databases are
separate. Large clones use PostgreSQL 16's `STRATEGY FILE_COPY`; its required
checkpoints finish before measurement, avoiding the much larger WAL volume of
the default block-logging strategy. Each run needs an empty output directory. Passwords are supplied through
an environment lookup, not persisted in generated XML.

Upstream's PostgreSQL example is `config/postgres/sample_chbenchmark_config.xml`.
This revision requires `-b tpcc,chbenchmark`: CH supplies analytical procedures,
while TPCC supplies the five order procedures. Poisson arrival uses an attribute
`<work arrival="poisson">`, verified against `DBWorkload.java`. Configurations use
16 TPCC terminals and two analytical terminals. Runtime TPCC rate is the locally
calibrated `BASE_RATE_TPS` times 1/5/10/20; analytical rate is 0.1 times the same
multiplier. The extra flash20 scenario restricts the active range to two of the
ten loaded warehouses; it does not reload or remove data.

Each scenario runs for 60 seconds, followed by BenchBase's drain. Raw CSV latency
is microseconds and is converted to milliseconds. Unexpected failures, expected
aborts and retries come from separate histograms; absent histogram information is
unknown, never silently counted as zero. TPC-C-derived NewOrder deliberately
includes expected aborts. The smoke report records observed samples and errors;
`count` counts timed latency records, including unsuccessful requests, rather
than committed transactions. `successful_attempts_histogram` separately records
the upstream success counter. Worker.java records raw latency only inside the
measurement window, while outcome counters cover the worker's execution;
therefore their counts need not match. `rejected` in upstream JSON maps to
`Results.getRetry()`, not unexpected failures. These counters stay separate.
The report does not treat target arrival rate as achieved throughput or claim a
latency guard passed. Raw CSVs/logs remain ignored in `results/`; the
measured summary is `experiments/results/ch_smoke.json`. The report marks run
completion, protected sample coverage and unexpected SQL errors separately. A
completed scenario with errors remains visible as an observation and must not be
treated as error-free performance validation.
The run's `execution_summary` contains elapsed time, measured requests,
throughput and goodput separately from the per-transaction latency records.

## Quick-commerce profile provenance

The 1/5/10/20 multipliers, skew, polling and dashboard frequencies are declared
experiment parameters in `backend/data/profiles.json`. Rate calibration uses
actual measurements on this Mac and dataset, not a company's reported traffic.
The precise company figures in the supplied step brief (Zepto, Zomato, Instamart
and Alibaba) were not verified as general calibration constants and are not used
as measured inputs. [Alibaba Cloud's flash-sale overview](https://www.alibabacloud.com/help/en/redis/use-cases/use-apsaradb-for-redis-to-build-a-business-system-that-can-handle-flash-sales)
describes sharp arrival spikes and post-sale status polling; this motivates the
scenario structure, not an exact multiplier or a Redis implementation. Sweep
these parameters in the later experiments rather than treating one scenario as
universal traffic.
