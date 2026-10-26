"""Safety-before-signature ordering, saturation and real mock proposal integration."""

from dataclasses import replace

import pytest

from app.catalog import get_catalog
from app.config import get_settings
from app.llm.base import SchemaContext, SuggestResult
from app.llm.mock import MockProvider
from app.models import RawCandidate
from app.pg import connect
from app.plans import signature_for
from app.propose import propose
from app.safety import check
from app.sandbox import InvalidDDL, single_clone
from app.shapes import REPORT_SHAPES, load_as_of, render


def test_safety_precedes_signature_and_saturation():
    class Provider:
        name = "fixture"

        def suggest(self, *args):
            return SuggestResult(
                [
                    RawCandidate("index", "DROP INDEX x", "bad"),
                    RawCandidate("index", "CREATE INDEX ON orders(customer_id)", "good"),
                ],
                {"prompt_tokens": 1, "completion_tokens": 1, "cost": None, "model": "fixture"},
            )

    calls = []

    def sig(candidate):
        assert "DROP" not in candidate.sql
        calls.append(candidate.sql)
        return "new-plan"

    result = propose(
        REPORT_SHAPES["recent_orders"],
        "SELECT 1",
        Provider(),
        lambda i: SchemaContext([], [], {}, "", i),
        sig,
        lambda r: (not r.sql.startswith("DROP"), "safety"),
        k_max=10,
        patience=2,
        job_id="test",
    )
    assert result.saturated and result.samples == 3 and len(calls) == 1
    assert result.distinct_plan_curve == [1, 1, 1]
    assert any(c["status"] == "rejected_safety" for c in result.rejected)
    assert result.usage["cost"] is None


def test_budget_stop_and_invalid_ddl():
    shape = REPORT_SHAPES["recent_orders"]
    provider = MockProvider()
    result = propose(
        shape,
        "SELECT 1",
        provider,
        lambda i: SchemaContext([], [], {}, "", i),
        lambda c: "x",
        lambda raw: (True, ""),
        k_max=10,
        patience=2,
        job_id="test",
        should_stop=lambda: True,
    )
    assert result.samples == 0 and not result.saturated

    def invalid(c):
        raise InvalidDDL("planted invalid")

    result = propose(
        shape,
        "SELECT 1",
        provider,
        lambda i: SchemaContext([], [], {}, "", i),
        invalid,
        lambda raw: (True, ""),
        k_max=1,
        patience=2,
        job_id="test",
    )
    assert result.rejected and all(r["status"] == "rejected_invalid_ddl" for r in result.rejected)


@pytest.mark.db
def test_mock_proposer_reproducible_and_saturated():
    with single_clone(get_settings()) as name, connect(name) as conn:
        shape = REPORT_SHAPES["recent_orders"]
        params = {"customer_id": 100, "as_of": load_as_of(conn).isoformat()}
        query = render(shape, conn, params)
        catalog = replace(get_catalog(conn), original_sql=query)

        def context(i):
            return SchemaContext(catalog.tables, catalog.indexes, catalog.row_counts, "baseline", i)

        def run():
            return propose(
                shape,
                query,
                MockProvider(1234, params),
                context,
                lambda c: signature_for(c, query, conn),
                lambda raw: check(raw, catalog),
                k_max=10,
                patience=3,
                job_id="test",
            )

        a, b = run(), run()
        assert a.to_dict() == b.to_dict()
        assert a.saturated and len(a.kept) >= 2
        assert len({c.signature for c in a.kept}) == len(a.kept)


@pytest.mark.db
def test_each_shape_fixture_safety():
    import numpy as np

    from app.shapes import sample_params

    with single_clone(get_settings()) as name, connect(name) as conn:
        catalog = get_catalog(conn)
        rng = np.random.default_rng(1234)
        for shape in REPORT_SHAPES.values():
            params = sample_params(shape, conn, rng, load_as_of(conn))
            query = render(shape, conn, params)
            context = SchemaContext(
                catalog.tables, catalog.indexes, catalog.row_counts, "baseline", 0
            )
            for fixture in shape.mock_candidates:
                one = replace(shape, mock_candidates=[fixture])
                raw = MockProvider(1234, params).suggest(one, context, 0, query).candidates[0]
                ok, reason = check(raw, replace(catalog, original_sql=query))
                assert ok, (shape.id, reason)
