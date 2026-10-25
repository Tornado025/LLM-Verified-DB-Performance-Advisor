"""PostgreSQL connection policy and trusted-value SQL rendering."""

import re
import time
from urllib.parse import quote

import psycopg

from app.config import Settings, get_settings


def dsn(dbname: str, settings: Settings | None = None) -> str:
    """Build an escaped URI; callers must never log it without masking."""
    s = settings or get_settings()
    host = f"[{s.pg_host}]" if ":" in s.pg_host else s.pg_host
    return (
        f"postgresql://{quote(s.pg_user, safe='')}:{quote(s.pg_password, safe='')}"
        f"@{host}:{s.pg_port}/{quote(dbname, safe='')}"
    )


def mask_dsn(s: str) -> str:
    """Redact URI and libpq keyword passwords, including quoted passwords."""
    s = re.sub(r"(postgres(?:ql)?://[^\s/@]*:)[^@\s]*(@)", r"\1***\2", s)
    return re.sub(
        r"(password\s*=\s*)(?:'(?:\\.|[^'])*'|\"[^\"]*\"|[^\s]+)", r"\1***", s, flags=re.IGNORECASE
    )


def set_session_defaults(conn: psycopg.Connection, settings: Settings | None = None) -> None:
    """Set transaction-independent application and statement timeout defaults."""
    s = settings or get_settings()
    conn.execute("/* dpa:sys */ SELECT set_config('application_name', 'dpa', false)")
    conn.execute(
        "/* dpa:sys */ SELECT set_config('statement_timeout', %s, false)", (str(s.stmt_timeout_ms),)
    )


def connect(
    dbname: str, autocommit: bool = True, settings: Settings | None = None
) -> psycopg.Connection:
    """Open one dedicated connection; golden is forbidden outside the seeder."""
    s = settings or get_settings()
    if dbname == s.golden_db:
        raise ValueError("Direct connections to golden are forbidden")
    for attempt in range(5):
        conn = None
        try:
            conn = psycopg.connect(dsn(dbname, s), autocommit=True, connect_timeout=5)
            set_session_defaults(conn, s)
            conn.autocommit = autocommit
            return conn
        except psycopg.OperationalError:
            if conn is not None:
                conn.close()
            if attempt == 4:
                raise
            time.sleep(1)
        except Exception:
            if conn is not None:
                conn.close()
            raise
    raise RuntimeError("Unreachable connection retry exhaustion")


def admin_connect(settings: Settings | None = None) -> psycopg.Connection:
    """Open the maintenance database with autocommit enabled."""
    s = settings or get_settings()
    return connect(s.maint_db, settings=s)


def render_sql(conn: psycopg.Connection, sql: str, params: dict) -> str:
    """Inline trusted sampler values, never untrusted LLM SQL."""
    with psycopg.ClientCursor(conn) as cursor:
        return cursor.mogrify(sql, params)


def terminate_connections(admin_conn: psycopg.Connection, dbname: str) -> None:
    """Terminate other sessions on the named database using a bound value."""
    admin_conn.execute(
        "/* dpa:sys */ SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = %s AND pid <> pg_backend_pid()",
        (dbname,),
    )
