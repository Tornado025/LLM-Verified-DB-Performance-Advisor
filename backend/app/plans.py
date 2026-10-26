"""Job-scoped plan comparison and session-local hypothetical B-tree screening.

These helpers consume trusted fixtures or already safety-checked candidates.
A plan change is only a screening signal, never correctness or latency evidence.
"""

import hashlib
import json
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

import psycopg
import sqlglot
from psycopg.pq import TransactionStatus
from sqlglot import exp

from app.config import get_settings
from app.errors import BadRequest
from app.measure import _select_only
from app.models import Candidate
from app.sandbox import InvalidDDL, _validate

_CACHE: ContextVar[dict | None] = ContextVar("plan_cache", default=None)


def _baseline(conn: psycopg.Connection) -> None:
    _validate(conn.info.dbname, get_settings())
    if not conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
        raise BadRequest("Plan screening requires an idle autocommit sandbox connection")


@contextmanager
def plan_cache() -> Iterator[None]:
    """Enable bounded explain reuse for one job, isolated from other jobs/sessions."""
    token = _CACHE.set({})
    try:
        yield
    finally:
        _CACHE.reset(token)


def invalidate_cache() -> None:
    """Invalidate this job's plans after DDL, ANALYZE or HypoPG changes."""
    cache = _CACHE.get()
    if cache is not None:
        cache.clear()


def explain(
    conn: psycopg.Connection,
    sql: str,
    *,
    analyze: bool = False,
    buffers: bool = False,
    generic: bool = False,
) -> dict:
    """Return one JSON plan, with no analyzed-query caching or golden/live access."""
    _baseline(conn)
    if generic and analyze:
        raise BadRequest("GENERIC_PLAN cannot be combined with ANALYZE")
    if buffers and not analyze:
        raise BadRequest("BUFFERS requires ANALYZE")
    _select_only(sql)
    cache = _CACHE.get()
    # Session identity and options extend the documented base key: HypoPG and
    # generic-plan settings are session-local and must not collide across clones.
    key = (conn.info.dbname, conn.info.backend_pid, sql, analyze, buffers, generic)
    if not analyze and cache is not None and key in cache:
        return deepcopy(cache[key])
    options = ["FORMAT JSON"]
    if analyze:
        options += ["ANALYZE", "TIMING OFF"]
    if buffers:
        options.append("BUFFERS")
    if generic:
        options.append("GENERIC_PLAN")
    with conn.transaction():
        conn.execute("/* dpa:sys */ SET TRANSACTION READ ONLY")
        row = conn.execute("EXPLAIN (" + ", ".join(options) + ") " + sql).fetchone()
    if row is None or not isinstance(row[0][0], dict) or "Plan" not in row[0][0]:
        raise BadRequest("Missing query plan")
    result = row[0][0]
    if not analyze and cache is not None:
        if len(cache) >= 256:
            cache.clear()
        cache[key] = deepcopy(result)
    return result


def _literals(value: str) -> str:
    # Replace quoted literals first, then standalone numbers (not column2/OIDs
    # embedded in identifiers). Quoted identifiers retain their exact spelling.
    tokens = re.compile(
        r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|"
        r"(?<![\w$])[-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?(?![\w$])"
    )
    replaced = tokens.sub(lambda m: m[0] if m[0].startswith('"') else "?", value)
    return re.sub(
        r'"(?:""|[^"])*"|[^"]+',
        lambda m: m[0] if m[0].startswith('"') else re.sub(r"\s+", " ", m[0]),
        replaced,
    ).strip()


def _sort_columns(value: str) -> list[str]:
    try:
        tree = sqlglot.parse_one("SELECT " + value, read="postgres")
        return sorted(c.sql(dialect="postgres") for c in tree.find_all(exp.Column))
    except sqlglot.errors.ParseError as exc:
        raise BadRequest("Cannot canonicalize sort key") from exc


def fingerprint(plan: dict) -> str:
    """Hash the canonical structural plan, ignoring runtime estimates/literals."""
    root = plan.get("Plan", plan)
    nodes = []

    def visit(node: dict, depth: int) -> None:
        if "Node Type" not in node:
            raise BadRequest("Invalid plan node")
        values = {
            key: node[key]
            for key in (
                "Node Type",
                "Relation Name",
                "Index Name",
                "Join Type",
                "Strategy",
                "Scan Direction",
                "Parent Relationship",
            )
            if key in node
        }
        if "Index Name" in values:
            values["Index Name"] = re.sub(r"<\d+>", "<hypo>", values["Index Name"])
        for key in ("Filter", "Index Cond"):
            if key in node:
                values[key] = _literals(node[key])
        if "Sort Key" in node:
            values["Sort Key"] = sorted(c for key in node["Sort Key"] for c in _sort_columns(key))
        nodes.append([depth, values])
        for child in node.get("Plans", []):
            visit(child, depth + 1)

    visit(root, 0)
    canonical = json.dumps(nodes, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(canonical.encode(), usedforsecurity=False).hexdigest()[:12]


def _index_tree(statement: str) -> exp.Create:
    try:
        trees = sqlglot.parse(statement, read="postgres")
    except sqlglot.errors.ParseError as exc:
        raise InvalidDDL("Invalid index syntax") from exc
    if (
        len(trees) != 1
        or not isinstance(trees[0], exp.Create)
        or trees[0].args.get("kind") != "INDEX"
    ):
        raise InvalidDDL("Expected one CREATE INDEX")
    tree = trees[0]
    index = tree.this
    if not isinstance(index, exp.Index):
        raise InvalidDDL("Missing index definition")
    params = index.args.get("params")
    method = params.args.get("using") if params else None
    if method is not None and method.name.lower() != "btree":
        raise InvalidDDL("Only B-tree hypothetical indexes are supported")
    return tree


def normalise_index_ddl(statement: str) -> str:
    """Normalize one B-tree definition for HypoPG without executing real DDL."""
    tree = _index_tree(statement)
    tree.set("concurrently", False)
    tree.set("exists", False)
    return tree.sql(dialect="postgres").rstrip(";")


def hypo_plan(conn: psycopg.Connection, create_index_sql: str, query_sql: str) -> dict:
    """Explain with one hypothetical index and always reset the session state."""
    _baseline(conn)
    invalidate_cache()
    try:
        ddl = normalise_index_ddl(create_index_sql)
        conn.execute("/* dpa:sys */ SELECT * FROM hypopg_create_index(%s)", (ddl,))
        return explain(conn, query_sql)
    except psycopg.Error as exc:
        raise InvalidDDL(exc.diag.message_primary or type(exc).__name__) from exc
    finally:
        # Autocommit ensures a failed statement cannot leave reset in an aborted tx.
        conn.execute("/* dpa:sys */ SELECT hypopg_reset()")
        invalidate_cache()


def _normalized_sql(statement: str) -> str:
    trees = sqlglot.parse(statement, read="postgres")
    if len(trees) != 1 or trees[0] is None:
        raise BadRequest("Expected one SQL statement")
    tree = trees[0]
    for node in tree.walk():
        node.comments = None
    for identifier in tree.find_all(exp.Identifier):
        if not identifier.args.get("quoted"):
            identifier.set("this", identifier.name.lower())
    return tree.sql(dialect="postgres", normalize=True)


def _stats_signature(statement: str) -> str:
    # CREATE STATISTICS is opaque to sqlglot; parse its ON list as expressions
    # rather than splitting on commas inside expressions or quoted identifiers.
    match = re.fullmatch(
        r"\s*(CREATE\s+STATISTICS\s+.+?\s+ON)\s+(.+?)\s+(FROM\s+.+?)\s*;?\s*",
        statement,
        re.I | re.S,
    )
    if match:
        columns = sqlglot.parse_one("SELECT " + match[2], read="postgres").expressions
        normalized = (
            match[1]
            + " "
            + ", ".join(sorted(_normalized_sql("SELECT " + c.sql())[7:] for c in columns))
            + " "
            + match[3]
        ).rstrip(";")
    else:
        tree = sqlglot.parse_one(statement, read="postgres")
        if not isinstance(tree, exp.Analyze):
            raise BadRequest("Unknown statistics command")
        normalized = _normalized_sql(statement)
    # Lowercase unquoted tokens only; quoted identifiers are case-sensitive.
    normalized = re.sub(
        r'"(?:""|[^"])*"|[^\"]+',
        lambda m: m[0] if m[0].startswith('"') else re.sub(r"\s+", " ", m[0].lower()),
        normalized,
    )
    return "stats:" + normalized.strip()


def signature_for(candidate: Candidate, original_sql: str, conn: psycopg.Connection) -> str:
    """Return a metric-independent screening signature for a trusted candidate."""
    _baseline(conn)
    if candidate.kind == "index":
        return fingerprint(hypo_plan(conn, candidate.sql, original_sql))
    if candidate.kind == "rewrite":
        return fingerprint(explain(conn, candidate.sql))
    if candidate.kind == "stats":
        return _stats_signature(candidate.sql)
    raise BadRequest("Unknown candidate kind")


def baseline_signature(conn: psycopg.Connection, original_sql: str) -> str:
    """Fingerprint the fixed-parameter baseline report plan."""
    return fingerprint(explain(conn, original_sql))


def _index_definition(statement: str) -> str:
    tree = _index_tree(statement)
    tree.set("concurrently", False)
    tree.set("exists", False)
    index = tree.this
    index.set("this", None)  # names do not change index structure
    table = index.args["table"]
    if not table.args.get("db"):
        table.set("db", exp.to_identifier("public"))
    params = index.args["params"]
    if not params.args.get("using"):
        params.set("using", exp.Var(this="btree"))
    return _normalized_sql(tree.sql(dialect="postgres"))


def _already_exists(conn: psycopg.Connection, statement: str) -> bool:
    definition = _index_definition(statement)
    for row in conn.execute(
        "/* dpa:sys */ SELECT pg_get_indexdef(i.indexrelid) FROM pg_index i "
        "JOIN pg_class t ON t.oid=i.indrelid JOIN pg_namespace n "
        "ON n.oid=t.relnamespace WHERE n.nspname='public' AND i.indisvalid"
    ):
        try:
            if _index_definition(row[0]) == definition:
                return True
        except InvalidDDL:
            continue
    return False


def filter_candidates(
    cands: Sequence[Candidate], shape_sql_rendered: str, conn_baseline: psycopg.Connection
) -> tuple[list[Candidate], list[Candidate]]:
    """Discard ineffective/duplicate plans; no filtering result authorizes a fix."""
    _baseline(conn_baseline)
    kept, dropped = [], []
    seen: dict[tuple[str, str], Candidate] = {}
    with plan_cache():
        baseline = baseline_signature(conn_baseline, shape_sql_rendered)
        for candidate in cands:
            if candidate.status != "proposed":
                dropped.append(candidate)
                continue
            try:
                # Existing indexes can themselves cause the baseline plan: this
                # explicit duplicate takes precedence over generic no-plan status.
                exists = candidate.kind == "index" and _already_exists(conn_baseline, candidate.sql)
                candidate.signature = signature_for(candidate, shape_sql_rendered, conn_baseline)
                key = (candidate.kind, candidate.signature)
                if exists:
                    candidate.status, candidate.reason = "skipped_duplicate", "already exists"
                elif candidate.kind != "stats" and candidate.signature == baseline:
                    candidate.status, candidate.reason = "skipped_noplan", "plan unchanged"
                elif key in seen:
                    candidate.status = "skipped_duplicate"
                    candidate.reason = "duplicate_of:" + seen[key].id
                elif candidate.kind == "rewrite" and _normalized_sql(
                    candidate.sql
                ) == _normalized_sql(shape_sql_rendered):
                    candidate.status, candidate.reason = "skipped_noplan", "SQL unchanged"
                else:
                    candidate.reason = None
                    kept.append(candidate)
                    seen[key] = candidate
                    continue
            except (InvalidDDL, psycopg.Error, sqlglot.errors.ParseError) as exc:
                candidate.status = "rejected_invalid_ddl"
                candidate.reason = (
                    exc.diag.message_primary if isinstance(exc, psycopg.Error) else str(exc)
                ) or type(exc).__name__
            dropped.append(candidate)
    return kept, dropped
