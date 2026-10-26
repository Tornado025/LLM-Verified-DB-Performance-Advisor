"""Read slow query statistics with tagged system/utility statements excluded."""

import re

import psycopg
from psycopg import sql

from app.errors import BadRequest


class StatementList(list[dict]):
    """List-compatible result carrying an explicit missing-extension warning flag."""

    def __init__(self, values: list[dict] | None = None, *, extension_missing: bool = False):
        super().__init__(values or [])
        self.extension_missing = extension_missing


def top_statements(conn: psycopg.Connection, limit: int = 20, order: str = "total") -> list[dict]:
    """Return the top non-system DML/report statements and their first-comment tag."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise BadRequest("Statement limit must be between 1 and 1000")
    if order not in {"total", "mean"}:
        raise BadRequest("Unknown statement order")
    row = conn.execute(
        "/* dpa:sys */ SELECT n.nspname FROM pg_extension e "
        "JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pg_stat_statements'"
    ).fetchone()
    if row is None:
        return StatementList(extension_missing=True)
    column = sql.Identifier("total_exec_time" if order == "total" else "mean_exec_time")
    try:
        rows = conn.execute(
            sql.SQL(
                "/* dpa:sys */ SELECT queryid,query,calls,total_exec_time,mean_exec_time,rows,"
                "shared_blks_hit,shared_blks_read FROM {}.pg_stat_statements "
                "WHERE dbid=(SELECT oid FROM pg_database WHERE datname=current_database()) "
                "ORDER BY {} DESC,queryid"
            ).format(sql.Identifier(row[0]), column)
        ).fetchall()
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedFunction):
        return StatementList(extension_missing=True)
    result = StatementList()
    for queryid, query, calls, total, mean, returned, hits, reads in rows:
        if re.search(r"/\*\s*dpa:sys\s*\*/", query):
            continue
        # Utility EXPLAIN/ANALYZE/CREATE/SET/transaction statements are excluded.
        stripped = re.sub(r"^\s*(?:(?:/\*.*?\*/|--[^\n]*(?:\n|$))\s*)*", "", query, flags=re.S)
        if not re.match(r"^(SELECT|WITH|INSERT|UPDATE|DELETE)\b", stripped, re.I):
            continue
        tag_match = re.match(r"^\s*/\*\s*dpa:(shape|txn)=([a-z_][a-z_0-9]*)\s*\*/", query)
        result.append(
            {
                "queryid": str(queryid),
                "query": query,
                "calls": int(calls),
                "total_ms": float(total),
                "mean_ms": float(mean),
                "rows": int(returned),
                "shared_blks_hit": int(hits),
                "shared_blks_read": int(reads),
                "tag": tag_match[1] + ":" + tag_match[2] if tag_match else None,
            }
        )
        if len(result) >= limit:
            break
    return result
