"""Acceptance checks on the real seeded live clone; never connect to golden."""

import os
from datetime import datetime, timedelta

import pytest

from scripts.seed import EXPECTED_INDEXES, connect_database, validate_report, verification_report

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def report() -> dict:
    """Measure the live fixture once without rebuilding or mutating any data."""
    with connect_database(os.getenv("LIVE_DB", "qc_live")) as conn:
        return verification_report(conn, os.getenv("GOLDEN_DB", "qc_golden"))


def test_row_counts(report: dict) -> None:
    """Scale and entity counts must meet the seed contract."""
    scale = int(report["metadata"]["scale"])
    expected = {
        "meta": 4,
        "stores": 50,
        "categories": 40,
        "products": 2000,
        "store_stock": 80_000,
        "customers": 20_000 * scale,
        "riders": 500,
        "orders": 300_000 * scale,
        "order_items": 900_000 * scale,
    }
    for table, count in expected.items():
        assert abs(report["counts"][table] / count - 1) <= 0.02, table


def test_status_mix(report: dict) -> None:
    """Keep active rows selective and preserve the delivered/cancelled mix."""
    assert set(report["statuses"]) == {
        "placed",
        "packing",
        "out_for_delivery",
        "delivered",
        "cancelled",
    }
    total = report["counts"]["orders"]
    assert 0.95 <= report["statuses"]["delivered"] / total <= 0.98
    assert 0.02 <= report["statuses"]["cancelled"] / total <= 0.04
    assert 0.001 <= report["active_fraction"] <= 0.005
    assert 0.001 <= report["active_all_fraction"] <= 0.005
    assert report["invalid_orders"] == 0


def test_no_secondary_indexes(report: dict) -> None:
    """Only primary keys and the product SKU unique constraint exist."""
    assert set(report["indexes"]) == EXPECTED_INDEXES


def test_meta_as_of_present(report: dict) -> None:
    """The frozen clock is UTC noon, and orders cover its preceding 30 days."""
    metadata = report["metadata"]
    assert {"as_of", "scale", "seed", "seeded_at"} == set(metadata)
    as_of = datetime.fromisoformat(metadata["as_of"])
    assert as_of.utcoffset() == timedelta(0)
    assert (as_of.hour, as_of.minute, as_of.second, as_of.microsecond) == (12, 0, 0, 0)
    first = datetime.fromisoformat(report["time_min"])
    last = datetime.fromisoformat(report["time_max"])
    assert as_of - timedelta(days=30) <= first < as_of - timedelta(days=29.9)
    assert as_of - timedelta(hours=1) < last <= as_of


def test_golden_idle(report: dict) -> None:
    """Seeding must leave zero clients connected to golden."""
    assert report["golden_idle"] is True


def test_item_counts_vary_per_order(report: dict) -> None:
    """Detect an uncorrelated LATERAL draw being reused for every order."""
    assert report["items_per_order"] == {
        "min": 1,
        "max": 5,
        "orders": report["counts"]["orders"],
    }


def test_skew_stockouts_and_extensions(report: dict) -> None:
    """Verify tuning opportunities and clone extension availability."""
    assert report["customer_top_1pct_share"] > 0.10
    assert report["product_top_10pct_share"] > 0.35
    assert report["recent_stockouts"] > 100
    assert report["delivered_yesterday"] > 1000 * int(report["metadata"]["scale"])
    assert {"hypopg", "pg_stat_statements"} <= set(report["extensions"])


def test_verification_fails_closed(report: dict) -> None:
    """A failed measured invariant must prevent successful seed completion."""
    invalid = dict(report, golden_idle=False)
    with pytest.raises(RuntimeError, match="golden_idle"):
        validate_report(invalid)
