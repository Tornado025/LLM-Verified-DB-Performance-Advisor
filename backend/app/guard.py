"""Fail-closed non-inferiority guard for protected order transactions."""

from __future__ import annotations

import logging
import math
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from app.cancel import check_cancelled
from app.config import Settings
from app.errors import BadRequest
from app.models import Candidate, GuardResult
from app.sandbox import clone_pair
from app.shapes import ORDER_TXNS
from app.stats import SequentialTest
from app.workload import build_schedule, load_pools, load_profiles, prepare_clone, run_block

_LOG = logging.getLogger(__name__)
_CH_TXNS = ("NewOrder", "Payment", "OrderStatus", "Delivery", "StockLevel")


def estimate_guard_seconds(settings: Settings, profiles: list[str] | None = None) -> float:
    """Estimate minimum measured workload time, excluding clone and setup costs."""
    selected = settings.guard_profiles if profiles is None else profiles
    if not selected or len(selected) != len(set(selected)):
        raise BadRequest("Guard needs a nonempty list of unique profiles")
    known = set(load_profiles())
    if not set(selected) <= known:
        raise BadRequest("Unknown guard profile")
    return float(2 * settings.block_seconds * settings.min_runs * len(selected))


def _safe_summary(test: SequentialTest, reason: str | None = None) -> dict[str, Any]:
    """Serialize sequential evidence without emitting NaN or infinity as JSON."""
    summary = test.summary()
    result: dict[str, Any] = {"n": summary.n, "state": summary.state}
    for key in ("mean_rel", "lower", "upper", "sd_log"):
        value = getattr(summary, key)
        result[key] = value if math.isfinite(value) else None
    if reason:
        result["reason"] = reason
    return result


def _notify(progress: Callable[[dict], None] | None, event: dict) -> None:
    if progress is not None:
        progress(event)


def _unexpected_errors(block: Any, txn: str) -> int:
    """Exclude expected out-of-stock attempts from unexpected-error rates."""
    errors = int(block.errors.get(txn, 0))
    expected = int(block.metadata.get("oos", {}).get(txn, 0))
    return max(0, errors - expected)


def _metric(values: list[float], statistic: str) -> float:
    if not values:
        raise ValueError("A guard statistic needs at least one latency sample")
    value = float(np.mean(values) if statistic == "mean" else np.quantile(values, 0.95))
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid guard latency statistic")
    return value


def _make_tests(settings: Settings, txn_names: tuple[str, ...], n_profiles: int):
    family = len(txn_names) * n_profiles
    # One sample arrives per valid block. The first look is at min_runs;
    # blocks before then cannot produce a decision or spend alpha.
    looks = settings.max_blocks - settings.min_runs + 1
    return {
        txn: SequentialTest(
            settings.delta,
            settings.alpha,
            looks,
            family,
            settings.min_runs,
            settings.max_blocks,
            mode="noninferiority",
        )
        for txn in txn_names
    }


def _invalid_cells(tests: dict[str, SequentialTest], reason: str) -> dict[str, dict]:
    return {txn: _safe_summary(test, reason) | {"state": "BLOCK"} for txn, test in tests.items()}


def _guard_custom_profile(
    pair,
    profile_name: str,
    seed: int,
    settings: Settings,
    tests: dict[str, SequentialTest],
    progress: Callable[[dict], None] | None,
    total_started: float,
) -> tuple[dict, tuple[str, str] | None, bool]:
    """Run paired open-loop blocks; return profile data, a failure, and stop flag."""
    profiles = load_profiles()
    profile = profiles[profile_name]
    prep_a = prepare_clone(pair.a, profile, seed)
    prep_b = prepare_clone(pair.b, profile, seed)
    pools = load_pools(pair.a, profile)
    blocks: list[dict] = []
    blocked_reason: str | None = None
    failed_cell: tuple[str, str] | None = None

    for index in range(settings.max_blocks):
        check_cancelled()
        schedule = build_schedule(
            profile, settings.block_seconds, seed + index, settings.base_rate_tps, pools
        )
        if index % 2 == 0:
            result_a = run_block(pair.a, schedule, settings.workers)
            time.sleep(1)
            result_b = run_block(pair.b, schedule, settings.workers)
        else:
            result_b = run_block(pair.b, schedule, settings.workers)
            time.sleep(1)
            result_a = run_block(pair.a, schedule, settings.workers)

        valid = bool(result_a.metadata.get("valid")) and bool(result_b.metadata.get("valid"))
        block_record: dict[str, Any] = {
            "index": index,
            "seed": seed + index,
            "scheduled": len(schedule),
            "valid": valid,
            "a_metadata": result_a.metadata,
            "b_metadata": result_b.metadata,
            "transactions": {},
        }
        if not valid:
            blocked_reason = "invalid_block"
            blocks.append(block_record)
            _notify(
                progress,
                {
                    "profile": profile_name,
                    "block": index + 1,
                    "state": "BLOCK",
                    "reason": blocked_reason,
                    "worst_upper": None,
                },
            )
            break

        scheduled = Counter(arrival.txn for arrival in schedule)
        for txn, test in tests.items():
            a_values = result_a.latencies.get(txn, [])
            b_values = result_b.latencies.get(txn, [])
            a_errors = _unexpected_errors(result_a, txn)
            b_errors = _unexpected_errors(result_b, txn)
            attempts = scheduled.get(txn, 0)
            error_delta = b_errors / attempts - a_errors / attempts if attempts else 0.0
            tx_record: dict[str, Any] = {
                "n_a": len(a_values),
                "n_b": len(b_values),
                "unexpected_errors_a": a_errors,
                "unexpected_errors_b": b_errors,
                "error_rate_delta": error_delta,
            }
            if error_delta > 0.01:
                tx_record.update({"state": "FAIL", "reason": "errors_increased"})
                block_record["transactions"][txn] = tx_record
                failed_cell = (profile_name, txn)
                blocked_reason = "errors_increased"
                break
            if test.state == "CONTINUE" and (
                len(a_values) >= settings.min_samples_per_block
                and len(b_values) >= settings.min_samples_per_block
            ):
                try:
                    stat_a = _metric(a_values, settings.guard_stat)
                    stat_b = _metric(b_values, settings.guard_stat)
                    p95_a = _metric(a_values, "p95")
                    p95_b = _metric(b_values, "p95")
                    d = math.log(stat_b / stat_a)
                    test.add([d])
                    tx_record.update(
                        {
                            "stat_a": stat_a,
                            "stat_b": stat_b,
                            "p95_a": p95_a,
                            "p95_b": p95_b,
                            "p95_ratio": p95_b / p95_a,
                            "d": d,
                        }
                    )
                    if test.state == "FAIL":
                        failed_cell = (profile_name, txn)
                        blocked_reason = "slowdown_exceeds_delta"
                except (ValueError, OverflowError):
                    blocked_reason = "invalid_statistic"
                    tx_record.update({"state": "BLOCK", "reason": blocked_reason})
            tx_record["state"] = test.state if test.state != "CONTINUE" else "CONTINUE"
            block_record["transactions"][txn] = tx_record
            if failed_cell:
                break

        blocks.append(block_record)
        if failed_cell or blocked_reason:
            _notify(
                progress,
                {
                    "profile": profile_name,
                    "block": index + 1,
                    "state": "FAIL" if failed_cell else "BLOCK",
                    "reason": blocked_reason,
                    "failed_cell": failed_cell,
                    "worst_upper": None,
                    "elapsed_seconds": time.perf_counter() - total_started,
                },
            )
            break
        finite_uppers = [
            float(summary["upper"])
            for summary in (_safe_summary(test) for test in tests.values())
            if summary["upper"] is not None
        ]
        worst_upper = max(finite_uppers) if finite_uppers else None
        _notify(
            progress,
            {
                "profile": profile_name,
                "block": index + 1,
                "states": {txn: test.state for txn, test in tests.items()},
                "worst_upper": worst_upper,
                "elapsed_seconds": time.perf_counter() - total_started,
            },
        )
        if all(test.state == "PASS" for test in tests.values()):
            break

    cells: dict[str, dict] = {}
    for txn, test in tests.items():
        reason = None
        if failed_cell == (profile_name, txn):
            if test.state == "FAIL":
                cells[txn] = _safe_summary(test, "slowdown_exceeds_delta")
            else:
                cells[txn] = {
                    "n": test.d.size,
                    "mean_rel": None,
                    "lower": None,
                    "upper": None,
                    "sd_log": None,
                    "state": "FAIL",
                    "reason": blocked_reason or "errors_increased",
                }
            continue
        if blocked_reason and test.state == "CONTINUE":
            reason = blocked_reason
        elif test.state == "CONTINUE":
            reason = "insufficient_samples" if test.d.size == 0 else "max_blocks_unproven"
        cell = _safe_summary(test, reason)
        if cell["state"] == "CONTINUE":
            cell["state"] = "BLOCK"
        cells[txn] = cell
    return (
        {
            "cells": cells,
            "blocks": blocks,
            "block_seconds": settings.block_seconds,
            "base_rate_tps": settings.base_rate_tps,
            "guard_stat": settings.guard_stat,
            "workload": "custom",
            "prepare": {"a": prep_a, "b": prep_b},
            "seconds": sum(
                float(block.get("a_metadata", {}).get("actual_seconds", 0))
                + float(block.get("b_metadata", {}).get("actual_seconds", 0))
                for block in blocks
            ),
        },
        failed_cell,
        bool(blocked_reason),
    )


def _guard_ch_profile(
    pair,
    profile_name: str,
    settings: Settings,
    tests: dict[str, SequentialTest],
    progress: Callable[[dict], None] | None,
    total_started: float,
) -> tuple[dict, tuple[str, str] | None, bool]:
    """Run paired BenchBase blocks against the separate CH database clones."""
    from app import benchbase

    root = benchbase.ROOT / "benchbase/results"
    root.mkdir(parents=True, exist_ok=True)
    blocks: list[dict] = []
    failed_cell: tuple[str, str] | None = None
    blocked_reason: str | None = None
    for index in range(settings.max_blocks):
        check_cancelled()
        block_started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="dpa_guard_ch_", dir=root) as folder:
            out_a, out_b = Path(folder) / "a", Path(folder) / "b"
            if index % 2 == 0:
                run_a = benchbase.run_profile(pair.a, profile_name, out_a, settings.block_seconds)
                time.sleep(1)
                run_b = benchbase.run_profile(pair.b, profile_name, out_b, settings.block_seconds)
            else:
                run_b = benchbase.run_profile(pair.b, profile_name, out_b, settings.block_seconds)
                time.sleep(1)
                run_a = benchbase.run_profile(pair.a, profile_name, out_a, settings.block_seconds)
        transactions: dict[str, dict] = {}
        for txn, test in tests.items():
            a = run_a["transactions"].get(txn, {})
            b = run_b["transactions"].get(txn, {})
            a_values, b_values = a.get("latencies_ms", []), b.get("latencies_ms", [])
            a_errors, b_errors = a.get("errors"), b.get("errors")
            if a_errors is None or b_errors is None:
                blocked_reason = "error_counts_unknown"
                transactions[txn] = {"state": "BLOCK", "reason": blocked_reason}
                break
            a_rate = a_errors / max(1, int(a.get("count", len(a_values))))
            b_rate = b_errors / max(1, int(b.get("count", len(b_values))))
            tx_record = {
                "n_a": len(a_values),
                "n_b": len(b_values),
                "unexpected_errors_a": a_errors,
                "unexpected_errors_b": b_errors,
                "error_rate_delta": b_rate - a_rate,
            }
            if b_rate - a_rate > 0.01:
                tx_record.update({"state": "FAIL", "reason": "errors_increased"})
                failed_cell = (profile_name, txn)
                transactions[txn] = tx_record
                break
            if test.state == "CONTINUE" and (
                len(a_values) >= settings.min_samples_per_block
                and len(b_values) >= settings.min_samples_per_block
            ):
                stat_a = _metric(a_values, settings.guard_stat)
                stat_b = _metric(b_values, settings.guard_stat)
                p95_a, p95_b = _metric(a_values, "p95"), _metric(b_values, "p95")
                d = math.log(stat_b / stat_a)
                test.add([d])
                tx_record.update(
                    {
                        "stat_a": stat_a,
                        "stat_b": stat_b,
                        "p95_a": p95_a,
                        "p95_b": p95_b,
                        "p95_ratio": p95_b / p95_a,
                        "d": d,
                    }
                )
                if test.state == "FAIL":
                    failed_cell = (profile_name, txn)
                    blocked_reason = "slowdown_exceeds_delta"
            tx_record["state"] = test.state
            transactions[txn] = tx_record
            if failed_cell:
                break
        blocks.append(
            {
                "index": index,
                "transactions": transactions,
                "valid": True,
                "seconds": time.perf_counter() - block_started,
            }
        )
        if failed_cell or blocked_reason:
            _notify(
                progress,
                {
                    "profile": profile_name,
                    "block": index + 1,
                    "state": "FAIL" if failed_cell else "BLOCK",
                    "reason": blocked_reason,
                    "failed_cell": failed_cell,
                    "worst_upper": None,
                    "elapsed_seconds": time.perf_counter() - total_started,
                },
            )
            break
        finite_uppers = [
            float(value["upper"])
            for value in (_safe_summary(test) for test in tests.values())
            if value["upper"] is not None
        ]
        _notify(
            progress,
            {
                "profile": profile_name,
                "block": index + 1,
                "states": {txn: test.state for txn, test in tests.items()},
                "worst_upper": max(finite_uppers) if finite_uppers else None,
                "elapsed_seconds": time.perf_counter() - total_started,
            },
        )
        if all(test.state == "PASS" for test in tests.values()):
            break

    cells: dict[str, dict] = {}
    for txn, test in tests.items():
        if failed_cell == (profile_name, txn):
            if test.state == "FAIL":
                cells[txn] = _safe_summary(test, "slowdown_exceeds_delta")
            else:
                cells[txn] = {
                    "n": test.d.size,
                    "mean_rel": None,
                    "lower": None,
                    "upper": None,
                    "sd_log": None,
                    "state": "FAIL",
                    "reason": blocked_reason or "errors_increased",
                }
        else:
            reason = (blocked_reason if test.state == "CONTINUE" else None) or (
                "insufficient_samples"
                if test.d.size == 0
                else "max_blocks_unproven"
                if test.state == "CONTINUE"
                else None
            )
            cells[txn] = _safe_summary(test, reason)
            if cells[txn]["state"] == "CONTINUE":
                cells[txn]["state"] = "BLOCK"
    return (
        {
            "cells": cells,
            "blocks": blocks,
            "block_seconds": settings.block_seconds,
            "base_rate_tps": settings.base_rate_tps,
            "guard_stat": settings.guard_stat,
            "workload": "ch",
            "prepare": None,
            "seconds": sum(block.get("seconds", 0) for block in blocks),
        },
        failed_cell,
        bool(blocked_reason),
    )


def run_guard(
    candidate: Candidate,
    profiles: list[str] | None,
    settings: Settings,
    *,
    seed: int,
    progress: Callable[[dict], None] | None = None,
    workload: str = "custom",
) -> GuardResult:
    """Verify candidate write cost against every protected transaction/profile."""
    if candidate.kind not in {"index", "stats"}:
        raise BadRequest("The write-cost guard applies only to index/statistics candidates")
    selected = settings.guard_profiles if profiles is None else profiles
    if (
        not selected
        or len(selected) != len(set(selected))
        or not set(selected) <= set(load_profiles())
        or seed < 0
        or workload not in {"custom", "ch"}
    ):
        raise BadRequest("Invalid guard profiles, seed, or workload")
    if workload == "ch" and any(
        name not in {"normal", "flash5", "flash10", "flash20"} for name in selected
    ):
        raise BadRequest("CH guard supports the four standard profiles")
    if settings.max_clones < 2:
        raise BadRequest("Guard requires at least two sandbox clone slots")

    txns = tuple(ORDER_TXNS) if workload == "custom" else _CH_TXNS
    results: dict[str, dict] = {}
    failed_cell: tuple[str, str] | None = None
    stopped = False
    started = time.perf_counter()
    clone_count = 0
    for profile_name in selected:
        check_cancelled()
        if stopped:
            break
        tests = _make_tests(settings, txns, len(selected))
        template = None if workload == "custom" else "qc_ch_golden"
        with clone_pair(candidate, settings, template=template) as pair:
            clone_count += 2
            if workload == "custom":
                result, failure, should_stop = _guard_custom_profile(
                    pair, profile_name, seed, settings, tests, progress, started
                )
            else:
                result, failure, should_stop = _guard_ch_profile(
                    pair, profile_name, settings, tests, progress, started
                )
        results[profile_name] = result
        if failure:
            failed_cell, stopped = failure, True
        elif should_stop:
            stopped = True

    passed = (
        not stopped
        and set(results) == set(selected)
        and all(
            cell["state"] == "PASS"
            for profile_data in results.values()
            for cell in profile_data["cells"].values()
        )
    )
    if not passed and failed_cell is None:
        candidate.status = "blocked_unproven"
    block_counts = {name: len(value["blocks"]) for name, value in results.items()}
    return GuardResult(passed, results, time.perf_counter() - started, block_counts, failed_cell)
