# Step 10 — Tiered equivalence checker for rewrites (`app/equivalence.py`)

**Goal (end state):** `check_rewrite(conn, original_sql, rewrite_sql, cfg)` returns an `EquivResult` and **rejects every
trap** in the labelled set (`backend/data/rewrite_pairs.json`, built in step 18) while accepting the correct rewrites.
The result is always `tested` / `rejected` / `unknown` — **never "proven"** — and `tested` always requires human approval.

**Prerequisites:** steps 04, 07, 08, 09.

## 10.1 Configuration

```python
@dataclass
class EquivConfig:
    cx_dbs: int = 50              # counterexample databases per timezone set
    rows_per_table: int = 30
    timezones: tuple = ("UTC", "Asia/Kolkata", "America/Los_Angeles")
    stmt_timeout_ms: int = 60000
    min_nonvacuous: float = 0.30  # below this the result is flagged weak
    seed: int = 1234
    use_llm_veto: bool = False
```
Run on the **baseline sandbox clone** (never `qc_live`). All work happens in schema `cx` (scratch) or read-only on `public`.

## 10.2 Tier 0 — syntax and shape

1. Parse both SQLs with sqlglot (`read="postgres"`). Parse error ⇒ `rejected` ("unparseable").
2. Both must be a single `SELECT` (CTEs allowed). No DML, no locking clause.
3. Compute the **output column names** and count for both (via `EXPLAIN`-free method: `SELECT * FROM (<sql>) q LIMIT 0` and read `cursor.description`). Mismatch (names or count or types family) ⇒ `rejected`.
4. Reject if the rewrite is textually the same after normalisation (return `unknown` "identical"; caller treats as no-op).

## 10.3 Tier 1 — rewrite-class recognisers (sqlglot AST)

Return `rewrite_class` 1, 2 or 3, or reject with "outside allowed classes". A rewrite may match more than one; record the lowest.

**Class 1 — index-friendly predicate.** In the original `WHERE`, find a comparison whose one side is a function/cast of a column
(`DATE(col)`, `col::date`, `CAST(col AS date)`, `date_trunc(unit, col)`, `EXTRACT(... FROM col)`) and whose other side is column-free.
The rewrite must contain **no** function on that column and must contain bounds on the bare column (`col >= lo AND col < hi`, or `BETWEEN`). Reject if the rewrite changes any other predicate.

**Class 2 — correlated subquery → join.** Original contains a correlated `EXISTS`/`IN`/scalar subquery (inner query references an outer alias).
The rewrite has no subquery for that predicate and has a `JOIN`/`USING` against the same relation. Flag `needs_distinct` if the original had `EXISTS`/`IN` semantics (set semantics): the rewrite must then use `DISTINCT` or a `GROUP BY` covering the output, or a semi-join form; otherwise reject at tier 1 with "join may duplicate rows".

**Class 3 — redundant removal.** The rewrite has strictly fewer of: `DISTINCT`, `ORDER BY` (only inside subqueries/CTEs without `LIMIT`/`OFFSET`, and not feeding window functions), or `JOIN`s than the original, and every remaining clause is identical (compare AST after removing those nodes).
* **Top-level `ORDER BY` removal is never allowed.** `DISTINCT` removal must be on a select list that includes a primary-key or unique column of the sole source relation (look up in catalog) **or** pass tier 2/3 — tier 1 only records the claim.

Anything else ⇒ `rejected` ("outside allowed classes").

## 10.4 Tier 2 — counterexample databases

Algorithm per counterexample database `k` (seeded by `seed + k`):

1. **Tables:** collect `exp.Table` names from both queries → set `T`. Create schema `cx` (drop first). For each `t`:
   `CREATE TABLE cx.t (LIKE public.t INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING INDEXES)` (this keeps PK/unique/CHECK; not FKs).
   Create sequences/defaults as copied. Do **not** copy data.
2. **Value pools** per column from: (a) literals extracted from both queries (sqlglot `exp.Literal`) coerced to the column type; (b) type-based pools:
   * integers: `{1..12}` plus the query literals; floats/numerics: `{0, 1, 2.5, 10, 100}`; booleans: `{true,false}`; text: `{'a','b','c','delivered','placed','packing', …literals…}`;
   * `timestamptz`/`timestamp`/`date`: **boundary pool** = for each query-literal date `D` (and the seeded `as_of`), instants `D−1ns`-ish: `D 00:00 ±{0, 1 s, 59 min, 23:59:59}` in **UTC and in Asia/Kolkata and America/Los_Angeles local midnights** (so rows sit on either side of midnight in different zones);
   * `NULL` allowed only if the column is nullable (probability 0.15).
3. **Generate rows** (`rows_per_table`): primary/unique columns get distinct values (sequential ints or unique text `u<k>_<i>`); foreign-key columns sample from the **already generated parent rows** (topologically order tables: parents first; if a parent is not in `T`, create it too with 5 rows). Force **duplicates** on non-unique join columns (this exposes duplicate-join rewrites) by drawing from a small pool (size 3–4).
4. `INSERT` with `executemany` (psycopg parameters; types cast by the pool).
5. For each timezone `z` in `cfg.timezones`: `SET TIME ZONE z; SET search_path = cx, public;` run **both** queries (the SQL text unchanged; table names resolve to `cx` first) with `statement_timeout`.
6. **Compare:** if both queries have a top-level `ORDER BY` and the comparison is meaningful, compare as **ordered lists**; otherwise as **multisets** (sort tuples with `repr`-safe key; `Decimal`/float compared with tolerance 1e-9; `None` sorts first).
7. Count `nonvacuous += 1` when the **original** returned ≥ 1 row.
8. First mismatch ⇒ `rejected` with `counterexample = {"tz": z, "tables": sampled rows (truncated to 20 per table), "orig_rows": [...], "new_rows": [...]}`.
9. After all `k`: `nonvacuous_fraction = nonvacuous / cx_dbs`; `weak = nonvacuous_fraction < cfg.min_nonvacuous`.
10. `DROP SCHEMA cx CASCADE` in `finally`.

If a column type has no generator (e.g. `jsonb`, geometry) and is nullable ⇒ use NULL; if NOT NULL ⇒ return `unknown` ("cannot generate data for <type>").
Reset `search_path` and `TIME ZONE` before returning.

Targeted counterexamples (always include, in addition to random ones) per class:
* Class 1: rows at 23:59:59 and 00:00:00 local time in each timezone for the compared column.
* Class 2: a parent with 2 matching children (duplicate join), a parent with 0 children, a parent with NULL join key.
* Class 3: two rows identical in the select list with different non-selected columns (DISTINCT removal), and rows whose order differs under `ORDER BY` removal.

## 10.5 Tier 3 — full-data comparison

On the baseline clone (read-only, `statement_timeout`):
```sql
SELECT count(*) FROM ((<orig>) EXCEPT ALL (<new>)) x;   -- must be 0
SELECT count(*) FROM ((<new>) EXCEPT ALL (<orig>)) x;   -- must be 0
SELECT (SELECT count(*) FROM (<orig>) a) = (SELECT count(*) FROM (<new>) b);
```
If both have a top-level `ORDER BY`: also compare `md5(string_agg(r::text, E'\n')) FROM (<sql>) r` for both. For `EXCEPT ALL` errors caused by unhashable types
(e.g. `json`) return tier 3 `unknown` rather than guessing. Repeat under `SET TIME ZONE` for the 3 zones (single run each; this is the real data).
Mismatch ⇒ `rejected`; otherwise `label = "tested"`.

## 10.6 Optional LLM veto
If `cfg.use_llm_veto`: ask the provider "Could these two queries differ for some database or session time zone? Answer JSON {\"differ\": bool, \"why\": str}". `differ=true` ⇒ downgrade to `rejected` ("LLM veto"). Never upgrade a rejection. Record the veto in `EquivResult.reason`.

## 10.7 Result object
`EquivResult.tier_reached` = last tier executed; `cx_dbs` = number actually run; `label`:
* `tested` only if tiers 0–3 completed without mismatch. `weak=True` still `tested` but UI shows a warning and `decide` treats weak rewrites as requiring explicit human confirmation.
* `unknown` for unsupported types/timeouts (caller treats like `rejected` for automation, shows reason).

## 10.8 The trap catalogue (must be in the labelled set and tests)

| ID | Class | Rewrite | Why wrong | Caught by |
|---|---|---|---|---|
| T1 | 1 | `delivered_at >= <as_of date − 1>::timestamp AT TIME ZONE 'UTC' AND delivered_at < …` | day boundary differs in Asia/Kolkata | tier 2 (tz) |
| T2 | 1 | `delivered_at::date = …` replaced by `delivered_at >= D AND delivered_at <= D + 1` | inclusive upper bound double counts midnight | tier 2 (boundary) |
| T3 | 1 | `BETWEEN D AND D` | returns only midnight instant | tier 2 |
| T4 | 2 | `EXISTS` → plain `JOIN` (no DISTINCT) | duplicates | tier 1 or tier 2 |
| T5 | 2 | `EXISTS` → `INNER JOIN` when original is `NOT EXISTS` | negation lost | tier 1/2 |
| T6 | 2 | `IN (subquery)` → join dropping a filter in the subquery | different rows | tier 2 |
| T7 | 3 | removing `DISTINCT` when select list lacks a unique key | duplicates appear | tier 2 |
| T8 | 3 | removing top-level `ORDER BY ... LIMIT` | different rows | tier 1 |
| T9 | 3 | removing a `JOIN` that filters rows (inner join to a table with missing keys) | rows disappear in original | tier 2 |
| T10 | 3 | removing `status = 'delivered'` filter | different rows | tier 2/3 |
| E1–E12 | 1–3 | genuinely equivalent variants (e.g. `delivered_at >= D AND delivered_at < D + 1` with session-timezone-consistent bounds; `EXISTS` → `JOIN` + `DISTINCT`; redundant `DISTINCT` on PK; redundant subquery `ORDER BY`) | — | must be `tested` (false-block check) |

## 10.9 Tests (`tests/test_equivalence.py`)
* Unit (no DB): class recognisers on AST pairs for each trap (tier-1 outcomes).
* DB: for every pair in `rewrite_pairs.json` assert `label == expected_label`; `E*` pairs `tested`; `T*` pairs `rejected`.
* `test_counterexample_content`: T1 returns a counterexample with `tz == "Asia/Kolkata"`.
* `test_nonvacuous_fraction`: ≥ 0.3 for the equivalent pairs on `orders`.
* `test_scratch_schema_cleaned`: no `cx` schema left after success or error.
* `test_search_path_and_tz_restored`.
* Metrics script prints the **false-accept rate per tier** over the labelled set (feeds RQ2-equivalence in step 19).

## 10.10 Done checklist
- [ ] Every trap rejected, every equivalent pair accepted on the labelled set (or documented exceptions in `PROGRESS.md`)
- [ ] False-accept/false-block counts per tier recorded
- [ ] Commit `step 10: equivalence`
