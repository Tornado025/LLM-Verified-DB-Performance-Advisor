"""Run measured CH-benCHmark profiles on fresh clones of the separate CH template."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.benchbase import ROOT, run_profile
from app.config import get_settings
from app.pg import admin_connect
from app.sandbox import create_clone, drop_clone


def main() -> None:
    """Checkpoint each real run; retain raw outputs outside Git for inspection."""
    settings = get_settings()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "experiments/results/ch_smoke.json"
    with admin_connect() as admin:
        row = admin.execute(
            "SELECT datallowconn FROM pg_database WHERE datname='qc_ch_golden'"
        ).fetchone()
        if row is None or row[0]:
            raise RuntimeError("Load and close qc_ch_golden before running CH acceptance")
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname='qc_ch'").fetchone()
        if not exists:
            admin.execute(sql.SQL("CREATE DATABASE qc_ch TEMPLATE qc_ch_golden STRATEGY FILE_COPY"))
    report = {
        "label": "CH-benCHmark / TPC-C-derived (not certified)",
        "seed": 1234,
        "created_at": stamp,
        "base_rate_tps": settings.base_rate_tps,
        "loaded_warehouses": 10,
        "runtime": json.loads((ROOT / "benchbase/runtime_manifest.json").read_text()),
        "runs": [],
        "runs_complete": False,
        "all_protected_sample_types_present": None,
        "unexpected_sql_errors_by_scenario": {},
    }
    scenarios = [(p, None) for p in ("normal", "flash5", "flash10", "flash20")]
    scenarios.append(("flash20", 2))
    for profile, active in scenarios:
        name = profile if active is None else profile + "_hot2"
        directory = ROOT / "benchbase/results" / stamp / name
        clone = create_clone(template="qc_ch_golden")
        try:
            result = run_profile(clone, profile, directory, 60, active_warehouses=active)
        finally:
            drop_clone(clone)
        result["scenario"] = name
        result["raw_output"] = str(directory.relative_to(ROOT))
        result["protected_samples_present"] = all(
            result["transactions"][txn]["count"] > 0
            for txn in ("NewOrder", "Payment", "OrderStatus", "Delivery", "StockLevel")
        )
        report["runs"].append(result)
        output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(name, {k: v["count"] for k, v in result["transactions"].items()}, flush=True)
    report["runs_complete"] = len(report["runs"]) == len(scenarios)
    report["all_protected_sample_types_present"] = all(
        r["protected_samples_present"] for r in report["runs"]
    )
    report["unexpected_sql_errors_by_scenario"] = {
        r["scenario"]: (
            sum(
                r["transactions"][txn]["errors"]
                for txn in ("NewOrder", "Payment", "OrderStatus", "Delivery", "StockLevel")
            )
            if all(
                r["transactions"][txn]["errors"] is not None
                for txn in ("NewOrder", "Payment", "OrderStatus", "Delivery", "StockLevel")
            )
            else None
        )
        for r in report["runs"]
    }
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    if not report["runs_complete"] or not report["all_protected_sample_types_present"]:
        raise SystemExit("CH smoke has missing protected transaction samples")


if __name__ == "__main__":
    main()
