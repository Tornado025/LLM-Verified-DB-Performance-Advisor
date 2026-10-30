"""Explicit planted demo on qc_live: approved qty index, flash traffic, automatic revert."""

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.models import Candidate
from app.monitor import LiveTraffic, MonitorThread, _index_sql, _index_state, apply, revert
from app.pg import connect
from app.store import Store


def run_demo(
    store: Store, *, interval_s: float = 5, max_windows: int = 12, controlled_delay: bool = False
) -> dict:
    """Run an explicitly planted fixture; fail honestly if no regression is measured."""
    job = store.create_job({"demo_only": True, "seed": 1234})
    candidate = Candidate(
        job + ":recent_orders:01",
        job,
        "recent_orders",
        "index",
        "CREATE INDEX ON store_stock(qty)",
        "Planted demo fixture, bypasses proposal/guard for monitor testing",
        0,
        status="selected",
        guard={"passed": True, "demo_fixture": True},
    )
    store.save_candidate(candidate)
    store.save_evidence(
        job,
        candidate.shape_id,
        {
            "shape_id": candidate.shape_id,
            "demo_only": True,
            "baseline": {"sql": "SELECT qty FROM store_stock"},
            "candidates": [candidate.to_dict()],
            "decision": {"candidate_id": candidate.id},
        },
    )
    probe_stop = threading.Event()
    delay = [0.001]

    def probe() -> None:
        with connect(get_settings().live_db) as conn:
            while not probe_stop.is_set():
                # Trusted test instrumentation, never LLM SQL or a production suggestion.
                conn.execute("/* dpa:shape=recent_orders */ SELECT pg_sleep(%s)", (delay[0],))
                probe_stop.wait(0.02)

    probe_thread = threading.Thread(target=probe, daemon=True)
    if controlled_delay:
        probe_thread.start()
    try:
        applied = apply(store, candidate.id, approver="human-approved-demo")
    except BaseException:
        probe_stop.set()
        if probe_thread.ident:
            probe_thread.join(5)
        raise
    delay[0] = 0.010
    traffic = LiveTraffic("flash10", seconds=interval_s, seed=1234)
    monitor = MonitorThread(store, interval_s, min_calls=5, consecutive=2)
    history = []
    try:
        traffic.start()
        for _ in range(max_windows):
            time.sleep(interval_s)
            monitor.evaluate()
            row = store.get_applied(applied["id"])
            if row["windows"]:
                history.append(row["windows"][-1])
            if row["state"] == "reverted":
                break
            if traffic.error:
                raise RuntimeError("Live traffic failed: " + traffic.error)
        row = store.get_applied(applied["id"])
        automatic = row["state"] == "reverted"
    finally:
        probe_stop.set()
        if probe_thread.ident:
            probe_thread.join(5)
        traffic.stop()
        if traffic.ident:
            traffic.join(get_settings().stmt_timeout_ms / 1000 + 10)
        if store.get_applied(applied["id"])["state"] != "reverted":
            revert(store, applied["id"], "demo cleanup without confirmed regression")
    return {
        "job_id": job,
        "demo_only": True,
        "controlled_delay": controlled_delay,
        "interpretation": "Injected statement delay tests monitor behavior; not index causality"
        if controlled_delay
        else "Natural index and workload change; no injected latency",
        "automatic_revert": automatic,
        "final_state": store.get_applied(applied["id"])["state"],
        "index_absent": _index_state(_index_sql(candidate)[0]) is None,
        "windows": history,
        "events": store.events_after(job),
        "baseline": applied["baseline"],
        "traffic_error": traffic.error,
    }


def main() -> None:
    """Require explicit demo approval before generating traffic or changing live."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approve-demo", action="store_true")
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--max-windows", type=int, default=12)
    parser.add_argument("--controlled-delay", action="store_true")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parents[2] / "experiments/results/step14_demo.json",
    )
    args = parser.parse_args()
    if not args.approve_demo:
        parser.error(
            "--approve-demo is required: this generates live traffic and applies a test index"
        )
    with TemporaryDirectory() as folder:
        store = Store(Path(folder) / "meta.sqlite")
        result = run_demo(
            store,
            interval_s=args.interval,
            max_windows=args.max_windows,
            controlled_delay=args.controlled_delay,
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in ("automatic_revert", "final_state", "index_absent", "traffic_error")
                }
            )
        )
        store.close()
        if not result["automatic_revert"] or not result["index_absent"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
