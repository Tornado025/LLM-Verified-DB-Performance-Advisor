# Step 02 — Schema and seeding

**Goal (end state):** `make seed` creates `qc_golden` (seeded, ANALYZE'd, never connected afterwards)
and `qc_live` (clone of golden). Verification queries confirm the intended skew, status mix, time spread,
and the frozen-clock `meta.as_of`. Re-running `make seed` is safe (it drops and rebuilds).

**Prerequisites:** step 01 acceptance passed.

## 2.1 Design decisions to remember

* Only **primary keys** (and `products.sku UNIQUE`) exist at first. There must be something to tune.
* `orders.created_at`, `orders.delivered_at`, `products.created_at`, `store_stock.updated_at` are
  **`timestamptz`** on purpose (time-zone trap for rewrite class 1).
* Foreign keys are declared (they do not create indexes in PostgreSQL). `order_items.order_id` therefore has **no index**
  — a deliberate tuning opportunity.
* Report shapes use the frozen clock `as_of` (stored in `meta`), so data never goes stale.

## 2.2 `backend/sql/schema.sql`

```sql
CREATE TABLE meta        (key text PRIMARY KEY, value text NOT NULL);
CREATE TABLE stores      (store_id int PRIMARY KEY, name text NOT NULL, city text NOT NULL,
                          lat double precision, lon double precision);
CREATE TABLE categories  (category_id int PRIMARY KEY, name text NOT NULL);
CREATE TABLE products    (product_id int PRIMARY KEY, sku text UNIQUE NOT NULL, name text NOT NULL,
                          category_id int NOT NULL REFERENCES categories, brand text,
                          price numeric(10,2) NOT NULL, is_active boolean NOT NULL DEFAULT true,
                          created_at timestamptz NOT NULL);
CREATE TABLE store_stock (store_id int NOT NULL REFERENCES stores, product_id int NOT NULL REFERENCES products,
                          qty int NOT NULL, reserved int NOT NULL DEFAULT 0, updated_at timestamptz NOT NULL,
                          PRIMARY KEY (store_id, product_id));
CREATE TABLE customers   (customer_id int PRIMARY KEY, name text NOT NULL, phone text, city text NOT NULL,
                          created_at timestamptz NOT NULL);
CREATE TABLE riders      (rider_id int PRIMARY KEY, store_id int NOT NULL REFERENCES stores,
                          name text NOT NULL, status text NOT NULL);
CREATE TABLE orders      (order_id bigserial PRIMARY KEY, customer_id int NOT NULL REFERENCES customers,
                          store_id int NOT NULL REFERENCES stores, status text NOT NULL,
                          total numeric(10,2) NOT NULL, payment_mode text NOT NULL,
                          created_at timestamptz NOT NULL, delivered_at timestamptz,
                          rider_id int REFERENCES riders);
CREATE TABLE order_items (order_item_id bigserial PRIMARY KEY, order_id bigint NOT NULL REFERENCES orders,
                          product_id int NOT NULL REFERENCES products, qty int NOT NULL,
                          unit_price numeric(10,2) NOT NULL);
```
Valid `orders.status` values: `placed`, `packing`, `out_for_delivery`, `delivered`, `cancelled`.
Valid `payment_mode`: `upi`, `card`, `cod`, `wallet`.

## 2.3 Scale constants (`seed.py --scale S`, integer ≥ 1)

| Entity | Count |
|---|---|
| stores | 50 |
| categories | 40 |
| products | 2000 |
| store_stock | ~80 % of (stores × products) ≈ 80 000 |
| customers | 20 000 × S |
| riders | 500 |
| orders | 300 000 × S |
| order_items | 1–5 per order (avg 3) ≈ 900 000 × S |

Rough sizes: S = 1 ≈ 0.25–0.4 GB, S = 10 ≈ 3–4 GB, S = 30 ≈ 10 GB. Choose S so the database is larger than
`shared_buffers` (256 MB) and ideally larger than RAM available to Docker for final experiments.

## 2.4 `seed.py` flow (implement in this order)

1. Parse args: `--scale`, `--seed` (default 1234; used with `SELECT setseed(...)`), `--drop` (default true), `--live-only`.
2. Connect to `MAINT_DB` with the admin role. If `--drop`: terminate connections to `qc_live` and
   `qc_golden`, `DROP DATABASE IF EXISTS qc_live; DROP DATABASE IF EXISTS qc_golden;`.
3. `CREATE DATABASE qc_golden;` connect to it; run `CREATE EXTENSION pg_stat_statements; CREATE EXTENSION hypopg;`
   then `schema.sql`.
4. Call `SELECT setseed(<seed>/1000000.0)` **in the same session** that runs the inserts (a single connection).
5. Run the inserts below (all ints formatted into the SQL by Python — no user input reaches SQL).
6. Insert `meta('as_of', <seed time>)` where the seed time is `date_trunc('day', now())` at seed time + 12 h
   (noon UTC today), and `meta('scale', S)`, `meta('seed', seed)`, `meta('seeded_at', now())`.
7. Create no extra indexes. `VACUUM (ANALYZE)` every table. Run `CHECKPOINT`.
8. Close **every** connection to golden. `CREATE DATABASE qc_live TEMPLATE qc_golden;`.
9. Print the verification report (section 2.6) from `qc_live`.

## 2.5 Insert statements

```sql
INSERT INTO categories SELECT g, 'category_' || g FROM generate_series(1, 40) g;

INSERT INTO stores
SELECT g, 'store_' || g,
       (ARRAY['Bengaluru','Mumbai','Delhi','Hyderabad','Chennai','Pune'])[1 + (g % 6)],
       12.80 + random() * 0.40, 77.40 + random() * 0.40
FROM generate_series(1, 50) g;

INSERT INTO products
SELECT g, 'SKU' || lpad(g::text, 6, '0'), 'product_' || g, 1 + (g % 40), 'brand_' || (g % 120),
       round((10 + random() * 490)::numeric, 2), random() > 0.03,
       :as_of::timestamptz - random() * interval '400 days'
FROM generate_series(1, 2000) g;

INSERT INTO store_stock
SELECT s.store_id, p.product_id, (random() * 200)::int, 0, :as_of::timestamptz - random() * interval '2 days'
FROM stores s JOIN products p ON random() < 0.8;

-- a few stock-outs updated in the 15 minutes before as_of  (used by stockouts_15m)
UPDATE store_stock SET qty = 0, updated_at = :as_of::timestamptz - random() * interval '14 minutes'
WHERE random() < 0.005;

INSERT INTO customers
SELECT g, 'customer_' || g, '9' || lpad((random() * 999999999)::bigint::text, 9, '0'),
       (ARRAY['Bengaluru','Mumbai','Delhi','Hyderabad','Chennai','Pune'])[1 + (g % 6)],
       :as_of::timestamptz - random() * interval '700 days'
FROM generate_series(1, :n_customers) g;

INSERT INTO riders SELECT g, 1 + (g % 50), 'rider_' || g, 'idle' FROM generate_series(1, 500) g;

WITH base AS MATERIALIZED (
  SELECT 1 + floor(power(random(), 2.2) * :n_customers)::int AS customer_id,   -- heavy buyers
         1 + floor(random() * 50)::int AS store_id,
         :as_of::timestamptz - random() * interval '30 days' AS created_at,
         random() AS r1, random() AS r2, random() AS r3
  FROM generate_series(1, :n_orders)
)
INSERT INTO orders (customer_id, store_id, status, total, payment_mode, created_at, delivered_at, rider_id)
SELECT customer_id, store_id,
       CASE WHEN created_at > :as_of::timestamptz - interval '3 hours' AND r1 < 0.9
              THEN (ARRAY['placed','packing','out_for_delivery'])[1 + floor(r2 * 3)::int]
            WHEN r1 > 0.97 THEN 'cancelled'
            ELSE 'delivered' END,
       round((50 + r3 * 1450)::numeric, 2),
       (ARRAY['upi','card','cod','wallet'])[1 + floor(r2 * 4)::int],
       created_at,
       CASE WHEN created_at <= :as_of::timestamptz - interval '3 hours' AND r1 <= 0.97
            THEN created_at + (10 + r2 * 30) * interval '1 minute' END,
       1 + floor(r3 * 500)::int
FROM base;

INSERT INTO order_items (order_id, product_id, qty, unit_price)
SELECT o.order_id, 1 + floor(power(random(), 3) * 2000)::int, 1 + floor(random() * 3)::int,
       round((10 + random() * 490)::numeric, 2)
FROM orders o CROSS JOIN LATERAL generate_series(1, 1 + floor(random() * 5)::int);
```
Notes:
* `:as_of` is a quoted ISO literal substituted by Python, e.g. `'2026-10-23T12:00:00+00:00'`; `:n_customers`, `:n_orders` are ints.
* `MATERIALIZED` is required: a CTE with volatile functions must not be inlined, or `r1/r2/r3` would be re-evaluated per reference.
* Orders in the last 3 h that are not delivered have `delivered_at IS NULL`. Cancelled orders have `delivered_at IS NULL`.
* `status IN ('placed','packing')` should be roughly **0.2–0.5 %** of orders (selective, so partial indexes matter).
* Items skew: `power(random(), 3)` makes low product IDs hot (top 10 % of products ≈ 46 % of lines).

## 2.6 Verification report (must print and assert)

| Check | SQL | Expected |
|---|---|---|
| Row counts | `SELECT count(*) FROM orders` etc. | match section 2.3 within 2 % |
| Status mix | `SELECT status, count(*) FROM orders GROUP BY 1` | delivered ≈ 96 %, cancelled ≈ 3 %, active (placed/packing/out_for_delivery) 0.1–0.5 % |
| Active fraction | `SELECT avg((status IN ('placed','packing'))::int) FROM orders` | 0.001–0.005 |
| Time spread | `SELECT min(created_at), max(created_at) FROM orders` | ~30 days ending at `as_of` |
| Delivered yesterday | `SELECT count(*) FROM orders WHERE DATE(delivered_at) = (SELECT value::timestamptz::date FROM meta WHERE key='as_of') - 1` | > 1000 at S = 1 |
| Customer skew | share of orders from the top 1 % of customers | > 10 % |
| Product skew | share of items on `product_id <= 200` | > 35 % |
| Stock-outs | `SELECT count(*) FROM store_stock WHERE qty = 0 AND updated_at >= as_of - interval '15 minutes'` | > 100 |
| No extra indexes | `SELECT indexname FROM pg_indexes WHERE schemaname='public'` | only PKs + `products_sku_key` |
| Extensions in live | `\dx` on `qc_live` | both present |
| Golden idle | `SELECT count(*) FROM pg_stat_activity WHERE datname='qc_golden'` | 0 |

## 2.7 Clone helper used later
Provide `seed.py --live-only` which drops and recreates only `qc_live` from golden. `make reset-live` = that.

## 2.8 Disk and time
Print the database size (`pg_database_size`) for golden. Report seed duration. Expect minutes at S = 1.

## 2.9 Tests (`backend/tests/test_seed.py`, `@pytest.mark.db`, small scale 1)
* `test_row_counts`, `test_status_mix`, `test_no_secondary_indexes`, `test_meta_as_of_present`, `test_golden_idle`.

## 2.10 Done checklist
- [x] `make up && make seed` succeeds, report printed and saved to `docs/PROGRESS.md`
- [x] All verification checks pass
- [x] `make test-db` for `test_seed.py` green
- [ ] Commit `step 02: schema and seeding`

## 2.11 Pitfalls
* `CREATE DATABASE ... TEMPLATE` fails if **any** session is connected to golden → close connections before cloning.
* Do not run `ANALYZE` only on `public` implicitly — run `VACUUM (ANALYZE)` explicitly; clones copy statistics from the template's pg_statistic.
  (Statistics are stored in the database, so a template copy **does** carry them; the "no statistics" gotcha applies to **dump/restore**. Still run `ANALYZE` on every clone as a defensive rule.)
* `random()` is session-seeded; calling `setseed` on a different connection than the inserts has no effect.

## Implementation clarifications

- Materialize one item-count draw per order before the lateral series. The sample
  uncorrelated `LATERAL generate_series(... random() ...)` may evaluate its count
  only once, giving every order the same item count.
- Derive delivered_at from the classified status so recent delivered orders also
  have a delivery timestamp, while active and cancelled orders have NULL.
- Golden disables direct connections after seeding; CREATE DATABASE TEMPLATE
  still clones it from the maintenance connection. Analyze every live clone.
- Scale 1 measured about 120 MiB in the initial run, below shared_buffers. Use
  `make seed SCALE=3` or higher and check the measured size for performance work.
- Per the user's instruction, wait for their commit message before committing.
