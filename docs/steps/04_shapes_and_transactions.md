# Step 04 — Query shapes and order transactions

**Goal (end state):** `app/shapes.py` defines **8 report shapes** and **5 protected order transactions**.
Each report shape renders concrete SQL from a seeded sampler and runs on `qc_live` in a sane time;
each order transaction runs and commits. The frozen clock is read from `meta`. The expected-outcome
table (section 4.6) is written into the mock-candidate fixtures.

**Prerequisites:** steps 02–03.

## 4.1 Data structures

```python
@dataclass
class Shape:
    id: str                     # snake_case, unique
    name: str                   # human title
    question: str               # plain-English question (also used by the LLM prompt)
    sql: str                    # %(name)s placeholders; starts with /* dpa:shape=<id> */
    sampler: Callable[[Connection, np.random.Generator, datetime], dict]
    mock_candidates: list[dict] # {"kind","sql","rationale","bad": bool, "expect": str}
    kind: str = "report"

@dataclass
class TxnDef:
    id: str                     # TxnType
    weight: float               # share of the mix
    description: str
```
`REPORT_SHAPES: dict[str, Shape]` (ordered), `ORDER_TXNS: dict[str, TxnDef]`.

Helper functions:
* `load_as_of(conn) -> datetime` (UTC): `SELECT value FROM meta WHERE key='as_of'`; cache per process.
* `as_of_literal(as_of) -> str` = `as_of.isoformat()`.
* `sample_params(shape, conn, rng, as_of) -> dict`; `render(shape, conn, params) -> str` (uses `pg.render_sql`).
* Parameter pools are loaded **once per process** and cached (`@lru_cache` on a pool-loader keyed by dbname): 500 customer ids
  `SELECT customer_id FROM orders TABLESAMPLE SYSTEM (0.5) LIMIT 500`; 50 store ids; 6 cities; etc.
* Samplers draw from pools with the provided `rng` (never `random`).

## 4.2 Report shapes (exact SQL)

All placeholders are `%(name)s`. `ts` below means a timestamptz literal rendered from `as_of`.

**R1 `recent_orders`** — params `customer_id`, `as_of`
```sql
/* dpa:shape=recent_orders */
SELECT order_id, store_id, total, created_at FROM orders
WHERE customer_id = %(customer_id)s AND created_at >= %(as_of)s::timestamptz - interval '30 days'
  AND created_at <= %(as_of)s::timestamptz
ORDER BY created_at DESC LIMIT 20
```
**R2 `delivered_yesterday`** — params `as_of`
```sql
/* dpa:shape=delivered_yesterday */
SELECT store_id, count(*) AS delivered, sum(total) AS revenue FROM orders
WHERE DATE(delivered_at) = (%(as_of)s::timestamptz)::date - 1
GROUP BY store_id ORDER BY store_id
```
**R3 `stockouts_15m`** — params `as_of`
```sql
/* dpa:shape=stockouts_15m */
SELECT s.store_id, p.product_id, p.name FROM store_stock s JOIN products p ON p.product_id = s.product_id
WHERE s.qty = 0 AND s.updated_at >= %(as_of)s::timestamptz - interval '15 minutes'
  AND s.updated_at <= %(as_of)s::timestamptz
ORDER BY s.store_id, p.product_id
```
**R4 `hourly_sales`** — params `as_of`
```sql
/* dpa:shape=hourly_sales */
SELECT store_id, date_trunc('hour', created_at) AS hr, count(*) AS orders, sum(total) AS revenue
FROM orders WHERE created_at >= %(as_of)s::timestamptz - interval '24 hours'
  AND created_at < %(as_of)s::timestamptz
GROUP BY store_id, hr ORDER BY hr, store_id
```
**R5 `open_order_customers`** — params `city`
```sql
/* dpa:shape=open_order_customers */
SELECT c.customer_id, c.name FROM customers c
WHERE c.city = %(city)s
  AND EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id
              AND o.status IN ('placed','packing'))
ORDER BY c.customer_id
```
**R6 `payout_report`** — params `start`, `end` (7-day window inside the 30 days)
```sql
/* dpa:shape=payout_report */
SELECT store_id, sum(total) AS payout FROM (
  SELECT DISTINCT order_id, store_id, total FROM orders
  WHERE status = 'delivered' AND delivered_at >= %(start)s::timestamptz AND delivered_at < %(end)s::timestamptz
  ORDER BY order_id) t
GROUP BY store_id ORDER BY store_id
```
**R7 `top_products_store`** — params `store_id`, `as_of`
```sql
/* dpa:shape=top_products_store */
SELECT oi.product_id, sum(oi.qty) AS units FROM order_items oi JOIN orders o ON o.order_id = oi.order_id
WHERE o.store_id = %(store_id)s AND o.created_at >= %(as_of)s::timestamptz - interval '7 days'
  AND o.created_at < %(as_of)s::timestamptz
GROUP BY oi.product_id ORDER BY units DESC, oi.product_id LIMIT 20
```
**R8 `picker_queue`** — params `store_id`
```sql
/* dpa:shape=picker_queue */
SELECT order_id, created_at, status FROM orders
WHERE store_id = %(store_id)s AND status IN ('placed','packing') ORDER BY created_at LIMIT 50
```
Each shape's `question` is the plain-English sentence ("Which customers in <city> have an open order?").

## 4.3 Protected order transactions (used by `workload.py` in step 11)

Define SQL strings in `shapes.py` (or `workload.py`) with tags. Each transaction runs inside one DB
transaction (`autocommit=False`, commit at the end; rollback on error).

| Type | Weight | Statements |
|---|---|---|
| `place_order` | 0.45 | `INSERT INTO orders(customer_id,store_id,status,total,payment_mode,created_at) VALUES (…,'placed',…,now()) RETURNING order_id`; then for 2–4 lines: `INSERT INTO order_items(order_id,product_id,qty,unit_price) VALUES (…)` and `UPDATE store_stock SET qty = qty - %(q)s, updated_at = now() WHERE store_id = %(s)s AND product_id = %(p)s AND qty >= %(q)s` |
| `update_status` | 0.20 | pick an id from the active pool; `UPDATE orders SET status = %(new)s, delivered_at = CASE WHEN %(new)s = 'delivered' THEN now() ELSE delivered_at END WHERE order_id = %(id)s`. Transition order: placed→packing→out_for_delivery→delivered |
| `order_status` | 0.15 | `SELECT order_id,status,total,created_at,delivered_at FROM orders WHERE order_id = %(id)s` |
| `stock_decrement` | 0.15 | single `UPDATE store_stock SET qty = qty - %(q)s, updated_at = now() WHERE store_id = %(s)s AND product_id = %(p)s AND qty >= %(q)s` |
| `picker_queue_read` | 0.05 | R8 query for a random store (tagged `dpa:txn=picker_queue_read`) |

Rules:
* If the conditional `UPDATE` affects 0 rows in `place_order`, roll back and count it as `oos` (still record the latency under `place_order`).
* Prevent artificial out-of-stock: `workload.prepare_clone` (step 11) sets `qty = 1000000` for the hot SKU set before each block.
* Every statement text begins with `/* dpa:txn=<type> */`.

## 4.4 Candidate fixtures for the **mock LLM** (`shape.mock_candidates`)
Implement each as a dict with `bad` flag and `expect` (qualitative expected pipeline outcome). Index DDL must not include `CONCURRENTLY`.

| Shape | Candidate | kind | expect |
|---|---|---|---|
| recent_orders | `CREATE INDEX ON orders (customer_id, created_at DESC)` | index | selected (large speedup; created_at/customer_id never updated) |
| recent_orders | `CREATE INDEX ON orders (customer_id)` | index | kept, slower than composite (sort remains) → dropped in race |
| recent_orders | `CREATE INDEX ON orders (status)` | index | skipped_noplan (plan unchanged) |
| recent_orders | `ANALYZE orders` | stats | skipped or no speedup |
| delivered_yesterday | correct range rewrite: `… WHERE delivered_at >= ((%(as_of)s::timestamptz)::date - 1) AND delivered_at < (%(as_of)s::timestamptz)::date …` rendered with literals | rewrite | tested; speedup only if an index on `delivered_at` exists (else equal) |
| delivered_yesterday | WRONG rewrite using `AT TIME ZONE 'UTC'` bounds | rewrite | rejected_equiv (tier 2, time zone Asia/Kolkata) |
| delivered_yesterday | `CREATE INDEX ON orders (DATE(delivered_at))` | index | rejected_invalid_ddl (`DATE(timestamptz)` is **STABLE**, not IMMUTABLE → PostgreSQL refuses it) |
| delivered_yesterday | `CREATE INDEX ON orders (delivered_at)` | index | speeds the correct rewrite only; guard likely FAIL at flash (delivered_at updated by `update_status`) |
| stockouts_15m | `CREATE INDEX ON store_stock (updated_at)` | index | failed_guard (updated on every decrement; kills HOT) |
| stockouts_15m | `CREATE INDEX ON store_stock (store_id, product_id) WHERE qty = 0` | index | failed_guard (qty is in the predicate) → expected final decision **block** |
| hourly_sales | `CREATE INDEX ON orders (created_at)` | index | selected |
| hourly_sales | `CREATE INDEX ON orders (store_id, created_at)` | index | passes; slower or equal |
| open_order_customers | join **without** DISTINCT | rewrite | rejected_equiv (duplicates; tier 2 counterexample) |
| open_order_customers | join **with** DISTINCT | rewrite | tested |
| open_order_customers | `CREATE INDEX ON orders (customer_id) WHERE status IN ('placed','packing')` | index | failed_guard under flash (status updated constantly) |
| payout_report | rewrite removing `DISTINCT` and subquery `ORDER BY` | rewrite | tested, small speedup |
| payout_report | rewrite also dropping `status = 'delivered'` | rewrite | rejected_equiv |
| payout_report | `CREATE INDEX ON orders (delivered_at)` | index | failed_guard under flash |
| top_products_store | `CREATE INDEX ON order_items (order_id)` | index | selected (large speedup; costs inserts → must pass guard) |
| top_products_store | `CREATE INDEX ON orders (store_id, created_at)` | index | passes |
| picker_queue | `CREATE INDEX ON orders (store_id, created_at) WHERE status IN ('placed','packing')` | index | failed_guard (status updates) |
| picker_queue | `CREATE INDEX ON orders (store_id, created_at)` | index | selected or tied |

These expectations are **hypotheses** to test, not hard-coded results. Use them in an integration test that only asserts
**invariants** (e.g. the wrong-time-zone rewrite is never selected; an invalid-DDL candidate is never applied).

## 4.5 Samplers and determinism
* `sample_params(shape, conn, rng, as_of)` must return identical params for the same `(rng state)`.
* R6 window: `start = as_of - interval '8 days'`, `end = as_of - interval '1 day'`; sampled start offset ±2 days.

## 4.6 Tests (`tests/test_shapes.py`, `@pytest.mark.db`)
* `test_all_shapes_render_and_run`: each shape runs on `qc_live` with a 60 s timeout; returns ≥ 0 rows; `R2` returns > 0 rows; `R3` returns > 0 rows.
* `test_samplers_deterministic`: same seed twice ⇒ same params.
* `test_txn_sql_tags`: every SQL string starts with the tag comment.
* `test_as_of_loaded`.
* `test_mock_fixture_coverage`: each shape has ≥ 2 mock candidates and at least one `bad=True`.

## 4.7 Baseline timing table (record in `docs/PROGRESS.md`)
For each shape run `EXPLAIN (ANALYZE, BUFFERS)` once and record time, plan top node, buffers. Slow shapes
(≥ 50 ms at scale 1) are the tuning targets. If a shape is fast at your scale, increase scale or document that it is a control.

## 4.8 Done checklist
- [x] 8 shapes + 5 transactions implemented; fixtures list complete
- [x] `make test-db` green; baseline table recorded
- [ ] Commit `step 04: shapes and transactions`


## Implementation clarifications

- Parameter pool block sampling uses REPEATABLE(1234) and stable ID ordering.
  Clear process caches after reseeding or replacing a database.
- Clamp the payout window start to at most as_of minus seven days; otherwise
  +2-day jitter puts the window end in the future, conflicting with section 4.2.
- Added a bad hourly-window rewrite and a bad missing-store-filter rewrite for
  the two shapes with no bad candidate in the supplied fixture table.
- Transactions are exercised on temporary tables in the live session, preserving
  seeded public rows. Full pipeline equivalence/guard verdict tests await later steps.
- Commit waits for the user's chosen message.
