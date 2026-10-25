"""Offline, read-only dataset inventory and bounded-memory gap measurements.

Only this script, the requested report/inventory, and temporary storage are written.
No application/database modules, network clients, or LLM providers are used.
"""

from __future__ import annotations

import argparse
import base64
import gc
import hashlib
import io
import json
import logging
import math
import os
import re
import resource
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
import zipfile
import zlib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psutil
import pyarrow as pa
import pyarrow.parquet as pq
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

ROOT = Path(__file__).resolve().parents[2]
EXTERNAL = ROOT / "backend/data/external"
OUT = ROOT / "experiments/results/dataset_inventory.json"
REPORT = ROOT / "docs/dataset_gap_report.md"
DATASETS = ("redset", "snowset", "skyserver", "sqlshare", "sede")
# Observed mappings: validated against every selected Parquet schema before scanning.
COLUMN_MAP = {
    "redset": {
        "cluster": "instance_id",
        "timestamp": "arrival_timestamp",
        "fingerprint": "feature_fingerprint",
        "runtime": "execution_duration_ms",
        "type": "query_type",
        "id": "query_id",
        "database": "database_id",
    },
    "snowset": {
        "cluster": "warehouseId",
        "timestamp": "createdTime",
        "runtime": "durationExec",
        "id": "queryId",
        "database": "databaseId",
    },
}
PATTERNS = ("P1", "P2", "P3a", "P3b", "P3c", "any")
COMMERCE = re.compile(
    r"(?:^|[^a-z])(?:orders?|customers?|products?|stock|inventory|sales)(?:$|[^a-z])", re.I
)
SQLISH = re.compile(
    r"\b(?:select\b.{0,150}\bfrom|create\s+(?:table|view)|insert\s+into)\b", re.I | re.S
)
PROCESS = psutil.Process()
MEMORY_MB = 1536
logging.getLogger("sqlglot").setLevel(logging.ERROR)
pa.set_cpu_count(2)


def emit(label: str, value: Any) -> None:
    """Print reproducible inspection output without unlimited SQL excerpts."""
    print(label, json.dumps(value, default=str, ensure_ascii=False), flush=True)


def check_memory() -> None:
    """Bound the active working set at batch boundaries and fail honestly if exceeded."""
    if PROCESS.memory_info().rss > MEMORY_MB * 1024**2:
        gc.collect()
        pa.default_memory_pool().release_unused()
        if PROCESS.memory_info().rss > MEMORY_MB * 1024**2:
            raise MemoryError(f"RSS exceeds configured {MEMORY_MB} MiB batch-boundary cap")


def clean(value: Any, key: str = "") -> Any:
    """Redact SQL text in row heads; keep observed field names and ordinary scalar values."""
    if isinstance(value, dict):
        return {str(k): clean(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v, key) for v in value]
    if isinstance(value, str):
        if key.lower() in {"querybody", "query", "sql", "text_sql"} or SQLISH.search(value):
            return {
                "redacted_sql_chars": len(value),
                "sha256": hashlib.sha256(value.encode()).hexdigest(),
            }
        return value[:160] + ("…" if len(value) > 160 else "")
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def sha256(path: Path) -> str:
    """Hash input bytes with bounded buffers; never trust an old download manifest."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(8 * 1024**2):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path, base: Path) -> dict:
    """Describe and hash an actual input, not an inferred source."""
    item = {
        "name": str(path.relative_to(base)),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }
    emit("FILE", item)
    return item


def schema_record(schema: pa.Schema) -> list[dict]:
    """Retain real Parquet names/types, including timezone annotations."""
    return [
        {"name": field.name, "type": str(field.type), "nullable": field.nullable}
        for field in schema
    ]


def pack(values: np.ndarray) -> dict:
    """Encode long exact time series compactly inside the one permitted JSON artifact."""
    array = np.asarray(values, dtype="<f8" if values.dtype.kind == "f" else "<i8")
    return {
        "encoding": "base64(zlib(little-endian numpy bytes))",
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "data": base64.b64encode(zlib.compress(array.tobytes())).decode(),
    }


class Trace:
    """Aggregate traces on temporary SQLite, with bounded Arrow/Pandas batches."""

    def __init__(self, path: Path, fingerprint: bool) -> None:
        self.db = sqlite3.connect(path)
        self.db.executescript("""
          PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
          PRAGMA cache_size=-32768; PRAGMA temp_store=FILE;
          CREATE TABLE minutes(c TEXT,t INTEGER,n INTEGER,PRIMARY KEY(c,t)) WITHOUT ROWID;
          CREATE TABLE fp(c TEXT,h INTEGER,f TEXT,n INTEGER,PRIMARY KEY(c,h,f)) WITHOUT ROWID;
          CREATE TABLE runtime(c TEXT,r REAL,n INTEGER,PRIMARY KEY(c,r)) WITHOUT ROWID;
          CREATE TABLE types(c TEXT,k TEXT,n INTEGER,PRIMARY KEY(c,k)) WITHOUT ROWID;
        """)
        self.fingerprint = fingerprint
        self.rows = 0
        self.nulls: Counter = Counter()
        self.id_seen: set = set()
        self.id_rows = self.id_duplicates = 0
        self.hashed_fp = self.fp_rows = self.negative_runtime = 0
        self.low = self.high = None

    def grouped(self, frame: pd.DataFrame, keys: list[str], table: str) -> None:
        """Upsert vectorized batch aggregates rather than retaining trace rows."""
        counts = frame.groupby(keys, sort=False, observed=True).size()
        if counts.empty:
            return
        placeholders = ",".join("?" for _ in range(len(keys) + 1))
        query = (
            f"INSERT INTO {table} VALUES ({placeholders}) ON CONFLICT DO UPDATE SET n=n+excluded.n"
        )
        rows = (
            (*[value.item() if isinstance(value, np.generic) else value for value in key], int(n))
            for key, n in counts.items()
        )
        self.db.executemany(query, rows)

    def add(self, batch: pa.RecordBatch, mapping: dict, namespace: str) -> None:
        """Observe key nulls, arrivals, runtimes, query types and provided fingerprints."""
        for name in mapping.values():
            self.nulls[name] += batch.column(name).null_count
        frame = batch.to_pandas()
        self.rows += len(frame)
        frame["c"] = namespace + ":" + frame[mapping["cluster"]].astype("string").fillna("<NULL>")
        stamp = pd.to_datetime(frame[mapping["timestamp"]], utc=True).dt.as_unit("ns")
        valid = stamp.notna() & frame[mapping["cluster"]].notna()
        frame["t"] = stamp.astype("int64") // 60_000_000_000
        arrivals = frame.loc[valid].copy()
        if len(arrivals):
            lo, hi = stamp[valid].min(), stamp[valid].max()
            self.low = lo if self.low is None else min(self.low, lo)
            self.high = hi if self.high is None else max(self.high, hi)
            self.grouped(arrivals, ["c", "t"], "minutes")
        frame["r"] = pd.to_numeric(frame[mapping["runtime"]], errors="coerce")
        self.negative_runtime += int((frame["r"] < 0).sum())
        self.grouped(frame.loc[frame["r"].notna() & (frame["r"] >= 0)], ["c", "r"], "runtime")
        if "type" in mapping:
            frame["k"] = frame[mapping["type"]].fillna("<NULL>").astype(str).str.lower()
            self.grouped(frame, ["c", "k"], "types")
        if "fingerprint" in mapping:
            arrivals["f"] = arrivals[mapping["fingerprint"]]
            good = arrivals["f"].notna() & arrivals["f"].ne("")
            self.fp_rows += int(good.sum())
            self.hashed_fp += int(arrivals.loc[good, "f"].str.fullmatch(r"[0-9a-fA-F]{64}").sum())
            arrivals["h"] = arrivals["t"] // 60
            self.grouped(arrivals.loc[good], ["c", "h", "f"], "fp")
        # Exact duplicate-key sanity check on a bounded physical-prefix sample only.
        remaining = max(0, 100_000 - self.id_rows)
        for c, key in (
            frame[["c", mapping["id"]]].iloc[:remaining].itertuples(index=False, name=None)
        ):
            if pd.isna(key):
                continue
            pair = (str(c), str(key))
            self.id_duplicates += pair in self.id_seen
            self.id_seen.add(pair)
            self.id_rows += 1
        self.db.commit()
        check_memory()

    def scope(self, cluster: str | None) -> dict:
        """Compute exact window statistics from disk aggregates, including zero minutes."""
        where, args = (" WHERE c=?", (cluster,)) if cluster is not None else ("", ())
        rows = self.db.execute(
            "SELECT t,SUM(n) FROM minutes" + where + " GROUP BY t ORDER BY t", args
        ).fetchall()
        if not rows:
            return {"n_queries": 0}
        stamps = np.array([r[0] for r in rows], dtype=np.int64)
        counts = np.array([r[1] for r in rows], dtype=np.int64)
        span = int(stamps[-1] - stamps[0] + 1)
        if span > 5_000_000:
            raise ValueError("Unexpected trace span exceeds the bounded dense-minute budget")
        dense = np.zeros(span, dtype=np.int64)
        dense[stamps - stamps[0]] = counts
        mean = float(dense.mean())
        hours = []
        for hour in range(int(stamps[0] // 60), int(stamps[-1] // 60) + 1):
            a, b = max(0, hour * 60 - int(stamps[0])), min(span, (hour + 1) * 60 - int(stamps[0]))
            segment = dense[a:b]
            mu = float(segment.mean())
            hours.append((hour, len(segment), float(segment.var() / mu) if mu else np.nan))
        days = sorted(set(int(t // 1440) for t in stamps))
        hod = np.bincount((stamps % 1440) // 60, weights=counts, minlength=24)
        histogram = self.db.execute(
            "SELECT r,SUM(n) FROM runtime" + where + " GROUP BY r ORDER BY r", args
        ).fetchall()
        runtime = {}
        if histogram:
            vals = np.array([r[0] for r in histogram])
            cum = np.cumsum(np.array([r[1] for r in histogram], dtype=np.int64))
            for q in (0.5, 0.95, 0.99):
                pos = q * (int(cum[-1]) - 1)
                lo, hi = math.floor(pos), math.ceil(pos)
                lower, upper = (
                    vals[np.searchsorted(cum, lo + 1)],
                    vals[np.searchsorted(cum, hi + 1)],
                )
                runtime[f"p{int(q * 100)}"] = float(lower + (upper - lower) * (pos - lo))
            runtime["n_nonnegative_nonnull"] = int(cum[-1])
        else:
            runtime = {"status": "not measured", "reason": "No nonnegative runtimes"}
        types = dict(self.db.execute("SELECT k,SUM(n) FROM types" + where + " GROUP BY k", args))
        all_types = sum(types.values())
        read = sum(v for k, v in types.items() if k == "select")
        write = sum(
            v for k, v in types.items() if k in {"insert", "update", "delete", "copy", "merge"}
        )
        result = {
            "n_queries": int(counts.sum()),
            "n_days": len(days),
            "minute_start_epoch": int(stamps[0]),
            "minute_counts": pack(dense),
            "minute_count_observation_policy": (
                "All minutes from first to last arrival, including zeros"
            ),
            "p99_to_mean_arrivals": float(np.quantile(dense, 0.99) / mean),
            "peak_to_mean_arrivals": float(dense.max() / mean),
            "hourly_index_of_dispersion": pack(np.array(hours, dtype=float)),
            "hourly_columns": [
                "epoch_hour",
                "observed_minutes",
                "population_variance/mean; NaN for zero mean",
            ],
            "hour_of_day_share_UTC_or_assumed_UTC": (hod / hod.sum()).tolist(),
            "runtime_percentiles_source_units": runtime,
            "query_type_counts": types,
            "read_write": {
                "read_share": read / all_types,
                "write_share": write / all_types,
                "other_share": (all_types - read - write) / all_types,
                "read_definition": "select",
                "write_definition": "insert/update/delete/copy/merge",
            }
            if all_types
            else {
                "status": "not measured",
                "reason": "No query-type label; I/O writes are not DML labels",
            },
        }
        if len(dense) > 1 and dense[:-1].std() and dense[1:].std():
            result["arrival_lag1_correlation"] = float(np.corrcoef(dense[:-1], dense[1:])[0, 1])
        else:
            result["arrival_lag1_correlation"] = None
        if not self.fingerprint:
            result["fingerprints"] = {
                "status": "not measured",
                "reason": "No template fingerprint in Snowset; queryId is an execution identifier",
            }
            return result
        # Pooled fingerprints are namespaced by cluster, avoiding accidental cross-tenant identity.
        group = "c,f" if cluster is None else "f"
        total = self.db.execute("SELECT COALESCE(SUM(n),0) FROM fp" + where, args).fetchone()[0]
        result["fingerprints"] = {
            "nonnull_queries": total,
            "identity": "cluster + feature_fingerprint",
        }
        for label, window in (("1h", "h"), ("1day", "CAST(h/24 AS INTEGER)"), ("whole_trace", "0")):
            groups = self.db.execute(
                "SELECT count(*) FROM (SELECT "
                + group
                + ","
                + window
                + " AS w FROM fp"
                + where
                + " GROUP BY "
                + group
                + ",w)",
                args,
            ).fetchone()[0]
            result["fingerprints"][label] = {
                "distinct_window_fingerprints": groups,
                "repetition_rate": (total - groups) / total if total else None,
            }
        daily = []
        previous_day = None
        for day in days:
            cond = ("c=? AND " if cluster is not None else "") + "h>=? AND h<?"
            values = (*args, day * 24, (day + 1) * 24)
            denom = self.db.execute(
                "SELECT COALESCE(SUM(n),0) FROM fp WHERE " + cond, values
            ).fetchone()[0]
            top = self.db.execute(
                "SELECT SUM(n) AS cnt FROM fp WHERE "
                + cond
                + " GROUP BY "
                + group
                + " ORDER BY cnt DESC LIMIT 100",
                values,
            ).fetchall()
            entry = {
                "epoch_day": day,
                "n_fingerprinted": denom,
                "top_k_coverage": {
                    str(k): sum(r[0] for r in top[:k]) / denom if denom else None
                    for k in (1, 10, 100)
                },
            }
            if previous_day is not None and day == previous_day + 1:
                # Join day-aggregated fingerprints; SQL spills temporary work to disk.
                sub = "SELECT c,f,SUM(n) n FROM fp WHERE " + cond + " GROUP BY c,f"
                prev_args = (*args, previous_day * 24, (previous_day + 1) * 24)
                previous = self.db.execute(
                    "SELECT count(*) FROM (" + sub + ")", prev_args
                ).fetchone()[0]
                current = self.db.execute("SELECT count(*) FROM (" + sub + ")", values).fetchone()[
                    0
                ]
                inter, seen = self.db.execute(
                    "SELECT count(*),COALESCE(SUM(b.n),0) FROM ("
                    + sub
                    + ") a JOIN ("
                    + sub
                    + ") b ON a.c=b.c AND a.f=b.f",
                    (*prev_args, *values),
                ).fetchone()
                union = previous + current - inter
                entry["previous_day_jaccard"] = inter / union if union else None
                entry["queries_seen_on_previous_day_share"] = seen / denom if denom else None
            daily.append(entry)
            previous_day = day
        result["daily_fingerprint_metrics"] = daily
        return result

    def finish(self) -> dict:
        """Report pooled and every observed cluster with sample scope and honest gaps."""
        self.db.execute("CREATE INDEX IF NOT EXISTS fp_hour_lookup ON fp(h,c,f)")
        clusters = [r[0] for r in self.db.execute("SELECT DISTINCT c FROM minutes ORDER BY c")]
        emit("TRACE_FINALIZE", {"rows_scanned": self.rows, "clusters": len(clusters)})
        result = {
            "rows_scanned": self.rows,
            "n_clusters": len(clusters),
            "time_span": {"min": str(self.low), "max": str(self.high)},
            "null_counts": dict(self.nulls),
            "null_rates": {k: n / self.rows for k, n in self.nulls.items()} if self.rows else {},
            "duplicate_execution_keys_prefix": {
                "rows": self.id_rows,
                "duplicates": self.id_duplicates,
                "scope": "first at most 100000 projected keys, not full-row duplicates",
            },
            "full_row_duplicates": {
                "status": "not measured",
                "reason": "No full-row sort/hash pass; bounded key-prefix check only",
            },
            "hash_like_fingerprints": {"nonnull": self.fp_rows, "hex64": self.hashed_fp},
            "negative_runtimes": self.negative_runtime,
            "pooled": self.scope(None),
            "per_cluster": {},
        }
        for i, cluster in enumerate(clusters):
            result["per_cluster"][cluster] = self.scope(cluster)
            if i % 50 == 0:
                emit("CLUSTER_PROGRESS", {"done": i + 1, "total": len(clusters)})
            check_memory()
        self.db.close()
        return result


def parquet_info(path: Path) -> tuple[pq.ParquetFile, dict]:
    """Inspect schema, footer count and a five-row head without reading a whole file."""
    file = pq.ParquetFile(path)
    head = next(file.iter_batches(batch_size=5), None)
    result = {
        "format": "Parquet",
        "compression": sorted(
            {
                file.metadata.row_group(i).column(0).compression
                for i in range(file.metadata.num_row_groups)
            }
        ),
        "row_count": file.metadata.num_rows,
        "columns": schema_record(file.schema_arrow),
        "head": clean(head.to_pylist()) if head else [],
    }
    return file, result


def scan_trace(
    file: pq.ParquetFile, trace: Trace, dataset: str, namespace: str, limit: int, already: int = 0
) -> int:
    """Project verified names and stream at most the requested physical-prefix rows."""
    mapping = COLUMN_MAP[dataset]
    missing = set(mapping.values()) - set(file.schema_arrow.names)
    if missing:
        raise ValueError(f"Observed schema is missing mapped fields: {missing}")
    count = 0
    for batch in file.iter_batches(batch_size=65536, columns=list(mapping.values())):
        if limit and already + count >= limit:
            break
        if limit:
            batch = batch.slice(0, min(len(batch), limit - already - count))
        trace.add(batch, mapping, namespace)
        count += len(batch)
        if trace.rows % 5_000_000 < 65536:
            emit(
                "TRACE_PROGRESS",
                {
                    "source": namespace,
                    "rows": trace.rows,
                    "rss_mb": round(PROCESS.memory_info().rss / 1024**2, 2),
                },
            )
    return count


def redset(folder: Path, work: Path, limit: int) -> dict:
    """Inspect all copies; measure full canonical files without double-counting samples."""
    result = {
        "path": str(folder),
        "present": folder.is_dir(),
        "files": [],
        "column_map": COLUMN_MAP["redset"],
        "timezone": "timestamp[us] has no timezone. UTC assumed for bucketing, not enco"
        "ded in source.",
        "licence": "Local LICENSE: Attribution-NonCommercial 4.0 International",
        "capabilities": {
            "table_row_data": "no",
            "sql_text": "no",
            "table_column_names": "no",
            "arrival_timestamps": "yes",
            "fingerprints_templates": "partial",
            "runtimes": "yes",
            "read_write_labels": "yes",
            "commerce_semantics": "no",
        },
        "capability_evidence": "feature_fingerprint is a hash, read/write_table_ids are anonymized"
        " IDs; no SQL/schema/commerce columns.",
    }
    if not folder.is_dir():
        return result
    accumulator = Trace(work / "redset.sqlite", True)
    selected = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        info = file_record(path, folder)
        if path.suffix == ".parquet":
            file, meta = parquet_info(path)
            info.update(meta)
            emit("INSPECT", {"file": info["name"], **meta})
            if path.name == "full.parquet":
                selected.append(str(path.relative_to(folder)))
                info["rows_measured"] = scan_trace(
                    file, accumulator, "redset", path.parent.name, limit
                )
        else:
            info.update(
                format="UTF-8 text",
                compression="none",
                row_count=sum(1 for _ in path.open()),
                columns=[{"name": "line", "type": "text"}],
                head=path.read_text().splitlines()[:5],
            )
            emit("INSPECT", info)
        result["files"].append(info)
    result["measurement_scope"] = {
        "canonical_sources": selected,
        "row_cap_per_canonical_file": limit or None,
        "selection": "physical prefix if capped; all canonical rows otherwise",
        "samples_excluded_from_aggregates": True,
    }
    result["metrics"] = accumulator.finish()
    return result


def snowset(folder: Path, work: Path, limit: int) -> dict:
    """Decompress into owned temp storage, inspect every member, remove extracted bytes."""
    result = {
        "path": str(folder),
        "present": folder.is_dir(),
        "files": [],
        "column_map": COLUMN_MAP["snowset"],
        "timezone": "createdTime/endTime/sec declare UTC",
        "licence": "not stated in the downloaded archive members; not measured beyond local files",
        "capabilities": {
            "table_row_data": "no",
            "sql_text": "no",
            "table_column_names": "no",
            "arrival_timestamps": "yes",
            "fingerprints_templates": "no",
            "runtimes": "yes",
            "read_write_labels": "no",
            "commerce_semantics": "no",
        },
        "capability_evidence": "Main has queryId/warehouseId/createdTime/durationExec/I-O counters"
        "; no SQL/hash/query-type. sec+queryId in ts-explosion describes re"
        "peated activity seconds.",
    }
    if not folder.is_dir():
        return result
    trace = Trace(work / "snowset.sqlite", False)
    for archive in sorted(folder.glob("*.tar.gz")):
        item = file_record(archive, folder)
        item.update(format="tar of Parquet", compression="gzip", members=[], row_count=0)
        directory = work / archive.name
        directory.mkdir()
        schema_set = {}
        measured = 0
        aux_low = aux_high = None
        try:
            with tarfile.open(archive, mode="r|gz") as tar:
                for member in tar:
                    if not member.isfile():
                        continue
                    # Flatten to generated local names, preventing archive path traversal.
                    path = directory / f"member-{len(item['members'])}.parquet"
                    digest = hashlib.sha256()
                    with tar.extractfile(member) as source, path.open("wb") as dest:
                        while block := source.read(8 * 1024**2):
                            digest.update(block)
                            dest.write(block)
                    entry = {
                        "name": member.name,
                        "bytes": member.size,
                        "sha256": digest.hexdigest(),
                    }
                    if not member.name.endswith(".parquet"):
                        entry.update(
                            format="non-Parquet",
                            row_count=None,
                            note="not measured: no supported member format",
                        )
                    else:
                        file = pq.ParquetFile(path)
                        columns = schema_record(file.schema_arrow)
                        schema_id = hashlib.sha256(str(columns).encode()).hexdigest()[:16]
                        if schema_id not in schema_set:
                            schema_set[schema_id] = columns
                            head = next(file.iter_batches(batch_size=5), None)
                            item.setdefault("heads_by_schema", {})[schema_id] = (
                                clean(head.to_pylist()) if head else []
                            )
                            emit(
                                "SNOW_SCHEMA",
                                {
                                    "archive": archive.name,
                                    "schema": columns,
                                    "head": item["heads_by_schema"][schema_id],
                                },
                            )
                        entry.update(
                            format="Parquet",
                            row_count=file.metadata.num_rows,
                            schema_id=schema_id,
                            compression=sorted(
                                {
                                    file.metadata.row_group(i).column(0).compression
                                    for i in range(file.metadata.num_row_groups)
                                }
                            ),
                        )
                        item["row_count"] += file.metadata.num_rows
                        if "main" in archive.name and (not limit or measured < limit):
                            measured += scan_trace(
                                file, trace, "snowset", "warehouse", limit, measured
                            )
                        elif "explosion" in archive.name:
                            # Exact time span can be read from footer statistics, without
                            # misclassifying hundreds of millions of active seconds as arrivals.
                            col = file.schema_arrow.get_field_index("sec")
                            for i in range(file.metadata.num_row_groups):
                                stats = file.metadata.row_group(i).column(col).statistics
                                if stats and stats.has_min_max:
                                    aux_low = (
                                        stats.min if aux_low is None else min(aux_low, stats.min)
                                    )
                                    aux_high = (
                                        stats.max if aux_high is None else max(aux_high, stats.max)
                                    )
                    emit("MEMBER", entry)
                    item["members"].append(entry)
                    path.unlink()
            item["schemas"] = schema_set
            item["rows_measured"] = measured
            if "explosion" in archive.name:
                item["time_span_footer"] = {"min": str(aux_low), "max": str(aux_high)}
                item["excluded_from_query_arrivals"] = True
                item["interpretation"] = (
                    "queryId repeats at successive sec values; active-query-seconds, no"
                    "t independent arrivals or template repeats"
                )
        finally:
            shutil.rmtree(directory)
        emit(
            "SNOW_ARCHIVE",
            {k: v for k, v in item.items() if k not in {"members", "heads_by_schema", "schemas"}},
        )
        result["files"].append(item)
    result["measurement_scope"] = {
        "canonical_source": "snowset-main only",
        "row_cap": limit or None,
        "physical_prefix_sample_if_capped": bool(limit),
        "runtime_unit": "durationExec source units; no unit declaration supplied locally; d"
        "o not normalize as PostgreSQL milliseconds without confirmation",
    }
    result["metrics"] = trace.finish()
    return result


def predicate(node: exp.Expression) -> bool:
    """True if a comparison belongs to a WHERE or JOIN predicate in this select scope."""
    parent = node.parent
    while parent is not None:
        if isinstance(parent, (exp.Where, exp.Join)):
            return True
        if isinstance(parent, exp.Select):
            return False
        parent = parent.parent
    return False


def query_patterns(trees: list[exp.Expression]) -> tuple[set, set]:
    """Detect structural candidates, not semantic equivalence or safe join removal."""
    patterns, tables = set(), set()
    for tree in trees:
        tables.update(t.name.casefold() for t in tree.find_all(exp.Table))
        for comparison in tree.find_all(
            exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between, exp.Like
        ):
            if not predicate(comparison):
                continue
            left, right = comparison.this, comparison.expression
            if isinstance(comparison, exp.Between):
                right = exp.Tuple(expressions=[comparison.args["low"], comparison.args["high"]])
            if left is None or right is None:
                continue
            for wrapped, other in ((left, right), (right, left)):
                if not list(other.find_all(exp.Column)) and any(
                    list(func.find_all(exp.Column)) for func in wrapped.find_all(exp.Func, exp.Cast)
                ):
                    patterns.add("P1")
        for scope in traverse_scope(tree):
            outer = scope.parent
            if outer is None:
                continue
            outer_names = set()
            while outer is not None:
                outer_names.update(outer.sources)
                outer = outer.parent
            if scope.can_be_correlated and any(
                col.table in outer_names for col in scope.external_columns if col.table
            ):
                patterns.add("P2")
        for select in tree.find_all(exp.Select):
            group = select.args.get("group")
            if select.args.get("distinct") and group:
                keys = {x.sql(normalize=True) for x in group.expressions}
                projected = {
                    (x.this if isinstance(x, exp.Alias) else x).sql(normalize=True)
                    for x in select.expressions
                }
                if keys and keys <= projected:
                    patterns.add("P3a")
            if (
                isinstance(select.parent, (exp.Subquery, exp.CTE))
                and select.args.get("order")
                and not select.args.get("limit")
                and not select.args.get("offset")
            ):
                patterns.add("P3b")
            for join in select.args.get("joins") or []:
                alias = join.this.alias_or_name
                if str(join.side).upper() != "LEFT" or not alias:
                    continue
                if any(
                    isinstance(x, exp.Star) or list(x.find_all(exp.Star))
                    for x in select.expressions
                ):
                    continue
                outside = [
                    col
                    for col in select.find_all(exp.Column)
                    if col.find_ancestor(exp.Join) is not join and col.table == alias
                ]
                # Unqualified columns may refer to the right side: retain uncertainty.
                if not outside and not any(not col.table for col in select.find_all(exp.Column)):
                    patterns.add("P3c")
    if patterns:
        patterns.add("any")
    return patterns, tables


class SQLStats:
    """Count SQL structure per query with bounded examples containing IDs only."""

    def __init__(self) -> None:
        self.n = self.parsed = self.fallback = self.commerce = self.duplicate_sql = 0
        self.patterns: Counter = Counter()
        self.statement_types: Counter = Counter()
        self.tables: Counter = Counter()
        self.failures: Counter = Counter()
        self.examples = {name: [] for name in PATTERNS}
        self.seen = set()
        self.low = self.high = None
        self.nulls: Counter = Counter()
        self.types: dict[str, set] = {}
        self.head = []
        self.duplicate_rows = 0
        self.row_seen = set()

    def add(self, row: dict, query: str | None, identity: str, stamp: str | None = None) -> None:
        """Parse original SQL in T-SQL then generic dialect; count opaque commands as failures."""
        self.n += 1
        for key, value in row.items():
            self.types.setdefault(key, set()).add(type(value).__name__)
            self.nulls[key] += value is None or value == ""
        if len(self.head) < 5:
            self.head.append(clean(row))
        row_hash = hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).digest()
        self.duplicate_rows += row_hash in self.row_seen
        self.row_seen.add(row_hash)
        if stamp:
            self.low = stamp if self.low is None else min(self.low, stamp)
            self.high = stamp if self.high is None else max(self.high, stamp)
        if not isinstance(query, str) or not query.strip():
            self.failures["missing_sql"] += 1
            return
        digest = hashlib.sha256(query.encode()).digest()
        self.duplicate_sql += digest in self.seen
        self.seen.add(digest)
        if len(query) > 1_000_000:
            self.failures["over_1M_character_parse_budget"] += 1
            return
        trees = None
        for dialect in ("tsql", None):
            try:
                parsed = [t for t in sqlglot.parse(query, read=dialect) if t is not None]
                if parsed and not any(isinstance(t, exp.Command) for t in parsed):
                    trees = parsed
                    self.fallback += dialect is None
                    break
            except (Exception, RecursionError):
                pass
        if trees is None:
            self.failures["parse_error_or_opaque_command"] += 1
            return
        try:
            patterns, tables = query_patterns(trees)
        except (Exception, RecursionError):
            self.failures["ast_scope_error"] += 1
            return
        self.parsed += 1
        self.statement_types.update(tree.key for tree in trees)
        self.patterns.update(patterns)
        self.tables.update(tables)
        self.commerce += any(COMMERCE.search(name) for name in tables)
        for pattern in patterns:
            if len(self.examples[pattern]) < 5:
                self.examples[pattern].append(identity)
        if self.n % 2000 == 0:
            emit("SQL_PROGRESS", {"n": self.n, "parsed": self.parsed})
            check_memory()

    def result(self) -> dict:
        """Return measured denominators, dialect failures and heuristic limitations."""
        return {
            "n_queries": self.n,
            "n_parsed": self.parsed,
            "parse_failure_rate": (self.n - self.parsed) / self.n if self.n else None,
            "default_dialect_fallback_success": self.fallback,
            "derived_statement_type_counts": dict(self.statement_types),
            "failures": dict(self.failures),
            "patterns": {
                name: {
                    "count": self.patterns[name],
                    "rate_of_parsed": self.patterns[name] / self.parsed if self.parsed else None,
                    "rate_of_all": self.patterns[name] / self.n if self.n else None,
                    "example_ids": self.examples[name],
                }
                for name in PATTERNS
            },
            "distinct_tables": len(self.tables),
            "top_20_tables": self.tables.most_common(20),
            "ecommerce_like_query_count": self.commerce,
            "ecommerce_like_share_of_parsed": (
                self.commerce / self.parsed if self.parsed else None
            ),
            "ecommerce_definition": (
                "lexical table-name tokens order/customer/product/stock/inventory/sales; "
                "not proof of commerce records"
            ),
            "duplicate_sql_texts": self.duplicate_sql,
            "duplicate_rows": self.duplicate_rows,
            "columns": [{"name": k, "types_observed": sorted(v)} for k, v in self.types.items()],
            "null_counts": dict(self.nulls),
            "head": self.head,
            "time_span": {"min": self.low, "max": self.high},
            "pattern_caveat": "AST candidates only; P2 qualified outer references; P3c heuristic,"
            " never proof; original SQL parsed without parameter substitution",
        }


def skyserver(folder: Path, work: Path, limit: int) -> dict:
    """Do not confuse the Zenodo numeric/binary benchmark bundle with a SQL log."""
    requested = EXTERNAL / "skyserver"
    result = {
        "requested_path": str(requested),
        "requested_path_exists": requested.is_dir(),
        "path": str(folder),
        "present": folder.is_dir(),
        "files": [],
        "path_note": "Requested skyserver/ is absent; actual download is skyserver_zenodo/",
        "licence": "Local source_record.json: cc-by-4.0",
        "capabilities": {
            k: "no"
            for k in [
                "table_row_data",
                "sql_text",
                "table_column_names",
                "arrival_timestamps",
                "fingerprints_templates",
                "runtimes",
                "read_write_labels",
                "commerce_semantics",
            ]
        },
        "capability_evidence": "Binary .data/query/answer and numeric skyserver.queries; no SQL st"
        "rings/schema/arrival fields. Binary row interpretation undocumente"
        "d locally.",
        "metrics": {
            "n_queries": None,
            "n_parsed": None,
            "parse_failure_rate": None,
            "status": "not measured",
            "reason": "This bundle is not a textual SQL workload",
        },
    }
    if not folder.is_dir():
        return result
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        info = file_record(path, folder)
        with path.open("rb") as stream:
            prefix = stream.read(4096)
        if path.name == "source_record.json":
            metadata = json.loads(path.read_text())
            info.update(
                format="JSON repository metadata",
                row_count=1,
                columns=[{"name": k, "type": type(v).__name__} for k, v in metadata.items()],
                head=[
                    {
                        "title": metadata.get("title"),
                        "licence": metadata.get("metadata", {}).get("license"),
                    }
                ],
            )
        elif b"\x00" in prefix or path.name == "skyserver.data":
            info.update(
                format="binary; record layout not stated locally",
                compression="not identified",
                columns=None,
                row_count=None,
                row_count_reason="not measured: binary schema unknown",
                head_hex_words=[prefix[i : i + 8].hex() for i in range(0, 40, 8)],
                uint64_le_head_hypothesis=np.frombuffer(prefix[:40], dtype="<u8").tolist(),
                uint32_le_word_count_hypothesis=info["bytes"] // 4
                if info["bytes"] % 4 == 0
                else None,
                binary_layout_hypotheses_not_schema=True,
            )
        else:
            rows = 0
            head = []
            widths: Counter = Counter()
            for line in path.open():
                parts = line.split()
                if not parts:
                    continue
                rows += 1
                widths[len(parts)] += 1
                if len(head) < 5:
                    head.append(parts)
            info.update(
                format="whitespace-delimited numeric text",
                compression="none",
                row_count=rows,
                columns=[
                    {"name": "unnamed_field_1", "type": "integer text"},
                    {"name": "unnamed_field_2", "type": "decimal text; meaning/units unknown"},
                ],
                head=head,
                observed_field_counts=dict(widths),
            )
        emit("INSPECT", info)
        result["files"].append(info)
    return result


class HashReader(io.RawIOBase):
    """Hash decompressed ZIP member bytes while csv/TextIOWrapper streams them."""

    def __init__(self, source: Any) -> None:
        self.source = source
        self.digest = hashlib.sha256()

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        data = self.source.read(len(buffer))
        self.digest.update(data)
        buffer[: len(data)] = data
        return len(data)


def sqlshare(folder: Path, work: Path, limit: int) -> dict:
    """Inspect scientific row data and the separate query/view text in-place in the ZIP."""
    import csv

    csv.field_size_limit(16 * 1024**2)
    result = {
        "path": str(folder),
        "present": folder.is_dir(),
        "files": [],
        "licence": "No licence file found in the ZIP; online terms not checked (offline)",
        "capabilities": {
            "table_row_data": "yes",
            "sql_text": "yes",
            "table_column_names": "yes",
            "arrival_timestamps": "no",
            "fingerprints_templates": "partial",
            "runtimes": "no",
            "read_write_labels": "partial",
            "commerce_semantics": "partial",
        },
        "capability_evidence": "ZIP contains data/<user>/ tables plus queries.txt and view_script."
        "txt. These are heterogeneous user data, not a connected commerce s"
        "chema. Lexical commerce matches are measured below.",
    }
    if not folder.is_dir():
        return result
    combined = SQLStats()
    for archive in sorted(folder.glob("*.zip")):
        item = file_record(archive, folder)
        item.update(
            format="ZIP",
            compression="ZIP_DEFLATED or per-member stored method",
            entries=[],
            columns="heterogeneous; see member columns",
            row_count=None,
        )
        with zipfile.ZipFile(archive) as zip_file:
            item["entry_count"] = len(zip_file.infolist())
            for member in zip_file.infolist():
                if member.is_dir():
                    continue
                entry = {
                    "name": member.filename,
                    "bytes": member.file_size,
                    "compressed_bytes": member.compress_size,
                    "compression_code": member.compress_type,
                }
                is_sql = member.filename.endswith(("/queries.txt", "/view_script.txt"))
                with zip_file.open(member) as source:
                    raw = HashReader(source)
                    stream = io.TextIOWrapper(
                        io.BufferedReader(raw), encoding="utf-8", errors="replace", newline=""
                    )
                    if is_sql:
                        stats = SQLStats()
                        buffer: list[str] = []
                        row_count = 0

                        def flush(
                            buffer: list[str] = buffer,
                            stats: SQLStats = stats,
                            member: zipfile.ZipInfo = member,
                        ) -> None:
                            nonlocal row_count
                            query = "".join(buffer).strip()
                            if query:
                                row_count += 1
                                if not limit or row_count <= limit:
                                    identity = f"{member.filename}:{row_count}"
                                    record = {"sql": query}
                                    stats.add(record, query, identity)
                                    if member.filename.endswith("/queries.txt"):
                                        combined.add(record, query, identity)
                            buffer.clear()

                        for line in stream:
                            if member.filename.endswith("/queries.txt") and re.fullmatch(
                                r"\s*_{20,}\s*", line
                            ):
                                flush()
                            elif member.filename.endswith("/view_script.txt") and re.match(
                                r"\s*create\s+view\b", line, re.I
                            ):
                                flush()
                                buffer.append(line)
                            else:
                                buffer.append(line)
                        flush()
                        entry.update(
                            format="SQL blocks",
                            row_count=row_count,
                            inspection=stats.result(),
                            column_origin=(
                                "sql is an extracted text-block label, not a source header; "
                                "example IDs are member:ordinal"
                            ),
                        )
                    else:
                        prefix = stream.read(8192)
                        # Reopen for CSV so no first-row bytes are lost to dialect sniffing.
                        stream.close()
                        try:
                            dialect = csv.Sniffer().sniff(prefix, delimiters=",\t|;")
                        except csv.Error:
                            dialect = csv.excel
                        count = 0
                        head = []
                        columns = None
                        widths: Counter = Counter()
                        error = None
                        with zip_file.open(member) as csv_source:
                            csv_raw = HashReader(csv_source)
                            csv_stream = io.TextIOWrapper(
                                io.BufferedReader(csv_raw),
                                encoding="utf-8",
                                errors="replace",
                                newline="",
                            )
                            try:
                                reader = csv.reader(csv_stream, dialect=dialect)
                                for row in reader:
                                    if columns is None:
                                        columns = row
                                        continue
                                    count += 1
                                    widths[len(row)] += 1
                                    if len(head) < 5:
                                        head.append(clean(row))
                            except (csv.Error, UnicodeError) as exc:
                                error = type(exc).__name__
                                while csv_stream.read(8 * 1024**2):
                                    pass
                            entry["sha256"] = csv_raw.digest.hexdigest()
                        entry.update(
                            format="delimited text inferred by csv.Sniffer",
                            delimiter=dialect.delimiter,
                            columns=[
                                {"name": c, "type": "text; source has no declared types"}
                                for c in (columns or [])
                            ],
                            row_count=None if error else count,
                            head=head,
                            row_width_counts=dict(widths),
                            parse_error=error,
                            header_assumption=(
                                "First CSV record treated as header; source-wide header "
                                "semantics not independently verified"
                            ),
                        )
                    if "sha256" not in entry:
                        entry["sha256"] = raw.digest.hexdigest()
                emit("ZIP_MEMBER", entry)
                item["entries"].append(entry)
                check_memory()
        result["files"].append(item)
    result["metrics"] = combined.result()
    result["measurement_scope"] = {
        "primary_sql": "queries.txt",
        "views_separate_not_pooled": True,
        "SQL_cap_per_member": limit or None,
        "table_row_counts": "full streaming CSV pass; header excluded; malformed CSV labelled n"
        "ot measured",
        "timestamp_note": "Timestamps inside scientific tables are domain observations, not q"
        "uery arrivals",
    }
    return result


def sede(folder: Path, work: Path, limit: int) -> dict:
    """Inspect original/fixed splits without assuming rewrite-equivalence labels."""
    result = {
        "path": str(folder),
        "present": folder.is_dir(),
        "files": [],
        "licence": "Local root LICENSE: Apache License 2.0; does not by itself resolve"
        " upstream SQL/data redistribution terms",
        "capabilities": {
            "table_row_data": "no",
            "sql_text": "yes",
            "table_column_names": "yes",
            "arrival_timestamps": "no",
            "fingerprints_templates": "partial",
            "runtimes": "no",
            "read_write_labels": "partial",
            "commerce_semantics": "partial",
        },
        "capability_evidence": "JSONL QueryBody/Title/Description/QuerySetId/CreationDate/validate"
        "d; schema JSON documents StackExchange tables without table rows. "
        "CreationDate is query artifact metadata, not an arrival trace.",
    }
    if not folder.is_dir():
        return result
    combined = SQLStats()
    source_fields = {}
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        info = file_record(path, folder)
        if path.suffix == ".jsonl" and path.parent.name == "sede":
            stats = SQLStats()
            total = 0
            for line in path.open():
                if not line.strip():
                    continue
                total += 1
                row = json.loads(line)
                if not limit or total <= limit:
                    stats.add(
                        row,
                        row.get("QueryBody"),
                        str(row.get("QuerySetId")),
                        row.get("CreationDate"),
                    )
                    if "_original" not in path.name:
                        combined.add(
                            row,
                            row.get("QueryBody"),
                            f"{path.name}:{row.get('QuerySetId')}",
                            row.get("CreationDate"),
                        )
            info.update(
                format="JSONL", compression="none", row_count=total, inspection=stats.result()
            )
            emit(
                "INSPECT",
                {
                    "name": info["name"],
                    "row_count": total,
                    "columns": stats.result()["columns"],
                    "head": stats.head,
                },
            )
        elif path.name == "tables_so.json":
            schema = json.loads(path.read_text())
            info.update(
                format="JSON schema metadata",
                row_count=len(schema),
                columns=list(schema[0]) if schema else [],
                table_names=schema[0].get("table_names_original", []) if schema else [],
            )
            source_fields = info
            emit("SCHEMA", info)
        else:
            info.update(
                format="repository auxiliary file",
                row_count=None,
                row_count_reason="not a data row file; hashed for provenance",
            )
        result["files"].append(info)
        check_memory()
    result["metrics"] = combined.result()
    result["schema_file"] = source_fields
    result["measurement_scope"] = {
        "primary_splits": ["train.jsonl", "val.jsonl", "test.jsonl"],
        "original_splits_inspected_separately": True,
        "cap_per_split": limit or None,
        "validated_is_not_equivalence_label": True,
    }
    return result


def enrich_inventory(inventory: dict) -> None:
    """Expose machine flags and file-level duplicate evidence without rescanning inputs."""
    for name, item in inventory["datasets"].items():
        item["capability_flags"] = {
            key: {"yes": True, "no": False, "partial": None}[state]
            for key, state in item.get("capabilities", {}).items()
        }
        item["capability_flag_note"] = "null means partial; see qualitative capability evidence"
        item["format"] = sorted({f.get("format", "unknown") for f in item.get("files", [])})
        item["time_span"] = item.get("metrics", {}).get("time_span")
        metrics = item.get("metrics", {})
        if name in {"sqlshare", "sede"} and metrics.get("n_queries"):
            metrics["null_rates_observed_fields"] = {
                key: count / metrics["n_queries"]
                for key, count in metrics.get("null_counts", {}).items()
            }
            metrics["null_rate_note"] = (
                "None/empty values for present fields divided by analyzed records; "
                "absent JSON keys are not included in these null counts"
            )
        groups: dict[str, list] = {}
        for file in item.get("files", []):
            if file.get("format") != "repository auxiliary file":
                groups.setdefault(file["sha256"], []).append(file["name"])
        item["identical_input_files"] = [names for names in groups.values() if len(names) > 1]
        if name == "sqlshare":
            entries = [entry for file in item["files"] for entry in file.get("entries", [])]
            item["table_members"] = sum("inspection" not in entry for entry in entries)
            item["table_rows_under_inferred_header_policy"] = sum(
                entry.get("row_count") or 0 for entry in entries if "inspection" not in entry
            )
    inventory["meta"]["database_connection_scope"] = (
        "Project/PostgreSQL connections only; temporary SQLite aggregation is excluded"
    )


def report_text(inventory: dict) -> str:
    """Build the requested evidence-based report without raw SQL redistribution."""
    ds = inventory["datasets"]
    lines = [
        "# Dataset gap analysis",
        "",
        "## Verdict",
        "",
        "The downloaded datasets are **not sufficient to populate the quick"
        "-commerce evaluation database with real commerce records**. "
        "Redset/Snowset contain execution metadata, SEDE contains SQL and s"
        "chema metadata without StackExchange rows, "
        "and the actual SkyServer Zenodo bundle is numeric/binary benchmark"
        " material, not a SQL log. "
        "SQLShare does include real underlying table rows, but they are het"
        "erogeneous user/scientific tables rather than "
        "a verified connected orders/items/products/stock/customer/store/ri"
        "der system. Metadata IDs are not customer/product IDs. "
        "Use these sources for workload calibration and SQL structure evide"
        "nce; retain explicitly synthetic operational data "
        "or obtain a separate commerce dataset. This is an offline finding "
        "about the inspected local copies, not every version of each source"
        ".",
        "",
        "## Run scope and reproducibility",
        "",
        f"- Generated: {inventory['meta']['created_at']}; "
        f"script SHA-256: `{inventory['meta']['script_sha256']}`.",
        f"- Command: `{inventory['meta']['command']}`.",
        f"- Row cap: {inventory['meta']['sample_rows']} "
        "(0 means all canonical trace rows / all SQL queries). "
        "Physical-prefix samples are diagnostic, not random representative estimates.",
        f"- Peak RSS: {inventory['meta']['peak_rss_mb']:.2f} MiB; "
        f"batch-boundary memory cap: {inventory['meta']['memory_cap_mb']} MiB.",
        "- No network, LLM, PostgreSQL connection or source-data changes. S"
        "nowset is streamed into owned temporary files, "
        "each removed after inspection; the run's temporary directory is cleaned on exit.",
        "- Inventory contains every input file/member name, bytes and fresh"
        "ly measured SHA-256, schema/type metadata and redacted heads. "
        "Long exact per-minute/hourly series use the documented base64/zlib"
        " little-endian array encoding.",
        "",
        "## Per-dataset evidence",
        "",
        "| Dataset | Contains | Missing | Derivable | Not usable for |",
        "|---|---|---|---|---|",
        "| Redset | Parquet query arrivals, instance/database/table IDs, du"
        "rations, query types, feature hashes | SQL text, real table/column"
        " names, table rows, commerce keys, rewrite labels | Generic DB bur"
        "st series, repeat/coverage/drift, read/write statement shares | Po"
        "pulating commerce entities; product/customer popularity; direct re"
        "play |",
        "| Snowset | Main query execution timings and I/O counters; auxilia"
        "ry active-query-seconds | SQL, template fingerprints, query-type l"
        "abels, table schema/rows, commerce keys | Generic warehouse arriva"
        "ls, duration distribution, concurrency after queryId join | Templa"
        "te repetition from queryId; inferring DML from physical writes; co"
        "mmerce load |",
        "| SkyServer | Actual `skyserver_zenodo/` numeric/binary benchmark "
        "files and local record metadata | Intended SQL query log; document"
        "ed binary record schema; commerce entities | Limited numeric fixtu"
        "re inspection after schema documentation | SQL-pattern prevalence,"
        " report-template extraction, query arrival calibration |",
        "| SQLShare | SQL blocks, view definitions, and real delimited user"
        " table data | Verified commerce relationships, trace arrival/runti"
        "mes, operational stock/riders, equivalence labels | SQL AST patter"
        "n prevalence; manual structure translation; optional non-commerce "
        "testbed | Blindly importing science rows as orders or using data t"
        "imestamps as query arrivals |",
        "| SEDE | SQL + title/description, query IDs, query-artifact dates,"
        " schema JSON; original/fixed split variants | Actual StackExchange"
        " table rows, execution timings/arrivals, commerce schema, equivale"
        "nce labels | SQL patterns, NL question structure, syntax-derived s"
        "tatement labels/templates | Treating `validated` or original/fixed"
        " pairs as rewrite-equivalence ground truth |",
        "",
    ]
    for name, item in ds.items():
        lines += [
            f"### {name}",
            "",
            f"Path: `{item.get('path')}`. Present: **{item.get('present')}**.",
        ]
        if name == "skyserver":
            lines.append(
                "Requested `backend/data/external/skyserver/` exists: "
                f"**{item.get('requested_path_exists')}**; "
                "actual alternate folder inspected explicitly."
            )
        if "error" in item:
            lines += ["**Analysis error:** " + item["error"], ""]
            continue
        metrics = item.get("metrics", {})
        lines += [
            "",
            f"Licence evidence: {item.get('licence')}",
            "",
            "Capability flags (yes/no/partial, based on observed fields):",
            "",
            "| Capability | Finding |",
            "|---|---|",
        ]
        for flag, value in item.get("capabilities", {}).items():
            lines.append(f"| {flag} | {value} |")
        lines += [
            "",
            item.get("capability_evidence", ""),
            "",
            "Input files and physical sizes:",
            "",
            "| Input | Bytes | Rows / interpretation |",
            "|---|---:|---|",
        ]
        for file in item.get("files", []):
            if name == "sede" and file.get("format") == "repository auxiliary file":
                continue
            rows = file.get("row_count")
            lines.append(
                f"| `{file['name']}` | {file['bytes']} | "
                f"{rows if rows is not None else 'not measured: non-row or undocumented format'} |"
            )
        if name in {"redset", "snowset"} and "pooled" in metrics:
            pooled = metrics["pooled"]
            lines += [
                "",
                f"Rows measured: **{metrics['rows_scanned']}**; "
                f"clusters/warehouses: **{metrics['n_clusters']}**; "
                f"pooled observed days: **{pooled.get('n_days')}**. "
                f"Time span: `{metrics.get('time_span')}`.",
                f"Timezone: {item.get('timezone')}",
                f"Arrival p99/mean: **{pooled.get('p99_to_mean_arrivals')}**; "
                f"max/mean: **{pooled.get('peak_to_mean_arrivals')}**.",
                "Runtime quantiles (source units): "
                f"`{pooled.get('runtime_percentiles_source_units')}`.",
                f"Read/write measurement: `{pooled.get('read_write')}`.",
                f"Fingerprints: `{pooled.get('fingerprints')}`.",
                "Per-cluster measures, exact minute counts, per-hour dispersion, da"
                "ily top-k coverage and consecutive-day drift are in the JSON; "
                "null fingerprints are excluded from fingerprint denominators and r"
                "eported as nulls, never replaced with query IDs.",
                f"Sanity checks: key null rates `{metrics.get('null_rates')}`; "
                "duplicate-key sample "
                f"`{metrics.get('duplicate_execution_keys_prefix')}`; "
                "full-row duplicates not measured (no full-row sort pass).",
                f"Scope/limitations: `{item.get('measurement_scope')}`.",
            ]
        elif name in {"sqlshare", "sede"}:
            lines += [
                "",
                f"SQL queries analysed: **{metrics.get('n_queries')}**; "
                f"parsed: **{metrics.get('n_parsed')}**; "
                f"failure rate: **{metrics.get('parse_failure_rate')}**. "
                f"Lexical e-commerce matches: **{metrics.get('ecommerce_like_query_count')}**; "
                f"share of parsed: **{metrics.get('ecommerce_like_share_of_parsed')}**.",
                f"Artifact time span (not arrivals): `{metrics.get('time_span')}`.",
                f"Duplicate SQL texts: **{metrics.get('duplicate_sql_texts')}**; "
                f"duplicate primary records: **{metrics.get('duplicate_rows')}**.",
                "",
                "| Pattern | Count | Rate among parsed | Example query IDs |",
                "|---|---:|---:|---|",
            ]
            for pattern, values in metrics.get("patterns", {}).items():
                lines.append(
                    f"| {pattern} | {values['count']} | {values['rate_of_parsed']} | "
                    f"`{values['example_ids']}` |"
                )
            lines += [
                "",
                f"Top referenced table names (per-query counts): `{metrics.get('top_20_tables')}`.",
                "P1=function-wrapped predicate; P2=qualified outer reference; P3a=D"
                "ISTINCT covering GROUP BY keys; "
                "P3b=derived/CTE ORDER BY without LIMIT/OFFSET; P3c=heuristic unused LEFT JOIN. "
                "These are candidate opportunities, not equivalent-rewrite labels. "
                "Query text is redacted; IDs identify examples.",
                f"Scope: `{item.get('measurement_scope')}`.",
            ]
        else:
            lines += [
                "",
                "SQL prevalence, arrivals, fingerprints, runtimes and commerce row "
                "mapping: **not measured / unavailable**, "
                "because the local binary layout lacks a SQL or execution schema. D"
                "o not invent column names or interpret floats as runtimes.",
            ]
        lines.append("")
    lines += [
        "## Gap matrix",
        "",
        "A = Available in the local source; D = Derivable with caveats; M = Missing. "
        "Availability does not establish transferability to quick-commerce.",
        "",
        "| Pipeline requirement | Redset | Snowset | SkyServer | SQLShare | SEDE |",
        "|---|---|---|---|---|---|",
    ]
    matrix = [
        (
            "Connected schema + rows: orders, order_items, products, store_stoc"
            "k, customers, stores, riders",
            "M",
            "M",
            "M",
            "M",
            "M",
        ),
        ("Hot-product/heavy-customer skew and order hour-of-day shape", "M", "M", "M", "M", "M"),
        (
            "Generic database burst/spike shape (not observed commerce flash sales)",
            "D",
            "D",
            "M",
            "M",
            "M",
        ),
        ("Recurring templates, repetition and day-to-day drift", "D", "M", "M", "D", "D"),
        ("Transferable slow-report query structures", "M", "M", "M", "D", "D"),
        ("Read/write statement mix", "A", "M", "M", "D", "D"),
        ("Order-processing transaction mix", "M", "M", "M", "M", "M"),
        ("Equivalent/non-equivalent rewrite ground truth", "M", "M", "M", "M", "M"),
        ("Parameter bindings for our PostgreSQL replay", "M", "M", "M", "M", "M"),
    ]
    legend = {"A": "Available", "D": "Derivable", "M": "Missing"}
    for row in matrix:
        lines.append("| " + row[0] + " | " + " | ".join(legend[x] for x in row[1:]) + " |")
    lines += [
        "",
        "## Derivation feasibility and populate plan",
        "",
        "| Method | Feasibility / effort | Concrete mapping | Risks |",
        "|---|---|---|---|",
        "| Redset/Snowset arrivals → minute series, p99/mean, hourly disper"
        "sion | Feasible / M | Evidence for profiles.json `rate_mult`; fit/"
        "compare piecewise rates versus current exponential build_schedule "
        "arrivals | OLAP queries are not orders; sampling, tenant mix and t"
        "imezone assumptions bias calibration |",
        "| Redset hash counts → repeats, top-k, drift | Partial / M | Candi"
        "date future `Shape.weight`/shape-frequency configuration; not curr"
        "ently defined in Shape | feature fingerprint need not equal SQL te"
        "mplate; cached NULLs and DB/tenant scope matter |",
        "| Snowset queryId → recurring SQL templates | Not feasible / S dia"
        "gnosis | No mapping: execution ID is not a template hash | Repeate"
        "d queryId in ts-explosion means activity across seconds |",
        "| Trace read/write labels → order transaction weights | Partial / "
        "M | Redset SELECT/INSERT/UPDATE mix supplies a scenario bound, not"
        " direct `TxnDef.weight` replacement | One place_order transaction "
        "has multiple statements; OLAP COPY differs from commerce writes |",
        "| SQLShare/SEDE AST patterns → report structures | Partial / L | M"
        "anually adapt structures to `recent_orders`, `delivered_yesterday`"
        ", `stockouts_15m`, `hourly_sales`, `open_order_customers`, `payout"
        "_report`, `top_products_store`, `picker_queue` | T-SQL syntax, tab"
        "le keys, NULLs, bag semantics, timezone and top-level ordering mus"
        "t be revalidated |",
        "| SEDE Title/Description → NL questions | Feasible structure reuse"
        " / M | `Shape.question`, generated questions.json after changing d"
        "omain nouns and bindings | Hidden assumptions and under-specificat"
        "ion; do not claim the question describes an order dataset |",
        "| Source rows → commerce records | Not feasible for a verified dir"
        "ect import / L if attempted | Every required commerce table still "
        "needs synthetic rows or separate licensed commerce input | Scienti"
        "fic rows/schema-only metadata are not orders/inventory |",
        "",
        "### Recommended order of work",
        "",
        "1. Keep the schema in step 02. Decide whether the evaluation is ho"
        "nestly labelled trace-calibrated synthetic commerce, "
        "or requires a separate real commerce dataset with provenance. None"
        " is newly downloaded or claimed by this analysis.",
        "2. Preserve deterministic `--seed`, `--scale` and UTC-noon `meta.a"
        "s_of`. The current `seed.py` binds `n_customers=20000*scale`, "
        "`n_orders=300000*scale`; `seed_helpers.sql` sets stores=50, catego"
        "ries=40, products=2000, riders=500, stock presence=0.8. "
        "These counts and relationships are synthetic, not derived from trace query counts.",
        "3. Compute full-coverage per-tenant calibration aggregates before "
        "choosing `profiles.json` `rate_mult` or a burst schedule. "
        "Do not change `BASE_RATE_TPS` directly from cloud query rates: kee"
        "p local saturation calibration in step 11. "
        "Use metadata-derived rates as sensitivity scenarios with clear nor"
        "malization and keep 5/10/20 as declared experiment levels unless c"
        "ontracts change.",
        "4. Keep `zipf_s`, `hot_set_size`, `hot_share` synthetic until orde"
        "r-item/customer records are available. "
        "Also retain declared assumptions for `status_poll_mult`, `dashboar"
        "d_refresh_s`, `pre_sale_load`, `analyze_after_load`. "
        "These traces cannot identify hot SKUs, dark-store stock, status po"
        "lling or catalogue updates.",
        "5. Keep `ORDER_TXNS` (`TxnDef.weight`) defaults from step 04: `pla"
        "ce_order`, `update_status`, `order_status`, "
        "`stock_decrement`, `picker_queue_read`. Any proposed adaptation mu"
        "st map statement groups to transactions and be swept, "
        "not estimated as real commerce mix from SELECT/UPDATE shares.",
        "6. Use SQLShare and SEDE IDs/pattern counts to hand-select structu"
        "res and adapt them onto the existing report shapes. "
        "`app/shapes.py`, `backend/data/profiles.json` and ORDER_TXNS are p"
        "lanned interfaces, not implemented files in this checkout. "
        "No shape-weight field currently exists; adding one requires a documented contract change.",
        "7. Sample `customer_id`, `store_id`, `product_id`, `city`, `as_of`"
        ", `start`, `end`, `q`, `new`, `id` from actual evaluation-DB pools"
        ". "
        "Cloud table/query IDs and SEDE placeholder names cannot substitute for replay parameters.",
        "8. Build human-labelled equivalent/trap pairs under step 10/18 and"
        " run the tiered checks. Parsing, SQLShare materializations, "
        "SEDE `validated`, and fixed/original query variants are not equiva"
        "lence proofs or labelled unsafe tuning examples.",
        "",
        "## Risks and licence notes",
        "",
        "- Redset local LICENSE states Attribution-NonCommercial 4.0 Intern"
        "ational. The local SkyServer record states cc-by-4.0. "
        "SEDE root code licence is Apache-2.0; underlying query/data rights"
        " require separate confirmation. Snowset and SQLShare "
        "local copies do not supply an explicit licence document. No online"
        " legal/usage terms were checked; no permission is inferred from pu"
        "blic download access.",
        "- No raw SQL is reproduced in this report; examples are source IDs"
        ". Do not redistribute dataset SQL or table rows until source-speci"
        "fic rights are confirmed.",
        "- Scientific metadata and query creation dates cannot validate a c"
        "ommerce flash-sale scenario. Physical I/O writes do not establish "
        "logical DML labels.",
        "- CSV dialect/header inference is heuristic; malformed/heterogeneo"
        "us members are identified in JSON. SQL parse failures and "
        "unresolved aliases affect prevalence; unqualified correlation is i"
        "ntentionally not asserted. LEFT JOIN removal may multiply rows eve"
        "n if right-side columns are unused.",
        "- Schema names are lexical evidence only. Source literals are not "
        "a complete parameter binding log. Sample-derived rates are not pop"
        "ulation rates; repeated query copies must be excluded.",
        "",
        "## Open questions",
        "",
        "- Which real commerce dataset, if any, supplies linked orders/item"
        "s/products plus permission to generate missing inventory/store/rid"
        "er fields?",
        "- Is a documented trace-calibrated synthetic commerce evaluation a"
        "cceptable, rather than an unsupported claim of real-commerce recor"
        "ds?",
        "- What are Snowset duration units and the release's queryId identi"
        "ty guarantees? Local files alone do not establish them.",
        "- Can the intended SDSS SQL query log be supplied? The downloaded "
        "Zenodo numeric bundle is not that log.",
        "- Are UTC bucketing assumptions for Redset valid, and how should p"
        "er-tenant observation gaps/cached-query NULL fingerprints be handl"
        "ed?",
        "- Which source licences permit redistribution of raw SQL and deriv"
        "ative NL examples? Which original/fixed SEDE variants should be us"
        "ed for each analysis?",
        "",
        "## Proposed documentation edits (not applied)",
        "",
        "```diff",
        "--- docs/steps/02_schema_and_seeding.md",
        "+ Explicitly label the current rows synthetic; separate source pro"
        "venance from trace-calibration provenance.",
        "+ Add optional versioned calibration metadata only after per-tenan"
        "t validation; retain deterministic seed/as_of.",
        "+ Mark all products/customer skew, store_stock, riders, statuses a"
        "nd transaction bindings generated unless a commerce source supplie"
        "s them.",
        "+ Require field-level provenance and tests before replacing synthe"
        "tic rows with real-commerce input.",
        "--- docs/steps/18_datasets_analysis.md",
        "+ Use the actually present skyserver_zenodo folder; flag its numer"
        "ic/binary format as unsuitable for SQL prevalence.",
        "+ Add separate branches for SQLShare underlying table inspection a"
        "nd its queries.txt/view_script.txt SQL.",
        "+ Remove assumptions that Snowset supplies SQL template fingerprin"
        "ts or logical read/write labels.",
        "+ Exclude ts-explosion active-query-seconds and Redset sample copi"
        "es from canonical query-arrival counts.",
        "+ Exclude SEDE _original variants from primary split pooling; do n"
        "ot treat validated/fixed pairs as equivalence labels.",
        "+ Store input/member hashes, sample scope, timezone/duration-unit "
        "assumptions and parse-failure denominators.",
        "```",
        "",
        "## Failures and unmeasured items",
        "",
    ]
    for name, item in ds.items():
        if item.get("error"):
            lines.append(f"- {name}: {item['error']}")
    lines += [
        "- Full-row trace duplicate counts are not measured: only a bounded"
        " execution-key prefix sanity check is performed.",
        "- SkyServer binary row count/record semantics and SQL prevalence a"
        "re not measured without a binary schema/SQL log.",
        "- SQLShare per-cell missingness across heterogeneous table rows is"
        " not measured; key nulls and row-width/CSV parse diagnostics are p"
        "rovided where meaningful.",
        "- No real commerce Zipf exponent, customer repeat-purchase distrib"
        "ution, order arrival profile, stock state or rewrite equivalence g"
        "round truth can be inferred from the inspected schemas.",
        "- Numbers are script outputs; no expected benchmark result is hard"
        "-coded as a measurement.",
        "",
    ]
    lines += [
        "## Additional inspection evidence",
        "",
        "The gap-matrix Derivable cells for SQLShare/SEDE recurrence mean static SQL "
        "normalization/repetition only. Execution-arrival repetition and day-to-day drift "
        "are Missing for those sources; artifact creation dates do not supply them.",
        "",
        "The memory setting is a batch-boundary budget, not an operating-system hard limit. "
        "The measured peak includes transient batch allocations; use the recorded peak "
        "when planning resources.",
        "",
    ]
    if "sqlshare" in ds and not ds["sqlshare"].get("error"):
        item = ds["sqlshare"]
        lines += [
            "### SQLShare filename false positives",
            "",
            "These are observed source members, not assumed commerce schemas. Row counts "
            "follow the documented inferred CSV-header policy. No cell values are copied.",
            "",
            "| Member | Rows | Actual header fields |",
            "|---|---:|---|",
        ]
        for file in item["files"]:
            for entry in file.get("entries", []):
                if any(
                    token in entry["name"].lower()
                    for token in (
                        "order_emails",
                        "coffee production",
                        "glycerol_stocks",
                        "retail_electric",
                    )
                ):
                    fields = [field["name"] for field in entry.get("columns", [])]
                    lines.append(f"| `{entry['name']}` | {entry.get('row_count')} | `{fields}` |")
        lines += [
            "",
            "An email-address column is not an order/customer relational model. "
            "Coffee production, electricity prices and glycerol-sample locations do not "
            "supply linked retail orders, SKU line items and dark-store inventory.",
            "",
            "SQLShare query-arrival time span is not measured: queries.txt contains SQL "
            "blocks without execution timestamps. Table-specific scientific observation "
            "date spans are not pooled across unrelated domains.",
            "",
        ]
    if inventory["meta"].get("report_renderer_sha256"):
        lines += [
            "Report-only enrichment preserved all measured statistics. Renderer SHA-256: "
            f"`{inventory['meta']['report_renderer_sha256']}`.",
            "",
        ]
    return "\n".join(lines)


def main() -> int:
    """Run requested dataset scopes with temporary spill storage and produce both artifacts."""
    global MEMORY_MB
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=("all", *DATASETS), default="all")
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=0,
        help="0=all; otherwise physical-prefix row cap per canonical source/split",
    )
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--memory-mb", type=int, default=1536)
    parser.add_argument(
        "--refresh-report",
        action="store_true",
        help="Render/enrich an existing inventory; do not rescan datasets",
    )
    args = parser.parse_args()
    if args.sample_rows < 0 or args.memory_mb < 256:
        parser.error("sample rows must be >=0 and memory cap >=256 MiB")
    destination = args.out.resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if destination != OUT and not any(
        destination.is_relative_to(root) for root in (temporary_root, Path("/private/tmp"))
    ):
        parser.error("--out must be the requested inventory path or a path in temporary storage")
    MEMORY_MB = args.memory_mb
    if args.refresh_report:
        inventory = json.loads(destination.read_text())
        enrich_inventory(inventory)
        inventory["meta"]["report_renderer_sha256"] = sha256(Path(__file__))
        inventory["meta"]["report_render_command"] = " ".join(sys.argv)
        destination.write_text(json.dumps(inventory, indent=2, default=str, allow_nan=False))
        if destination == OUT:
            REPORT.write_text(report_text(inventory))
        emit("SUMMARY", {"mode": "report-only; measurements preserved", "out": str(destination)})
        return 0
    started = time.perf_counter()
    inventory = {
        "meta": {
            "created_at": datetime.now(UTC).isoformat(),
            "command": " ".join(sys.argv),
            "script_sha256": sha256(Path(__file__)),
            "sample_rows": args.sample_rows,
            "memory_cap_mb": MEMORY_MB,
            "network_used": False,
            "llm_calls": 0,
            "database_connections": 0,
            "trace_fingerprint_definition": "cluster-scoped feature fingerprints; nulls excluded",
            "sql_example_policy": "no raw SQL copied; heads redacted; query IDs retained",
        },
        "datasets": {},
    }
    names = DATASETS if args.dataset == "all" else (args.dataset,)
    handlers = {
        "redset": redset,
        "snowset": snowset,
        "skyserver": skyserver,
        "sqlshare": sqlshare,
        "sede": sede,
    }
    failures = []
    with tempfile.TemporaryDirectory(prefix="dpa-inventory-") as temp:
        work = Path(temp)
        os.environ["SQLITE_TMPDIR"] = temp
        for name in names:
            folder = EXTERNAL / ("skyserver_zenodo" if name == "skyserver" else name)
            emit("DATASET_BEGIN", {"name": name, "path": str(folder)})
            try:
                inventory["datasets"][name] = handlers[name](folder, work, args.sample_rows)
            except Exception as exc:
                failures.append(name)
                inventory["datasets"][name] = {
                    "path": str(folder),
                    "present": folder.is_dir(),
                    "error": f"{type(exc).__name__}: {exc}",
                    "measurement_status": "not measured: analysis did not complete",
                }
                emit(
                    "DATASET_FAILED", {"name": name, "error": inventory["datasets"][name]["error"]}
                )
            gc.collect()
            pa.default_memory_pool().release_unused()
            emit(
                "DATASET_END",
                {"name": name, "rss_mb": round(PROCESS.memory_info().rss / 1024**2, 2)},
            )
        inventory["meta"].update(
            duration_seconds=round(time.perf_counter() - started, 3),
            peak_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            / (1024**2 if sys.platform == "darwin" else 1024),
            failures=failures,
            temporary_storage_removed_on_exit=True,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        enrich_inventory(inventory)
        destination.write_text(json.dumps(inventory, indent=2, default=str, allow_nan=False))
        if destination == OUT:
            REPORT.write_text(report_text(inventory))
    emit(
        "SUMMARY",
        {
            "datasets_inspected": list(inventory["datasets"]),
            "failed": failures,
            "inventory": str(destination),
            "peak_rss_mb": inventory["meta"]["peak_rss_mb"],
            "not_checked": [
                "online licences",
                "SkyServer binary record semantics",
                "full-row trace duplicates",
                "commerce source records",
                "semantic rewrite ground truth",
            ],
        },
    )
    return int(bool(failures))


if __name__ == "__main__":
    sys.exit(main())
