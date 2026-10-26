"""Pure safety checks: hostile text never requires a database."""

from dataclasses import replace

import pytest

from app.catalog import Catalog
from app.models import RawCandidate
from app.safety import check


@pytest.fixture
def catalog():
    from pathlib import Path

    import sqlglot
    from sqlglot import exp

    trees = sqlglot.parse(
        (Path(__file__).parents[1] / "sql/schema.sql").read_text(), read="postgres"
    )
    tables = []
    for tree in trees:
        schema = tree.this
        columns = [
            {"name": c.name, "type": c.args["kind"].sql(dialect="postgres")}
            for c in schema.expressions
            if isinstance(c, exp.ColumnDef)
        ]
        tables.append({"name": schema.this.name, "columns": columns})
    return Catalog(tables, [], {}, [], [], original_sql="SELECT order_id FROM orders")


@pytest.mark.parametrize(
    "kind,sql",
    [
        ("index", "CREATE INDEX ON orders(customer_id,created_at DESC) INCLUDE(store_id)"),
        ("index", "CREATE INDEX ON orders(customer_id) WHERE status IN ('placed','packing')"),
        ("index", "CREATE INDEX ON orders(DATE(delivered_at))"),
        ("index", "CREATE INDEX CONCURRENTLY x ON orders(customer_id)"),
        ("stats", "ANALYZE orders"),
        ("stats", "ANALYZE orders(status,store_id)"),
        ("stats", "CREATE STATISTICS s ON status,store_id FROM orders"),
        ("stats", "CREATE STATISTICS s (dependencies,ndistinct) ON status,store_id FROM orders"),
        ("rewrite", "SELECT order_id FROM orders WHERE customer_id=1"),
        (
            "rewrite",
            "SELECT o.order_id FROM orders o JOIN customers c ON c.customer_id=o.customer_id",
        ),
    ],
)
def test_accept_valid(catalog, kind, sql):
    raw = RawCandidate(kind, sql, "fixture")
    ok, reason = check(raw, catalog)
    assert ok, reason
    assert "CONCURRENTLY" not in raw.sql


@pytest.mark.parametrize(
    "kind,sql",
    [
        ("index", "DROP INDEX x"),
        ("index", "CREATE UNIQUE INDEX ON orders(customer_id)"),
        ("index", "CREATE INDEX ON orders(customer_id); DROP TABLE orders"),
        ("index", "CREATE INDEX ON orders USING hash(customer_id)"),
        ("index", "CREATE INDEX ON unknown_table(customer_id)"),
        ("index", "CREATE INDEX ON orders(unknown_column)"),
        ("index", "CREATE INDEX ON orders(customer_id) INCLUDE(unknown_column)"),
        ("index", "CREATE INDEX ON orders(customer_id) WHERE created_at>now()"),
        ("index", "CREATE INDEX ON orders(order_id,store_id,customer_id,status,total)"),
        ("index", "CREATE INDEX ON pg_catalog.pg_class(oid)"),
        ("stats", "VACUUM orders"),
        ("stats", "ANALYZE VERBOSE orders"),
        ("stats", "ANALYZE unknown_table"),
        ("stats", "ANALYZE orders(unknown_column)"),
        ("stats", "CREATE STATISTICS s ON unknown_column,status FROM orders"),
        ("rewrite", "DELETE FROM orders"),
        ("rewrite", "WITH d AS (DELETE FROM orders RETURNING *) SELECT order_id FROM d"),
        ("rewrite", "SELECT order_id INTO unwanted FROM orders"),
        ("rewrite", "SELECT order_id FROM orders FOR UPDATE"),
        ("rewrite", "SELECT pg_sleep(1) AS order_id"),
        ("rewrite", "SELECT set_config('x','y',false) AS order_id"),
        ("rewrite", "SELECT unknown_function(order_id) FROM orders"),
        ("rewrite", "SELECT order_id FROM information_schema.tables"),
        ("rewrite", "SELECT missing AS order_id FROM orders"),
        ("rewrite", "SELECT order_id AS changed_name FROM orders"),
        ("rewrite", "SELECT order_id,customer_id FROM orders"),
        ("rewrite", "SELECT order_id FROM orders -- injection"),
        ("rewrite", "/* comment */ SELECT order_id FROM orders"),
        ("index", ""),
        ("index", "x" * 4001),
    ],
)
def test_reject_unsafe(catalog, kind, sql):
    assert not check(RawCandidate(kind, sql, ""), catalog)[0]


def test_rewrite_requires_baseline(catalog):
    assert not check(
        RawCandidate("rewrite", "SELECT order_id FROM orders", ""),
        replace(catalog, original_sql=None),
    )[0]


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE INDEX ON orders(customer_id text_pattern_ops)",
        "CREATE INDEX ON orders(CAST(status AS evil))",
        "CREATE INDEX ON orders(customer_id) WITH (fillfactor=50)",
        "CREATE INDEX ON orders(customer_id) WHERE customer_id IN (SELECT customer_id FROM orders)",
    ],
)
def test_reject_extension_points(catalog, sql):
    assert not check(RawCandidate("index", sql, ""), catalog)[0]


def test_exists_is_a_predicate_not_an_external_function(catalog):
    query = (
        "SELECT order_id FROM orders WHERE EXISTS "
        "(SELECT 1 FROM customers c WHERE c.customer_id=orders.customer_id)"
    )
    assert check(RawCandidate("rewrite", query, ""), catalog)[0]
