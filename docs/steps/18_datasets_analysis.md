# Step 18 — Datasets, trace analysis, rewrite-prevalence study, labelled rewrite set

**Goal (end state):** four artefacts exist and are reproducible by scripts:
(1) `experiments/results/trace_stats.json` (Redset + Snowset workload statistics),
(2) `experiments/results/rewrite_prevalence.json` (SkyServer, SQLShare, SEDE pattern counts),
(3) `backend/data/rewrite_pairs.json` (labelled equivalent / non-equivalent pairs, ≥ 60),
(4) optional calibration data (Instacart / REES46) summarised in `backend/data/calibration_shapes.json`.
Each artefact states its sample size, parse-failure rate and the exact input files.

**Prerequisites:** steps 02, 04, 10 (for the labelled set check). The raw datasets already downloaded live under
`backend/data/external/{redset,snowset,skyserver,sqlshare,sede}/` (git-ignored). Record file names + sizes in `PROGRESS.md`.

## 18.1 What each dataset is for (do not misuse)

| Dataset | Executable? | Use here |
|---|---|---|
| Redset (Amazon Redshift, metadata) | No SQL text | Arrival burstiness, repetition rate, read/write mix, drift → RQ4 context, profile shaping |
| Snowset (Snowflake, metadata; **decompress first**) | No SQL text | Burst shape, cache behaviour → profile shaping |
| SkyServer log | SQL text (SQL Server dialect, verify) | Rewrite-pattern prevalence |
| SQLShare | SQL text (user tables) | Rewrite-pattern prevalence; "non-DBA users" evidence |
| SEDE | SQL text + title/description (StackOverflow schema, T-SQL) | Few-shot examples, prevalence, optional second testbed |
| Instacart (optional) | Orders | Hour-of-day shape, product skew |
| REES46 (optional) | Events incl. Black Friday | Spike shape, hot-product skew |

Never claim these are the evaluation database. The evaluation databases are the seeded quick-commerce DB and CH-benCHmark.

## 18.2 `scripts/analyse_traces.py`

Inspect first: for each input print the file list, schema/columns, row count, and a 5-row head. **Do not assume column names**: map them in a small
`COLUMN_MAP` dict at the top of the script after looking (e.g. arrival timestamp, query type, fingerprint/hash, runtime, tables read/written) and fail with a clear message when a mapped column is missing.

Metrics (computed per cluster/warehouse **and** pooled; use pandas/pyarrow with chunked reading for 18 GB / 11 GB — process one file at a time, never load everything):
1. **Repetition rate:** share of queries whose fingerprint was seen earlier in the same trace/window (windows: 1 h, 1 day, whole trace).
2. **Top-k coverage:** share of queries covered by the top 1/10/100 fingerprints per day.
3. **Arrival burstiness:** queries per minute; peak-to-mean ratio (p99/mean), index of dispersion (var/mean) per hour; autocorrelation at lag 1 min.
4. **Read/write mix:** fraction of SELECT vs INSERT/UPDATE/DELETE/COPY/DDL.
5. **Drift:** day-over-day Jaccard overlap of fingerprint sets and the share of day N+1 queries whose fingerprint appeared on day N (this is the RQ4 "coverage of tomorrow").
6. **Runtime distribution:** p50/p95/p99 and the share of total runtime from the top 1 % of fingerprints (to justify "tune the few recurring slow shapes").
7. For each metric also output the sample size and the number of clusters/days used.
Output JSON: `{"redset": {...}, "snowset": {...}, "meta": {"inputs": [...], "created_at": "…"}}`.
Resource rule: print memory use; use `pyarrow.parquet.ParquetFile.iter_batches` for parquet.
Snowset: if still compressed, decompress to a temp dir first (`gzip`/`zstd` as appropriate), delete the temp files at the end.

## 18.3 `scripts/rewrite_prevalence.py` (sqlglot)

For each SQL text dataset: stream queries; parse with `sqlglot.parse_one(sql, read="tsql")` (fall back to `read=None`); record parse failure.
Detect patterns on the AST (each query counted at most once per pattern):

| Pattern | Detector |
|---|---|
| P1 function on column in `WHERE`/`JOIN ON` comparison | comparison node where one side contains `exp.Func`/`exp.Cast` wrapping an `exp.Column` and the other side has no column; subset: date functions (`DATE`, `CAST(... AS DATE)`, `CONVERT`, `DATEPART`, `YEAR`, `MONTH`, `date_trunc`) |
| P2 correlated subquery | `exp.Subquery`/`Exists`/`In` whose inner select references an alias defined in an outer scope (use `sqlglot.optimizer.scope.traverse_scope`) |
| P3a redundant `DISTINCT` | `DISTINCT` combined with `GROUP BY` over the same columns, or `DISTINCT` on a select list that contains all `GROUP BY` keys |
| P3b ORDER BY in subquery without TOP/LIMIT | `ORDER BY` in a derived table/CTE without `LIMIT`/`TOP`/`OFFSET` |
| P3c redundant join (heuristic) | `LEFT JOIN` whose right side columns never appear elsewhere; report as "candidate" only |
| Any of P1–P3 | union |
Output per dataset: `n_queries`, `n_parsed`, parse_fail_rate, per-pattern counts and rates, example query ids (≤ 5 per pattern, **no raw query text** if the licence forbids redistribution — store ids only).
Interpretation text for the paper: "X % of real ad-hoc queries contain at least one of the three rewrite opportunities".
Limitations to state: heuristic detectors, T-SQL specifics, parser failures.

## 18.4 `scripts/build_rewrite_testset.py` → `data/rewrite_pairs.json`

Format:
```json
[{"id":"T1","shape":"delivered_yesterday","class":1,"expected":"rejected","kind":"trap",
  "note":"AT TIME ZONE 'UTC' bounds wrong in Asia/Kolkata",
  "original":"SELECT …","rewrite":"SELECT …","params":{"as_of":"…"}}]
```
Composition (≥ 60 pairs, balanced):
* Class 1: 10 equivalent (E), 10 traps (T) — variants of `delivered_yesterday`, `hourly_sales`, `recent_orders` date predicates; vary bounds style (`>= … <`, `BETWEEN`, `AT TIME ZONE`, `::date`, `date_trunc`).
* Class 2: 10 E, 10 T — `open_order_customers` and similar `EXISTS`/`IN`/scalar-subquery forms; join with/without DISTINCT; `NOT EXISTS` mis-rewrites; subquery filter dropped.
* Class 3: 10 E, 10 T — `payout_report`-like; DISTINCT on PK vs non-key; subquery ORDER BY removal vs top-level ORDER BY with LIMIT; join removal vs filtering joins; filter-dropping.
* Every item has a human-written `note` explaining the expected label. The trap catalogue T1–T10 from step 10 **must** be present with those ids.
* Validate each original runs on the clone (EXPLAIN) and each rewrite parses; a script flag `--verify` runs equivalence and prints a confusion matrix against `expected`.
* Optional extra source: sample real **non-equivalent** pairs from VeriEQL's LeetCode set after translating schemas — out of scope unless time permits; note in `PROGRESS.md`.

## 18.5 Optional calibration (`load_instacart.py`, REES46)

Only if the files are downloaded. Compute (a) hour-of-day order share (24 values), (b) product popularity rank-frequency fit (Zipf exponent via log-log regression), (c) for REES46, per-minute event counts around the Black Friday peak and peak-to-baseline ratio.
Write `data/calibration_shapes.json`; the workload profiles reference it **only** to justify `zipf_s` and multipliers in the paper, not to drive runtime behaviour.

## 18.6 Data governance
* Respect licences (Redset CC BY-NC 4.0, others per their pages); keep raw data out of git; keep only aggregates in git.
* Do not publish raw SQL text from datasets whose licence forbids redistribution.
* Record dataset versions/hashes (`sha256` of each input file) in the output JSON `meta`.

## 18.7 Tests (`tests/test_analysis.py`)
* Detector unit tests on hand-written SQL strings for each pattern (positive and negative).
* `test_rewrite_pairs_schema`: valid JSON, unique ids, ≥ 60 pairs, each class has both labels, T1–T10 present.
* `test_trace_metrics_tiny`: metrics on a synthetic 1000-row DataFrame match hand-computed values.

## 18.8 Done checklist
- [ ] Four artefacts generated; sample sizes and failure rates recorded
- [ ] Confusion matrix of equivalence vs labels recorded (feeds step 19 RQ2-equivalence)
- [ ] Commit `step 18: datasets and analysis`
