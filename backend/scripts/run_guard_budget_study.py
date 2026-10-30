"""Measure harmless-index BLOCK frequency at two sequential guard budgets.

Runs the four load profiles serially on fresh clone pairs. The same 80
paired blocks are replayed under 40- and 80-block policies; this keeps host
conditions comparable while retaining each policy's correct look correction.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings
from app.shapes import ORDER_TXNS
from app.stats import SequentialTest

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "experiments/results/step12_guard_budget_study"
PROFILES = ("normal", "flash5", "flash10", "flash20")
SEEDS = (3101, 5201, 7301)
BUDGETS = (40, 80)


def _save(path: Path, value: dict) -> None:
    """Write an atomic checkpoint for a long running local study."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def _evaluate(blocks: Sequence[dict], budget: int, family: int, min_runs: int) -> dict:
    """Replay the unchanged guard rule through one measurement budget."""
    tests = {
        txn: SequentialTest(0.05, 0.05, budget - min_runs + 1, family, min_runs, budget)
        for txn in ORDER_TXNS
    }
    terminal: str | None = None
    terminal_cell: str | None = None
    for block in blocks[:budget]:
        if not block["valid"]:
            terminal = "BLOCK"
            break
        for txn, test in tests.items():
            record = block["transactions"][txn]
            attempts = record["n_a"]
            a_unexpected = record["errors_a"] - record["oos_a"]
            b_unexpected = record["errors_b"] - record["oos_b"]
            if attempts and (b_unexpected - a_unexpected) / attempts > 0.01:
                terminal, terminal_cell = "FAIL", txn
                break
            if test.state == "CONTINUE" and record["d"] is not None:
                test.add([record["d"]])
                if test.state == "FAIL":
                    terminal, terminal_cell = "FAIL", txn
                    break
        if terminal or all(test.state == "PASS" for test in tests.values()):
            break
    states = {txn: test.summary().to_dict() for txn, test in tests.items()}
    state = terminal or (
        "PASS" if all(test.state == "PASS" for test in tests.values()) else "BLOCK"
    )
    if not terminal and state == "BLOCK":
        for cell in states.values():
            if cell["state"] == "CONTINUE":
                cell["state"] = "BLOCK"
    return {"state": state, "failed_cell": terminal_cell, "cells": states}


def _analyze(status: dict) -> dict:
    """Summarize observed verdicts and small-sample rates from completed runs."""
    completed = []
    for profile in PROFILES:
        for seed in SEEDS:
            path = OUT / f"{profile}_{seed}.json"
            if not path.exists():
                continue
            data = json.loads(path.read_text())
            blocks = data.get("arms", {}).get("reused", {}).get("blocks", [])
            if len(blocks) < max(BUDGETS):
                continue
            completed.append(
                {
                    "profile": profile,
                    "seed": seed,
                    "elapsed_seconds": data.get("elapsed_seconds"),
                    "valid_blocks": sum(bool(block["valid"]) for block in blocks),
                    "file": str(path.relative_to(ROOT)),
                    "budgets": {
                        str(budget): _evaluate(
                            blocks, budget, family=5 * 4, min_runs=status["min_runs"]
                        )
                        for budget in BUDGETS
                    },
                }
            )
    rates = {}
    for profile in PROFILES:
        subset = [row for row in completed if row["profile"] == profile]
        rates[profile] = {
            str(budget): {
                "n": len(subset),
                "block": sum(row["budgets"][str(budget)]["state"] == "BLOCK" for row in subset),
                "pass": sum(row["budgets"][str(budget)]["state"] == "PASS" for row in subset),
                "fail": sum(row["budgets"][str(budget)]["state"] == "FAIL" for row in subset),
            }
            for budget in BUDGETS
        }
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "design": {
            "candidate": "CREATE INDEX ON customers(city)",
            "profiles": PROFILES,
            "seeds": SEEDS,
            "budgets": BUDGETS,
            "family": 20,
            "delta": 0.05,
            "alpha": 0.05,
            "guard_stat": "mean",
            "same_blocks_replayed": True,
            "interpretation": "Small controlled pilot, not the production-wide BLOCK rate",
        },
        "runs": completed,
        "counts": rates,
    }


def main() -> None:
    """Run missing measurements serially, checkpointing after every replicate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analyze-only", action="store_true")
    parser.add_argument("--profiles", nargs="+", choices=PROFILES, default=PROFILES)
    args = parser.parse_args()
    settings = get_settings()
    if settings.guard_stat != "mean" or settings.min_runs >= min(BUDGETS):
        parser.error("Study requires GUARD_STAT=mean and MIN_RUNS below 40")
    if not math.isclose(settings.delta, 0.05) or not math.isclose(settings.alpha, 0.05):
        parser.error("Study requires the original 5% delta and alpha")
    status_path = OUT / "status.json"
    status = {
        "started_at": datetime.now(UTC).isoformat(),
        "state": "running",
        "min_runs": settings.min_runs,
        "base_rate_tps": settings.base_rate_tps,
        "block_seconds": settings.block_seconds,
        "workers": settings.workers,
        "current": None,
    }
    if not args.analyze_only:
        for profile in args.profiles:
            for seed in SEEDS:
                path = OUT / f"{profile}_{seed}.json"
                if path.exists():
                    data = json.loads(path.read_text())
                    if len(data.get("arms", {}).get("reused", {}).get("blocks", [])) >= 80:
                        continue
                status["current"] = {"profile": profile, "seed": seed}
                _save(status_path, status)
                command = [
                    sys.executable,
                    str(Path(__file__).with_name("diagnose_guard_clone_lifecycle.py")),
                    "--profile",
                    profile,
                    "--pairs",
                    "80",
                    "--candidate",
                    "city",
                    "--arms",
                    "reused",
                    "--seed",
                    str(seed),
                    "--out",
                    str(path),
                ]
                subprocess.run(command, cwd=ROOT, check=True)
                _save(OUT / "summary.json", _analyze(status))
        status["state"] = "complete"
        status["current"] = None
        status["finished_at"] = datetime.now(UTC).isoformat()
        _save(status_path, status)
    _save(OUT / "summary.json", _analyze(status))


if __name__ == "__main__":
    main()
