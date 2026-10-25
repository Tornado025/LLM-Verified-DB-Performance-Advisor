"""Measure real A/A variability on identical SQL without sequential early stopping."""

import argparse
import json
import logging
import os
import platform
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import sleep

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
from app.measure import run_once, warmup
from app.noise import NoiseInjector
from app.pg import connect
from app.sandbox import metrics, single_clone
from app.shapes import REPORT_SHAPES, load_as_of, render, sample_params
from app.stats import paired_log_ratios


def main() -> None:
    """Write seeded 50-pair time/buffer measurements for three duty levels."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=int, default=50)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument(
        "--out", type=Path, default=Path("../experiments/results/noise_report.json")
    )
    parser.add_argument("--cpu-workers", type=int, default=None)
    args = parser.parse_args()
    if args.pairs < 2:
        parser.error("At least two pairs required")
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    result = {
        "meta": {
            "seed": args.seed,
            "created_at": datetime.now(UTC).isoformat(),
            "cpu_workers": args.cpu_workers
            if args.cpu_workers is not None
            else max(1, (os.cpu_count() or 2) // 2),
            "pairs": args.pairs,
            "host": platform.platform(),
            "setup": "PostgreSQL 16 in Docker Desktop; CPU and disk noise on macOS host (indirect)",
            "noise_units": "requested host worker duty cycle, NOT measured latency noise",
            "default_metric": "time",
            "reason": "Time measures latency safety; buffers are an RQ3 screen only.",
        },
        "results": [],
        "clones": [],
    }
    rng = np.random.default_rng(args.seed)
    # Prior scale-3 baseline: fast stockouts, medium payout, slow product join.
    for id in ("stockouts_15m", "payout_report", "top_products_store"):
        with single_clone(settings) as dbname:
            result["clones"].append(dict(metrics[dbname]))
            with connect(dbname) as conn:
                conn.execute("SET TIME ZONE 'UTC'")
                result["meta"]["scale"] = int(
                    conn.execute("SELECT value FROM meta WHERE key='scale'").fetchone()[0]
                )
                shape = REPORT_SHAPES[id]
                params = sample_params(shape, conn, rng, load_as_of(conn))
                query = render(shape, conn, params)
                for level in (0.0, 0.05, 0.10):
                    injector = NoiseInjector(level, cpu_workers=args.cpu_workers, seed=args.seed)
                    rows = []
                    with injector:
                        sleep(1)  # allow spawned host workers to initialize
                        warmup(conn, query)
                        for metric in ("time", "buffers"):
                            a, b = [], []
                            for i in range(args.pairs):
                                values = {
                                    side: run_once(conn, query, metric)
                                    for side in (("a", "b") if i % 2 == 0 else ("b", "a"))
                                }
                                a.append(values["a"])
                                b.append(values["b"])
                            d = paired_log_ratios(a, b)
                            sd = float(np.std(d, ddof=1))
                            row = {
                                "shape": id,
                                "metric": metric,
                                "level": level,
                                "params": params,
                                "a_vals": a,
                                "b_vals": b,
                                "d": d.tolist(),
                                "sd_log": sd,
                                "mean_log": float(np.mean(d)),
                                "mde_log_80": 2.8 * sd / np.sqrt(args.pairs),
                                "outside_5pct": float(np.mean(np.abs(np.expm1(d)) > 0.05)),
                            }
                            rows.append(row)
                    for row in rows:
                        row["host_cpu_percent"] = injector.cpu_percent
                        result["results"].append(row)
                        print(
                            f"{id} {level:g} {row['metric']} sd={row['sd_log']:.5f} "
                            f"outside5={row['outside_5pct']:.3f}",
                            flush=True,
                        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=args.out.parent, suffix=".tmp", delete=False
    ) as handle:
        temp = handle.name
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write("\n")
    os.replace(temp, args.out)


if __name__ == "__main__":
    main()
