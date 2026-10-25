"""Backend contracts, persistence, concurrency and connection policy checks."""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields
from datetime import datetime

import psycopg
import pytest

from app.config import Settings, get_settings
from app.errors import NotFound
from app.logging_conf import SecretFilter
from app.models import Candidate, Summary
from app.pg import connect, dsn, mask_dsn, render_sql
from app.store import Store


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch):
    """Make configuration tests independent of developer environment secrets."""
    for f in fields(Settings):
        monkeypatch.delenv(f.name.upper(), raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def store(tmp_path):
    """Provide an isolated file-backed WAL store."""
    result = Store(tmp_path / "meta.sqlite")
    yield result
    result.close()


def test_settings_defaults():
    s = get_settings()
    assert s.llm_provider == "mock"
    assert s.delta == 0.05 and s.alpha == 0.05
    assert s.looks == 19
    assert s.guard_profiles == ["normal", "flash5", "flash10", "flash20"]
    assert get_settings() is s
    with pytest.raises(FrozenInstanceError):
        s.delta = 0.1


@pytest.mark.parametrize(
    "changes",
    [
        {"delta": 0},
        {"delta": 1},
        {"alpha": 0.5},
        {"min_runs": 2},
        {"max_runs": 5},
        {"k_max": 0},
        {"patience": 0},
        {"race_batch": 0},
        {"llm_provider": "unknown"},
        {"llm_provider": "openrouter"},
        {"llm_provider": "ollama"},
        {"metric": "unknown"},
        {"guard_profiles": ["unknown"]},
        {"guard_profiles": []},
        {"stmt_timeout_ms": 0},
    ],
)
def test_settings_validation_errors(changes):
    with pytest.raises(ValueError):
        Settings(**changes)


def test_settings_public_masks_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "private-key")
    monkeypatch.setenv("PG_PASSWORD", "private-password")
    s = get_settings()
    assert s.public()["openrouter_api_key"] == "***"
    assert s.public()["pg_password"] == "***"
    assert s.openrouter_api_key == "private-key"
    monkeypatch.setenv("DELTA", "0.1")
    assert get_settings().delta == 0.05
    get_settings.cache_clear()
    assert get_settings().delta == 0.1


@pytest.mark.parametrize(
    "text",
    [
        "postgresql://user:secret@localhost/db",
        "postgres://user:p%40ss@localhost/db",
        "host=localhost password=secret dbname=x",
        "password='a secret\\' value' host=localhost",
    ],
)
def test_mask_dsn(text):
    result = mask_dsn(text)
    assert "***" in result and "secret" not in result and "p%40ss" not in result


def test_logging_redacts_args_and_exceptions(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
    record = logging.LogRecord(
        "test", logging.ERROR, "", 1, "%s %s", ("secret-key", "postgresql://u:hidden@host/db"), None
    )
    assert SecretFilter().filter(record)
    assert "secret-key" not in record.getMessage()
    assert "hidden" not in record.getMessage()
    try:
        raise ValueError("secret-key postgresql://u:hidden@host/db")
    except ValueError:
        import sys

        record.exc_info = sys.exc_info()
    SecretFilter().filter(record)
    assert "secret-key" not in record.exc_text and "hidden" not in record.exc_text


def test_store_job_roundtrip(store):
    job = store.create_job({"seed": 1234})
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", job)
    assert store.get_job(job)["params"] == {"seed": 1234}
    assert datetime.fromisoformat(store.get_job(job)["created_at"]).utcoffset().total_seconds() == 0
    store.set_job_status(job, "done", {"result": True})
    assert store.get_job(job)["finished_at"] is not None
    assert store.list_jobs(1)[0]["result"] == {"result": True}
    with pytest.raises(NotFound):
        store.get_job("missing")


def test_store_events_ordered_and_incremental(store):
    job = store.create_job({})
    ids = [store.emit(job, "system", "info", str(i), {"i": i}) for i in range(50)]
    assert [e["id"] for e in store.events_after(job)] == ids
    assert [e["id"] for e in store.events_after(job, ids[24])] == ids[25:]
    store.emit(job, "system", "warn", "x" * 3000, {"large": "é" * 210000})
    event = store.events_after(job, ids[-1])[0]
    assert len(event["message"]) == 2000 and event["data"] == {"truncated": True}
    with pytest.raises(Exception, match="FOREIGN KEY"):
        store.emit("missing", "system", "info", "bad")


def test_store_threadsafe(store):
    job = store.create_job({})

    def worker(i):
        for j in range(100):
            store.emit(job, "system", "info", f"{i}:{j}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(8)))
    events = store.events_after(job, limit=1000)
    ids = [e["id"] for e in events]
    assert len(ids) == len(set(ids)) == 800 and ids == sorted(ids)
    assert len({e["message"] for e in events}) == 800


def test_store_candidate_upsert(store):
    job = store.create_job({})
    c = Candidate(f"{job}:shape:01", job, "shape", "rewrite", "SELECT 1", "reason", 1)
    store.save_candidate(c)
    c.status = "selected"
    store.save_candidate(c)
    assert store.get_candidate(c.id) == c
    assert store.get_candidates(job) == [c]
    summary = Summary(6, 0.01, 0.0, 0.02, 0.01, "PASS")
    assert Summary.from_dict(summary.to_dict()) == summary


def test_store_applied_lifecycle(store):
    job = store.create_job({})
    id = store.create_applied(
        "candidate", job, "index", ddl="CREATE INDEX example", baseline={"mean": 10}
    )
    assert store.get_applied(id)["baseline"] == {"mean": 10}
    store.update_applied(id, state="healthy", windows=[{"mean": 10}])
    store.update_applied(
        id, state="reverted", reverted_at="2026-10-25T00:00:00+00:00", revert_reason="regression"
    )
    row = store.list_applied()[0]
    assert row["state"] == "reverted" and row["windows"] == [{"mean": 10}]
    with pytest.raises(ValueError):
        store.update_applied(id, ddl="unsafe")


def test_evidence_experiments_and_reopen(tmp_path):
    path = tmp_path / "persistent.sqlite"
    s = Store(path)
    job = s.create_job({})
    s.save_evidence(job, "shape", {"verified": False})
    s.save_evidence(job, "shape", {"verified": True})
    s.put_experiment("rq1", {"n": 1})
    s.close()
    s = Store(path)
    assert s.get_evidence(job) == [{"verified": True}]
    assert s.get_experiment("rq1") == {"n": 1}
    assert s.get_experiment("missing") is None
    s.close()


def test_connect_policy_and_retries(monkeypatch):
    s = Settings(pg_user="user@x", pg_password="pass:/@")
    assert "user%40x:pass%3A%2F%40@" in dsn("db name", s)
    with pytest.raises(ValueError, match="golden"):
        connect(s.golden_db, settings=s)
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise psycopg.OperationalError("offline")

    monkeypatch.setattr("app.pg.psycopg.connect", fail)
    monkeypatch.setattr("app.pg.time.sleep", lambda seconds: None)
    with pytest.raises(psycopg.OperationalError):
        connect(s.live_db, settings=s)
    assert len(calls) == 5


@pytest.mark.db
def test_connect_live(live_conn):
    assert live_conn.execute("SHOW application_name").fetchone()[0] == "dpa"
    assert live_conn.execute("SHOW statement_timeout").fetchone()[0] == "1min"
    assert live_conn.autocommit


@pytest.mark.db
def test_render_sql_quotes(live_conn):
    sql = render_sql(live_conn, "SELECT %(value)s::text", {"value": "O'Reilly"})
    assert "O''Reilly" in sql
    assert live_conn.execute(sql).fetchone()[0] == "O'Reilly"


def test_every_contract_setting_present():
    """Catch accidentally omitted environment settings as the contract evolves."""
    from pathlib import Path

    contract = (Path(__file__).parents[2] / "docs/steps/00_contracts.md").read_text()
    section = contract.split("## 0.2")[1].split("## 0.3")[0]
    expected = set(re.findall(r"^([A-Z][A-Z_0-9]*)=", section, re.MULTILINE))
    assert {f.name.upper() for f in fields(Settings)} == expected


def test_separate_store_connections_share_wal(tmp_path):
    """Independent connections safely persist into the same metadata file."""
    path = tmp_path / "shared.sqlite"
    a, b = Store(path), Store(path)
    job = a.create_job({})
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(lambda s: [s.emit(job, "system", "info", "event") for _ in range(100)], [a, b])
        )
    assert len(b.events_after(job)) == 200
    a.close()
    b.close()
