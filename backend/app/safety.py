"""Pure fail-closed SQL allowlist. Passing is syntax/scope screening, not equivalence."""

import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

from app.catalog import Catalog
from app.models import RawCandidate

_SAFE_FUNCS = {
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
    "DATE",
    "DATE_TRUNC",
    "TIMESTAMP_TRUNC",
    "EXTRACT",
    "COALESCE",
    "NULLIF",
    "LOWER",
    "UPPER",
    "CURRENT_DATE",
    "CURRENT_TIMESTAMP",
    "CAST",
    "TRY_CAST",
    "AND",
    "OR",
    "CASE",
    "IF",
    "EXISTS",
}
_IMMUTABLE_FUNCS = _SAFE_FUNCS - {"CURRENT_DATE", "CURRENT_TIMESTAMP"}
_ID = r'(?:[A-Za-z_][A-Za-z_0-9]*|"(?:""|[^"])+")'
_TABLE = _ID + r"(?:\." + _ID + r")?"


def _name(identifier: exp.Expr) -> str:
    if isinstance(identifier, exp.Identifier):
        return identifier.name if identifier.args.get("quoted") else identifier.name.lower()
    return identifier.name.lower()


def _tables(catalog: Catalog) -> dict[str, set[str]]:
    return {t["name"]: {c["name"] for c in t["columns"]} for t in catalog.tables}


def _table(table: exp.Table, catalog: Catalog) -> tuple[str, set[str]]:
    name = _name(table.this)
    if (
        table.catalog
        or (table.db and _name(table.args["db"]) != "public")
        or name not in _tables(catalog)
    ):
        raise ValueError("Unknown or non-public table")
    return name, _tables(catalog)[name]


def _functions(tree: exp.Expr, immutable: bool = False) -> None:
    allowed = _IMMUTABLE_FUNCS if immutable else _SAFE_FUNCS
    if any(t.this.name == "USERDEFINED" for t in tree.find_all(exp.DataType)):
        raise ValueError("User-defined cast types are outside scope")
    for function in tree.find_all(exp.Func):
        name = function.name.upper() if isinstance(function, exp.Anonymous) else function.sql_name()
        if (
            name not in allowed
            or name.lower().startswith(("pg_", "lo_", "dblink"))
            or name.lower() in {"current_setting", "set_config"}
        ):
            raise ValueError("Function outside allowlist")
    # Schema-qualified/custom functions and user-defined operators are rejected.
    if tree.find(exp.Dot) or tree.find(exp.Anonymous):
        raise ValueError("Unrecognized function")
    if immutable and any(type(n).__name__.startswith("Current") for n in tree.walk()):
        raise ValueError("Current-time expression in index")


def _columns(tree: exp.Expr, name: str, columns: set[str]) -> None:
    for column in tree.find_all(exp.Column):
        if (
            column.catalog
            or (column.db and column.db != "public")
            or (column.table and column.table != name)
            or _name(column.this) not in columns
        ):
            raise ValueError("Unknown column or qualifier")


def _qualified(tree: exp.Expr, catalog: Catalog) -> exp.Select:
    if not isinstance(tree, exp.Select):
        raise ValueError("Expected SELECT")
    schema: dict[str, object] = {
        "public": {t["name"]: {c["name"]: c["type"] for c in t["columns"]} for t in catalog.tables}
    }
    return qualify(
        tree.copy(),
        dialect="postgres",
        schema=schema,
        db="public",
        validate_qualify_columns=True,
        identify=False,
        quote_identifiers=False,
    )


def _rewrite(tree: exp.Expr, catalog: Catalog) -> None:
    if not isinstance(tree, exp.Select):
        raise ValueError("Rewrite must be one SELECT")
    if any(
        tree.find(t)
        for t in (
            exp.Insert,
            exp.Update,
            exp.Delete,
            exp.Create,
            exp.Drop,
            exp.Into,
            exp.Lock,
            exp.Command,
            exp.Copy,
        )
    ):
        raise ValueError("Side-effecting rewrite")
    _functions(tree)
    ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if table.name not in ctes or table.db:
            _table(table, catalog)
    qualified = _qualified(tree, catalog)
    if catalog.original_sql is None:
        raise ValueError("Baseline SQL required for rewrite output checks")
    original = _qualified(sqlglot.parse_one(catalog.original_sql, read="postgres"), catalog)
    if qualified.named_selects != original.named_selects or not qualified.named_selects:
        raise ValueError("Rewrite output names/count differ from baseline")


def _stats(statement: str, tree: exp.Expr, catalog: Catalog) -> None:
    if isinstance(tree, exp.Analyze):
        if any(
            tree.args.get(k)
            for k in ("kind", "mode", "partition", "properties", "expression", "options")
        ):
            raise ValueError("ANALYZE options not allowed")
        tables = tree.args.get("tables") or []
        if len(tables) != 1:
            raise ValueError("ANALYZE requires one known table")
        table = tables[0]
        if isinstance(table.this, exp.Anonymous):
            name = table.this.name
            target = exp.to_table(name)
            target.set("db", table.args.get("db"))
            target.set("catalog", table.args.get("catalog"))
            _, cols = _table(target, catalog)
            for col in table.this.expressions:
                if not isinstance(col, exp.Column) or col.table or _name(col.this) not in cols:
                    raise ValueError("Unknown ANALYZE column")
        else:
            _table(table, catalog)
        return
    match = re.fullmatch(
        r"\s*CREATE\s+STATISTICS\s+(" + _ID + r")\s*"
        r"(?:\((dependencies|ndistinct|mcv)(?:\s*,\s*(?:dependencies|ndistinct|mcv))*\)\s*)?"
        r"ON\s+(.+?)\s+FROM\s+(" + _TABLE + r")\s*;?\s*",
        statement,
        re.I | re.S,
    )
    if not match:
        raise ValueError("Unknown statistics syntax")
    table = sqlglot.parse_one("SELECT * FROM " + match[4], read="postgres").find(exp.Table)
    if table is None:
        raise ValueError("Missing table")
    name, columns = _table(table, catalog)
    listed = sqlglot.parse_one("SELECT " + match[3], read="postgres").expressions
    if len(listed) < 2 or not all(isinstance(c, exp.Column) for c in listed):
        raise ValueError("Statistics require at least two simple columns")
    for c in listed:
        _columns(c, name, columns)


def check(raw: RawCandidate, catalog: Catalog) -> tuple[bool, str]:
    """Screen every untrusted proposal before any database call; never execute SQL."""
    try:
        text = raw.sql
        if (
            raw.kind not in {"index", "stats", "rewrite"}
            or not isinstance(text, str)
            or not 0 < len(text.strip()) <= 4000
        ):
            raise ValueError("Invalid candidate kind/length")
        if "--" in text or "/*" in text or "*/" in text:
            raise ValueError("SQL comments are forbidden")
        trees = sqlglot.parse(text, read="postgres")
        if len(trees) != 1 or trees[0] is None:
            raise ValueError("Exactly one SQL statement required")
        tree = trees[0]
        if raw.kind == "rewrite":
            _rewrite(tree, catalog)
        elif raw.kind == "stats":
            _stats(text, tree, catalog)
        else:
            if (
                not isinstance(tree, exp.Create)
                or tree.args.get("kind") != "INDEX"
                or tree.args.get("unique")
                or tree.args.get("replace")
            ):
                raise ValueError("Only non-unique CREATE INDEX allowed")
            index = tree.this
            if not isinstance(index, exp.Index) or not isinstance(
                index.args.get("table"), exp.Table
            ):
                raise ValueError("Invalid index")
            name, columns = _table(index.args["table"], catalog)
            params = index.args.get("params")
            if not params or not 1 <= len(params.args.get("columns", [])) <= 4:
                raise ValueError("Index requires 1–4 keys")
            method = params.args.get("using")
            if method and method.name.lower() != "btree":
                raise ValueError("Only B-tree allowed")
            if any(params.args.get(k) for k in ("tablespace", "with_storage", "partition_by")):
                raise ValueError("Index options outside scope")
            if index.find(exp.Opclass):
                raise ValueError("Custom operator classes are outside scope")
            if index.find(exp.Select) or index.find(exp.Subquery):
                raise ValueError("Index subqueries forbidden")
            _functions(index, immutable=True)
            _columns(index, name, columns)
            for column in params.args.get("include") or []:
                if not isinstance(column, exp.Identifier) or _name(column) not in columns:
                    raise ValueError("Unknown INCLUDE column")
            tree.set("concurrently", False)
            raw.sql = tree.sql(dialect="postgres")
        return True, "allowed; correctness/performance still unverified"
    except (ValueError, TypeError, KeyError, AttributeError, sqlglot.errors.SqlglotError) as exc:
        return False, str(exc)
