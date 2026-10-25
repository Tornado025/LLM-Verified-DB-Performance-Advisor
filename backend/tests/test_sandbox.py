"""Disposable clone ownership, safety and cleanup integration checks."""

from dataclasses import replace

import pytest

from app.config import get_settings
from app.errors import BadRequest, SandboxError
from app.models import Candidate
from app.pg import admin_connect, connect
from app.sandbox import InvalidDDL, clone_pair, drop_clone, list_clones, metrics, single_clone


def candidate(statement="CREATE INDEX step07_customer ON orders(customer_id)"):
    return Candidate(
        "test:shape:01", "test", "recent_orders", "index", statement, "test fixture", 1
    )


def test_refuse_bad_name():
    for name in ("qc_live", "qc_golden", "qc_sbx_", "qc_sbx_x';DROP DATABASE qc_live"):
        with pytest.raises(BadRequest):
            drop_clone(name)


@pytest.mark.db
def test_clone_roundtrip():
    s = get_settings()
    with single_clone(s) as name:
        assert name in list_clones()
        with connect(name) as conn:
            scale = int(conn.execute("SELECT value FROM meta WHERE key='scale'").fetchone()[0])
            assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == 300000 * scale
            conn.execute("INSERT INTO meta VALUES('clone_probe','1')")
        assert metrics[name]["seconds"] > 0 and metrics[name]["template_bytes"] > 0
    assert name not in list_clones()
    # A second clone independently confirms template content without connecting to golden.
    with single_clone(s) as second, connect(second) as conn:
        assert conn.execute("SELECT value FROM meta WHERE key='clone_probe'").fetchone() is None
    with admin_connect() as admin:
        assert (
            admin.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname=%s", (s.golden_db,)
            ).fetchone()[0]
            == 0
        )


@pytest.mark.db
def test_clone_pair_ddl_only_on_b():
    before = set(list_clones())
    with clone_pair(candidate(), get_settings()) as pair:
        for db, expected in ((pair.a, 0), (pair.b, 1)):
            with connect(db) as conn:
                assert (
                    conn.execute(
                        "SELECT count(*) FROM pg_indexes WHERE indexname='step07_customer'"
                    ).fetchone()[0]
                    == expected
                )
    assert set(list_clones()) == before


@pytest.mark.db
def test_invalid_ddl_marks_candidate():
    c = candidate("CREATE INDEX ON orders (DATE(delivered_at))")
    before = set(list_clones())
    with pytest.raises(InvalidDDL), clone_pair(c, get_settings()):
        pytest.fail("Invalid DDL unexpectedly succeeded")
    assert c.status == "rejected_invalid_ddl" and c.reason
    assert set(list_clones()) == before


@pytest.mark.db
def test_cleanup_on_exception():
    before = set(list_clones())
    with pytest.raises(RuntimeError), clone_pair(candidate(), get_settings()):
        raise RuntimeError("planted")
    assert set(list_clones()) == before


@pytest.mark.db
def test_disk_hint_refuses_and_releases_slot():
    with pytest.raises(SandboxError), single_clone(replace(get_settings(), free_disk_gb=1e-9)):
        pytest.fail("Insufficient disk accepted")
    with single_clone(get_settings()):
        pass
