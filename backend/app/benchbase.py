"""Pinned BenchBase CH/TPC-C-derived benchmark adapter; never targets qc_live."""

from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
from psycopg import sql

from app.config import get_settings
from app.errors import AdvisorError, BadRequest
from app.pg import admin_connect, connect
from app.sandbox import _validate

ROOT = Path(__file__).resolve().parents[2]
JAVA = ROOT / "benchbase/.runtime/jdk23/jdk-23.0.2+7/Contents/Home/bin/java"


def _jar() -> Path:
    settings = get_settings()
    path = (
        Path(settings.benchbase_jar)
        if settings.benchbase_jar
        else ROOT / "benchbase/.runtime/dist/benchbase-postgres/benchbase.jar"
    )
    if not path.is_file() or not JAVA.is_file():
        raise BadRequest("Build BenchBase and its pinned Java runtime first")
    return path.resolve()


def _target(db: str) -> None:
    if db not in {"qc_ch", "qc_ch_golden"}:
        _validate(db, get_settings())


def _config(db: str, profile: str, scale: int, seconds: int) -> ET.ElementTree[ET.Element[str]]:
    _target(db)
    s = get_settings()
    if profile not in {"normal", "flash5", "flash10", "flash20"} or scale < 1 or seconds < 1:
        raise BadRequest("Invalid CH benchmark configuration")
    tree = ET.parse(ROOT / f"benchbase/configs/ch_{profile}.xml")
    root = tree.getroot()
    options = urlencode(
        {
            "sslmode": "disable",
            "ApplicationName": "dpa-ch",
            "reWriteBatchedInserts": "true",
            "options": f"-c statement_timeout={s.stmt_timeout_ms}",
        }
    )

    def set_value(path: str, value: str) -> None:
        element = root.find(path)
        if element is None:
            raise BadRequest("Malformed BenchBase template: " + path)
        element.text = value

    set_value("url", f"jdbc:postgresql://{s.pg_host}:{s.pg_port}/{db}?" + options)
    set_value("username", s.pg_user)
    set_value("scalefactor", str(scale))
    set_value("works/work/time", str(seconds))
    multiplier = {"normal": 1, "flash5": 5, "flash10": 10, "flash20": 20}[profile]
    set_value("works/work/rate[@bench='tpcc']", str(s.base_rate_tps * multiplier))
    set_value("works/work/rate[@bench='chbenchmark']", str(0.1 * multiplier))
    return tree


def _execute(db: str, profile: str, scale: int, seconds: int, out_dir: Path, load: bool) -> None:
    jar = _jar()
    tree = _config(db, profile, scale, seconds)
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    runtime = ROOT / "benchbase/.runtime"
    with tempfile.NamedTemporaryFile("w", dir=runtime, suffix=".xml", delete=False) as handle:
        tree.write(handle, encoding="unicode", xml_declaration=True)
        config = Path(handle.name)
    env = dict(os.environ)
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("PG_PASSWORD", None)
    env["DPA_BENCHBASE_PASSWORD"] = get_settings().pg_password
    command = [
        str(JAVA),
        "-Xmx2g",
        "-jar",
        str(jar),
        "-b",
        "tpcc,chbenchmark",
        "-c",
        str(config),
        "--create=" + str(load).lower(),
        "--load=" + str(load).lower(),
        "--execute=" + str(not load).lower(),
        "-d",
        str(out_dir),
        "--json-histograms",
        str(out_dir / "histograms.json"),
    ]
    try:
        with (out_dir / "run.log").open("w") as output:
            completed = subprocess.run(
                command,
                cwd=jar.parent,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=1800 if load else seconds + 180,
            )
        if completed.returncode:
            raise AdvisorError(
                "BenchBase failed; inspect ignored run.log", {"exit_code": completed.returncode}
            )
    except subprocess.TimeoutExpired:
        raise AdvisorError("BenchBase timed out; run is invalid") from None
    finally:
        config.unlink(missing_ok=True)


def load_ch(db: str, scale: int) -> None:
    """Initialize the separate CH database; never rebuild commerce databases."""
    if db not in {"qc_ch", "qc_ch_golden"} or scale < 1:
        raise BadRequest("CH load requires qc_ch or qc_ch_golden")
    with admin_connect() as admin:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (db,)).fetchone()
        if exists:
            raise BadRequest("CH database already exists; explicit reset is required")
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db)))
    _execute(db, "normal", scale, 120, ROOT / "benchbase/results/load", True)
    with connect(db) as conn:
        row = conn.execute("SELECT count(*) FROM warehouse").fetchone()
        warehouses = row[0] if row else None
        if warehouses != scale:
            raise AdvisorError("CH warehouse load count mismatch")
        conn.execute("ANALYZE")
    if db == "qc_ch_golden":
        with admin_connect() as admin:
            admin.execute("ALTER DATABASE qc_ch_golden ALLOW_CONNECTIONS false")


def run_profile(
    db: str, profile: str, out_dir: Path, seconds: int, active_warehouses: int | None = None
) -> dict:
    """Run one measured composite profile; report observed samples, not target TPS."""
    _target(db)
    if db == "qc_ch_golden":
        raise BadRequest("Execute on a CH clone, never its golden template")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise BadRequest("Use an empty output directory for each measured run")
    with connect(db) as conn:
        row = conn.execute("SELECT to_regclass('public.warehouse')").fetchone()
        if row is None or row[0] is None:
            raise BadRequest("Database is not a loaded CH benchmark")
        row = conn.execute("SELECT count(*) FROM warehouse").fetchone()
        if row is None:
            raise BadRequest("Missing warehouse count")
        scale = int(row[0])
    if active_warehouses is not None:
        if not 2 <= active_warehouses <= scale:
            raise BadRequest("Active warehouse range must be 2..loaded scale")
        scale = active_warehouses
    _execute(db, profile, scale, seconds, out_dir, False)
    result = parse_results(out_dir)
    return {
        "profile": profile,
        "seconds": seconds,
        "active_warehouses": scale,
        "transactions": result,
        "execution_summary": parse_summary(out_dir),
    }


def parse_results(out_dir: Path) -> dict[str, dict]:
    """Read raw microsecond samples and independent error/abort/retry histograms."""
    files = sorted(out_dir.glob("*.raw.csv"))
    if not files:
        raise AdvisorError("BenchBase produced no raw samples")
    samples: dict[str, list[float]] = defaultdict(list)
    for path in files:
        with path.open() as handle:
            for row in csv.DictReader(handle):
                latency = float(row["Latency (microseconds)"]) / 1000
                if not math.isfinite(latency) or latency < 0:
                    raise AdvisorError("BenchBase produced invalid latency samples")
                samples[row["Transaction Name"].strip()].append(latency)
    hist_path = out_dir / "histograms.json"
    hist = json.loads(hist_path.read_text()) if hist_path.exists() else None

    def counts(kind: str) -> dict[str, int] | None:
        if hist is None or "HISTOGRAM" not in hist.get(kind, {}):
            return None
        values = hist[kind]["HISTOGRAM"]
        return {name.split("/")[0].split(".")[-1]: int(value) for name, value in values.items()}

    unexpected, aborted, retries = counts("unexpected"), counts("aborted"), counts("rejected")
    successful = counts("completed")
    names = set(samples) | {"NewOrder", "Payment", "OrderStatus", "Delivery", "StockLevel"}
    return {
        name: {
            "mean": float(np.mean(values)) if values else None,
            "p50": float(np.quantile(values, 0.5)) if values else None,
            "p95": float(np.quantile(values, 0.95)) if values else None,
            "p99": float(np.quantile(values, 0.99)) if values else None,
            "count": len(values),
            "errors": unexpected.get(name, 0) if unexpected is not None else None,
            "aborted": aborted.get(name, 0) if aborted is not None else None,
            "retries": retries.get(name, 0) if retries is not None else None,
            "successful_attempts_histogram": (
                successful.get(name, 0) if successful is not None else None
            ),
            "latencies_ms": values,
        }
        for name in sorted(names)
        for values in [samples[name]]
    }


def parse_summary(out_dir: Path) -> dict[str, float | int | None]:
    """Read measured time and throughput separately from per-transaction samples."""
    summaries = sorted(out_dir.glob("*.summary.json"))
    if len(summaries) > 1:
        raise AdvisorError("BenchBase produced multiple summary files")
    result: dict[str, float | int | None] = {
        "elapsed_seconds": None,
        "measured_requests": None,
        "throughput_per_second": None,
        "goodput_per_second": None,
    }
    if not summaries:
        return result
    source = json.loads(summaries[0].read_text())
    try:
        elapsed = float(source["Elapsed Time (nanoseconds)"]) / 1_000_000_000
        measured = int(source["Measured Requests"])
        throughput = float(source["Throughput (requests/second)"])
        goodput = float(source["Goodput (requests/second)"])
    except (KeyError, TypeError, ValueError):
        raise AdvisorError("BenchBase summary is missing execution metrics") from None
    if measured < 0 or not all(math.isfinite(v) and v >= 0 for v in (elapsed, throughput, goodput)):
        raise AdvisorError("BenchBase summary contains invalid execution metrics")
    return {
        "elapsed_seconds": elapsed,
        "measured_requests": measured,
        "throughput_per_second": throughput,
        "goodput_per_second": goodput,
    }
