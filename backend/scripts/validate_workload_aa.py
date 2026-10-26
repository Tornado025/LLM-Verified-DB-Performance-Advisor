"""Paired A/A workload acceptance with checkpoints and honest sequential bounds."""

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
from app.pg import connect
from app.sandbox import metrics as clone_metrics
from app.sandbox import single_clone
from app.shapes import ORDER_TXNS, REPORT_SHAPES, load_as_of, render
from app.stats import summarize
from app.workload import (
    _weights,
    build_schedule,
    load_pools,
    load_profiles,
    prepare_clone,
    run_block,
)


def _clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def run_aa(
    base_rate: float,
    *,
    min_pairs: int = 12,
    max_pairs: int = 40,
    seconds: float | None = None,
    workers: int = 16,
    seed: int = 1234,
    out: Path | None = None,
    resume: bool = False,
) -> dict:
    """Run fresh pairs to avoid cross-block runtime-id/state drift; never fake PASS."""
    if not 12 <= min_pairs <= max_pairs <= 40:
        raise ValueError("A/A requires 12..40 paired blocks")
    started = perf_counter()
    settings = get_settings()
    report = {
        "meta": {
            "seed": seed,
            "base_rate": base_rate,
            "workers": workers,
            "created_at": datetime.now(UTC).isoformat(),
            "min_pairs": min_pairs,
            "max_pairs": max_pairs,
            "family": 10,
            "looks": max_pairs - 6 + 1,
            "mean_log_limit": 0.03,
            "delta": settings.delta,
            "alpha": settings.alpha,
            "protocol": (
                "Fresh pair per block; counterbalanced; report warmup; 1s inter-side cooldown"
            ),
        },
        "profiles": {},
        "passed": False,
    }
    elapsed_before = 0.0
    if resume:
        if out is None or not out.is_file():
            raise ValueError("Resume requires an existing checkpoint")
        saved = json.loads(out.read_text())
        for key, value in report["meta"].items():
            if key != "created_at" and saved["meta"].get(key) != value:
                raise ValueError("Checkpoint policy differs: " + key)
        if settings.min_samples_per_block != 5:
            raise ValueError("Checkpoint protocol requires five samples per type")
        if set(saved["profiles"]) - {"normal", "flash5"}:
            raise ValueError("Unexpected checkpoint profiles")
        report = saved
        elapsed_before = float(report["meta"].get("elapsed_seconds", 0))
        report["meta"].setdefault("resumed_at", []).append(datetime.now(UTC).isoformat())

    def checkpoint():
        if out:
            out.parent.mkdir(parents=True, exist_ok=True)
            temporary = out.with_suffix(out.suffix + ".tmp")
            try:
                temporary.write_text(json.dumps(_clean(report), indent=2, allow_nan=False) + "\n")
                temporary.replace(out)
            finally:
                temporary.unlink(missing_ok=True)

    for profile_name in ("normal", "flash5"):
        profile = load_profiles()[profile_name]
        names, weights = _weights(profile)
        block_seconds = seconds or max(
            8,
            2 * profile.dashboard_refresh_s,
            math.ceil(
                10 / (base_rate * profile.rate_mult * weights[names.index("picker_queue_read")])
            ),
        )
        observations = {txn: [] for txn in ORDER_TXNS}
        evidence = report["profiles"].get(
            profile_name, {"seconds": block_seconds, "blocks": [], "cells": {}, "passed": False}
        )
        if evidence["seconds"] != block_seconds or len(evidence["blocks"]) > max_pairs:
            raise ValueError("Checkpoint duration or block budget differs")
        for i, block in enumerate(evidence["blocks"]):
            expected_order = ["a", "b"] if i % 2 == 0 else ["b", "a"]
            if block["seed"] != seed + i or block["order"] != expected_order:
                raise ValueError("Checkpoint seed/order prefix is inconsistent")
            for side in ("a", "b"):
                result = block[side]
                if (
                    not result["metadata"]["valid"]
                    or result["dropped"]
                    or result["seconds"] != block_seconds
                    or result["metadata"]["profile"] != profile_name
                    or result["metadata"]["seed"] != seed + i
                    or block["clone_" + side]["copy_strategy"] != "FILE_COPY"
                ):
                    raise ValueError("Checkpoint contains an invalid/different block")
            for txn in ORDER_TXNS:
                av, bv = block["a"]["latencies"][txn], block["b"]["latencies"][txn]
                if any(not math.isfinite(v) or v <= 0 for v in av + bv):
                    raise ValueError("Checkpoint has invalid latency samples")
                if min(len(av), len(bv)) >= settings.min_samples_per_block:
                    observations[txn].append(float(np.log(np.mean(bv) / np.mean(av))))
        report["profiles"][profile_name] = evidence
        if evidence["passed"]:
            if len(evidence["blocks"]) < min_pairs or not all(
                summarize(d, settings.delta, settings.alpha, max_pairs - 5, 10, 6, max_pairs).state
                == "PASS"
                and abs(float(np.mean(d))) < 0.03
                for d in observations.values()
            ):
                raise ValueError("Checkpoint PASS is inconsistent with raw evidence")
            continue
        for i in range(len(evidence["blocks"]), max_pairs):
            with single_clone(settings) as a, single_clone(settings) as b:
                prepare_a = prepare_clone(a, profile, seed + i)
                prepare_b = prepare_clone(b, profile, seed + i)
                pools = load_pools(a, profile)
                schedule = build_schedule(profile, block_seconds, seed + i, base_rate, pools)
                order = ("a", "b") if i % 2 == 0 else ("b", "a")
                results = {}
                for side in order:
                    db = a if side == "a" else b
                    with connect(db) as conn:
                        as_of = load_as_of(conn)
                        for shape_id in ("hourly_sales", "stockouts_15m"):
                            conn.execute(
                                render(REPORT_SHAPES[shape_id], conn, {"as_of": as_of.isoformat()})
                            ).fetchall()
                    results[side] = run_block(db, schedule, workers)
                    sleep(1)
                for txn in ORDER_TXNS:
                    av, bv = results["a"].latencies[txn], results["b"].latencies[txn]
                    if (
                        len(av) >= settings.min_samples_per_block
                        and len(bv) >= settings.min_samples_per_block
                    ):
                        observations[txn].append(float(np.log(np.mean(bv) / np.mean(av))))
                evidence["blocks"].append(
                    {
                        "seed": seed + i,
                        "order": order,
                        "clone_a": clone_metrics[a],
                        "clone_b": clone_metrics[b],
                        "prepare_a": prepare_a,
                        "prepare_b": prepare_b,
                        "a": results["a"].to_dict(),
                        "b": results["b"].to_dict(),
                    }
                )
                for txn, d in observations.items():
                    summary = summarize(
                        d, settings.delta, settings.alpha, max_pairs - 6 + 1, 10, 6, max_pairs
                    )
                    evidence["cells"][txn] = summary.to_dict() | {
                        "mean_log": float(np.mean(d)) if d else None
                    }
                valid = all(result.metadata.get("valid", False) for result in results.values())
                evidence["passed"] = bool(
                    valid
                    and i + 1 >= min_pairs
                    and all(
                        cell["state"] == "PASS" and abs(cell["mean_log"]) < 0.03
                        for cell in evidence["cells"].values()
                    )
                )
                report["meta"]["elapsed_seconds"] = elapsed_before + perf_counter() - started
                checkpoint()
                print(
                    profile_name,
                    i + 1,
                    {
                        t: (
                            c["n"],
                            round(c["mean_log"], 4) if c["mean_log"] is not None else None,
                            c["state"],
                        )
                        for t, c in evidence["cells"].items()
                    },
                    flush=True,
                )
                if not valid:
                    raise RuntimeError("Invalid A/A workload block; inspect checkpoint")
            if evidence["passed"]:
                break
    report["passed"] = all(p["passed"] for p in report["profiles"].values())
    report["meta"]["elapsed_seconds"] = elapsed_before + perf_counter() - started
    checkpoint()
    return report


def main() -> None:
    """Use measured calibration and save every paired block as it completes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parents[2] / "experiments/results/workload_aa.json",
    )
    parser.add_argument("--base-rate", type=float, default=None)
    parser.add_argument("--seconds", type=float, default=None)
    parser.add_argument("--max-pairs", type=int, default=40)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    base = args.base_rate or get_settings().base_rate_tps
    result = run_aa(
        base, max_pairs=args.max_pairs, seconds=args.seconds, out=args.out, resume=args.resume
    )
    if not result["passed"]:
        raise SystemExit("A/A acceptance failed; measured evidence retained")


if __name__ == "__main__":
    main()
