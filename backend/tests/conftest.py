"""Shared fixtures; database connections are opened only by database tests."""

import os
from collections.abc import Iterator

import numpy as np
import psycopg
import pytest
from dotenv import load_dotenv

load_dotenv("../.env", override=False)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Keep real LLM calls opt-in, even when explicitly selecting their marker."""
    if os.getenv("RUN_LLM_TESTS") != "1":
        for item in items:
            if "llm" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="Set RUN_LLM_TESTS=1 to call an LLM"))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Allow the intentionally empty scaffold suite; preserve all failure codes."""
    if exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
        session.exitstatus = pytest.ExitCode.OK


@pytest.fixture
def rng() -> np.random.Generator:
    """Return a fresh deterministic generator for each test."""
    return np.random.default_rng(1234)


@pytest.fixture
def settings():
    """Load the settings implementation introduced in step 03."""
    from app.config import get_settings

    return get_settings()


@pytest.fixture(autouse=True)
def require_database(request: pytest.FixtureRequest) -> None:
    """Skip database tests if the maintenance database is unavailable."""
    if request.node.get_closest_marker("db") is None:
        return
    try:
        with psycopg.connect(
            host=os.getenv("PG_HOST", "localhost"),
            port=int(os.getenv("PG_PORT", "5432")),
            user=os.getenv("PG_USER", "advisor"),
            password=os.getenv("PG_PASSWORD", "advisor"),
            dbname=os.getenv("MAINT_DB", "postgres"),
            connect_timeout=3,
            autocommit=True,
        ):
            pass
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL maintenance database is unavailable; run make up")


@pytest.fixture
def live_conn() -> Iterator[psycopg.Connection]:
    """Provide a dedicated live connection using step 03 connection policy."""
    from app.pg import connect

    with connect(os.getenv("LIVE_DB", "qc_live"), autocommit=True) as conn:
        yield conn
