"""Compare reused and fresh guard clone pairs without changing the safety rule."""

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter, sleep

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
from app.models import Candidate
from app.pg import connect
from app.sandbox import clone_pair
from app.shapes import ORDER_TXNS
from app.workload import build_schedule, load_pools, load_profiles, prepare_clone, run_block


def _summary(blocks: list[dict]) -> dict:
    """Summarize only valid paired blocks, retaining every block in the report."""
    results = {}
    for txn in ORDER_TXNS:
        values = [
            block["transactions"][txn]["d"]
            for block in blocks
            if block["valid"] and block["transactions"][txn]["d"] is not None
        ]
        results[txn] = {
            "n": len(values),
            "mean_rel": math.expm1(float(np.mean(values))) if values else None,
            "sd_log": float(np.std(values, ddof=1)) if len(values) >= 2 else None,
        }
    return results


def _checkpoint(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(path)


def _hot_stats(dbname: str) -> dict:
    """Read stock-table update counters after workload connections have closed."""
    with connect(dbname) as conn:
        row = conn.execute(
            "/* dpa:sys */ SELECT n_tup_upd,n_tup_hot_upd "
            "FROM pg_stat_user_tables WHERE relname='store_stock'"
        ).fetchone()
    if row is None:
        raise RuntimeError("store_stock statistics unavailable")
    return {"updates": int(row[0]), "hot_updates": int(row[1])}


def _stock_statement_stats(dbname: str) -> dict:
    """Read stock-update costs; pg_stat_statements merges both txn labels by query ID."""
    with connect(dbname) as conn:
        row = conn.execute(
            "/* dpa:sys */ SELECT COALESCE(sum(calls),0), "
            "COALESCE(sum(total_exec_time),0), COALESCE(sum(rows),0) "
            "FROM pg_stat_statements WHERE dbid = "
            "(SELECT oid FROM pg_database WHERE datname=current_database()) "
            "AND query LIKE '%UPDATE store_stock SET qty = qty -%'"
        ).fetchone()
    if row is None:
        raise RuntimeError("Stock statement statistics unavailable")
    return {"calls": int(row[0]), "total_exec_ms": float(row[1]), "updated_rows": int(row[2])}


def _pools(dbname: str, profile, stocked_only: bool) -> dict:
    """Optionally restrict the diagnostic to preloaded hot products."""
    pools = load_pools(dbname, profile)
    if stocked_only:
        pools["product_ids"] = list(pools["hot_product_ids"])
    return pools


def _paired_block(
    pair, profile, seed: int, index: int, settings, pools: dict, record_timeline: bool
) -> dict:
    schedule = build_schedule(
        profile, settings.block_seconds, seed + index, settings.base_rate_tps, pools
    )
    order = ("a", "b") if index % 2 == 0 else ("b", "a")
    results = {}
    for side in order:
        results[side] = run_block(
            getattr(pair, side), schedule, settings.workers, record_timeline=record_timeline
        )
        if side == order[0]:
            sleep(1)
    a, b = results["a"], results["b"]
    valid = bool(a.metadata.get("valid")) and bool(b.metadata.get("valid"))
    transactions = {}
    for txn in ORDER_TXNS:
        av, bv = a.latencies.get(txn, []), b.latencies.get(txn, [])
        enough = min(len(av), len(bv)) >= settings.min_samples_per_block
        mean_a = float(np.mean(av)) if av else None
        mean_b = float(np.mean(bv)) if bv else None
        transactions[txn] = {
            "n_a": len(av),
            "n_b": len(bv),
            "mean_a_ms": mean_a,
            "mean_b_ms": mean_b,
            "d": math.log(mean_b / mean_a) if enough and mean_a and mean_b else None,
            "errors_a": a.errors.get(txn, 0),
            "errors_b": b.errors.get(txn, 0),
            "oos_a": a.metadata.get("oos", {}).get(txn, 0),
            "oos_b": b.metadata.get("oos", {}).get(txn, 0),
        }
        if record_timeline:
            for side, result in (("a", a), ("b", b)):
                successful = [
                    entry
                    for entry in result.metadata["timeline"]
                    if entry["txn"] == txn and entry["status"] == "ok"
                ]
                transactions[txn][f"n_success_{side}"] = len(successful)
                transactions[txn][f"success_latency_ms_{side}"] = (
                    float(np.mean([(r["finish_s"] - r["target_s"]) * 1000 for r in successful]))
                    if successful
                    else None
                )
                transactions[txn][f"success_service_ms_{side}"] = (
                    float(np.mean([(r["finish_s"] - r["start_s"]) * 1000 for r in successful]))
                    if successful
                    else None
                )
            for name in ("success_latency", "success_service"):
                av = transactions[txn][f"{name}_ms_a"]
                bv = transactions[txn][f"{name}_ms_b"]
                transactions[txn][f"d_{name}"] = (
                    math.log(bv / av)
                    if min(transactions[txn]["n_success_a"], transactions[txn]["n_success_b"])
                    >= settings.min_samples_per_block
                    and av
                    and bv
                    else None
                )
    return {
        "index": index,
        "seed": seed + index,
        "order": order,
        "scheduled": len(schedule),
        "valid": valid,
        "actual_seconds_a": a.metadata.get("actual_seconds"),
        "actual_seconds_b": b.metadata.get("actual_seconds"),
        "queue_lag_p95_ms_a": a.metadata.get("queue_lag_p95_ms"),
        "queue_lag_p95_ms_b": b.metadata.get("queue_lag_p95_ms"),
        "workload_lag_ms_a": a.metadata.get("workload_lag_ms"),
        "workload_lag_ms_b": b.metadata.get("workload_lag_ms"),
        "dropped_a": a.dropped,
        "dropped_b": b.dropped,
        "transactions": transactions,
    }


def main() -> None:
    """Run the same flash profile/seeds with one reused pair and fresh pairs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", choices=("normal", "flash5", "flash10", "flash20"), default="flash5"
    )
    parser.add_argument("--pairs", type=int, default=8)
    parser.add_argument("--candidate", choices=("city", "qty"), default="city")
    parser.add_argument("--timeline", action="store_true")
    parser.add_argument("--stocked-only", action="store_true")
    parser.add_argument("--arms", choices=("both", "reused", "fresh"), default="both")
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parents[2] / "experiments/results/step12_clone_lifecycle.json",
    )
    args = parser.parse_args()
    if args.pairs < 2 or args.seed < 0:
        parser.error("pairs must be at least 2 and seed must be nonnegative")
    settings = get_settings()
    profile = load_profiles()[args.profile]
    candidate_sql = (
        "CREATE INDEX guard_diagnostic_city ON customers(city)"
        if args.candidate == "city"
        else "CREATE INDEX guard_diagnostic_qty ON store_stock(qty)"
    )
    candidate = Candidate(
        id="diagnostic-harmless-index",
        job_id="diagnostic",
        shape_id="recent_orders",
        kind="index",
        sql=candidate_sql,
        rationale="controlled clone lifecycle comparison",
        sample_idx=0,
    )
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "profile": args.profile,
        "seed": args.seed,
        "pairs_per_arm": args.pairs,
        "base_rate_tps": settings.base_rate_tps,
        "block_seconds": settings.block_seconds,
        "workers": settings.workers,
        "min_samples_per_block": settings.min_samples_per_block,
        "candidate_sql": candidate.sql,
        "record_timeline": args.timeline,
        "stocked_only": args.stocked_only,
        "arms": {
            arm: {"blocks": []}
            for arm in (("reused", "fresh") if args.arms == "both" else (args.arms,))
        },
    }
    started = perf_counter()
    if "reused" in report["arms"]:
        with clone_pair(candidate, settings) as pair:
            with connect(pair.a) as conn:
                report["arms"]["reused"]["dbid_a"] = conn.execute(
                    "/* dpa:sys */ SELECT oid FROM pg_database WHERE datname=current_database()"
                ).fetchone()[0]
            with connect(pair.b) as conn:
                report["arms"]["reused"]["dbid_b"] = conn.execute(
                    "/* dpa:sys */ SELECT oid FROM pg_database WHERE datname=current_database()"
                ).fetchone()[0]
            report["arms"]["reused"]["prepare"] = {
                "a": prepare_clone(pair.a, profile, args.seed),
                "b": prepare_clone(pair.b, profile, args.seed),
            }
            pools = _pools(pair.a, profile, args.stocked_only)
            statement_before = {
                "a": _stock_statement_stats(pair.a),
                "b": _stock_statement_stats(pair.b),
            }
            for index in range(args.pairs):
                block = _paired_block(
                    pair, profile, args.seed, index, settings, pools, args.timeline
                )
                block["hot_stats"] = {"a": _hot_stats(pair.a), "b": _hot_stats(pair.b)}
                statement_after = {
                    "a": _stock_statement_stats(pair.a),
                    "b": _stock_statement_stats(pair.b),
                }
                block["stock_statement"] = {
                    side: {
                        key: statement_after[side][key] - statement_before[side][key]
                        for key in statement_before[side]
                    }
                    for side in ("a", "b")
                }
                statement_before = statement_after
                report["arms"]["reused"]["blocks"].append(block)
                report["arms"]["reused"]["summary"] = _summary(report["arms"]["reused"]["blocks"])
                report["elapsed_seconds"] = perf_counter() - started
                _checkpoint(args.out, report)
                print(f"reused {index + 1}/{args.pairs} valid={block['valid']}", flush=True)
    if "fresh" in report["arms"]:
        for index in range(args.pairs):
            with clone_pair(candidate, settings) as pair:
                prepare = {
                    "a": prepare_clone(pair.a, profile, args.seed),
                    "b": prepare_clone(pair.b, profile, args.seed),
                }
                pools = _pools(pair.a, profile, args.stocked_only)
                block = _paired_block(
                    pair, profile, args.seed, index, settings, pools, args.timeline
                )
                block["hot_stats"] = {"a": _hot_stats(pair.a), "b": _hot_stats(pair.b)}
                block["prepare"] = prepare
            report["arms"]["fresh"]["blocks"].append(block)
            report["arms"]["fresh"]["summary"] = _summary(report["arms"]["fresh"]["blocks"])
            report["elapsed_seconds"] = perf_counter() - started
            _checkpoint(args.out, report)
            print(f"fresh {index + 1}/{args.pairs} valid={block['valid']}", flush=True)
    print(json.dumps({arm: data["summary"] for arm, data in report["arms"].items()}))


if __name__ == "__main__":
    main()
