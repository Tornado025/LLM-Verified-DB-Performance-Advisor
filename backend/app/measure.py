"""Read-only EXPLAIN measurements and counterbalanced paired sequential tests."""

import logging
import math
from time import perf_counter

import numpy as np
import psycopg
import sqlglot
from psycopg.pq import TransactionStatus
from sqlglot import exp

from app.config import get_settings
from app.errors import BadRequest
from app.models import PairedResult
from app.stats import SequentialTest, paired_log_ratios

_LOG = logging.getLogger(__name__)


class QueryTimeout(Exception):
    """A censored query runtime; never treat this bound as a successful sample."""

    def __init__(self, timeout_ms: float, side: str | None = None):
        super().__init__("Query exceeded statement timeout")
        self.timeout_ms = timeout_ms
        self.side = side


def _select_only(statement: str) -> None:
    try:
        trees = sqlglot.parse(statement, read="postgres")
    except sqlglot.errors.ParseError as exc:
        raise BadRequest("Invalid measurement SQL") from exc
    if (
        len(trees) != 1
        or not isinstance(trees[0], (exp.Select, exp.Union, exp.Intersect, exp.Except))
        or any(
            trees[0].find(kind)
            for kind in (
                exp.Insert,
                exp.Update,
                exp.Delete,
                exp.Create,
                exp.Drop,
                exp.Into,
                exp.Lock,
            )
        )
    ):
        raise BadRequest("Measurement requires one read-only SELECT")

    for function in trees[0].find_all(exp.Anonymous):
        if function.name.lower() != "pg_sleep":
            raise BadRequest("Unrecognized function in measurement SELECT")


def run_once(conn: psycopg.Connection, sql: str, metric: str = "time") -> float:
    """Measure a trusted SELECT in a read-only transaction with a local timeout."""
    _select_only(sql)
    if metric not in {"time", "buffers"}:
        raise BadRequest("Unknown measurement metric")
    if not conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
        raise BadRequest("Measurement requires an idle autocommit connection")
    timeout = get_settings().stmt_timeout_ms
    try:
        with conn.transaction():
            conn.execute("/* dpa:sys */ SET TRANSACTION READ ONLY")
            conn.execute(
                "/* dpa:sys */ SELECT set_config('statement_timeout',%s,true)", (str(timeout),)
            )
            row = conn.execute(
                "EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, FORMAT JSON) " + sql
            ).fetchone()
            if row is None:
                raise BadRequest("No EXPLAIN result")
            plan = row[0][0]
            node = plan["Plan"]
            value = (
                plan["Execution Time"]
                if metric == "time"
                else sum(
                    node.get(key, 0)
                    for key in (
                        "Shared Hit Blocks",
                        "Shared Read Blocks",
                        "Local Hit Blocks",
                        "Local Read Blocks",
                    )
                )
            )
            if not math.isfinite(value) or value < 0:
                raise BadRequest("Invalid measured metric")
            return max(float(value), 1e-3)
    except psycopg.errors.QueryCanceled as exc:
        raise QueryTimeout(float(timeout)) from exc


def warmup(conn: psycopg.Connection, sql: str, n: int = 2) -> None:
    """Discard repeated executions to reduce clone cache asymmetry."""
    for _ in range(n):
        run_once(conn, sql)


def paired_report(
    conn_a: psycopg.Connection,
    sql_a: str,
    conn_b: psycopg.Connection,
    sql_b: str,
    *,
    metric: str,
    delta: float | None = None,
    alpha: float,
    looks: int,
    family: int,
    min_n: int,
    max_n: int,
    batch: int,
    mode: str = "noninferiority",
    budget_s: float | None = None,
    rng: np.random.Generator | None = None,
) -> PairedResult:
    """Alternate A/B order; stop at a verdict or budget, retaining raw evidence.

    rng is reserved for caller-controlled future randomization; alternating order
    is deterministic. A timeout raises QueryTimeout with side metadata, requiring
    callers to reject candidate timeouts and block baseline timeouts.
    """
    if batch < 1 or (budget_s is not None and (not math.isfinite(budget_s) or budget_s <= 0)):
        raise BadRequest("Invalid measurement budget or batch")
    test = SequentialTest(
        get_settings().delta if delta is None else delta, alpha, looks, family, min_n, max_n, mode
    )
    start = perf_counter()
    a_vals: list[float] = []
    b_vals: list[float] = []

    def timed(side: str) -> float:
        try:
            return run_once(
                conn_a if side == "a" else conn_b, sql_a if side == "a" else sql_b, metric
            )
        except QueryTimeout as exc:
            exc.side = side
            raise

    for side in ("a", "b"):
        for _ in range(2):
            timed(side)
    while len(a_vals) < max_n and test.state == "CONTINUE":
        pending: list[float] = []
        count = min(batch, max_n - len(a_vals))
        for _ in range(count):
            if budget_s is not None and perf_counter() - start >= budget_s:
                break
            values = {
                side: timed(side) for side in (("a", "b") if len(a_vals) % 2 == 0 else ("b", "a"))
            }
            a_vals.append(values["a"])
            b_vals.append(values["b"])
            pending.append(float(paired_log_ratios([values["a"]], [values["b"]])[0]))
        if pending:
            test.add(pending)
        if len(pending) < count:
            break
    return PairedResult(
        test.d.tolist(), test.summary(), perf_counter() - start, metric, a_vals, b_vals
    )


def screen_with_buffers(
    conn_a: psycopg.Connection, sql_a: str, conn_b: psycopg.Connection, sql_b: str, **kwargs
) -> PairedResult:
    """Use time only for an uncertain buffer screen; this is not a time-safety claim.

    A returned buffers PASS establishes a buffer-metric result only. Callers needing
    latency non-inferiority must run time explicitly. RQ3 compares the two signals.
    """
    kwargs.pop("metric", None)
    buffer_result = paired_report(conn_a, sql_a, conn_b, sql_b, metric="buffers", **kwargs)
    if buffer_result.summary.state in {"CONTINUE", "BLOCK"}:
        _LOG.info("Buffer screen borderline; measuring time")
        if kwargs.get("budget_s") is not None:
            remaining = kwargs["budget_s"] - buffer_result.seconds
            if remaining <= 0:
                return buffer_result
            kwargs["budget_s"] = remaining
        result = paired_report(conn_a, sql_a, conn_b, sql_b, metric="time", **kwargs)
        result.seconds += buffer_result.seconds
        return result
    _LOG.info("Buffer screen decisive: %s (buffer metric only)", buffer_result.summary.state)
    return buffer_result
