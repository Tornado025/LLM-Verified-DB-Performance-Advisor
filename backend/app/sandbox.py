"""Bounded disposable clones; golden is accessed only through maintenance SQL."""

import argparse
import atexit
import logging
import math
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter
from uuid import uuid4

import psycopg
import sqlglot
from psycopg import sql
from sqlglot import exp

from app.config import Settings, get_settings
from app.errors import BadRequest, SandboxError
from app.models import Candidate, ClonePair
from app.pg import admin_connect, connect, terminate_connections

_LOG = logging.getLogger(__name__)
_LOCK = threading.RLock()
_RESERVE = threading.Lock()
_SEMAPHORE: threading.BoundedSemaphore | None = None
_LIMIT: int | None = None
_OWNED: dict[str, Settings] = {}
metrics: dict[str, dict] = {}


class InvalidDDL(SandboxError):
    """Candidate DDL was rejected by PostgreSQL in a disposable clone."""


def _validate(name: str, settings: Settings) -> None:
    prefix = settings.sandbox_prefix
    if (
        not prefix
        or len(name.encode()) > 63
        or name
        in {settings.live_db, settings.golden_db, settings.maint_db, "template0", "template1"}
        or re.fullmatch(re.escape(prefix) + r"[0-9a-f]{8}", name) is None
    ):
        raise BadRequest("Invalid sandbox database name")


def _semaphore(settings: Settings) -> threading.BoundedSemaphore:
    global _SEMAPHORE, _LIMIT
    with _LOCK:
        if settings.max_clones < 1:
            raise SandboxError("MAX_CLONES must be positive")
        if _SEMAPHORE is None:
            _SEMAPHORE = threading.BoundedSemaphore(settings.max_clones)
            _LIMIT = settings.max_clones
        elif settings.max_clones != _LIMIT:
            raise SandboxError("Restart process before changing MAX_CLONES")
        return _SEMAPHORE


def db_size_bytes(dbname: str) -> int:
    """Measure size through maintenance, never a golden connection."""
    with admin_connect() as admin:
        row = admin.execute("/* dpa:sys */ SELECT pg_database_size(%s)", (dbname,)).fetchone()
        if row is None:
            raise SandboxError("Database size unavailable")
        return int(row[0])


def _create(
    name: str | None, template: str | None, settings: Settings, reserved: bool = False
) -> str:
    name = name or settings.sandbox_prefix + uuid4().hex[:8]
    _validate(name, settings)
    template = template or settings.golden_db
    if template not in {settings.golden_db, "qc_ch_golden"}:
        _validate(template, settings)
    semaphore = _semaphore(settings)
    if not reserved:
        with _RESERVE:
            semaphore.acquire()
    created = False
    start = perf_counter()
    try:
        with admin_connect(settings) as admin:
            row = admin.execute("/* dpa:sys */ SELECT pg_database_size(%s)", (template,)).fetchone()
            if row is None:
                raise SandboxError("Template size unavailable")
            size = int(row[0])
            if settings.free_disk_gb is not None and (
                not math.isfinite(settings.free_disk_gb)
                or settings.free_disk_gb <= 0
                or size * 2 > settings.free_disk_gb * 1024**3
            ):
                raise SandboxError("Free disk hint cannot accommodate a clone pair")
            admin.execute(
                # Large templates otherwise emit their full contents into WAL,
                # triggering checkpoints during the subsequent paired blocks.
                # FILE_COPY completes its forced checkpoints before returning.
                sql.SQL("/* dpa:sys */ CREATE DATABASE {} TEMPLATE {} STRATEGY FILE_COPY").format(
                    sql.Identifier(name), sql.Identifier(template)
                )
            )
            created = True
        with _LOCK:
            _OWNED[name] = settings
        _analyze(name, None, settings)
        with _LOCK:
            metrics[name] = {
                "seconds": perf_counter() - start,
                "template_bytes": size,
                "copy_strategy": "FILE_COPY",
            }
        _LOG.info(
            "Created clone %s in %.3f seconds (%d template bytes)",
            name,
            perf_counter() - start,
            size,
        )
        return name
    except BaseException as exc:
        if created:
            _drop(name, settings)
        else:
            semaphore.release()
        if not isinstance(exc, Exception) or isinstance(exc, SandboxError):
            raise
        raise SandboxError("Clone creation failed", {"error_type": type(exc).__name__}) from exc


def create_clone(name: str | None = None, template: str | None = None) -> str:
    """Acquire a slot, clone and analyze a private database."""
    return _create(name, template, get_settings())


def _drop(name: str, settings: Settings) -> None:
    _validate(name, settings)
    with admin_connect(settings) as admin:
        terminate_connections(admin, name)
        admin.execute(
            sql.SQL("/* dpa:sys */ DROP DATABASE IF EXISTS {}").format(sql.Identifier(name))
        )
    with _LOCK:
        if _OWNED.pop(name, None) is not None:
            _semaphore(settings).release()


def drop_clone(name: str) -> None:
    """Drop a validated sandbox, releasing a locally owned slot only once."""
    _drop(name, _OWNED.get(name, get_settings()))


def list_clones() -> list[str]:
    """List only names conforming to this project's sandbox naming rule."""
    s = get_settings()
    with admin_connect(s) as admin:
        names = [row[0] for row in admin.execute("/* dpa:sys */ SELECT datname FROM pg_database")]
    return sorted(n for n in names if re.fullmatch(re.escape(s.sandbox_prefix) + r"[0-9a-f]{8}", n))


def drop_all_clones() -> int:
    """Explicitly clean all matching project clones; return number removed."""
    names = list_clones()
    for name in names:
        drop_clone(name)
    return len(names)


def _analyze(dbname: str, table: str | None, settings: Settings) -> None:
    _validate(dbname, settings)
    with connect(dbname, settings=settings) as conn:
        statement: sql.SQL | sql.Composed = sql.SQL("/* dpa:sys */ ANALYZE")
        if table is not None:
            statement += sql.SQL(" {}").format(sql.Identifier(table))
        conn.execute(statement)


def analyze(dbname: str, table: str | None = None) -> None:
    """Analyze a sandbox, optionally a single public table."""
    _analyze(dbname, table, _OWNED.get(dbname, get_settings()))


def apply_ddl(dbname: str, statement: str) -> None:
    """Apply one trusted index/statistics fixture in a sandbox only.

    This is not the later LLM safety layer; its caller must safety-check LLM output.
    """
    s = _OWNED.get(dbname, get_settings())
    _validate(dbname, s)
    parsed = sqlglot.parse(statement, read="postgres")
    if len(parsed) != 1 or parsed[0] is None:
        raise BadRequest("Exactly one DDL statement is required")
    tree = parsed[0]
    allowed = isinstance(tree, exp.Analyze) or (
        isinstance(tree, exp.Create)
        and tree.args.get("kind") == "INDEX"
        and not tree.args.get("concurrently")
    )
    # sqlglot treats PostgreSQL CREATE STATISTICS as an opaque command. Accept
    # that single command only; PostgreSQL still validates its syntax in a clone.
    allowed = allowed or (
        isinstance(tree, exp.Command)
        and re.match(r"^\s*CREATE\s+STATISTICS\s+", statement, re.I) is not None
    )
    if not allowed:
        raise BadRequest("Only nonconcurrent index/statistics DDL is allowed")
    with connect(dbname, settings=s) as conn:
        conn.execute(statement)


@contextmanager
def single_clone(settings: Settings) -> Iterator[str]:
    """Own one clone for exactly the lifetime of a context."""
    name = _create(None, None, settings)
    try:
        yield name
    finally:
        _drop(name, settings)


@contextmanager
def clone_pair(candidate: Candidate, settings: Settings) -> Iterator[ClonePair]:
    """Reserve both slots atomically, apply only to B, and clean both on errors."""
    _validate(settings.sandbox_prefix + "00000000", settings)
    if settings.max_clones < 2:
        raise SandboxError("A clone pair requires MAX_CLONES >= 2")
    semaphore = _semaphore(settings)
    # Serialize acquisition of the pair to avoid partial-reservation deadlocks.
    with _RESERVE:
        semaphore.acquire()
        semaphore.acquire()
    names: list[str] = []
    unconsumed = 2
    try:
        for _ in range(2):
            unconsumed -= 1  # _create assumes ownership of this slot even on failure
            names.append(_create(None, None, settings, reserved=True))
        if candidate.kind in {"index", "stats"}:
            try:
                apply_ddl(names[1], candidate.sql)
            except psycopg.Error as exc:
                candidate.status = "rejected_invalid_ddl"
                candidate.reason = exc.diag.message_primary or type(exc).__name__
                raise InvalidDDL(candidate.reason) from exc
        elif candidate.kind != "rewrite":
            raise BadRequest("Unknown candidate kind")
        for name in names:
            _analyze(name, None, settings)
        yield ClonePair(*names)
    finally:
        for _ in range(unconsumed):
            semaphore.release()
        # Attempt every cleanup even when dropping one database fails.
        failures = []
        for name in reversed(names):
            try:
                _drop(name, settings)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise SandboxError("Sandbox cleanup failed") from failures[0]


def _cleanup_owned() -> None:
    for name, settings in list(_OWNED.items()):
        try:
            _drop(name, settings)
        except Exception:
            _LOG.exception("Could not clean owned clone %s", name)


atexit.register(_cleanup_owned)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup", action="store_true")
    if parser.parse_args().cleanup:
        print(drop_all_clones())
