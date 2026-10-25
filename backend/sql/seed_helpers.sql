-- Values use psycopg bind parameters. All randomness stays in the seeded session.
INSERT INTO categories SELECT g, 'category_' || g FROM generate_series(1, 40) g;

INSERT INTO stores
SELECT g, 'store_' || g,
       (ARRAY['Bengaluru','Mumbai','Delhi','Hyderabad','Chennai','Pune'])[1 + (g %% 6)],
       12.80 + random() * 0.40, 77.40 + random() * 0.40
FROM generate_series(1, 50) g;

INSERT INTO products
SELECT g, 'SKU' || lpad(g::text, 6, '0'), 'product_' || g, 1 + (g %% 40),
       'brand_' || (g %% 120), round((10 + random() * 490)::numeric, 2), random() > 0.03,
       %(as_of)s::timestamptz - random() * interval '400 days'
FROM generate_series(1, 2000) g;

INSERT INTO store_stock
SELECT s.store_id, p.product_id, (random() * 200)::int, 0,
       %(as_of)s::timestamptz - random() * interval '2 days'
FROM stores s JOIN products p ON random() < 0.8;

UPDATE store_stock
SET qty = 0, updated_at = %(as_of)s::timestamptz - random() * interval '14 minutes'
WHERE random() < 0.005;

INSERT INTO customers
SELECT g, 'customer_' || g, '9' || lpad((random() * 999999999)::bigint::text, 9, '0'),
       (ARRAY['Bengaluru','Mumbai','Delhi','Hyderabad','Chennai','Pune'])[1 + (g %% 6)],
       %(as_of)s::timestamptz - random() * interval '700 days'
FROM generate_series(1, %(n_customers)s) g;

INSERT INTO riders
SELECT g, 1 + (g %% 50), 'rider_' || g, 'idle' FROM generate_series(1, 500) g;

WITH base AS MATERIALIZED (
  SELECT 1 + floor(power(random(), 2.2) * %(n_customers)s)::int AS customer_id,
         1 + floor(random() * 50)::int AS store_id,
         %(as_of)s::timestamptz - random() * interval '30 days' AS created_at,
         random() AS r1, random() AS r2, random() AS r3
  FROM generate_series(1, %(n_orders)s)
), classified AS MATERIALIZED (
  SELECT *,
         CASE WHEN created_at > %(as_of)s::timestamptz - interval '3 hours' AND r1 < 0.9
                THEN (ARRAY['placed','packing','out_for_delivery'])[1 + floor(r2 * 3)::int]
              WHEN r1 > 0.97 THEN 'cancelled'
              ELSE 'delivered' END AS status
  FROM base
)
INSERT INTO orders (customer_id, store_id, status, total, payment_mode,
                    created_at, delivered_at, rider_id)
SELECT customer_id, store_id, status, round((50 + r3 * 1450)::numeric, 2),
       (ARRAY['upi','card','cod','wallet'])[1 + floor(r2 * 4)::int], created_at,
       CASE WHEN status = 'delivered'
            THEN created_at + (10 + r2 * 30) * interval '1 minute' END,
       1 + floor(r3 * 500)::int
FROM classified;

-- Materialize one draw per order: an uncorrelated LATERAL random() can run only once.
WITH item_counts AS MATERIALIZED (
  SELECT order_id, 1 + floor(random() * 5)::int AS n_items
  FROM orders ORDER BY order_id
)
INSERT INTO order_items (order_id, product_id, qty, unit_price)
SELECT o.order_id, 1 + floor(power(random(), 3) * 2000)::int,
       1 + floor(random() * 3)::int, round((10 + random() * 490)::numeric, 2)
FROM item_counts o CROSS JOIN LATERAL generate_series(1, o.n_items);
