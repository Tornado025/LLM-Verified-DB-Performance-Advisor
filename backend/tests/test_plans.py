"""Plan identity invariants and real sandbox HypoPG/filter behavior."""

from copy import deepcopy

import numpy as np
import pytest

from app.catalog import get_catalog
from app.config import get_settings
from app.errors import BadRequest
from app.models import Candidate
from app.observe import StatementList, top_statements
from app.pg import connect, render_sql
from app.plans import (
    baseline_signature,
    explain,
    filter_candidates,
    fingerprint,
    hypo_plan,
    normalise_index_ddl,
    plan_cache,
    signature_for,
)
from app.sandbox import InvalidDDL, clone_pair, single_clone
from app.shapes import REPORT_SHAPES, load_as_of, render, sample_params


def make_candidate(sql, kind="index", number=1):
    return Candidate(
        f"test:recent_orders:{number:02d}", "test", "recent_orders", kind, sql, "fixture", number
    )


@pytest.fixture(scope="module")
def baseline():
    with single_clone(get_settings()) as name, connect(name) as conn:
        shape = REPORT_SHAPES["recent_orders"]
        query = render(shape, conn, {"customer_id": 100, "as_of": load_as_of(conn).isoformat()})
        yield conn, query


def test_fingerprint_ignores_costs():
    plan = {
        "Plan": {
            "Node Type": "Seq Scan",
            "Relation Name": "orders",
            "Total Cost": 100,
            "Filter": "((customer_id = 12) AND (status = 'placed'::text))",
        }
    }
    changed = deepcopy(plan)
    changed["Plan"].update(
        {
            "Total Cost": 100000,
            "Actual Rows": 900,
            "Shared Hit Blocks": 20,
            "Filter": "((customer_id = 99) AND (status = 'packing'::text))",
        }
    )
    assert fingerprint(plan) == fingerprint(changed)
    assert len(fingerprint(plan)) == 12
    changed["Plan"]["Node Type"] = "Index Scan"
    assert fingerprint(plan) != fingerprint(changed)


def test_hypothetical_oid_and_sort_normalization():
    a = {
        "Node Type": "Index Scan",
        "Index Name": "<123>btree_orders_customer_id",
        "Sort Key": ["created_at DESC", "store_id"],
    }
    b = dict(
        a,
        **{
            "Index Name": "<456>btree_orders_customer_id",
            "Sort Key": ["store_id ASC", "created_at ASC"],
        },
    )
    assert fingerprint(a) == fingerprint(b)
    a["Filter"], b["Filter"] = "column2 = 10", "column3 = 10"
    assert fingerprint(a) != fingerprint(b)


def test_normalise_index_ddl():
    result = normalise_index_ddl(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS x ON orders(customer_id);"
    )
    assert (
        "CONCURRENTLY" not in result and "IF NOT EXISTS" not in result and not result.endswith(";")
    )
    for sql in (
        "DROP TABLE orders",
        "CREATE INDEX ON orders USING hash(customer_id)",
        "CREATE INDEX ON orders(customer_id); SELECT 1",
    ):
        with pytest.raises(InvalidDDL):
            normalise_index_ddl(sql)


@pytest.mark.db
def test_fingerprint_changes_with_index(baseline):
    conn, query = baseline
    original = baseline_signature(conn, query)
    ddl = "CREATE INDEX ON orders(customer_id,created_at DESC)"
    assert fingerprint(hypo_plan(conn, ddl, query)) != original
    assert fingerprint(hypo_plan(conn, ddl, query)) == fingerprint(hypo_plan(conn, ddl, query))
    with plan_cache():
        assert baseline_signature(conn, query) == original
        hypo_plan(conn, ddl, query)
        assert baseline_signature(conn, query) == original


@pytest.mark.db
def test_hypo_reset_always(baseline):
    conn, _ = baseline
    with pytest.raises(InvalidDDL):
        hypo_plan(conn, "CREATE INDEX ON orders(customer_id)", "SELECT unknown_column FROM orders")
    assert conn.execute("SELECT * FROM hypopg_list_indexes").fetchall() == []
    assert conn.execute("SELECT 1").fetchone()[0] == 1


@pytest.mark.db
def test_noplan_for_status_index(baseline):
    conn, query = baseline
    c = make_candidate("CREATE INDEX ON orders(status)")
    kept, dropped = filter_candidates([c], query, conn)
    assert not kept and dropped == [c] and c.status == "skipped_noplan"


@pytest.mark.db
def test_duplicate_plans(baseline):
    conn, query = baseline
    c1 = make_candidate("CREATE INDEX one ON orders(customer_id,created_at DESC)")
    c2 = make_candidate("CREATE INDEX two ON orders(customer_id,created_at DESC)", number=2)
    kept, dropped = filter_candidates([c1, c2], query, conn)
    assert kept == [c1] and dropped == [c2]
    assert c2.status == "skipped_duplicate" and c1.id in c2.reason
    covering = make_candidate(
        "CREATE INDEX ON orders(customer_id,created_at DESC) INCLUDE(store_id)", number=3
    )
    signature = signature_for(covering, query, conn)
    # The requested pair is observational: INCLUDE may generate a different name
    # or plan, so don't hard-code that physically distinct index into a duplicate.
    kept, dropped = filter_candidates([c1, covering], query, conn)
    assert (covering in dropped) == (signature == c1.signature)
    print(f"include_signature_equal={signature == c1.signature}")


@pytest.mark.db
def test_invalid_expression_index():
    c = make_candidate("CREATE INDEX ON orders(DATE(delivered_at))")
    with pytest.raises(InvalidDDL), clone_pair(c, get_settings()):
        pytest.fail("PostgreSQL accepted nonimmutable DATE(timestamptz)")
    assert c.status == "rejected_invalid_ddl"


@pytest.mark.db
def test_existing_index_skipped():
    with single_clone(get_settings()) as name, connect(name) as conn:
        conn.execute("CREATE INDEX existing_customer ON orders(customer_id)")
        query = "SELECT order_id FROM orders WHERE customer_id=100"
        c = make_candidate("CREATE INDEX different_name ON public.orders USING btree(customer_id)")
        kept, dropped = filter_candidates([c], query, conn)
        assert not kept and dropped == [c]
        assert c.status == "skipped_duplicate" and c.reason == "already exists"


@pytest.mark.db
def test_explain_options_and_live_refusal(baseline, live_conn):
    conn, _ = baseline
    assert "Plan" in explain(conn, "SELECT * FROM orders WHERE customer_id=$1", generic=True)
    assert "Execution Time" in explain(conn, "SELECT * FROM stores", analyze=True, buffers=True)
    with pytest.raises(BadRequest):
        explain(conn, "SELECT 1", generic=True, analyze=True)
    with pytest.raises(BadRequest):
        explain(conn, "DELETE FROM orders", analyze=True)
    with pytest.raises(BadRequest):
        explain(live_conn, "SELECT 1")


@pytest.mark.db
def test_catalog_and_stats_signatures(baseline):
    conn, query = baseline
    catalog = get_catalog(conn)
    orders = next(t for t in catalog.tables if t["name"] == "orders")
    assert any(
        c["name"] == "created_at" and c["type"] == "timestamp with time zone"
        for c in orders["columns"]
    )
    assert len(catalog.primary_keys) == 9 and catalog.foreign_keys
    assert catalog.row_counts["orders"] > 0 and catalog.indexes
    a = make_candidate("CREATE STATISTICS s ON status, store_id FROM orders", "stats")
    b = make_candidate("create statistics s on store_id,status from orders;", "stats", 2)
    assert signature_for(a, query, conn) == signature_for(b, query, conn)
    stats = make_candidate("ANALYZE orders", "stats")
    kept, _ = filter_candidates([stats], query, conn)
    assert kept == [stats] and stats.signature == "stats:analyze orders"


@pytest.mark.db
def test_observe_tags(baseline):
    conn, _ = baseline
    conn.execute("/* dpa:shape=recent_orders */ SELECT sum(store_id*store_id) FROM stores")
    conn.execute(
        "/* dpa:txn=picker_queue_read */ SELECT max(category_id*category_id) FROM categories"
    )
    rows = top_statements(conn, limit=1000)
    assert {"shape:recent_orders", "txn:picker_queue_read"} <= {row["tag"] for row in rows}
    assert all("dpa:sys" not in row["query"] for row in rows)
    assert isinstance(rows, StatementList) and not rows.extension_missing
    assert all(isinstance(row["queryid"], str) for row in rows)


@pytest.mark.db
def test_all_mock_fixtures_filter(baseline):
    conn, _ = baseline
    rng = np.random.default_rng(1234)
    total = 0
    for shape in REPORT_SHAPES.values():
        params = sample_params(shape, conn, rng, load_as_of(conn))
        query = render(shape, conn, params)
        candidates = [
            Candidate(
                f"test:{shape.id}:{i:02d}",
                "test",
                shape.id,
                c["kind"],
                render_sql(conn, c["sql"], params) if c["kind"] == "rewrite" else c["sql"],
                c["rationale"],
                i,
            )
            for i, c in enumerate(shape.mock_candidates, 1)
        ]
        kept, dropped = filter_candidates(candidates, query, conn)
        assert len(kept) + len(dropped) == len(candidates)
        assert all(c.status == "proposed" for c in kept)
        assert all(
            c.status in {"skipped_noplan", "skipped_duplicate", "rejected_invalid_ddl"}
            for c in dropped
        )
        assert not conn.execute("SELECT * FROM hypopg_list_indexes").fetchall()
        print(shape.id, [(c.kind, c.status) for c in candidates])
        total += len(candidates)
    assert total >= 22


def test_missing_extension_flag_and_observe_validation():
    from types import SimpleNamespace

    conn = SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchone=lambda: None))
    rows = top_statements(conn)
    assert rows == [] and rows.extension_missing
    with pytest.raises(BadRequest):
        top_statements(conn, order="injected SQL")


def test_job_cache_scope_options_and_invalidation(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.plans import invalidate_cache

    calls = []

    def execute(statement):
        if statement.startswith("EXPLAIN"):
            calls.append(statement)
        return SimpleNamespace(fetchone=lambda: ([{"Plan": {"Node Type": "Result"}}],))

    conn = SimpleNamespace(
        info=SimpleNamespace(dbname="test", backend_pid=1), transaction=nullcontext, execute=execute
    )
    monkeypatch.setattr("app.plans._baseline", lambda conn: None)
    with plan_cache():
        plan = explain(conn, "SELECT 1")
        plan["Plan"]["Node Type"] = "mutated"
        assert explain(conn, "SELECT 1")["Plan"]["Node Type"] == "Result"
        assert len(calls) == 1
        explain(conn, "SELECT 1", generic=True)
        assert len(calls) == 2
        invalidate_cache()
        explain(conn, "SELECT 1")
        assert len(calls) == 3
    explain(conn, "SELECT 1")
    assert len(calls) == 4


@pytest.mark.db
def test_valid_expression_and_covering_indexes(baseline):
    conn, _ = baseline
    for ddl, query in [
        (
            "CREATE INDEX ON products(lower(name))",
            "SELECT product_id FROM products WHERE lower(name)='product_1'",
        ),
        (
            "CREATE INDEX ON orders(customer_id) INCLUDE(store_id)",
            "SELECT customer_id,store_id FROM orders WHERE customer_id=100",
        ),
        (
            "CREATE INDEX ON store_stock(updated_at) WHERE qty=0",
            "SELECT product_id FROM store_stock WHERE qty=0",
        ),
    ]:
        assert "Plan" in hypo_plan(conn, ddl, query)
        assert not conn.execute("SELECT * FROM hypopg_list_indexes").fetchall()


def test_quoted_identifiers_keep_significant_whitespace():
    from app.plans import _stats_signature

    a = {"Node Type": "Seq Scan", "Filter": '"two  spaces" = 1'}
    b = {"Node Type": "Seq Scan", "Filter": '"two spaces" = 1'}
    assert fingerprint(a) != fingerprint(b)
    assert _stats_signature('ANALYZE "two  spaces"') != _stats_signature('ANALYZE "two spaces"')
