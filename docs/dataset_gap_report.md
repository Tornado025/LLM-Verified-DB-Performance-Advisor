# Dataset gap analysis

## Verdict

The downloaded datasets are **not sufficient to populate the quick-commerce evaluation database with real commerce records**. Redset/Snowset contain execution metadata, SEDE contains SQL and schema metadata without StackExchange rows, and the actual SkyServer Zenodo bundle is numeric/binary benchmark material, not a SQL log. SQLShare does include real underlying table rows, but they are heterogeneous user/scientific tables rather than a verified connected orders/items/products/stock/customer/store/rider system. Metadata IDs are not customer/product IDs. Use these sources for workload calibration and SQL structure evidence; retain explicitly synthetic operational data or obtain a separate commerce dataset. This is an offline finding about the inspected local copies, not every version of each source.

## Run scope and reproducibility

- Generated: 2026-10-24T03:13:58.587654+00:00; script SHA-256: `4e8929880f86b64013ee3705dbf489339cf92a92985ca8308da7b8ee0ddb33fc`.
- Command: `backend/scripts/analyse_inventory.py --dataset all --sample-rows 0`.
- Row cap: 0 (0 means all canonical trace rows / all SQL queries). Physical-prefix samples are diagnostic, not random representative estimates.
- Peak RSS: 1540.94 MiB; batch-boundary memory cap: 1536 MiB.
- No network, LLM, PostgreSQL connection or source-data changes. Snowset is streamed into owned temporary files, each removed after inspection; the run's temporary directory is cleaned on exit.
- Inventory contains every input file/member name, bytes and freshly measured SHA-256, schema/type metadata and redacted heads. Long exact per-minute/hourly series use the documented base64/zlib little-endian array encoding.

## Per-dataset evidence

| Dataset | Contains | Missing | Derivable | Not usable for |
|---|---|---|---|---|
| Redset | Parquet query arrivals, instance/database/table IDs, durations, query types, feature hashes | SQL text, real table/column names, table rows, commerce keys, rewrite labels | Generic DB burst series, repeat/coverage/drift, read/write statement shares | Populating commerce entities; product/customer popularity; direct replay |
| Snowset | Main query execution timings and I/O counters; auxiliary active-query-seconds | SQL, template fingerprints, query-type labels, table schema/rows, commerce keys | Generic warehouse arrivals, duration distribution, concurrency after queryId join | Template repetition from queryId; inferring DML from physical writes; commerce load |
| SkyServer | Actual `skyserver_zenodo/` numeric/binary benchmark files and local record metadata | Intended SQL query log; documented binary record schema; commerce entities | Limited numeric fixture inspection after schema documentation | SQL-pattern prevalence, report-template extraction, query arrival calibration |
| SQLShare | SQL blocks, view definitions, and real delimited user table data | Verified commerce relationships, trace arrival/runtimes, operational stock/riders, equivalence labels | SQL AST pattern prevalence; manual structure translation; optional non-commerce testbed | Blindly importing science rows as orders or using data timestamps as query arrivals |
| SEDE | SQL + title/description, query IDs, query-artifact dates, schema JSON; original/fixed split variants | Actual StackExchange table rows, execution timings/arrivals, commerce schema, equivalence labels | SQL patterns, NL question structure, syntax-derived statement labels/templates | Treating `validated` or original/fixed pairs as rewrite-equivalence ground truth |

### redset

Path: `/Users/rishonjoby/Documents/ChatGPT/LLM-Verified DB Performance Advisor/backend/data/external/redset`. Present: **True**.

Licence evidence: Local LICENSE: Attribution-NonCommercial 4.0 International

Capability flags (yes/no/partial, based on observed fields):

| Capability | Finding |
|---|---|
| table_row_data | no |
| sql_text | no |
| table_column_names | no |
| arrival_timestamps | yes |
| fingerprints_templates | partial |
| runtimes | yes |
| read_write_labels | yes |
| commerce_semantics | no |

feature_fingerprint is a hash, read/write_table_ids are anonymized IDs; no SQL/schema/commerce columns.

Input files and physical sizes:

| Input | Bytes | Rows / interpretation |
|---|---:|---|
| `LICENSE` | 19347 | 408 |
| `README` | 41 | 1 |
| `provisioned/full.parquet` | 17666893301 | 433107811 |
| `provisioned/sample_0.001.parquet` | 27284280 | 433108 |
| `provisioned/sample_0.01.parquet` | 246314811 | 4331078 |
| `serverless/full.parquet` | 322619676 | 8244382 |
| `serverless/sample_0.001.parquet` | 542916 | 8244 |
| `serverless/sample_0.01.parquet` | 4906080 | 82444 |

Rows measured: **441352193**; clusters/warehouses: **400**; pooled observed days: **93**. Time span: `{'min': '2024-02-29 23:59:55.128878+00:00', 'max': '2024-05-31 00:00:04.403927+00:00'}`.
Timezone: timestamp[us] has no timezone. UTC assumed for bucketing, not encoded in source.
Arrival p99/mean: **2.383770846653524**; max/mean: **12.091077036973056**.
Runtime quantiles (source units): `{'p50': 47.0, 'p95': 5051.0, 'p99': 37626.0, 'n_nonnegative_nonnull': 441352193}`.
Read/write measurement: `{'read_share': 0.2036461932794792, 'write_share': 0.5011839739516146, 'other_share': 0.2951698327689062, 'read_definition': 'select', 'write_definition': 'insert/update/delete/copy/merge'}`.
Fingerprints: `{'nonnull_queries': 388345961, 'identity': 'cluster + feature_fingerprint', '1h': {'distinct_window_fingerprints': 103197989, 'repetition_rate': 0.7342627467161941}, '1day': {'distinct_window_fingerprints': 67600435, 'repetition_rate': 0.8259272870356955}, 'whole_trace': {'distinct_window_fingerprints': 42839860, 'repetition_rate': 0.8896863510832291}}`.
Per-cluster measures, exact minute counts, per-hour dispersion, daily top-k coverage and consecutive-day drift are in the JSON; null fingerprints are excluded from fingerprint denominators and reported as nulls, never replaced with query IDs.
Sanity checks: key null rates `{'instance_id': 0.0, 'arrival_timestamp': 0.0, 'feature_fingerprint': 0.12009962302373788, 'execution_duration_ms': 0.0, 'query_type': 0.0, 'query_id': 0.0, 'database_id': 0.0}`; duplicate-key sample `{'rows': 100000, 'duplicates': 0, 'scope': 'first at most 100000 projected keys, not full-row duplicates'}`; full-row duplicates not measured (no full-row sort pass).
Scope/limitations: `{'canonical_sources': ['provisioned/full.parquet', 'serverless/full.parquet'], 'row_cap_per_canonical_file': None, 'selection': 'physical prefix if capped; all canonical rows otherwise', 'samples_excluded_from_aggregates': True}`.

### snowset

Path: `/Users/rishonjoby/Documents/ChatGPT/LLM-Verified DB Performance Advisor/backend/data/external/snowset`. Present: **True**.

Licence evidence: not stated in the downloaded archive members; not measured beyond local files

Capability flags (yes/no/partial, based on observed fields):

| Capability | Finding |
|---|---|
| table_row_data | no |
| sql_text | no |
| table_column_names | no |
| arrival_timestamps | yes |
| fingerprints_templates | no |
| runtimes | yes |
| read_write_labels | no |
| commerce_semantics | no |

Main has queryId/warehouseId/createdTime/durationExec/I-O counters; no SQL/hash/query-type. sec+queryId in ts-explosion describes repeated activity seconds.

Input files and physical sizes:

| Input | Bytes | Rows / interpretation |
|---|---:|---|
| `snowset-main.parquet.tar.gz` | 8202394513 | 69182074 |
| `ts-explosion.parquet.tar.gz` | 3002710180 | 801891730 |

Rows measured: **69182074**; clusters/warehouses: **2051**; pooled observed days: **15**. Time span: `{'min': '2018-02-21 20:00:00.286000+00:00', 'max': '2018-03-07 04:11:22.719000+00:00'}`.
Timezone: createdTime/endTime/sec declare UTC
Arrival p99/mean: **1.4251666407110029**; max/mean: **1.7720164330430452**.
Runtime quantiles (source units): `{'p50': 986.0, 'p95': 13276.0, 'p99': 64605.0, 'n_nonnegative_nonnull': 69182074}`.
Read/write measurement: `{'status': 'not measured', 'reason': 'No query-type label; I/O writes are not DML labels'}`.
Fingerprints: `{'status': 'not measured', 'reason': 'No template fingerprint in Snowset; queryId is an execution identifier'}`.
Per-cluster measures, exact minute counts, per-hour dispersion, daily top-k coverage and consecutive-day drift are in the JSON; null fingerprints are excluded from fingerprint denominators and reported as nulls, never replaced with query IDs.
Sanity checks: key null rates `{'warehouseId': 0.0, 'createdTime': 0.0, 'durationExec': 0.0, 'queryId': 0.0, 'databaseId': 0.046478774255886}`; duplicate-key sample `{'rows': 100000, 'duplicates': 0, 'scope': 'first at most 100000 projected keys, not full-row duplicates'}`; full-row duplicates not measured (no full-row sort pass).
Scope/limitations: `{'canonical_source': 'snowset-main only', 'row_cap': None, 'physical_prefix_sample_if_capped': False, 'runtime_unit': 'durationExec source units; no unit declaration supplied locally; do not normalize as PostgreSQL milliseconds without confirmation'}`.

### skyserver

Path: `/Users/rishonjoby/Documents/ChatGPT/LLM-Verified DB Performance Advisor/backend/data/external/skyserver_zenodo`. Present: **True**.
Requested `backend/data/external/skyserver/` exists: **False**; actual alternate folder inspected explicitly.

Licence evidence: Local source_record.json: cc-by-4.0

Capability flags (yes/no/partial, based on observed fields):

| Capability | Finding |
|---|---|
| table_row_data | no |
| sql_text | no |
| table_column_names | no |
| arrival_timestamps | no |
| fingerprints_templates | no |
| runtimes | no |
| read_write_labels | no |
| commerce_semantics | no |

Binary .data/query/answer and numeric skyserver.queries; no SQL strings/schema/arrival fields. Binary row interpretation undocumented locally.

Input files and physical sizes:

| Input | Bytes | Rows / interpretation |
|---|---:|---|
| `answer` | 1266600 | not measured: non-row or undocumented format |
| `answer_0.001` | 1266600 | not measured: non-row or undocumented format |
| `answer_0.1` | 1266600 | not measured: non-row or undocumented format |
| `answer_1` | 1266600 | not measured: non-row or undocumented format |
| `answer_1e-05` | 1266600 | not measured: non-row or undocumented format |
| `answer_2e-07` | 1266600 | not measured: non-row or undocumented format |
| `query` | 2533200 | not measured: non-row or undocumented format |
| `query_0.001` | 2533200 | not measured: non-row or undocumented format |
| `query_0.1` | 2533200 | not measured: non-row or undocumented format |
| `query_1` | 2533200 | not measured: non-row or undocumented format |
| `query_1e-05` | 2533200 | not measured: non-row or undocumented format |
| `query_2e-07` | 2533200 | not measured: non-row or undocumented format |
| `skyserver.data` | 2342536884 | not measured: non-row or undocumented format |
| `skyserver.queries` | 2692722 | 158325 |
| `source_record.json` | 7491 | 1 |

SQL prevalence, arrivals, fingerprints, runtimes and commerce row mapping: **not measured / unavailable**, because the local binary layout lacks a SQL or execution schema. Do not invent column names or interpret floats as runtimes.

### sqlshare

Path: `/Users/rishonjoby/Documents/ChatGPT/LLM-Verified DB Performance Advisor/backend/data/external/sqlshare`. Present: **True**.

Licence evidence: No licence file found in the ZIP; online terms not checked (offline)

Capability flags (yes/no/partial, based on observed fields):

| Capability | Finding |
|---|---|
| table_row_data | yes |
| sql_text | yes |
| table_column_names | yes |
| arrival_timestamps | no |
| fingerprints_templates | partial |
| runtimes | no |
| read_write_labels | partial |
| commerce_semantics | partial |

ZIP contains data/<user>/ tables plus queries.txt and view_script.txt. These are heterogeneous user data, not a connected commerce schema. Lexical commerce matches are measured below.

Input files and physical sizes:

| Input | Bytes | Rows / interpretation |
|---|---:|---|
| `sqlshare_data_release1.zip` | 3646871470 | not measured: non-row or undocumented format |

SQL queries analysed: **11121**; parsed: **10758**; failure rate: **0.032640949554896145**. Lexical e-commerce matches: **1**; share of parsed: **9.295408068414203e-05**.
Artifact time span (not arrivals): `{'min': None, 'max': None}`.
Duplicate SQL texts: **1845**; duplicate primary records: **1845**.

| Pattern | Count | Rate among parsed | Example query IDs |
|---|---:|---:|---|
| P1 | 353 | 0.03281279048150214 | `['sqlshare_data_release1/queries.txt:46', 'sqlshare_data_release1/queries.txt:47', 'sqlshare_data_release1/queries.txt:48', 'sqlshare_data_release1/queries.txt:49', 'sqlshare_data_release1/queries.txt:50']` |
| P2 | 104 | 0.009667224391150772 | `['sqlshare_data_release1/queries.txt:1125', 'sqlshare_data_release1/queries.txt:1141', 'sqlshare_data_release1/queries.txt:1157', 'sqlshare_data_release1/queries.txt:1158', 'sqlshare_data_release1/queries.txt:1159']` |
| P3a | 4 | 0.0003718163227365681 | `['sqlshare_data_release1/queries.txt:319', 'sqlshare_data_release1/queries.txt:320', 'sqlshare_data_release1/queries.txt:321', 'sqlshare_data_release1/queries.txt:1718']` |
| P3b | 0 | 0.0 | `[]` |
| P3c | 17 | 0.0015802193716304145 | `['sqlshare_data_release1/queries.txt:5397', 'sqlshare_data_release1/queries.txt:5550', 'sqlshare_data_release1/queries.txt:5551', 'sqlshare_data_release1/queries.txt:5552', 'sqlshare_data_release1/queries.txt:5778']` |
| any | 478 | 0.04443205056701989 | `['sqlshare_data_release1/queries.txt:46', 'sqlshare_data_release1/queries.txt:47', 'sqlshare_data_release1/queries.txt:48', 'sqlshare_data_release1/queries.txt:49', 'sqlshare_data_release1/queries.txt:50']` |

Top referenced table names (per-query counts): `[['xstal_tracker.csv', 181], ['ssgcid_ec_map', 173], ['ec_pathway.csv', 158], ['qdod cgigas gene descriptions (swiss-prot)', 119], ['proteins_cleaved_pdb', 109], ['tbl_organizer', 103], ['sfl_view', 90], ['tbl_conference', 88], ['all3col', 85], ['sds_view', 84], ['v2_geomics_fe-cu-mn-zn-vedamati.csv', 80], ['uw 2010 salaries', 79], ['qdod_cgigas_go_goslim', 73], ['geomics_key.csv', 73], ['1385_query_log', 72], ['all sequenced proteins all treatments.txt', 72], ['stats_view', 71], ['author', 71], ['v2_gdgt data for francois database.csv', 71], ['tokyo_1_suna.csv', 69]]`.
P1=function-wrapped predicate; P2=qualified outer reference; P3a=DISTINCT covering GROUP BY keys; P3b=derived/CTE ORDER BY without LIMIT/OFFSET; P3c=heuristic unused LEFT JOIN. These are candidate opportunities, not equivalent-rewrite labels. Query text is redacted; IDs identify examples.
Scope: `{'primary_sql': 'queries.txt', 'views_separate_not_pooled': True, 'SQL_cap_per_member': None, 'table_row_counts': 'full streaming CSV pass; header excluded; malformed CSV labelled not measured', 'timestamp_note': 'Timestamps inside scientific tables are domain observations, not query arrivals'}`.

### sede

Path: `/Users/rishonjoby/Documents/ChatGPT/LLM-Verified DB Performance Advisor/backend/data/external/sede`. Present: **True**.

Licence evidence: Local root LICENSE: Apache License 2.0; does not by itself resolve upstream SQL/data redistribution terms

Capability flags (yes/no/partial, based on observed fields):

| Capability | Finding |
|---|---|
| table_row_data | no |
| sql_text | yes |
| table_column_names | yes |
| arrival_timestamps | no |
| fingerprints_templates | partial |
| runtimes | no |
| read_write_labels | partial |
| commerce_semantics | partial |

JSONL QueryBody/Title/Description/QuerySetId/CreationDate/validated; schema JSON documents StackExchange tables without table rows. CreationDate is query artifact metadata, not an arrival trace.

Input files and physical sizes:

| Input | Bytes | Rows / interpretation |
|---|---:|---|
| `data/sede/test.jsonl` | 467750 | 857 |
| `data/sede/test_original.jsonl` | 481375 | 857 |
| `data/sede/train.jsonl` | 5390659 | 10309 |
| `data/sede/val.jsonl` | 460550 | 857 |
| `data/sede/val_original.jsonl` | 470782 | 857 |
| `stackexchange_schema/tables_so.json` | 25731 | 1 |

SQL queries analysed: **12023**; parsed: **9376**; failure rate: **0.22016135739831988**. Lexical e-commerce matches: **1**; share of parsed: **0.00010665529010238908**.
Artifact time span (not arrivals): `{'min': '2010-05-27 20:08:16', 'max': '2020-12-10 4:10:13'}`.
Duplicate SQL texts: **215**; duplicate primary records: **0**.

| Pattern | Count | Rate among parsed | Example query IDs |
|---|---:|---:|---|
| P1 | 1760 | 0.18771331058020477 | `['test.jsonl:539106', 'test.jsonl:539672', 'test.jsonl:542264', 'test.jsonl:542974', 'test.jsonl:551415']` |
| P2 | 374 | 0.03988907849829351 | `['test.jsonl:544775', 'test.jsonl:559288', 'test.jsonl:567274', 'test.jsonl:661156', 'test.jsonl:670935']` |
| P3a | 18 | 0.0019197952218430034 | `['test.jsonl:1094697', 'train.jsonl:144998', 'train.jsonl:154715', 'train.jsonl:210397', 'train.jsonl:210430']` |
| P3b | 6 | 0.0006399317406143345 | `['test.jsonl:580682', 'test.jsonl:895215', 'train.jsonl:122812', 'train.jsonl:364259', 'train.jsonl:784516']` |
| P3c | 28 | 0.0029863481228668944 | `['test.jsonl:847310', 'test.jsonl:1075294', 'test.jsonl:1287006', 'train.jsonl:65916', 'train.jsonl:126726']` |
| any | 2074 | 0.22120307167235495 | `['test.jsonl:539106', 'test.jsonl:539672', 'test.jsonl:542264', 'test.jsonl:542974', 'test.jsonl:544775']` |

Top referenced table names (per-query counts): `[['posts', 6434], ['users', 3095], ['tags', 1709], ['posttags', 1529], ['votes', 808], ['posthistory', 637], ['comments', 608], ['badges', 289], ['posttypes', 261], ['votetypes', 96], ['postlinks', 92], ['user_by_tag', 90], ['suggestededits', 84], ['tagsynonyms', 62], ['raw', 57], ['posthistorytypes', 55], ['reviewtasks', 48], ['postfeedback', 40], ['pendingflags', 30], ['closereasontypes', 29]]`.
P1=function-wrapped predicate; P2=qualified outer reference; P3a=DISTINCT covering GROUP BY keys; P3b=derived/CTE ORDER BY without LIMIT/OFFSET; P3c=heuristic unused LEFT JOIN. These are candidate opportunities, not equivalent-rewrite labels. Query text is redacted; IDs identify examples.
Scope: `{'primary_splits': ['train.jsonl', 'val.jsonl', 'test.jsonl'], 'original_splits_inspected_separately': True, 'cap_per_split': None, 'validated_is_not_equivalence_label': True}`.

## Gap matrix

A = Available in the local source; D = Derivable with caveats; M = Missing. Availability does not establish transferability to quick-commerce.

| Pipeline requirement | Redset | Snowset | SkyServer | SQLShare | SEDE |
|---|---|---|---|---|---|
| Connected schema + rows: orders, order_items, products, store_stock, customers, stores, riders | Missing | Missing | Missing | Missing | Missing |
| Hot-product/heavy-customer skew and order hour-of-day shape | Missing | Missing | Missing | Missing | Missing |
| Generic database burst/spike shape (not observed commerce flash sales) | Derivable | Derivable | Missing | Missing | Missing |
| Recurring templates, repetition and day-to-day drift | Derivable | Missing | Missing | Derivable | Derivable |
| Transferable slow-report query structures | Missing | Missing | Missing | Derivable | Derivable |
| Read/write statement mix | Available | Missing | Missing | Derivable | Derivable |
| Order-processing transaction mix | Missing | Missing | Missing | Missing | Missing |
| Equivalent/non-equivalent rewrite ground truth | Missing | Missing | Missing | Missing | Missing |
| Parameter bindings for our PostgreSQL replay | Missing | Missing | Missing | Missing | Missing |

## Derivation feasibility and populate plan

| Method | Feasibility / effort | Concrete mapping | Risks |
|---|---|---|---|
| Redset/Snowset arrivals → minute series, p99/mean, hourly dispersion | Feasible / M | Evidence for profiles.json `rate_mult`; fit/compare piecewise rates versus current exponential build_schedule arrivals | OLAP queries are not orders; sampling, tenant mix and timezone assumptions bias calibration |
| Redset hash counts → repeats, top-k, drift | Partial / M | Candidate future `Shape.weight`/shape-frequency configuration; not currently defined in Shape | feature fingerprint need not equal SQL template; cached NULLs and DB/tenant scope matter |
| Snowset queryId → recurring SQL templates | Not feasible / S diagnosis | No mapping: execution ID is not a template hash | Repeated queryId in ts-explosion means activity across seconds |
| Trace read/write labels → order transaction weights | Partial / M | Redset SELECT/INSERT/UPDATE mix supplies a scenario bound, not direct `TxnDef.weight` replacement | One place_order transaction has multiple statements; OLAP COPY differs from commerce writes |
| SQLShare/SEDE AST patterns → report structures | Partial / L | Manually adapt structures to `recent_orders`, `delivered_yesterday`, `stockouts_15m`, `hourly_sales`, `open_order_customers`, `payout_report`, `top_products_store`, `picker_queue` | T-SQL syntax, table keys, NULLs, bag semantics, timezone and top-level ordering must be revalidated |
| SEDE Title/Description → NL questions | Feasible structure reuse / M | `Shape.question`, generated questions.json after changing domain nouns and bindings | Hidden assumptions and under-specification; do not claim the question describes an order dataset |
| Source rows → commerce records | Not feasible for a verified direct import / L if attempted | Every required commerce table still needs synthetic rows or separate licensed commerce input | Scientific rows/schema-only metadata are not orders/inventory |

### Recommended order of work

1. Keep the schema in step 02. Decide whether the evaluation is honestly labelled trace-calibrated synthetic commerce, or requires a separate real commerce dataset with provenance. None is newly downloaded or claimed by this analysis.
2. Preserve deterministic `--seed`, `--scale` and UTC-noon `meta.as_of`. The current `seed.py` binds `n_customers=20000*scale`, `n_orders=300000*scale`; `seed_helpers.sql` sets stores=50, categories=40, products=2000, riders=500, stock presence=0.8. These counts and relationships are synthetic, not derived from trace query counts.
3. Compute full-coverage per-tenant calibration aggregates before choosing `profiles.json` `rate_mult` or a burst schedule. Do not change `BASE_RATE_TPS` directly from cloud query rates: keep local saturation calibration in step 11. Use metadata-derived rates as sensitivity scenarios with clear normalization and keep 5/10/20 as declared experiment levels unless contracts change.
4. Keep `zipf_s`, `hot_set_size`, `hot_share` synthetic until order-item/customer records are available. Also retain declared assumptions for `status_poll_mult`, `dashboard_refresh_s`, `pre_sale_load`, `analyze_after_load`. These traces cannot identify hot SKUs, dark-store stock, status polling or catalogue updates.
5. Keep `ORDER_TXNS` (`TxnDef.weight`) defaults from step 04: `place_order`, `update_status`, `order_status`, `stock_decrement`, `picker_queue_read`. Any proposed adaptation must map statement groups to transactions and be swept, not estimated as real commerce mix from SELECT/UPDATE shares.
6. Use SQLShare and SEDE IDs/pattern counts to hand-select structures and adapt them onto the existing report shapes. `app/shapes.py`, `backend/data/profiles.json` and ORDER_TXNS are planned interfaces, not implemented files in this checkout. No shape-weight field currently exists; adding one requires a documented contract change.
7. Sample `customer_id`, `store_id`, `product_id`, `city`, `as_of`, `start`, `end`, `q`, `new`, `id` from actual evaluation-DB pools. Cloud table/query IDs and SEDE placeholder names cannot substitute for replay parameters.
8. Build human-labelled equivalent/trap pairs under step 10/18 and run the tiered checks. Parsing, SQLShare materializations, SEDE `validated`, and fixed/original query variants are not equivalence proofs or labelled unsafe tuning examples.

## Risks and licence notes

- Redset local LICENSE states Attribution-NonCommercial 4.0 International. The local SkyServer record states cc-by-4.0. SEDE root code licence is Apache-2.0; underlying query/data rights require separate confirmation. Snowset and SQLShare local copies do not supply an explicit licence document. No online legal/usage terms were checked; no permission is inferred from public download access.
- No raw SQL is reproduced in this report; examples are source IDs. Do not redistribute dataset SQL or table rows until source-specific rights are confirmed.
- Scientific metadata and query creation dates cannot validate a commerce flash-sale scenario. Physical I/O writes do not establish logical DML labels.
- CSV dialect/header inference is heuristic; malformed/heterogeneous members are identified in JSON. SQL parse failures and unresolved aliases affect prevalence; unqualified correlation is intentionally not asserted. LEFT JOIN removal may multiply rows even if right-side columns are unused.
- Schema names are lexical evidence only. Source literals are not a complete parameter binding log. Sample-derived rates are not population rates; repeated query copies must be excluded.

## Open questions

- Which real commerce dataset, if any, supplies linked orders/items/products plus permission to generate missing inventory/store/rider fields?
- Is a documented trace-calibrated synthetic commerce evaluation acceptable, rather than an unsupported claim of real-commerce records?
- What are Snowset duration units and the release's queryId identity guarantees? Local files alone do not establish them.
- Can the intended SDSS SQL query log be supplied? The downloaded Zenodo numeric bundle is not that log.
- Are UTC bucketing assumptions for Redset valid, and how should per-tenant observation gaps/cached-query NULL fingerprints be handled?
- Which source licences permit redistribution of raw SQL and derivative NL examples? Which original/fixed SEDE variants should be used for each analysis?

## Proposed documentation edits (not applied)

```diff
--- docs/steps/02_schema_and_seeding.md
+ Explicitly label the current rows synthetic; separate source provenance from trace-calibration provenance.
+ Add optional versioned calibration metadata only after per-tenant validation; retain deterministic seed/as_of.
+ Mark all products/customer skew, store_stock, riders, statuses and transaction bindings generated unless a commerce source supplies them.
+ Require field-level provenance and tests before replacing synthetic rows with real-commerce input.
--- docs/steps/18_datasets_analysis.md
+ Use the actually present skyserver_zenodo folder; flag its numeric/binary format as unsuitable for SQL prevalence.
+ Add separate branches for SQLShare underlying table inspection and its queries.txt/view_script.txt SQL.
+ Remove assumptions that Snowset supplies SQL template fingerprints or logical read/write labels.
+ Exclude ts-explosion active-query-seconds and Redset sample copies from canonical query-arrival counts.
+ Exclude SEDE _original variants from primary split pooling; do not treat validated/fixed pairs as equivalence labels.
+ Store input/member hashes, sample scope, timezone/duration-unit assumptions and parse-failure denominators.
```

## Failures and unmeasured items

- Full-row trace duplicate counts are not measured: only a bounded execution-key prefix sanity check is performed.
- SkyServer binary row count/record semantics and SQL prevalence are not measured without a binary schema/SQL log.
- SQLShare per-cell missingness across heterogeneous table rows is not measured; key nulls and row-width/CSV parse diagnostics are provided where meaningful.
- No real commerce Zipf exponent, customer repeat-purchase distribution, order arrival profile, stock state or rewrite equivalence ground truth can be inferred from the inspected schemas.
- Numbers are script outputs; no expected benchmark result is hard-coded as a measurement.

## Additional inspection evidence

The gap-matrix Derivable cells for SQLShare/SEDE recurrence mean static SQL normalization/repetition only. Execution-arrival repetition and day-to-day drift are Missing for those sources; artifact creation dates do not supply them.

The memory setting is a batch-boundary budget, not an operating-system hard limit. The measured peak includes transient batch allocations; use the recorded peak when planning resources.

### SQLShare filename false positives

These are observed source members, not assumed commerce schemas. Row counts follow the documented inferred CSV-header policy. No cell values are copied.

| Member | Rows | Actual header fields |
|---|---:|---|
| `sqlshare_data_release1/data/1041/table_order_emails.csv` | 98 | `['Email Address']` |
| `sqlshare_data_release1/data/1318/table_Coffee Production Long.csv` | 825 | `['Country', 'Coffee Types', '2000/01', '2001/02', '2002/03', '2003/04', '2004/05', '2005/06', '2006/07', '2007/08', '2008/09']` |
| `sqlshare_data_release1/data/1318/table_Coffee Production.csv` | 55 | `['Country', 'Coffee Types', '2000/01', '2001/02', '2002/03', '2003/04', '2004/05', '2005/06', '2006/07', '2007/08', '2008/09']` |
| `sqlshare_data_release1/data/1333/table_united_states_retail_electric_prices___june_2009` | 51 | `['state', 'date', 'price', 'type']` |
| `sqlshare_data_release1/data/188/table_Glycerol_Stocks_Locations_merged_tabs_cleaned.csv` | 12227 | `['Order', 'Vessel label', 'Address', 'LSE #', 'Glycerol Stock ID', 'VCID #', 'Target_name', 'Production step', 'Harvest_Date', 'Glycerol Name', 'Received SBRI', 'Orig SBRI Location', 'Original SBRI address', 'Please ignore ORIGINAL UW PPG Box address', 'Please ignore ORIGINAL UW PPG Box', 'Set']` |

An email-address column is not an order/customer relational model. Coffee production, electricity prices and glycerol-sample locations do not supply linked retail orders, SKU line items and dark-store inventory.

SQLShare query-arrival time span is not measured: queries.txt contains SQL blocks without execution timestamps. Table-specific scientific observation date spans are not pooled across unrelated domains.

Report-only enrichment preserved all measured statistics. Renderer SHA-256: `9361b3e31d1bd9225a4036310d5caed278e1b367b0457634016d1ea3361b293f`.
