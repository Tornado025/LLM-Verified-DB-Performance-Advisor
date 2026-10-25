"""Thread-safe SQLite metadata storage with ordered incremental events."""

import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import ParamSpec, TypeVar
from uuid import uuid4

from app.errors import NotFound
from app.models import Candidate

P = ParamSpec("P")
R = TypeVar("R")

_LOCK = threading.RLock()
_LOG = logging.getLogger(__name__)


def locked(method: Callable[P, R]) -> Callable[P, R]:
    """Serialize all store operations across instances and threads."""

    @wraps(method)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with _LOCK:
            return method(*args, **kwargs)

    return wrapped


def now() -> str:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC).isoformat()


class Store:
    """Persist jobs, evidence, applied changes and experiment results."""

    @locked
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    @locked
    def close(self) -> None:
        """Release the SQLite connection."""
        self.conn.close()

    @locked
    def create_job(self, params: dict) -> str:
        """Create a queued job with its immutable input parameters."""
        job_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:4]
        while self.conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
            job_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:4]
        self.conn.execute(
            "INSERT INTO jobs(id,created_at,status,params) VALUES(?,?,?,?)",
            (job_id, now(), "queued", json.dumps(params)),
        )
        return job_id

    @locked
    def get_job(self, job_id: str) -> dict:
        """Read one job, raising a structured error when missing."""
        row = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise NotFound("Job not found")
        result = dict(row)
        for key in ("params", "result"):
            result[key] = json.loads(result[key]) if result[key] is not None else None
        return result

    @locked
    def list_jobs(self, limit: int = 100) -> list[dict]:
        """Return newest jobs first."""
        return [
            self.get_job(row[0])
            for row in self.conn.execute(
                "SELECT id FROM jobs ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
            )
        ]

    @locked
    def set_job_status(self, job_id: str, status: str, result: dict | None = None) -> None:
        """Update status and record terminal completion time."""
        self.get_job(job_id)
        if status not in {"queued", "running", "done", "failed", "cancelled"}:
            raise ValueError("Unknown job status")
        self.conn.execute(
            "UPDATE jobs SET status=?,finished_at=?,result=? WHERE id=?",
            (
                status,
                now() if status in {"done", "failed", "cancelled"} else None,
                json.dumps(result) if result is not None else None,
                job_id,
            ),
        )

    @locked
    def emit(
        self, job_id: str, step: str, level: str, message: str, data: dict | None = None
    ) -> int:
        """Persist an event and log its bounded message."""
        levels = {
            "info": logging.INFO,
            "warn": logging.WARNING,
            "error": logging.ERROR,
            "result": logging.INFO,
        }
        if level not in levels:
            raise ValueError("Unknown event level")
        message = message[:2000]
        encoded = json.dumps(data) if data is not None else None
        if encoded is not None and len(encoded.encode("utf-8")) > 200 * 1024:
            encoded = json.dumps({"truncated": True})
        cursor = self.conn.execute(
            "INSERT INTO events(job_id,ts,step,level,message,data) VALUES(?,?,?,?,?,?)",
            (job_id, now(), step, level, message, encoded),
        )
        _LOG.log(levels[level], "%s", message)
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    @locked
    def events_after(self, job_id: str, last_id: int = 0, limit: int = 500) -> list[dict]:
        """Read the next ordered event page, strictly after the cursor."""
        rows = self.conn.execute(
            "SELECT * FROM events WHERE job_id=? AND id>? ORDER BY id LIMIT ?",
            (job_id, last_id, limit),
        )
        return [
            dict(row) | {"data": json.loads(row["data"]) if row["data"] else None} for row in rows
        ]

    @locked
    def save_candidate(self, c: Candidate) -> None:
        """Upsert one complete candidate while keeping indexed fields synchronized."""
        self.conn.execute(
            "INSERT INTO candidates VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) "
            "DO UPDATE SET job_id=excluded.job_id,shape_id=excluded.shape_id,"
            "kind=excluded.kind,sql=excluded.sql,status=excluded.status,data=excluded.data",
            (c.id, c.job_id, c.shape_id, c.kind, c.sql, c.status, json.dumps(c.to_dict())),
        )

    @locked
    def get_candidate(self, id: str) -> Candidate:
        """Restore a candidate contract object."""
        row = self.conn.execute("SELECT data FROM candidates WHERE id=?", (id,)).fetchone()
        if row is None:
            raise NotFound("Candidate not found")
        return Candidate.from_dict(json.loads(row[0]))

    @locked
    def get_candidates(self, job_id: str) -> list[Candidate]:
        """Return candidates in stable identifier order."""
        return [
            self.get_candidate(row[0])
            for row in self.conn.execute(
                "SELECT id FROM candidates WHERE job_id=? ORDER BY id", (job_id,)
            )
        ]

    @locked
    def save_evidence(self, job_id: str, shape_id: str, card: dict) -> None:
        """Upsert the evidence card for a job and shape."""
        self.conn.execute(
            "INSERT INTO evidence VALUES(?,?,?) ON CONFLICT(job_id,shape_id) "
            "DO UPDATE SET card=excluded.card",
            (job_id, shape_id, json.dumps(card)),
        )

    @locked
    def get_evidence(self, job_id: str) -> list[dict]:
        """Read evidence cards in stable shape order."""
        return [
            json.loads(row[0])
            for row in self.conn.execute(
                "SELECT card FROM evidence WHERE job_id=? ORDER BY shape_id", (job_id,)
            )
        ]

    @locked
    def create_applied(
        self,
        candidate_id: str,
        job_id: str,
        kind: str,
        ddl: str | None = None,
        undo_ddl: str | None = None,
        rewrite_sql: str | None = None,
        baseline: dict | None = None,
        state: str = "watching",
    ) -> str:
        """Record metadata for an applied change; this executes no PostgreSQL SQL."""
        if state not in {"healthy", "watching", "reverted", "revert_failed"}:
            raise ValueError("Unknown applied state")
        applied_id = uuid4().hex
        self.conn.execute(
            "INSERT INTO applied VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                applied_id,
                candidate_id,
                job_id,
                kind,
                ddl,
                undo_ddl,
                rewrite_sql,
                json.dumps(baseline),
                state,
                now(),
                None,
                None,
                json.dumps([]),
            ),
        )
        return applied_id

    @locked
    def get_applied(self, id: str) -> dict:
        """Read an applied change and decode its measurements."""
        row = self.conn.execute("SELECT * FROM applied WHERE id=?", (id,)).fetchone()
        if row is None:
            raise NotFound("Applied change not found")
        result = dict(row)
        for key in ("baseline", "windows"):
            result[key] = json.loads(result[key]) if result[key] is not None else None
        return result

    @locked
    def list_applied(self) -> list[dict]:
        """Return all applied changes, newest first."""
        return [
            self.get_applied(row[0])
            for row in self.conn.execute("SELECT id FROM applied ORDER BY applied_at DESC, id DESC")
        ]

    @locked
    def update_applied(self, id: str, **changes: object) -> None:
        """Update only mutable monitoring fields using validated column names."""
        self.get_applied(id)
        allowed = {"state", "reverted_at", "revert_reason", "windows", "baseline"}
        if not set(changes) <= allowed:
            raise ValueError("Unknown applied update field")
        if "state" in changes and changes["state"] not in {
            "healthy",
            "watching",
            "reverted",
            "revert_failed",
        }:
            raise ValueError("Unknown applied state")
        if not changes:
            return
        columns = ",".join(f"{key}=?" for key in changes)
        values = [json.dumps(v) if k in {"baseline", "windows"} else v for k, v in changes.items()]
        self.conn.execute(f"UPDATE applied SET {columns} WHERE id=?", (*values, id))

    @locked
    def put_experiment(self, name: str, payload: dict) -> None:
        """Upsert an experiment's measured result payload."""
        self.conn.execute(
            "INSERT INTO experiments VALUES(?,?,?) ON CONFLICT(name) "
            "DO UPDATE SET updated_at=excluded.updated_at,payload=excluded.payload",
            (name, now(), json.dumps(payload)),
        )

    @locked
    def get_experiment(self, name: str) -> dict | None:
        """Return an experiment payload, or None if not run yet."""
        row = self.conn.execute("SELECT payload FROM experiments WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else None


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
  params TEXT NOT NULL, result TEXT);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
  ts TEXT NOT NULL, step TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL, data TEXT);
CREATE INDEX IF NOT EXISTS events_job_id ON events(job_id, id);
CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), shape_id TEXT NOT NULL,
  kind TEXT NOT NULL, sql TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence (
  job_id TEXT NOT NULL REFERENCES jobs(id), shape_id TEXT NOT NULL, card TEXT NOT NULL,
  PRIMARY KEY (job_id, shape_id));
CREATE TABLE IF NOT EXISTS applied (
  id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, job_id TEXT NOT NULL, kind TEXT NOT NULL,
  ddl TEXT, undo_ddl TEXT, rewrite_sql TEXT, baseline TEXT, state TEXT NOT NULL,
  applied_at TEXT NOT NULL, reverted_at TEXT, revert_reason TEXT, windows TEXT);
CREATE TABLE IF NOT EXISTS experiments (
  name TEXT PRIMARY KEY, updated_at TEXT NOT NULL, payload TEXT NOT NULL);
"""
