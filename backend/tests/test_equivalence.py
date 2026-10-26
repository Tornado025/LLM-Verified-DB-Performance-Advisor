"""Structural claims, all labelled traps/equivalents, and fail-closed integration."""

import hashlib
import json
import os
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from app.config import get_settings
from app.equivalence import EquivConfig, check_rewrite, compare_rows, recognize_class
from app.errors import BadRequest
from app.pg import connect
from app.sandbox import single_clone
from scripts.equivalence_report import build_report

PAIRS = json.loads((Path(__file__).parents[1] / "data/rewrite_pairs.json").read_text())


@pytest.mark.parametrize("pair", PAIRS, ids=[p["id"] for p in PAIRS])
def test_class_recognizers(pair):
    cls, reason = recognize_class(pair["original"], pair["rewrite"])
    if pair["expected"] == "tested":
        assert cls == pair["class"] and reason is None
    elif pair["id"] in {"T4", "T5", "T8", "T10"}:
        assert reason
    else:
        assert cls == pair["class"]


def test_row_comparison_multiplicity_null_numeric():
    assert compare_rows([(None, Decimal("1.0")), (2, 3)], [(2, 3), (None, 1.0)], False)
    assert not compare_rows([(1,), (1,)], [(1,), (2,)], False)
    assert not compare_rows([(1,), (2,)], [(2,), (1,)], True)
    assert compare_rows([(Decimal("0.0000000005"),)], [(0.0,)], False)
    assert not compare_rows([(Decimal("0.000000002"),)], [(0.0,)], False)
    assert compare_rows([], [], False)


def test_config_validation():
    with pytest.raises(ValueError):
        EquivConfig(cx_dbs=0)
    with pytest.raises(ValueError):
        EquivConfig(timezones=())


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    with single_clone(get_settings()) as name, connect(name) as conn:
        report = build_report(conn, PAIRS, EquivConfig())
        assert (
            conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname='cx'").fetchone()[0] == 0
        )
    report["meta"]["pairs_sha256"] = hashlib.sha256(
        (Path(__file__).parents[1] / "data/rewrite_pairs.json").read_bytes()
    ).hexdigest()
    path = Path(
        os.getenv("EQUIV_REPORT_OUT", str(tmp_path_factory.mktemp("equivalence") / "report.json"))
    )
    path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


@pytest.mark.db
@pytest.mark.parametrize("pair", PAIRS, ids=[p["id"] for p in PAIRS])
def test_labelled_set(pair, measured):
    result = next(r["result"] for r in measured["results"] if r["id"] == pair["id"])
    assert result["label"] == pair["expected"], result
    if pair["expected"] == "tested":
        assert result["tier_reached"] == 3 and result["cx_dbs"] == 50
        assert result["nonvacuous_fraction"] >= 0.3 and not result["weak"]


@pytest.mark.db
def test_counterexample_content(measured):
    result = next(r["result"] for r in measured["results"] if r["id"] == "T1")
    assert result["counterexample"]["tz"] == "Asia/Kolkata"
    assert result["counterexample"]["tables"]["orders"]
    assert result["tier_reached"] == 2


@pytest.mark.db
def test_session_and_scratch_restored():
    pair = next(p for p in PAIRS if p["id"] == "E1")
    with single_clone(get_settings()) as name, connect(name) as conn:
        conn.execute("SET TIME ZONE 'America/New_York'")
        conn.execute("SET search_path=pg_catalog, public")
        before = (
            conn.execute("SHOW TimeZone").fetchone(),
            conn.execute("SHOW search_path").fetchone(),
        )
        seq_before = conn.execute(
            "SELECT last_value,is_called FROM public.orders_order_id_seq"
        ).fetchone()
        result = check_rewrite(conn, pair["original"], pair["rewrite"], EquivConfig(cx_dbs=2))
        assert result.label == "tested", result
        after = (
            conn.execute("SHOW TimeZone").fetchone(),
            conn.execute("SHOW search_path").fetchone(),
        )
        assert before == after
        assert (
            seq_before
            == conn.execute(
                "SELECT last_value,is_called FROM public.orders_order_id_seq"
            ).fetchone()
        )
        assert (
            conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname='cx'").fetchone()[0] == 0
        )


@pytest.mark.db
def test_live_forbidden_identical_and_unsafe(live_conn):
    with pytest.raises(BadRequest):
        check_rewrite(live_conn, "SELECT 1", "SELECT DISTINCT 1", EquivConfig())
    with single_clone(get_settings()) as name, connect(name) as conn:
        assert check_rewrite(conn, "SELECT 1", "SELECT 1", EquivConfig()).label == "unknown"
        assert (
            check_rewrite(conn, "SELECT 1", "DELETE FROM orders", EquivConfig()).label == "rejected"
        )
        assert (
            check_rewrite(
                conn, "SELECT order_id FROM orders", "SELECT customer_id FROM orders", EquivConfig()
            ).label
            == "rejected"
        )


@pytest.mark.db
def test_timeout_and_optional_veto_fail_closed():
    pair = next(p for p in PAIRS if p["id"] == "E1")
    with single_clone(get_settings()) as name, connect(name) as conn:
        cfg = EquivConfig(cx_dbs=1, use_llm_veto=True)
        assert check_rewrite(conn, pair["original"], pair["rewrite"], cfg).label == "unknown"
        cfg = replace(cfg, veto_fn=lambda a, b: {"differ": True, "why": "mock veto"})
        assert check_rewrite(conn, pair["original"], pair["rewrite"], cfg).label == "rejected"
        cfg = replace(cfg, veto_fn=lambda a, b: {"differ": False, "why": "no issue found"})
        assert check_rewrite(conn, pair["original"], pair["rewrite"], cfg).label == "tested"
        result = check_rewrite(
            conn, pair["original"], pair["rewrite"], EquivConfig(stmt_timeout_ms=1)
        )
        assert result.label == "unknown"
        assert (
            conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname='cx'").fetchone()[0] == 0
        )


@pytest.mark.db
@pytest.mark.parametrize("nullable,expected_tier", [(False, 2), (True, 3)])
def test_unsupported_types_unknown_and_cleaned(nullable, expected_tier):
    with single_clone(get_settings()) as name, connect(name) as conn:
        conn.execute(
            "CREATE TABLE public.unsupported (id int PRIMARY KEY, payload json "
            + ("" if nullable else "NOT NULL")
            + ")"
        )
        original = (
            "SELECT id,payload FROM (SELECT id,payload FROM unsupported ORDER BY id) q ORDER BY id"
        )
        rewrite = "SELECT id,payload FROM unsupported ORDER BY id"
        result = check_rewrite(conn, original, rewrite, EquivConfig(cx_dbs=1))
        assert result.label == "unknown" and result.tier_reached == expected_tier, result
        assert (
            conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname='cx'").fetchone()[0] == 0
        )


@pytest.mark.db
def test_error_cleanup_and_type_family_mismatch(monkeypatch):
    pair = next(p for p in PAIRS if p["id"] == "E1")
    with single_clone(get_settings()) as name, connect(name) as conn:
        result = check_rewrite(
            conn,
            "SELECT DISTINCT order_id FROM orders",
            "SELECT order_id::text AS order_id FROM orders",
            EquivConfig(),
        )
        assert result.label == "rejected" and result.tier_reached == 0

        def fail(*args):
            import psycopg

            raise psycopg.DataError("planted insertion failure")

        monkeypatch.setattr("app.equivalence._insert_rows", fail)
        result = check_rewrite(conn, pair["original"], pair["rewrite"], EquivConfig(cx_dbs=1))
        assert result.label == "unknown"
        assert (
            conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname='cx'").fetchone()[0] == 0
        )


def test_internal_cte_order_removal_recognized():
    a = (
        "WITH q AS (SELECT order_id,total FROM orders ORDER BY total) "
        "SELECT order_id,total FROM q ORDER BY order_id"
    )
    b = (
        "WITH q AS (SELECT order_id,total FROM orders) "
        "SELECT order_id,total FROM q ORDER BY order_id"
    )
    assert recognize_class(a, b) == (3, None)


def test_llm_veto_adapter_is_explicit_and_validates_json():
    from app.equivalence import make_llm_veto

    calls = []

    def complete(messages):
        calls.append(messages)
        return '{"differ":true,"why":"test mismatch"}', {"model": "mock", "cost": 0.0}

    assert calls == []
    veto = make_llm_veto(complete)
    result = veto("SELECT 1", "SELECT DISTINCT 1")
    assert result["differ"] and result["usage"]["model"] == "mock" and len(calls) == 1
    with pytest.raises(ValueError):
        make_llm_veto(lambda m: ('{"differ":"yes"}', {}))("a", "b")


@pytest.mark.db
def test_safety_gate_precedes_candidate_execution(monkeypatch):
    import app.equivalence as module

    def forbidden(*args):
        pytest.fail("Candidate SQL reached the database before safety rejection")

    with single_clone(get_settings()) as name, connect(name) as conn:
        monkeypatch.setattr(module, "_description", forbidden)
        result = check_rewrite(
            conn,
            "SELECT order_id FROM orders",
            "SELECT oid AS order_id FROM pg_catalog.pg_class",
            EquivConfig(),
        )
        assert result.label == "rejected" and result.reason.startswith("safety:")
