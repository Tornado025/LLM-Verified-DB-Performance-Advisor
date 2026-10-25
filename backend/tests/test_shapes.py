"""Report execution, deterministic sampling and atomic commerce transaction tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest
from psycopg.pq import TransactionStatus

from app.errors import BadRequest, Conflict
from app.pg import render_sql
from app.shapes import (
    ORDER_TXNS,
    REPORT_SHAPES,
    TXN_SQL,
    OutOfStock,
    as_of_literal,
    clear_caches,
    load_as_of,
    load_pools,
    render,
    run_transaction,
    sample_params,
)


@pytest.mark.db
def test_all_shapes_render_and_run(live_conn):
    as_of = load_as_of(live_conn)
    rng = np.random.default_rng(1234)
    assert len(REPORT_SHAPES) == 8
    for shape in REPORT_SHAPES.values():
        query = render(shape, live_conn, sample_params(shape, live_conn, rng, as_of))
        assert query.startswith(f"/* dpa:shape={shape.id} */")
        rows = live_conn.execute(query).fetchall()
        if shape.id in {"delivered_yesterday", "stockouts_15m"}:
            assert len(rows) > 0


@pytest.mark.db
def test_samplers_deterministic(live_conn):
    as_of = load_as_of(live_conn)
    for shape in REPORT_SHAPES.values():
        a, b = np.random.default_rng(314), np.random.default_rng(314)
        for _ in range(10):
            assert sample_params(shape, live_conn, a, as_of) == sample_params(
                shape, live_conn, b, as_of
            )
    clear_caches()
    first = load_pools(live_conn.info.dbname)
    clear_caches()
    assert load_pools(live_conn.info.dbname) == first
    for _ in range(20):
        p = sample_params(
            REPORT_SHAPES["payout_report"], live_conn, np.random.default_rng(_), as_of
        )
        start, end = datetime.fromisoformat(p["start"]), datetime.fromisoformat(p["end"])
        assert end - start == timedelta(days=7)
        assert as_of - timedelta(days=10) <= start <= as_of - timedelta(days=6)
        assert as_of - timedelta(days=30) <= start < end <= as_of


def test_txn_sql_tags():
    assert set(ORDER_TXNS) == set(TXN_SQL)
    assert sum(t.weight for t in ORDER_TXNS.values()) == pytest.approx(1)
    for id, statements in TXN_SQL.items():
        assert all(s.startswith(f"/* dpa:txn={id} */") for s in statements)


@pytest.mark.db
def test_as_of_loaded(live_conn):
    clear_caches()
    as_of = load_as_of(live_conn)
    expected = datetime.fromisoformat(
        live_conn.execute("SELECT value FROM meta WHERE key='as_of'").fetchone()[0]
    )
    assert as_of == expected and as_of.tzinfo == UTC
    assert load_as_of(live_conn) is as_of
    with pytest.raises(BadRequest):
        as_of_literal(datetime(2026, 1, 1))


def test_mock_fixture_coverage():
    for shape in REPORT_SHAPES.values():
        assert len(shape.mock_candidates) >= 2
        assert any(c["bad"] for c in shape.mock_candidates)
        for candidate in shape.mock_candidates:
            assert set(candidate) == {"kind", "sql", "rationale", "bad", "expect"}
            assert "CONCURRENTLY" not in candidate["sql"]
            assert candidate["expect"] and candidate["rationale"]


@pytest.fixture
def txn_conn(live_conn):
    """Shadow commerce tables in this session; no persistent commerce rows change."""
    live_conn.execute("""
        CREATE TEMP TABLE orders (
          order_id bigserial PRIMARY KEY, customer_id int, store_id int, status text,
          total numeric, payment_mode text, created_at timestamptz, delivered_at timestamptz);
        CREATE TEMP TABLE order_items (
          order_item_id bigserial PRIMARY KEY, order_id bigint REFERENCES orders,
          product_id int, qty int, unit_price numeric);
        CREATE TEMP TABLE store_stock (
          store_id int, product_id int, qty int, updated_at timestamptz,
          PRIMARY KEY(store_id,product_id));
        INSERT INTO store_stock VALUES (1,1,100,now()),(1,2,100,now()),(1,3,0,now());
    """)
    yield live_conn


def order_params():
    return {
        "customer_id": 1,
        "store_id": 1,
        "payment_mode": "upi",
        "total": Decimal("25"),
        "lines": [
            {"p": 1, "q": 1, "unit_price": Decimal("5")},
            {"p": 2, "q": 2, "unit_price": Decimal("10")},
        ],
    }


@pytest.mark.db
def test_all_transactions_commit(txn_conn):
    conn = txn_conn
    id = run_transaction(conn, "place_order", order_params())
    assert conn.info.transaction_status == TransactionStatus.IDLE
    assert (
        conn.execute("SELECT count(*) FROM order_items WHERE order_id=%s", (id,)).fetchone()[0] == 2
    )
    assert conn.execute("SELECT qty FROM store_stock WHERE product_id=2").fetchone()[0] == 98
    assert run_transaction(conn, "order_status", {"id": id})[0][1] == "placed"
    assert run_transaction(conn, "picker_queue_read", {"store_id": 1})[0][0] == id
    for status in ("packing", "out_for_delivery", "delivered"):
        assert run_transaction(conn, "update_status", {"id": id, "new": status}) == 1
    assert conn.execute("SELECT delivered_at FROM orders WHERE order_id=%s", (id,)).fetchone()[0]
    assert run_transaction(conn, "stock_decrement", {"s": 1, "p": 1, "q": 2}) == 1
    assert conn.execute("SELECT qty FROM store_stock WHERE product_id=1").fetchone()[0] == 97
    assert conn.info.transaction_status == TransactionStatus.IDLE


@pytest.mark.db
def test_out_of_stock_rolls_back_entire_order(txn_conn):
    params = order_params()
    params["lines"][1]["p"] = 3
    with pytest.raises(OutOfStock) as error:
        run_transaction(txn_conn, "place_order", params)
    assert error.value.code == "oos"
    assert txn_conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 0
    assert txn_conn.execute("SELECT count(*) FROM order_items").fetchone()[0] == 0
    assert txn_conn.execute("SELECT qty FROM store_stock WHERE product_id=1").fetchone()[0] == 100
    with pytest.raises(OutOfStock):
        run_transaction(txn_conn, "stock_decrement", {"s": 1, "p": 3, "q": 1})
    assert txn_conn.info.transaction_status == TransactionStatus.IDLE


@pytest.mark.db
def test_invalid_transition_and_negative_qty_rejected(txn_conn):
    id = run_transaction(txn_conn, "place_order", order_params())
    with pytest.raises(BadRequest):
        run_transaction(txn_conn, "update_status", {"id": id, "new": "delivered"})
    assert run_transaction(txn_conn, "order_status", {"id": id})[0][1] == "placed"
    with pytest.raises(BadRequest):
        run_transaction(txn_conn, "stock_decrement", {"s": 1, "p": 1, "q": -1})
    with pytest.raises(Conflict):
        run_transaction(txn_conn, "update_status", {"id": -1})


@pytest.mark.db
def test_mock_rewrite_semantics_on_counterexamples(txn_conn):
    """Exercise planted SQL only; future pipeline safety/equivalence is not built yet."""
    conn = txn_conn
    conn.execute("CREATE TEMP TABLE customers(customer_id int, name text, city text)")
    conn.execute("INSERT INTO customers VALUES(1,'customer','Bengaluru')")
    conn.execute("""INSERT INTO orders(customer_id,store_id,status,total,created_at,delivered_at)
      VALUES (1,1,'placed',10,'2026-10-24 10:00+00',NULL),
             (1,1,'packing',10,'2026-10-24 10:00+00',NULL),
             (1,1,'delivered',10,'2026-10-23 19:00+00','2026-10-23 20:00+00'),
             (1,1,'cancelled',10,'2026-10-23 19:00+00','2026-10-23 20:00+00')""")

    def rows(sql, params):
        return conn.execute(render_sql(conn, sql, params)).fetchall()

    for id, params in [
        ("open_order_customers", {"city": "Bengaluru"}),
        ("payout_report", {"start": "2026-10-20", "end": "2026-10-25"}),
        ("delivered_yesterday", {"as_of": "2026-10-25T12:00:00+00:00"}),
    ]:
        shape = REPORT_SHAPES[id]
        for tz in ("UTC", "Asia/Kolkata", "America/New_York"):
            conn.execute("SELECT set_config('TimeZone',%s,false)", (tz,))
            original = rows(shape.sql, params)
            for c in shape.mock_candidates:
                if c["kind"] == "rewrite" and not c["bad"]:
                    assert rows(c["sql"], params) == original
        bad = next(c for c in shape.mock_candidates if c["kind"] == "rewrite" and c["bad"])
        conn.execute("SET TIME ZONE 'Asia/Kolkata'")
        assert rows(bad["sql"], params) != rows(shape.sql, params)


@pytest.mark.db
def test_transaction_commits_with_autocommit_disabled(txn_conn):
    txn_conn.autocommit = False
    run_transaction(txn_conn, "place_order", order_params())
    assert txn_conn.info.transaction_status == TransactionStatus.IDLE
    # A transaction already in progress must never silently become a nested savepoint.
    txn_conn.execute("SELECT 1")
    with pytest.raises(Conflict):
        run_transaction(txn_conn, "order_status", {"id": 1})
    txn_conn.rollback()
    txn_conn.autocommit = True
    assert txn_conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 1


@pytest.mark.db
def test_invalid_expression_index_is_not_created(txn_conn):
    import psycopg

    with pytest.raises(psycopg.errors.InvalidObjectDefinition), txn_conn.transaction():
        txn_conn.execute("CREATE INDEX ON pg_temp.orders (DATE(delivered_at))")
    assert txn_conn.info.transaction_status == TransactionStatus.IDLE
