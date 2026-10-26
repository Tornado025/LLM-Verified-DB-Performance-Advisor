"""Measure prefix-tier trap survival and final verdicts on the labelled rewrite set."""

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
from app.equivalence import EquivConfig, check_rewrite
from app.pg import connect
from app.sandbox import single_clone


def build_report(conn, pairs: list[dict], cfg: EquivConfig) -> dict:
    """Run each labelled case once; report actual verdicts, never injected labels."""
    started = perf_counter()
    results = []
    for pair in pairs:
        begin = perf_counter()
        result = check_rewrite(conn, pair["original"], pair["rewrite"], cfg)
        results.append(
            {
                "id": pair["id"],
                "expected": pair["expected"],
                "result": result.to_dict(),
                "seconds": perf_counter() - begin,
            }
        )
        print(
            f"{pair['id']}: {result.label} tier={result.tier_reached} "
            f"cx={result.cx_dbs} nonvacuous={result.nonvacuous_fraction} {result.reason}",
            flush=True,
        )
    tiers = {}
    for tier in range(4):
        traps = [r for r in results if r["expected"] == "rejected"]
        equivalents = [r for r in results if r["expected"] == "tested"]

        def survived(row, tier=tier):
            result = row["result"]
            return result["tier_reached"] > tier or result["label"] == "tested"

        fa = sum(survived(r) for r in traps)
        fb = sum(not survived(r) for r in equivalents)
        tiers[str(tier)] = {
            "false_accept_count": fa,
            "trap_count": len(traps),
            "false_accept_rate": fa / len(traps),
            "false_block_count": fb,
            "equivalent_count": len(equivalents),
            "false_block_rate": fb / len(equivalents),
        }
    config = asdict(cfg)
    config.pop("veto_fn")
    scale = conn.execute("SELECT value FROM public.meta WHERE key='scale'").fetchone()[0]
    return {
        "meta": {
            "created_at": datetime.now(UTC).isoformat(),
            "config": config,
            "scale": int(scale),
            "pairs": len(pairs),
            "seconds": perf_counter() - started,
            "interpretation": "Prefix-tier trap survival; only final tested is accepted. "
            "Not independent tests or deployment approvals.",
            "set_scope": "Step 10 seed catalogue: T1-T10 and E1-E12. Step 18 expands to >=60.",
        },
        "tiers": tiers,
        "results": results,
    }


def main() -> None:
    """Run an explicit clone sweep and save measured JSON for RQ2 equivalence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pairs", type=Path, default=Path(__file__).parents[1] / "data/rewrite_pairs.json"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parents[2] / "experiments/results/equivalence_report.json",
    )
    args = parser.parse_args()
    pairs = json.loads(args.pairs.read_text())
    with single_clone(get_settings()) as name, connect(name) as conn:
        report = build_report(conn, pairs, EquivConfig())
    report["meta"]["pairs_sha256"] = hashlib.sha256(args.pairs.read_bytes()).hexdigest()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report["tiers"], indent=2))


if __name__ == "__main__":
    main()
