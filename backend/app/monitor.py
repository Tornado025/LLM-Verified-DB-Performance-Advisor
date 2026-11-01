"""Approved live changes and conservative monitoring of statement latency windows."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import time
from datetime import UTC, datetime

import sqlglot
from psycopg import sql
from sqlglot import exp

from app.catalog import get_catalog
from app.config import get_settings
from app.errors import Conflict, SafetyRejected, SandboxError
from app.models import RawCandidate
from app.pg import connect
from app.plans import _index_definition
from app.safety import check
from app.shapes import ORDER_TXNS
from app.store import Store, now
from app.workload import build_schedule, load_pools, load_profiles, run_block

_LOG = logging.getLogger(__name__)
_CHANGE_LOCK = threading.RLock()
_TRAFFIC_LOCK = threading.Lock()
_TRAFFIC_ACTIVE = False


def snapshot(shape_id: str) -> dict:
    """Read live cumulative statement counters, never transaction latency."""
    names = ["shape:" + shape_id] + ["txn:" + name for name in ORDER_TXNS]
    groups = {name: {"calls": 0, "total_ms": 0.0, "sum_sq_ms": 0.0} for name in names}
    with connect(get_settings().live_db) as conn:
        rows = conn.execute(
            "/* dpa:sys */ SELECT query,calls,total_exec_time,mean_exec_time,stddev_exec_time "
            "FROM pg_stat_statements WHERE dbid=(SELECT oid FROM pg_database "
            "WHERE datname=current_database())"
        ).fetchall()
    for query, calls, total, mean, sd in rows:
        tag = re.match(r"^\s*/\*\s*dpa:(shape|txn)=([a-z_0-9]+)\s*\*/", query)
        if not tag or tag[1] + ":" + tag[2] not in groups:
            continue
        group = groups[tag[1] + ":" + tag[2]]
        group["calls"] += int(calls)
        group["total_ms"] += float(total)
        group["sum_sq_ms"] += int(calls) * (float(sd) ** 2 + float(mean) ** 2)
    return {"groups": groups, "taken_at": now()}


def _delta(previous: dict, current: dict) -> dict | None:
    calls = current["calls"] - previous.get("calls", 0)
    total = current["total_ms"] - previous.get("total_ms", 0)
    squares = current.get("sum_sq_ms", 0) - previous.get("sum_sq_ms", 0)
    if calls < 0 or total < -1e-6 or squares < -1e-6:
        return None
    mean = total / calls if calls else None
    sd = math.sqrt(max(0, squares / calls - mean**2)) if calls and mean is not None else None
    return {"calls": calls, "total_ms": total, "mean_ms": mean, "sd_ms": sd}


def capture_baseline(shape_id: str) -> dict:
    """Measure a pre-change window; low traffic is filled by explicit normal live traffic."""
    settings = get_settings()
    before = snapshot(shape_id)
    profile = load_profiles()["normal"]
    pools = load_pools(settings.live_db, profile)
    # This fallback is part of apply and intentionally writes ordinary order traffic to live.
    schedule = build_schedule(
        profile, settings.baseline_seconds, 1234, settings.base_rate_tps, pools
    )
    result = run_block(settings.live_db, schedule, settings.workers)
    if not result.metadata.get("valid"):
        raise Conflict("Baseline traffic measurement was invalid; change not applied")
    after = snapshot(shape_id)
    groups: dict[str, dict] = {}
    for name, current in after["groups"].items():
        window = _delta(before["groups"].get(name, {}), current)
        groups[name] = current | {"mean_ms": window["mean_ms"] if window else None}
    return {
        "groups": groups,
        "taken_at": after["taken_at"],
        "source": "pre_change_normal_live_traffic",
        "statement_latency": True,
    }


def _execute(statement: str) -> None:
    """Run approved live DDL with lock timeout and a Python cancellation deadline."""
    settings = get_settings()
    with connect(settings.live_db) as conn:
        conn.execute("/* dpa:sys */ SET lock_timeout='5s'")
        conn.execute("/* dpa:sys */ SET statement_timeout=0")
        timer = threading.Timer(settings.apply_timeout_s, conn.cancel)
        timer.daemon = True
        timer.start()
        try:
            conn.execute(statement)
        finally:
            timer.cancel()


def _index_state(name: str) -> bool | None:
    with connect(get_settings().live_db) as conn:
        row = conn.execute(
            "/* dpa:sys */ SELECT i.indisvalid FROM pg_index i "
            "JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname=%s",
            (name,),
        ).fetchone()
    return bool(row[0]) if row else None


def _index_sql(candidate) -> tuple[str, str, str]:
    digest = hashlib.sha1(candidate.sql.encode(), usedforsecurity=False).hexdigest()[:8]
    shape = re.sub(r"[^a-z0-9_]", "_", candidate.shape_id.lower())[:40]
    name = f"dpa_{shape}_{digest}"
    tree = sqlglot.parse_one(candidate.sql, read="postgres")
    tree.this.set("this", exp.to_identifier(name))
    tree.set("concurrently", True)
    tree.set("exists", False)
    undo = (
        sql.SQL("DROP INDEX CONCURRENTLY IF EXISTS public.{}")
        .format(sql.Identifier(name))
        .as_string()
    )
    return name, tree.sql(dialect="postgres"), undo


def apply(
    store: Store, candidate_id: str, approver: str = "human", *, confirm: bool = False
) -> dict:
    """Record explicit human approval, recheck evidence/safety, then apply one change."""
    if not isinstance(approver, str) or not approver.strip():
        raise Conflict("A named approver is required")
    with _CHANGE_LOCK:
        candidate = store.get_candidate(candidate_id)
        existing = [
            row
            for row in store.list_applied()
            if row["candidate_id"] == candidate_id and row["state"] != "reverted"
        ]
        if existing:
            return existing[0]
        if candidate.status not in {"selected", "approved"}:
            raise Conflict("Only a selected candidate can be approved")
        cards = [
            card
            for card in store.get_evidence(candidate.job_id)
            if card.get("shape_id") == candidate.shape_id
        ]
        if not cards or cards[0].get("decision", {}).get("candidate_id") != candidate.id:
            raise Conflict("Evidence decision does not select this candidate")
        card = cards[0]
        saved = next((c for c in card.get("candidates", []) if c["id"] == candidate.id), None)
        if not saved or saved.get("sql") != candidate.sql or saved.get("kind") != candidate.kind:
            raise Conflict("Candidate differs from verified evidence")
        if candidate.kind == "rewrite":
            if (candidate.equiv or {}).get("label") != "tested" or (saved.get("equiv") or {}).get(
                "label"
            ) != "tested":
                raise Conflict("Rewrite has not been tested")
            if (
                (candidate.equiv or {}).get("weak")
                or (saved.get("equiv") or {}).get("weak")
                or card["decision"].get("requires_confirmation")
            ) and not confirm:
                raise Conflict("Weak rewrite needs explicit confirmation")
        elif not (candidate.guard or {}).get("passed") or not (saved.get("guard") or {}).get(
            "passed"
        ):
            raise Conflict("Candidate has no passing guard")
        settings = get_settings()
        with connect(settings.live_db) as conn:
            catalog = get_catalog(conn)
            catalog.original_sql = card.get("baseline", {}).get("sql")
            raw = RawCandidate(candidate.kind, candidate.sql, candidate.rationale)
            allowed, reason = check(raw, catalog)
            if not allowed:
                raise SafetyRejected(reason)
            if candidate.kind == "index":
                definition = _index_definition(raw.sql)
                if any(
                    _index_definition(item["definition"]) == definition for item in catalog.indexes
                ):
                    raise Conflict("An identical live index already exists")
        digest = hashlib.sha256(candidate.sql.encode()).hexdigest()
        store.save_approval(candidate.id, approver.strip(), confirm, digest)
        candidate.status = "approved"
        store.save_candidate(candidate)
        store.emit(
            candidate.job_id,
            "apply",
            "info",
            "Human approval recorded",
            {"candidate_id": candidate.id, "approver": approver, "confirmed": confirm},
        )
        if candidate.kind == "rewrite":
            applied_id = store.create_applied(
                candidate.id,
                candidate.job_id,
                "rewrite",
                rewrite_sql=candidate.sql,
                state="healthy",
            )
        else:
            baseline = capture_baseline(candidate.shape_id)
            name, ddl, undo = None, raw.sql, None
            if candidate.kind == "index":
                name, ddl, undo = _index_sql(candidate)
                if _index_state(name) is not None:
                    raise Conflict("Managed index name is already occupied")
            elif re.match(r"^\s*CREATE\s+STATISTICS", raw.sql, re.I):
                match = re.match(r'^\s*CREATE\s+STATISTICS\s+("[^"]+"|\w+)', raw.sql, re.I)
                if not match:
                    raise SafetyRejected("Cannot identify statistics object")
                stat_name = match[1].strip('"')
                with connect(settings.live_db) as conn:
                    if conn.execute(
                        "/* dpa:sys */ SELECT 1 FROM pg_statistic_ext WHERE stxname=%s",
                        (stat_name,),
                    ).fetchone():
                        raise Conflict("Statistics object already exists")
                undo = (
                    sql.SQL("DROP STATISTICS IF EXISTS public.{}")
                    .format(sql.Identifier(stat_name))
                    .as_string()
                )
            applied_id = store.create_applied(
                candidate.id,
                candidate.job_id,
                candidate.kind,
                ddl=ddl,
                undo_ddl=undo,
                baseline=baseline,
            )
            try:
                _execute(ddl)
                if name and _index_state(name) is not True:
                    raise SandboxError("Concurrent index build left an invalid index")
                if candidate.kind == "stats" and undo:
                    tree_match = re.search(r"\bFROM\s+(\w+)\s*;?$", raw.sql, re.I)
                    if tree_match:
                        _execute(
                            sql.SQL("ANALYZE public.{}")
                            .format(sql.Identifier(tree_match[1]))
                            .as_string()
                        )
            except Exception:
                cleanup_ok = False
                try:
                    if undo:
                        _execute(undo)
                    cleanup_ok = name is None or _index_state(name) is None
                finally:
                    store.update_applied(
                        applied_id,
                        state="reverted" if cleanup_ok else "revert_failed",
                        reverted_at=now() if cleanup_ok else None,
                        revert_reason="apply_failed",
                    )
                    store.emit(
                        candidate.job_id,
                        "apply",
                        "error",
                        "Apply failed; cleanup attempted",
                        {"candidate_id": candidate.id, "cleanup_ok": cleanup_ok},
                    )
                raise
        candidate.status = "applied"
        store.save_candidate(candidate)
        store.emit(
            candidate.job_id,
            "apply",
            "result",
            "Approved change applied",
            {
                "candidate_id": candidate.id,
                "applied_id": applied_id,
                "statement_latency": True,
                "no_undo": candidate.kind == "stats"
                and not store.get_applied(applied_id)["undo_ddl"],
            },
        )
        return store.get_applied(applied_id)


def revert(store: Store, applied_id: str, reason: str, actor: str = "human") -> dict:
    """Undo only a recorded approved change; preserve failures for human review."""
    if actor not in {"human", "monitor"}:
        raise Conflict("Unknown revert actor")
    with _CHANGE_LOCK:
        row = store.get_applied(applied_id)
        if row["state"] == "reverted":
            return row
        if not store.get_approval(row["candidate_id"]):
            raise Conflict("Applied change has no approval record")
        if not row["undo_ddl"] and row["kind"] != "rewrite":
            raise Conflict("ANALYZE has no automatic undo")
        for attempt in range(3):
            try:
                if row["undo_ddl"]:
                    _execute(row["undo_ddl"])
                if row["kind"] == "index":
                    match = re.search(r'public\."?([a-z0-9_]+)"?$', row["undo_ddl"])
                    if not match or _index_state(match[1]) is not None:
                        raise SandboxError("Index removal could not be verified")
                break
            except Exception:
                if attempt == 2:
                    store.update_applied(applied_id, state="revert_failed", revert_reason=reason)
                    store.emit(
                        row["job_id"],
                        "monitor",
                        "error",
                        "Revert failed",
                        {"applied_id": applied_id, "reason": reason},
                    )
                    raise
                time.sleep(1)
        store.update_applied(applied_id, state="reverted", reverted_at=now(), revert_reason=reason)
        candidate = store.get_candidate(row["candidate_id"])
        candidate.status = "reverted"
        store.save_candidate(candidate)
        store.emit(
            row["job_id"],
            "monitor",
            "warn",
            "Applied change reverted",
            {
                "applied_id": applied_id,
                "actor": actor,
                "reason": reason,
                "windows": row["windows"][-2:],
            },
        )
        return store.get_applied(applied_id)


def evaluate_window(
    row: dict,
    cumulative: dict,
    *,
    delta: float,
    min_calls: int,
    consecutive: int,
    z_gate: bool = False,
) -> dict:
    """Compare counter differences, requiring repeated regression of the same group."""
    baseline = row["baseline"]
    prior_windows = row.get("windows", [])
    previous = prior_windows[-1]["cumulative"] if prior_windows else baseline
    groups: dict[str, dict] = {}
    reset = False
    for name, current in cumulative["groups"].items():
        diff = _delta(previous["groups"].get(name, {}), current)
        base_mean = baseline["groups"].get(name, {}).get("mean_ms")
        if diff is None:
            reset = True
            groups[name] = {"state": "reset", "streak": 0}
            continue
        if diff["calls"] < min_calls or not base_mean or base_mean <= 0:
            groups[name] = diff | {"state": "insufficient", "streak": 0}
            continue
        relative = diff["mean_ms"] / base_mean - 1
        sd = diff["sd_ms"]
        z = (
            (diff["mean_ms"] - base_mean) / (sd / math.sqrt(diff["calls"]))
            if sd and sd > 0
            else None
        )
        regressed = relative > delta and (not z_gate or (z is not None and z > 2))
        old_streak = (
            prior_windows[-1]["groups"].get(name, {}).get("streak", 0) if prior_windows else 0
        )
        groups[name] = diff | {
            "rel": relative,
            "z": z,
            "state": "regressed" if regressed else "clean",
            "streak": old_streak + 1 if regressed else 0,
        }
    max_streak = max((g["streak"] for g in groups.values()), default=0)
    return {
        "taken_at": cumulative["taken_at"],
        "cumulative": cumulative,
        "groups": groups,
        "reset": reset,
        "state": "watching" if max_streak else "healthy",
        "revert": not reset and max_streak >= consecutive,
        "streak": max_streak,
    }


class MonitorThread(threading.Thread):
    """Periodically evaluate applied changes, reverting at most one per interval."""

    def __init__(
        self,
        store: Store,
        interval_s: float = 60,
        min_calls: int = 30,
        consecutive: int = 2,
        *,
        z_gate: bool = False,
    ):
        super().__init__(name="dpa-monitor", daemon=True)
        if interval_s <= 0 or min_calls < 1 or consecutive < 1:
            raise ValueError("Invalid monitor policy")
        self.store, self.interval_s = store, interval_s
        self.min_calls, self.consecutive, self.z_gate = min_calls, consecutive, z_gate
        self.stop_event = threading.Event()

    def stop(self) -> None:
        """Request shutdown without blocking the caller."""
        self.stop_event.set()

    def evaluate(self) -> None:
        """Evaluate one interval and perform at most one automatic revert."""
        reverted = False
        with _CHANGE_LOCK:
            for row in self.store.list_applied():
                if row["state"] not in {"healthy", "watching"} or row["kind"] == "rewrite":
                    continue
                candidate = self.store.get_candidate(row["candidate_id"])
                current = snapshot(candidate.shape_id)
                window = evaluate_window(
                    row,
                    current,
                    delta=get_settings().delta,
                    min_calls=self.min_calls,
                    consecutive=self.consecutive,
                    z_gate=self.z_gate,
                )
                windows = (row["windows"] + [window])[-500:]
                self.store.update_applied(row["id"], state=window["state"], windows=windows)
                if window["reset"]:
                    # Reset invalidates attribution; require a new baseline window.
                    baseline = current | {
                        "groups": {
                            name: value | {"mean_ms": None}
                            for name, value in current["groups"].items()
                        }
                    }
                    baseline["rebaseline_pending"] = True
                    self.store.update_applied(row["id"], baseline=baseline, windows=[])
                    self.store.emit(
                        row["job_id"],
                        "monitor",
                        "warn",
                        "Counters reset; baseline invalidated",
                        {"applied_id": row["id"]},
                    )
                    continue
                missing = [
                    name
                    for name, group in row["baseline"]["groups"].items()
                    if group.get("mean_ms") is None
                ]
                if missing and row["baseline"].get("rebaseline_pending"):
                    baseline = row["baseline"]
                    for name in missing:
                        diff = _delta(
                            (row["windows"][-1]["cumulative"] if row["windows"] else baseline)[
                                "groups"
                            ].get(name, {}),
                            current["groups"][name],
                        )
                        if diff and diff["calls"] >= self.min_calls:
                            baseline["groups"][name]["mean_ms"] = diff["mean_ms"]
                    self.store.update_applied(row["id"], baseline=baseline)
                age = (
                    datetime.now(UTC) - datetime.fromisoformat(row["applied_at"])
                ).total_seconds()
                self.store.emit(
                    row["job_id"],
                    "monitor",
                    "info",
                    "Statement latency window measured",
                    {"applied_id": row["id"], "window": window},
                )
                if (
                    window["revert"]
                    and not reverted
                    and row["undo_ddl"]
                    and (age >= get_settings().min_age_s or window["streak"] >= 2)
                ):
                    revert(
                        self.store, row["id"], "confirmed statement latency regression", "monitor"
                    )
                    reverted = True

    def run(self) -> None:
        """Poll until stopped; preserve errors as audit events and retry next interval."""
        while not self.stop_event.wait(self.interval_s):
            try:
                self.evaluate()
            except Exception:
                _LOG.exception("Monitor interval failed")


class LiveTraffic(threading.Thread):
    """Own the single live workload generator and its latest measured summary."""

    def __init__(self, profile: str = "normal", *, seconds: float = 60, seed: int = 1234):
        super().__init__(name="dpa-live-traffic", daemon=True)
        if profile not in load_profiles() or seconds <= 0 or seed < 0:
            raise ValueError("Invalid live traffic policy")
        self.profile, self.seconds, self.seed = profile, seconds, seed
        self.stop_event = threading.Event()
        self.latest: dict | None = None
        self.recent: list[dict] = []
        self.error: str | None = None

    def start(self) -> None:
        """Reserve the one live-generator slot before starting the thread."""
        global _TRAFFIC_ACTIVE
        with _TRAFFIC_LOCK:
            if _TRAFFIC_ACTIVE:
                raise Conflict("Live traffic is already running")
            _TRAFFIC_ACTIVE = True
        try:
            super().start()
        except BaseException:
            with _TRAFFIC_LOCK:
                _TRAFFIC_ACTIVE = False
            raise

    def stop(self) -> None:
        """Request that the current block and generator stop."""
        self.stop_event.set()

    def run(self) -> None:
        """Run ordinary live transactions without clone-style bulk preparation."""
        global _TRAFFIC_ACTIVE
        settings = get_settings()
        index = 0
        try:
            while not self.stop_event.is_set():
                profile = load_profiles()[self.profile]
                pools = load_pools(settings.live_db, profile)
                schedule = build_schedule(
                    profile, self.seconds, self.seed + index, settings.base_rate_tps, pools
                )
                result = run_block(
                    settings.live_db, schedule, settings.workers, stop_event=self.stop_event
                )
                self.latest = {
                    "taken_at": now(),
                    "profile": self.profile,
                    "seconds": result.seconds,
                    "errors": result.errors,
                    "dropped": result.dropped,
                    "metadata": result.metadata,
                    "calls": {k: len(v) for k, v in result.latencies.items()},
                    "mean_ms": {
                        k: sum(v) / len(v) if v else None for k, v in result.latencies.items()
                    },
                }
                self.recent = (self.recent + [self.latest])[-120:]
                index += 1
        except Exception as exc:
            self.error = type(exc).__name__
            _LOG.exception("Live traffic failed")
        finally:
            with _TRAFFIC_LOCK:
                _TRAFFIC_ACTIVE = False
