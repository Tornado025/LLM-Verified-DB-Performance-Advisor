"""Build and verify the deterministic quick-commerce template and live clone.

Only this seeding tool connects to golden. All subsequent validation uses live
or the maintenance database. No application modules are required before step 03.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg import sql

BACKEND = Path(__file__).resolve().parents[1]
TABLES = (
    "meta",
    "stores",
    "categories",
    "products",
    "store_stock",
    "customers",
    "riders",
    "orders",
    "order_items",
)
STATUSES = {"placed", "packing", "out_for_delivery", "delivered", "cancelled"}
EXPECTED_INDEXES = {f"{table}_pkey" for table in TABLES} | {"products_sku_key"}
LOGGER = logging.getLogger(__name__)


def database_names() -> tuple[str, str, str]:
    """Validate names before any destructive maintenance command can run."""
    golden = os.getenv("GOLDEN_DB", "qc_golden")
    live = os.getenv("LIVE_DB", "qc_live")
    maintenance = os.getenv("MAINT_DB", "postgres")
    names = (golden, live, maintenance)
    if any(not name or len(name.encode()) > 63 for name in names) or len(set(names)) != 3:
        raise ValueError("Database names must be distinct, nonempty, and at most 63 bytes")
    if any(name in {"template0", "template1", "postgres"} for name in (golden, live)):
        raise ValueError("Golden and live must not name PostgreSQL system databases")
    return names


def connect_database(dbname: str) -> psycopg.Connection:
    """Open an isolated UTC session without building or logging a password DSN."""
    conn = psycopg.connect(
        host=os.getenv("PG_HOST", "localhost"),
        port=int(os.getenv("PG_PORT", "5432")),
        user=os.getenv("PG_USER", "advisor"),
        password=os.getenv("PG_PASSWORD", "advisor"),
        dbname=dbname,
        connect_timeout=5,
        autocommit=True,
    )
    try:
        conn.execute("/* dpa:sys */ SET TIME ZONE 'UTC'")
        conn.execute(
            "/* dpa:sys */ SELECT set_config('statement_timeout', %s, false)",
            (str(int(os.getenv("STMT_TIMEOUT_MS", "60000"))),),
        )
    except Exception:
        conn.close()
        raise
    return conn


def drop_database(admin: psycopg.Connection, name: str) -> None:
    """Disconnect clients and drop only a validated development database."""
    conn_names = database_names()[:2]
    if name not in conn_names:
        raise ValueError("Refusing to drop a database other than golden or live")
    admin.execute(
        "/* dpa:sys */ SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        (name,),
    )
    admin.execute(sql.SQL("/* dpa:sys */ DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))


def populate_golden(golden: str, scale: int, seed: int) -> None:
    """Populate a fresh golden database and close its sole seeding session."""
    with connect_database(golden) as conn:
        conn.execute("/* dpa:sys */ CREATE EXTENSION pg_stat_statements")
        conn.execute("/* dpa:sys */ CREATE EXTENSION hypopg")
        conn.execute("/* dpa:sys */ " + (BACKEND / "sql/schema.sql").read_text())
        # Disable parallel execution to preserve random draw ordering in seed inserts.
        conn.execute("/* dpa:sys */ SET max_parallel_workers_per_gather = 0")
        conn.execute("/* dpa:sys */ SELECT setseed(%s)", (seed / 1_000_000.0,))
        as_of = conn.execute(
            "/* dpa:sys */ SELECT date_trunc('day', now()) + interval '12 hours'"
        ).fetchone()[0]
        params = {"as_of": as_of, "n_customers": 20_000 * scale, "n_orders": 300_000 * scale}
        # This is our static SQL file, with no semicolons inside literals/comments.
        for statement in (BACKEND / "sql/seed_helpers.sql").read_text().split(";"):
            if statement.strip():
                conn.execute("/* dpa:sys */ " + statement, params)
        with conn.cursor() as cursor:
            cursor.executemany(
                "/* dpa:sys */ INSERT INTO meta (key, value) VALUES (%s, %s)",
                [
                    ("as_of", as_of.isoformat()),
                    ("scale", str(scale)),
                    ("seed", str(seed)),
                    ("seeded_at", datetime.now(UTC).isoformat()),
                ],
            )
        for table in TABLES:
            conn.execute(sql.SQL("/* dpa:sys */ VACUUM (ANALYZE) {}").format(sql.Identifier(table)))
        conn.execute("/* dpa:sys */ CHECKPOINT")


def clone_live(admin: psycopg.Connection, golden: str, live: str) -> None:
    """Clone from idle golden without ever connecting to it."""
    if admin.execute(
        "/* dpa:sys */ SELECT count(*) FROM pg_stat_activity WHERE datname = %s", (golden,)
    ).fetchone()[0]:
        raise RuntimeError("Golden must have zero connections before cloning")
    admin.execute(
        sql.SQL("/* dpa:sys */ CREATE DATABASE {} TEMPLATE {}").format(
            sql.Identifier(live), sql.Identifier(golden)
        )
    )
    with connect_database(live) as conn:
        for table in TABLES:
            conn.execute(sql.SQL("/* dpa:sys */ ANALYZE {}").format(sql.Identifier(table)))


def verification_report(conn: psycopg.Connection, golden: str) -> dict[str, Any]:
    """Measure every acceptance invariant using live, never golden."""
    metadata = dict(conn.execute("/* dpa:sys */ SELECT key, value FROM meta").fetchall())
    counts = {
        table: conn.execute(
            sql.SQL("/* dpa:sys */ SELECT count(*) FROM {}").format(sql.Identifier(table))
        ).fetchone()[0]
        for table in TABLES
    }
    statuses = dict(
        conn.execute("/* dpa:sys */ SELECT status, count(*) FROM orders GROUP BY status").fetchall()
    )
    first, last = conn.execute(
        "/* dpa:sys */ SELECT min(created_at), max(created_at) FROM orders"
    ).fetchone()
    per_order = conn.execute(
        "/* dpa:sys */ SELECT min(n), max(n), count(*) FROM "
        "(SELECT order_id, count(*) n FROM order_items GROUP BY order_id) q"
    ).fetchone()
    customer_share = conn.execute(
        "/* dpa:sys */ SELECT sum(n)::float8 / (SELECT count(*) FROM orders) FROM "
        "(SELECT count(*) n FROM orders GROUP BY customer_id ORDER BY n DESC "
        "LIMIT (SELECT greatest(1, count(*) / 100) FROM customers)) q"
    ).fetchone()[0]
    product_share = conn.execute(
        "/* dpa:sys */ SELECT avg((product_id <= 200)::int)::float8 FROM order_items"
    ).fetchone()[0]
    as_of = datetime.fromisoformat(metadata["as_of"])
    yesterday = conn.execute(
        "/* dpa:sys */ SELECT count(*) FROM orders WHERE DATE(delivered_at) = "
        "%s::timestamptz::date - 1",
        (as_of,),
    ).fetchone()[0]
    stockouts = conn.execute(
        "/* dpa:sys */ SELECT count(*) FROM store_stock WHERE qty = 0 "
        "AND updated_at >= %s::timestamptz - interval '15 minutes'",
        (as_of,),
    ).fetchone()[0]
    invalid_orders = conn.execute(
        "/* dpa:sys */ SELECT count(*) FROM orders WHERE "
        "(status = 'delivered') <> (delivered_at IS NOT NULL) "
        "OR delivered_at < created_at OR payment_mode NOT IN ('upi','card','cod','wallet')"
    ).fetchone()[0]
    indexes = [
        row[0]
        for row in conn.execute(
            "/* dpa:sys */ SELECT indexname FROM pg_indexes "
            "WHERE schemaname='public' ORDER BY indexname"
        )
    ]
    extensions = [
        row[0]
        for row in conn.execute("/* dpa:sys */ SELECT extname FROM pg_extension ORDER BY extname")
    ]
    golden_idle = (
        conn.execute(
            "/* dpa:sys */ SELECT count(*) FROM pg_stat_activity WHERE datname=%s", (golden,)
        ).fetchone()[0]
        == 0
    )
    golden_size = conn.execute("/* dpa:sys */ SELECT pg_database_size(%s)", (golden,)).fetchone()[0]
    buffers = conn.execute(
        "/* dpa:sys */ SELECT pg_size_bytes(current_setting('shared_buffers'))"
    ).fetchone()[0]
    return {
        "metadata": metadata,
        "counts": counts,
        "statuses": statuses,
        "active_fraction": sum(statuses.get(s, 0) for s in ("placed", "packing"))
        / counts["orders"],
        "active_all_fraction": sum(
            statuses.get(s, 0) for s in ("placed", "packing", "out_for_delivery")
        )
        / counts["orders"],
        "time_min": first.isoformat(),
        "time_max": last.isoformat(),
        "delivered_yesterday": yesterday,
        "customer_top_1pct_share": customer_share,
        "product_top_10pct_share": product_share,
        "recent_stockouts": stockouts,
        "items_per_order": {"min": per_order[0], "max": per_order[1], "orders": per_order[2]},
        "invalid_orders": invalid_orders,
        "indexes": indexes,
        "extensions": extensions,
        "golden_idle": golden_idle,
        "golden_size_bytes": golden_size,
        "shared_buffers_bytes": buffers,
        "larger_than_shared_buffers": golden_size > buffers,
    }


def validate_report(report: dict[str, Any]) -> None:
    """Fail explicitly on missing or out-of-range data, even under python -O."""
    counts = report["counts"]
    metadata = report["metadata"]
    scale = int(metadata["scale"])
    expected = {
        "meta": 4,
        "stores": 50,
        "categories": 40,
        "products": 2000,
        "store_stock": 80_000,
        "customers": 20_000 * scale,
        "riders": 500,
        "orders": 300_000 * scale,
        "order_items": 900_000 * scale,
    }
    checks = {f"count_{table}": abs(counts[table] / n - 1) <= 0.02 for table, n in expected.items()}
    as_of = datetime.fromisoformat(metadata["as_of"])
    first = datetime.fromisoformat(report["time_min"])
    last = datetime.fromisoformat(report["time_max"])
    statuses = report["statuses"]
    checks.update(
        {
            "scale": scale >= 1,
            "as_of_utc_noon": as_of.utcoffset() == timedelta(0)
            and (as_of.hour, as_of.minute, as_of.second, as_of.microsecond) == (12, 0, 0, 0),
            "seed_present": -1_000_000 <= int(metadata["seed"]) <= 1_000_000,
            "seeded_at_utc": datetime.fromisoformat(metadata["seeded_at"]).utcoffset()
            == timedelta(0),
            "statuses": set(statuses) == STATUSES,
            "delivered_fraction": 0.95 <= statuses.get("delivered", 0) / counts["orders"] <= 0.98,
            "cancelled_fraction": 0.02 <= statuses.get("cancelled", 0) / counts["orders"] <= 0.04,
            "active_fraction": 0.001 <= report["active_fraction"] <= 0.005,
            "active_all_fraction": 0.001 <= report["active_all_fraction"] <= 0.005,
            "time_spread": as_of - timedelta(days=30) <= first < as_of - timedelta(days=29.9)
            and as_of - timedelta(hours=1) < last <= as_of,
            "delivered_yesterday": report["delivered_yesterday"] > 1000 * scale,
            "customer_skew": report["customer_top_1pct_share"] > 0.10,
            "product_skew": report["product_top_10pct_share"] > 0.35,
            "stockouts": report["recent_stockouts"] > 100,
            "items_per_order": report["items_per_order"]
            == {"min": 1, "max": 5, "orders": counts["orders"]},
            "valid_orders": report["invalid_orders"] == 0,
            "no_secondary_indexes": set(report["indexes"]) == EXPECTED_INDEXES,
            "extensions": {"pg_stat_statements", "hypopg"} <= set(report["extensions"]),
            "golden_idle": report["golden_idle"],
        }
    )
    report["checks"] = checks
    if failed := [name for name, passed in checks.items() if not passed]:
        raise RuntimeError("Seed verification failed: " + ", ".join(failed))


def seed_databases(scale: int, seed: int, drop: bool, live_only: bool) -> dict[str, Any]:
    """Build or reset the local development fixture, then return measured evidence."""
    if scale < 1 or not -1_000_000 <= seed <= 1_000_000:
        raise ValueError("Scale must be >= 1 and seed must be between -1000000 and 1000000")
    golden, live, maintenance = database_names()
    start = time.perf_counter()
    with connect_database(maintenance) as admin:
        # A server-wide advisory lock prevents two seeding sessions from interleaving.
        if not admin.execute("/* dpa:sys */ SELECT pg_try_advisory_lock(62719, 2)").fetchone()[0]:
            raise RuntimeError("Another seed/reset operation is running")
        existing = {
            row[0]
            for row in admin.execute(
                "/* dpa:sys */ SELECT datname FROM pg_database WHERE datname IN (%s, %s)",
                (golden, live),
            )
        }
        if live_only and golden not in existing:
            raise RuntimeError("Golden is missing; run a full seed first")
        if not drop and (live in existing or (not live_only and golden in existing)):
            raise RuntimeError("Databases already exist; --no-drop leaves them untouched")
        if drop:
            drop_database(admin, live)
            if not live_only:
                drop_database(admin, golden)
        if not live_only:
            admin.execute(
                sql.SQL("/* dpa:sys */ CREATE DATABASE {}").format(sql.Identifier(golden))
            )
            populate_golden(golden, scale, seed)
            # Template creation remains possible while direct connections are forbidden.
            admin.execute(
                sql.SQL("/* dpa:sys */ ALTER DATABASE {} ALLOW_CONNECTIONS false").format(
                    sql.Identifier(golden)
                )
            )
        clone_live(admin, golden, live)
    with connect_database(live) as conn:
        report = verification_report(conn, golden)
    report["mode"] = "live_only" if live_only else "full"
    report["duration_seconds"] = round(time.perf_counter() - start, 3)
    return report


def main(argv: list[str] | None = None) -> int:
    """Run the seeder and print measured JSON, returning nonzero on any failed check."""
    load_dotenv(BACKEND.parent / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", type=int, default=1)
    parser.add_argument(
        "--seed", type=int, default=1234, help="PostgreSQL seed in [-1000000, 1000000]"
    )
    parser.add_argument("--drop", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--live-only", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        report = seed_databases(args.scale, args.seed, args.drop, args.live_only)
        try:
            validate_report(report)
        finally:
            print(json.dumps(report, indent=2, sort_keys=True))
        if not report["larger_than_shared_buffers"]:
            LOGGER.warning("Dataset is smaller than shared_buffers; increase scale for experiments")
    except (psycopg.Error, ValueError, RuntimeError, OSError) as exc:
        # Connection exceptions can contain host/user details. Never print their DSN text.
        message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        LOGGER.error("Seeding failed: %s", message)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
