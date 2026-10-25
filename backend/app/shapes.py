"""Seeded report shapes, candidate hypotheses and protected commerce transactions."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import numpy as np
import psycopg
from psycopg.pq import TransactionStatus

from app.errors import BadRequest, Conflict
from app.pg import connect, render_sql


@dataclass
class Shape:
    """A parameterized report and its unverified mock candidate hypotheses."""

    id: str
    name: str
    question: str
    sql: str
    sampler: Callable[[psycopg.Connection, np.random.Generator, datetime], dict]
    mock_candidates: list[dict]
    kind: str = "report"


@dataclass
class TxnDef:
    """Protected transaction identity and workload mix weight."""

    id: str
    weight: float
    description: str


@lru_cache
def _clock(dbname: str) -> datetime:
    with connect(dbname) as conn:
        row = conn.execute("/* dpa:sys */ SELECT value FROM meta WHERE key='as_of'").fetchone()
    if row is None:
        raise BadRequest("Frozen clock is missing")
    value = datetime.fromisoformat(row[0])
    if value.tzinfo is None:
        raise BadRequest("Frozen clock must have a timezone")
    return value.astimezone(UTC)


def load_as_of(conn: psycopg.Connection) -> datetime:
    """Read the seeded UTC clock once per database per process."""
    return _clock(conn.info.dbname)


def as_of_literal(as_of: datetime) -> str:
    """Return an aware timestamp literal for trusted SQL rendering."""
    if as_of.tzinfo is None:
        raise BadRequest("Frozen clock must have a timezone")
    return as_of.isoformat()


@lru_cache
def load_pools(dbname: str) -> dict[str, tuple]:
    """Cache stable pools; REPEATABLE makes the initial block sample reproducible."""
    with connect(dbname) as conn:
        customers = tuple(
            row[0]
            for row in conn.execute(
                "/* dpa:sys */ SELECT customer_id FROM orders TABLESAMPLE SYSTEM (0.5) "
                "REPEATABLE (1234) ORDER BY order_id LIMIT 500"
            )
        )
        if not customers:
            customers = tuple(
                row[0]
                for row in conn.execute(
                    "/* dpa:sys */ SELECT customer_id FROM customers ORDER BY customer_id LIMIT 500"
                )
            )
        stores = tuple(
            row[0]
            for row in conn.execute("/* dpa:sys */ SELECT store_id FROM stores ORDER BY store_id")
        )
        cities = tuple(
            row[0]
            for row in conn.execute(
                "/* dpa:sys */ SELECT DISTINCT city FROM customers ORDER BY city"
            )
        )
        active = tuple(
            row[0]
            for row in conn.execute(
                "/* dpa:sys */ SELECT order_id FROM orders WHERE status IN "
                "('placed','packing','out_for_delivery') ORDER BY order_id LIMIT 5000"
            )
        )
    if not customers or not stores or not cities:
        raise BadRequest("Parameter pools are empty; seed the database first")
    return {"customers": customers, "stores": stores, "cities": cities, "active_orders": active}


def clear_caches() -> None:
    """Invalidate frozen clocks and pools after a reseed or database replacement."""
    _clock.cache_clear()
    load_pools.cache_clear()


def _sample(kind: str, conn: psycopg.Connection, rng: np.random.Generator, as_of: datetime) -> dict:
    params: dict[str, Any] = {"as_of": as_of_literal(as_of)}
    if kind in {"customer", "store", "city"}:
        pools = load_pools(conn.info.dbname)
        pool, key = {
            "customer": ("customers", "customer_id"),
            "store": ("stores", "store_id"),
            "city": ("cities", "city"),
        }[kind]
        values = pools[pool]
        params[key] = values[int(rng.integers(len(values)))]
    if kind == "window":
        start = as_of - timedelta(days=8) + timedelta(days=int(rng.integers(-2, 3)))
        # Clip the +2-day draw so the seven-day report never extends past as_of.
        start = min(start, as_of - timedelta(days=7))
        return {"start": as_of_literal(start), "end": as_of_literal(start + timedelta(days=7))}
    if kind == "city":
        return {"city": params["city"]}
    return params


def sample_params(
    shape: Shape, conn: psycopg.Connection, rng: np.random.Generator, as_of: datetime
) -> dict:
    """Draw parameters using only the caller's explicit generator."""
    return shape.sampler(conn, rng, as_of)


def render(shape: Shape, conn: psycopg.Connection, params: dict) -> str:
    """Render a trusted report with escaped sampler values."""
    return render_sql(conn, shape.sql, params)


REPORT_SHAPES: dict[str, Shape] = {}


REPORT_SHAPES["recent_orders"] = Shape(
    "recent_orders",
    "Recent Orders",
    "What are this customer’s 20 most recent orders in the last 30 days?",
    """/* dpa:shape=recent_orders */
SELECT order_id, store_id, total, created_at FROM orders
WHERE customer_id = %(customer_id)s AND created_at >= %(as_of)s::timestamptz - interval '30 days'
  AND created_at <= %(as_of)s::timestamptz
ORDER BY created_at DESC LIMIT 20
""",
    lambda conn, rng, as_of: _sample("customer", conn, rng, as_of),
    [],
)

REPORT_SHAPES["delivered_yesterday"] = Shape(
    "delivered_yesterday",
    "Delivered Yesterday",
    "How many orders were delivered yesterday at each store, and what was their revenue?",
    """/* dpa:shape=delivered_yesterday */
SELECT store_id, count(*) AS delivered, sum(total) AS revenue FROM orders
WHERE DATE(delivered_at) = (%(as_of)s::timestamptz)::date - 1
GROUP BY store_id ORDER BY store_id
""",
    lambda conn, rng, as_of: _sample("clock", conn, rng, as_of),
    [],
)

REPORT_SHAPES["stockouts_15m"] = Shape(
    "stockouts_15m",
    "Stockouts 15M",
    "Which products became out of stock in each store in the last 15 minutes?",
    """/* dpa:shape=stockouts_15m */
SELECT s.store_id, p.product_id, p.name FROM store_stock s
JOIN products p ON p.product_id = s.product_id
WHERE s.qty = 0 AND s.updated_at >= %(as_of)s::timestamptz - interval '15 minutes'
  AND s.updated_at <= %(as_of)s::timestamptz
ORDER BY s.store_id, p.product_id
""",
    lambda conn, rng, as_of: _sample("clock", conn, rng, as_of),
    [],
)

REPORT_SHAPES["hourly_sales"] = Shape(
    "hourly_sales",
    "Hourly Sales",
    "What were hourly order counts and revenue by store in the last 24 hours?",
    """/* dpa:shape=hourly_sales */
SELECT store_id, date_trunc('hour', created_at) AS hr, count(*) AS orders, sum(total) AS revenue
FROM orders WHERE created_at >= %(as_of)s::timestamptz - interval '24 hours'
  AND created_at < %(as_of)s::timestamptz
GROUP BY store_id, hr ORDER BY hr, store_id
""",
    lambda conn, rng, as_of: _sample("clock", conn, rng, as_of),
    [],
)

REPORT_SHAPES["open_order_customers"] = Shape(
    "open_order_customers",
    "Open Order Customers",
    "Which customers in this city have an open order?",
    """/* dpa:shape=open_order_customers */
SELECT c.customer_id, c.name FROM customers c
WHERE c.city = %(city)s
  AND EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id
              AND o.status IN ('placed','packing'))
ORDER BY c.customer_id
""",
    lambda conn, rng, as_of: _sample("city", conn, rng, as_of),
    [],
)

REPORT_SHAPES["payout_report"] = Shape(
    "payout_report",
    "Payout Report",
    "What are delivered-order payouts by store for this seven-day window?",
    """/* dpa:shape=payout_report */
SELECT store_id, sum(total) AS payout FROM (
  SELECT DISTINCT order_id, store_id, total FROM orders
  WHERE status = 'delivered' AND delivered_at >= %(start)s::timestamptz
    AND delivered_at < %(end)s::timestamptz
  ORDER BY order_id) t
GROUP BY store_id ORDER BY store_id
""",
    lambda conn, rng, as_of: _sample("window", conn, rng, as_of),
    [],
)

REPORT_SHAPES["top_products_store"] = Shape(
    "top_products_store",
    "Top Products Store",
    "Which 20 products sold the most units at this store in the last seven days?",
    """/* dpa:shape=top_products_store */
SELECT oi.product_id, sum(oi.qty) AS units FROM order_items oi
JOIN orders o ON o.order_id = oi.order_id
WHERE o.store_id = %(store_id)s AND o.created_at >= %(as_of)s::timestamptz - interval '7 days'
  AND o.created_at < %(as_of)s::timestamptz
GROUP BY oi.product_id ORDER BY units DESC, oi.product_id LIMIT 20
""",
    lambda conn, rng, as_of: _sample("store", conn, rng, as_of),
    [],
)

REPORT_SHAPES["picker_queue"] = Shape(
    "picker_queue",
    "Picker Queue",
    "Which orders are awaiting picking at this store?",
    """/* dpa:shape=picker_queue */
SELECT order_id, created_at, status FROM orders
WHERE store_id = %(store_id)s AND status IN ('placed','packing') ORDER BY created_at LIMIT 50
""",
    lambda conn, rng, as_of: _sample("store", conn, rng, as_of),
    [],
)


def _candidate(shape_id: str, kind: str, sql: str, rationale: str, bad: bool, expect: str) -> None:
    REPORT_SHAPES[shape_id].mock_candidates.append(
        {"kind": kind, "sql": sql, "rationale": rationale, "bad": bad, "expect": expect}
    )


# Expectations are hypotheses. Neither bad nor expect can authorize an application.
for shape_id, sql, bad, expect in [
    ("recent_orders", "CREATE INDEX ON orders (customer_id, created_at DESC)", False, "selected"),
    ("recent_orders", "CREATE INDEX ON orders (customer_id)", False, "dropped_race"),
    ("recent_orders", "CREATE INDEX ON orders (status)", True, "skipped_noplan"),
    (
        "delivered_yesterday",
        "CREATE INDEX ON orders (DATE(delivered_at))",
        True,
        "rejected_invalid_ddl",
    ),
    (
        "delivered_yesterday",
        "CREATE INDEX ON orders (delivered_at)",
        True,
        "failed_guard under flash; helps the range rewrite only",
    ),
    ("stockouts_15m", "CREATE INDEX ON store_stock (updated_at)", True, "failed_guard"),
    (
        "stockouts_15m",
        "CREATE INDEX ON store_stock (store_id, product_id) WHERE qty = 0",
        True,
        "failed_guard; final block",
    ),
    ("hourly_sales", "CREATE INDEX ON orders (created_at)", False, "selected"),
    (
        "hourly_sales",
        "CREATE INDEX ON orders (store_id, created_at)",
        False,
        "passes; slower or equal",
    ),
    (
        "open_order_customers",
        "CREATE INDEX ON orders (customer_id) WHERE status IN ('placed','packing')",
        True,
        "failed_guard under flash",
    ),
    ("payout_report", "CREATE INDEX ON orders (delivered_at)", True, "failed_guard under flash"),
    (
        "top_products_store",
        "CREATE INDEX ON order_items (order_id)",
        False,
        "selected if guard passes",
    ),
    ("top_products_store", "CREATE INDEX ON orders (store_id, created_at)", False, "passes"),
    (
        "picker_queue",
        "CREATE INDEX ON orders (store_id, created_at) WHERE status IN ('placed','packing')",
        True,
        "failed_guard",
    ),
    ("picker_queue", "CREATE INDEX ON orders (store_id, created_at)", False, "selected or tied"),
]:
    _candidate(
        shape_id, "index", sql, "Test report benefit against protected write cost.", bad, expect
    )
_candidate(
    "recent_orders",
    "stats",
    "ANALYZE orders",
    "Refresh planner statistics.",
    False,
    "skipped or no speedup",
)
_original = REPORT_SHAPES["delivered_yesterday"].sql
_range = _original.replace(
    "DATE(delivered_at) = (%(as_of)s::timestamptz)::date - 1",
    "delivered_at >= ((%(as_of)s::timestamptz)::date - 1) "
    "AND delivered_at < (%(as_of)s::timestamptz)::date",
)
_candidate(
    "delivered_yesterday",
    "rewrite",
    _range,
    "Use session-local calendar day bounds.",
    False,
    "tested; speedup needs delivered_at index",
)
_wrong_range = _original.replace(
    "DATE(delivered_at) = (%(as_of)s::timestamptz)::date - 1",
    "delivered_at >= (((%(as_of)s::timestamptz AT TIME ZONE 'UTC')::date - 1) "
    "::timestamp AT TIME ZONE 'UTC') AND delivered_at < "
    "(((%(as_of)s::timestamptz AT TIME ZONE 'UTC')::date)::timestamp AT TIME ZONE 'UTC')",
)
_candidate(
    "delivered_yesterday",
    "rewrite",
    _wrong_range,
    "Deliberate UTC calendar-day trap.",
    True,
    "rejected_equiv at tier 2 in Asia/Kolkata",
)
for distinct, bad in [("", True), ("DISTINCT ", False)]:
    _candidate(
        "open_order_customers",
        "rewrite",
        "/* dpa:shape=open_order_customers */ SELECT "
        + distinct
        + "c.customer_id, c.name FROM customers c JOIN orders o "
        "ON o.customer_id = c.customer_id WHERE c.city = %(city)s "
        "AND o.status IN ('placed','packing') ORDER BY c.customer_id",
        "Convert EXISTS into a join; duplicate handling is essential.",
        bad,
        "rejected_equiv: duplicate customers" if bad else "tested",
    )
_payout = (
    "/* dpa:shape=payout_report */ SELECT store_id, sum(total) AS payout FROM orders "
    "WHERE status = 'delivered' AND delivered_at >= %(start)s::timestamptz "
    "AND delivered_at < %(end)s::timestamptz GROUP BY store_id ORDER BY store_id"
)
_candidate(
    "payout_report",
    "rewrite",
    _payout,
    "Primary key makes DISTINCT redundant.",
    False,
    "tested; small speedup",
)
_candidate(
    "payout_report",
    "rewrite",
    _payout.replace("status = 'delivered' AND ", ""),
    "Deliberately drop a required status predicate.",
    True,
    "rejected_equiv",
)
# The supplied table has no bad candidate for these two shapes; add explicit semantic traps.
_candidate(
    "hourly_sales",
    "rewrite",
    REPORT_SHAPES["hourly_sales"].sql.replace("interval '24 hours'", "interval '12 hours'"),
    "Deliberately omit half of the reporting window.",
    True,
    "rejected_equiv",
)
_candidate(
    "top_products_store",
    "rewrite",
    REPORT_SHAPES["top_products_store"].sql.replace("o.store_id = %(store_id)s AND ", ""),
    "Deliberately include sales from other stores.",
    True,
    "rejected_equiv",
)

ORDER_TXNS: dict[str, TxnDef] = {
    id: TxnDef(id, weight, description)
    for id, weight, description in [
        ("place_order", 0.45, "Create an order and reserve stock for 2–4 lines."),
        ("update_status", 0.20, "Advance an active order by one delivery state."),
        ("order_status", 0.15, "Read one order's current state."),
        ("stock_decrement", 0.15, "Conditionally decrement one store/product stock row."),
        ("picker_queue_read", 0.05, "Read the next 50 open orders for one store."),
    ]
}

TXN_SQL: dict[str, tuple[str, ...]] = {
    "place_order": (
        "/* dpa:txn=place_order */ INSERT INTO orders "
        "(customer_id,store_id,status,total,payment_mode,created_at) "
        "VALUES (%(customer_id)s,%(store_id)s,'placed',%(total)s,%(payment_mode)s,now()) "
        "RETURNING order_id",
        "/* dpa:txn=place_order */ INSERT INTO order_items "
        "(order_id,product_id,qty,unit_price) VALUES (%(order_id)s,%(p)s,%(q)s,%(unit_price)s)",
        "/* dpa:txn=place_order */ UPDATE store_stock SET qty = qty - %(q)s, "
        "updated_at = now() WHERE store_id = %(s)s AND product_id = %(p)s AND qty >= %(q)s",
    ),
    "update_status": (
        "/* dpa:txn=update_status */ SELECT status FROM orders WHERE order_id = %(id)s FOR UPDATE",
        "/* dpa:txn=update_status */ UPDATE orders SET status = %(new)s, "
        "delivered_at = CASE WHEN %(new)s = 'delivered' THEN now() ELSE delivered_at END "
        "WHERE order_id = %(id)s",
    ),
    "order_status": (
        "/* dpa:txn=order_status */ SELECT order_id,status,total,created_at,delivered_at "
        "FROM orders WHERE order_id = %(id)s",
    ),
    "stock_decrement": (
        "/* dpa:txn=stock_decrement */ UPDATE store_stock SET qty = qty - %(q)s, "
        "updated_at = now() WHERE store_id = %(s)s AND product_id = %(p)s AND qty >= %(q)s",
    ),
    "picker_queue_read": (
        REPORT_SHAPES["picker_queue"].sql.replace(
            "dpa:shape=picker_queue", "dpa:txn=picker_queue_read"
        ),
    ),
}


class OutOfStock(Conflict):
    """Conditional stock update failed; caller records latency and oos count."""

    code = "oos"


def run_transaction(conn: psycopg.Connection, txn: str, params: dict) -> Any:
    """Execute one protected transaction atomically, committing only on success.

    An idle connection is required so this cannot silently become an uncommitted
    nested savepoint. The later workload runner measures elapsed time on both
    success and OutOfStock. No LLM-supplied SQL is accepted here.
    """
    if txn not in TXN_SQL:
        raise BadRequest("Unknown protected transaction")
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise Conflict("Protected transaction requires an idle connection")
    statements = TXN_SQL[txn]
    with conn.transaction():
        if txn == "place_order":
            lines = params["lines"]
            if not 2 <= len(lines) <= 4 or any(line["q"] <= 0 for line in lines):
                raise BadRequest("Orders require 2–4 positive-quantity lines")
            row = conn.execute(statements[0], params).fetchone()
            if row is None:
                raise Conflict("Order insert returned no identifier")
            order_id = row[0]
            # Stable lock ordering prevents reverse-product deadlocks across orders.
            for line in sorted(lines, key=lambda line: line["p"]):
                values = dict(line, order_id=order_id, s=params["store_id"])
                conn.execute(statements[1], values)
                if conn.execute(statements[2], values).rowcount != 1:
                    raise OutOfStock("Product unavailable")
            return order_id
        if txn == "update_status":
            row = conn.execute(statements[0], params).fetchone()
            transitions = {
                "placed": "packing",
                "packing": "out_for_delivery",
                "out_for_delivery": "delivered",
            }
            if row is None or row[0] not in transitions:
                raise Conflict("Order is not active")
            new = transitions[row[0]]
            if "new" in params and params["new"] != new:
                raise BadRequest("Order statuses must advance one step")
            return conn.execute(statements[1], dict(params, new=new)).rowcount
        if txn == "stock_decrement":
            if params["q"] <= 0:
                raise BadRequest("Stock decrement must be positive")
            count = conn.execute(statements[0], params).rowcount
            if count != 1:
                raise OutOfStock("Product unavailable")
            return count
        return conn.execute(statements[0], params).fetchall()
