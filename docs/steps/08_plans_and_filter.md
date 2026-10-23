# Step 08 — Plans, fingerprints, HypoPG filter (`app/plans.py`, `app/observe.py`)

**Goal (end state):** given a shape and a candidate, `plans.signature_for` returns a stable plan fingerprint;
candidates that do **not** change the plan, or that duplicate another candidate's plan, are filtered out
before any expensive measurement. `observe.top_statements` returns slow statements from `pg_stat_statements`.

**Prerequisites:** steps 02–04, 07.

## 8.1 `plans.explain`

```python
def explain(conn, sql: str, *, analyze=False, buffers=False, generic=False) -> dict
```
* Runs `EXPLAIN (FORMAT JSON[, ANALYZE][, BUFFERS][, TIMING OFF][, GENERIC_PLAN])` and returns `row[0][0]` (the dict with key `"Plan"`).
* `generic=True` is for parameterised text containing `$1` (PostgreSQL 16 supports `GENERIC_PLAN`; not combinable with `ANALYZE`).
* For `analyze=True` accept only `SELECT`.
* Add a short in-process cache keyed by `(dbname, sql, analyze)` only for non-analyze explains within one job (invalidate when a HypoPG index is created/reset).

## 8.2 Plan fingerprint

`fingerprint(plan) -> str` (12 hex chars of SHA-1) built from a canonical string of the plan tree:

For every node in pre-order, include: `Node Type`, `Relation Name`, `Index Name`, `Join Type`, `Strategy` (agg), `Scan Direction`,
`Parent Relationship`, sorted `Sort Key` (column names only), and `Filter`/`Index Cond` **with literal values replaced by `?`**
(regex on numbers and quoted strings). Exclude: costs, rows, widths, loops, timings, buffers, `Plan Rows`, `Parallel Aware` workers count.

Rules:
* Hypothetical indexes show up as `Index Name` like `<13568>btree_orders_customer_id_created_at`: normalise by replacing the numeric OID prefix `<\d+>` with `<hypo>`.
* Two plans equal ⇔ same fingerprint. Unit-test with plans that differ only in cost ⇒ equal.

## 8.3 HypoPG-based plan for an index candidate

```python
def hypo_plan(conn, create_index_sql: str, query_sql: str) -> dict
```
1. `ddl = normalise_index_ddl(create_index_sql)`: strip `CONCURRENTLY`, `IF NOT EXISTS`, trailing `;`, and an explicit index **name** is allowed (hypopg ignores it).
2. `SELECT * FROM hypopg_create_index(%s)` (use a parameter, not f-string).
3. `plan = explain(conn, query_sql)`; **always** `SELECT hypopg_reset()` in `finally`.
4. Return `plan`. If HypoPG raises (unsupported index type, unknown column), raise `InvalidDDL` with the message.
5. HypoPG supports B-tree (plain, partial, expression, covering with `INCLUDE`). Anything else ⇒ `InvalidDDL`.
6. HypoPG cannot check some errors that real creation hits (e.g. non-immutable expression). **Keep the rule:** the DDL must also succeed on the candidate clone (step 07); failure there ⇒ `rejected_invalid_ddl`.

## 8.4 Signature per candidate kind

```python
def signature_for(candidate, original_sql, conn) -> str
```
| kind | signature |
|---|---|
| `index` | `fingerprint(hypo_plan(conn, candidate.sql, original_sql))` |
| `rewrite` | `fingerprint(explain(conn, candidate.sql))` (the **new** SQL's plan) |
| `stats` | `"stats:" + normalised DDL` (e.g. `analyze orders`; lower-case, collapsed whitespace; `CREATE STATISTICS` keeps column list sorted) |

`baseline_signature(conn, original_sql) = fingerprint(explain(conn, original_sql))`.

## 8.5 Filter rules (`filter_candidates(cands, shape_sql_rendered, conn_baseline) -> (kept, dropped)`)

Apply in this order and set `status` + `reason`:
1. **No plan change:** `candidate.signature == baseline_signature` ⇒ `skipped_noplan` ("plan unchanged").
   * Exception: `stats` candidates are never dropped as no-plan (their effect is on statistics); they are kept.
2. **Duplicate plan:** a previously kept candidate of the **same kind** has the same signature ⇒ `skipped_duplicate` (keep the first, record `duplicate_of`).
3. **Rewrite SQL equals original** after sqlglot normalisation ⇒ `skipped_noplan`.
4. **Index already exists:** check `pg_indexes`/`pg_index` for an identical definition (same table, column list, predicate) ⇒ `skipped_duplicate` ("already exists").
5. Everything else stays `proposed`.

Use the **baseline clone** connection (statistics fresh) — never `qc_live` — for plans. The same rendered SQL (one fixed parameter set per shape per job) is used for baseline and candidates so plans are comparable.

## 8.6 `catalog` helper for the LLM context and safety

`get_catalog(conn) -> Catalog` returns tables, columns (name, type, nullable), existing indexes (name, definition), row estimates (`pg_class.reltuples`), primary/foreign keys. Used by `safety.check` and prompts (step 09).

## 8.7 `app/observe.py`

```python
def top_statements(conn, limit=20, order="total") -> list[dict]
```
Query `pg_stat_statements` (columns `queryid, query, calls, total_exec_time, mean_exec_time, rows, shared_blks_hit, shared_blks_read`),
ordered by `total_exec_time` (or `mean_exec_time` when `order="mean"`), excluding statements tagged `/* dpa:sys */` and utility statements.
Return `{"queryid": str, "query": str, "calls": int, "total_ms": float, "mean_ms": float, "tag": "shape:recent_orders" | "txn:place_order" | None}`; parse the tag from the first comment. If the extension is missing return `[]` and a flag so the UI can warn.

## 8.8 Tests (`tests/test_plans.py`, `@pytest.mark.db`)

* `test_fingerprint_ignores_costs`: change `Total Cost` in a copy ⇒ same fingerprint.
* `test_fingerprint_changes_with_index`: baseline vs HypoPG index on `orders(customer_id, created_at DESC)` for `recent_orders` differ.
* `test_hypo_reset_always`: after an exception, `hypopg_list_indexes` is empty.
* `test_noplan_for_status_index`: `CREATE INDEX ON orders (status)` on `recent_orders` ⇒ `skipped_noplan`.
* `test_duplicate_plans`: two different index DDLs yielding the same plan ⇒ second `skipped_duplicate` (construct with `(customer_id, created_at DESC)` vs `(customer_id, created_at DESC) INCLUDE (store_id)`; if plans differ, assert the opposite and adjust — document what you observe).
* `test_invalid_expression_index`: `CREATE INDEX ON orders (DATE(delivered_at))` — HypoPG may accept it; the clone step must reject it. Assert the **end-to-end** outcome is `rejected_invalid_ddl` using step 07's `clone_pair`.
* `test_existing_index_skipped`: create an index on a clone, propose the same ⇒ `skipped_duplicate`.
* `test_observe_tags`: run two tagged statements, `top_statements` shows tags.

## 8.9 Done checklist
- [ ] Fingerprint stable across runs; tests green
- [ ] Filter proven on the mock fixtures of step 04
- [ ] Commit `step 08: plans and filter`
