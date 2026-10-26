"""Read-only public schema metadata for prompts and the later safety allowlist."""

from dataclasses import dataclass

import psycopg

from app.models import Model


@dataclass
class Catalog(Model):
    """Public tables with columns, indexes, estimates and declared key constraints."""

    tables: list[dict]
    indexes: list[dict]
    row_counts: dict[str, float]
    primary_keys: list[dict]
    foreign_keys: list[dict]
    original_sql: str | None = None


def get_catalog(conn: psycopg.Connection) -> Catalog:
    """Read metadata only; row_counts are planner estimates, not exact counts."""
    tables = []
    estimates = {}
    for name, estimate in conn.execute(
        "/* dpa:sys */ SELECT c.relname,c.reltuples "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname"
    ):
        columns = [
            {"name": row[0], "type": row[1], "nullable": row[2]}
            for row in conn.execute(
                "/* dpa:sys */ SELECT a.attname,format_type(a.atttypid,a.atttypmod),"
                "NOT a.attnotnull "
                "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped "
                "ORDER BY a.attnum",
                (name,),
            )
        ]
        estimates[name] = float(estimate)
        tables.append(
            {"name": name, "schema": "public", "columns": columns, "row_estimate": float(estimate)}
        )
    indexes = [
        {"table": row[0], "name": row[1], "definition": row[2]}
        for row in conn.execute(
            "/* dpa:sys */ SELECT tablename,indexname,indexdef FROM pg_indexes "
            "WHERE schemaname='public' ORDER BY tablename,indexname"
        )
    ]
    primary, foreign = [], []
    for name, table, kind, definition, columns, ref_table, ref_columns in conn.execute(
        "/* dpa:sys */ SELECT k.conname,t.relname,k.contype,pg_get_constraintdef(k.oid),"
        "ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY x(attnum,ord) "
        "JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=x.attnum ORDER BY x.ord),"
        "rt.relname,ARRAY(SELECT a.attname FROM unnest(k.confkey) WITH ORDINALITY x(attnum,ord) "
        "JOIN pg_attribute a ON a.attrelid=k.confrelid AND a.attnum=x.attnum ORDER BY x.ord) "
        "FROM pg_constraint k JOIN pg_class t ON t.oid=k.conrelid "
        "JOIN pg_namespace n ON n.oid=t.relnamespace LEFT JOIN pg_class rt ON rt.oid=k.confrelid "
        "WHERE n.nspname='public' AND k.contype IN ('p','f') ORDER BY t.relname,k.conname"
    ):
        item = {"name": name, "table": table, "columns": columns, "definition": definition}
        if kind == "p":
            primary.append(item)
        else:
            item.update(referenced_table=ref_table, referenced_columns=ref_columns)
            foreign.append(item)
    return Catalog(tables, indexes, estimates, primary, foreign)
