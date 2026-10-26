"""Measure baseline saturation and recommend an integer flash-sale base rate."""

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
from app.sandbox import single_clone
from app.workload import build_schedule, load_pools, load_profiles, prepare_clone, run_block


def main() -> None:
    """Run increasing 20-second blocks and save observed rates and latency samples."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).parents[1] / "data/calibration.json"
    )
    args = parser.parse_args()
    settings = get_settings()
    result = {
        "meta": {
            "seed": args.seed,
            "seconds": args.seconds,
            "workers": args.workers,
            "created_at": datetime.now(UTC).isoformat(),
            "method": "highest tested rate with place_order p95 below 4x low-load p95",
        },
        "blocks": [],
    }
    baseline = None
    saturation = None
    exceeded = False
    profile = load_profiles()["normal"]
    with single_clone(settings) as dbname:
        result["prepare"] = prepare_clone(dbname, profile, args.seed)
        for rate in (5, 10, 20, 40, 80, 160, 320, 640, 1280, 2560):
            pools = load_pools(dbname, profile)
            schedule = build_schedule(
                profile, args.seconds, args.seed + len(result["blocks"]), rate, pools
            )
            block = run_block(dbname, schedule, args.workers)
            values = block.latencies["place_order"]
            p95 = float(np.quantile(values, 0.95)) if values else None
            if baseline is None and p95 is not None:
                baseline = p95
            valid = block.metadata.get("valid", False) and p95 is not None
            good = bool(valid and p95 < 4 * baseline)
            result["blocks"].append(
                {"rate": rate, "p95_place_order_ms": p95, "good": good, "result": block.to_dict()}
            )
            print(f"rate={rate} place_order_p95={p95} good={good} valid={valid}", flush=True)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
            if not valid:
                raise SystemExit("Invalid calibration block; no saturation recommendation made")
            if good:
                saturation = rate
            else:
                exceeded = True
                break
    if saturation is None:
        raise SystemExit("No usable low-load block; calibration failed")
    base = max(3, min(60, math.floor(0.65 * saturation / 20)))
    minimum = math.ceil(100 / base)
    result.update(
        saturation_tps=saturation,
        saturation_censored=not exceeded,
        base_rate_tps=base,
        flash20_fraction_of_measured_saturation=20 * base / saturation,
        minimum_block_seconds=minimum,
        low_load_p95_ms={
            txn: float(np.quantile(values, 0.95)) if values else None
            for txn, values in result["blocks"][0]["result"]["latencies"].items()
        },
    )
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(f"BASE_RATE_TPS={base}\nBLOCK_SECONDS>={minimum}", flush=True)
    if not exceeded:
        print("WARNING: saturation is a lower bound; highest configured rate remained good.")
    if not 0.6 <= 20 * base / saturation <= 0.7:
        print(
            "WARNING: integer clamp/rounding misses the 60–70% target; review before experiments."
        )


if __name__ == "__main__":
    main()
