"""Seeded open-loop commerce traffic with honest queueing and dispatcher diagnostics.

Schedules contain logical pool indices and precomputed parameters. Dispatch never
waits for database work; finish-target latency includes overload queueing. Dedicated
connections belong to their worker thread. Only clone preparation performs bulk
setup writes; run_block executes the five trusted order transactions.
"""

import json
import logging
import math
import threading
from collections.abc import Sequence
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from queue import Empty, Queue
from time import perf_counter
from typing import Any

import numpy as np
import psycopg

from app.cancel import current_event
from app.config import get_settings
from app.errors import BadRequest, Conflict
from app.models import Arrival, BlockResult, ProfileSpec
from app.pg import connect
from app.sandbox import _validate
from app.shapes import ORDER_TXNS, REPORT_SHAPES, OutOfStock, load_as_of, render, run_transaction

_LOG = logging.getLogger(__name__)


class Schedule(list[Arrival]):
    """List-compatible arrivals with the requested duration/profile preserved."""

    def __init__(
        self,
        arrivals: Sequence[Arrival],
        seconds: float,
        profile: ProfileSpec,
        seed: int,
        pools: dict,
    ):
        super().__init__(arrivals)
        self.seconds, self.profile, self.seed, self.pools = seconds, profile, seed, pools


def load_profiles() -> dict[str, ProfileSpec]:
    """Read explicit experiment parameters, not company traffic claims."""
    entries = json.loads((Path(__file__).parents[1] / "data/profiles.json").read_text())
    return {entry["name"]: ProfileSpec(**entry) for entry in entries}


def load_pools(dbname: str, profile: ProfileSpec) -> dict:
    """Load one immutable parameter pool snapshot before a block."""
    with connect(dbname) as conn:
        customers = [
            r[0]
            for r in conn.execute(
                "/* dpa:sys */ SELECT customer_id FROM customers ORDER BY customer_id LIMIT 20000"
            )
        ]
        stores = [
            r[0]
            for r in conn.execute("/* dpa:sys */ SELECT store_id FROM stores ORDER BY store_id")
        ]
        products = [
            (r[0], str(r[1]))
            for r in conn.execute(
                "/* dpa:sys */ SELECT product_id,price FROM products ORDER BY product_id"
            )
        ]
        active_rows = list(
            conn.execute(
                "/* dpa:sys */ SELECT order_id,status FROM orders WHERE status IN "
                "('placed','packing','out_for_delivery') ORDER BY order_id DESC LIMIT 2000"
            )
        )
        active = [r[0] for r in active_rows]
    if not all((customers, stores, products, active)):
        raise BadRequest("Empty workload pools")
    return {
        "customer_ids": customers,
        "store_ids": stores,
        "product_ids": [p[0] for p in products],
        "prices": {p[0]: p[1] for p in products},
        "active_order_ids": active,
        "hot_product_ids": [p[0] for p in products[: profile.hot_set_size]],
        "active_order_statuses": {r[0]: r[1] for r in active_rows},
    }


def _weights(profile: ProfileSpec) -> tuple[list[str], np.ndarray]:
    names = list(ORDER_TXNS)
    weights = np.array(
        [
            ORDER_TXNS[t].weight * (profile.status_poll_mult if t == "order_status" else 1)
            for t in names
        ],
        dtype=float,
    )
    return names, weights / weights.sum()


def build_schedule(
    profile: ProfileSpec, seconds: float, seed: int, base_rate: float, pools: dict
) -> list[Arrival]:
    """Precompute Poisson arrivals and all transaction parameters from one RNG."""
    if (
        not math.isfinite(seconds)
        or seconds <= 0
        or not math.isfinite(base_rate)
        or base_rate <= 0
        or seed < 0
        or not all(
            math.isfinite(value)
            for value in (
                profile.hot_share,
                profile.zipf_s,
                profile.rate_mult,
                profile.status_poll_mult,
                profile.dashboard_refresh_s,
            )
        )
        or not 0 <= profile.hot_share <= 1
        or profile.hot_set_size < 1
        or profile.dashboard_refresh_s <= 0
        or profile.zipf_s <= 0
        or profile.rate_mult <= 0
        or profile.status_poll_mult <= 0
    ):
        raise ValueError("Invalid schedule policy")
    for key in ("customer_ids", "store_ids", "product_ids", "active_order_ids"):
        if not pools.get(key):
            raise ValueError("Empty " + key)
    rng = np.random.default_rng(seed)
    names, weights = _weights(profile)
    customers = pools["customer_ids"]
    stores = pools["store_ids"]
    products = pools["product_ids"]
    hot = list(pools.get("hot_product_ids", products[: profile.hot_set_size]))[
        : profile.hot_set_size
    ]
    if not hot:
        raise ValueError("Empty hot set")
    customer_weights = np.arange(1, len(customers) + 1, dtype=float) ** -0.8
    customer_weights /= customer_weights.sum()
    hot_weights = np.arange(1, len(hot) + 1, dtype=float) ** -profile.zipf_s
    hot_weights /= hot_weights.sum()

    def product() -> int:
        return (
            int(rng.choice(hot, p=hot_weights))
            if rng.random() < profile.hot_share
            else int(rng.choice(products))
        )

    update_budget = [
        {"placed": 3, "packing": 2, "out_for_delivery": 1}.get(
            pools.get("active_order_statuses", {}).get(id, "placed"), 3
        )
        for id in pools["active_order_ids"]
    ]
    eligible = list(range(len(update_budget)))
    created_by_pool: dict[int, int] = {}
    rate = base_rate * profile.rate_mult
    t = 0.0
    arrivals: list[Arrival] = []
    while True:
        t += float(rng.exponential(1 / rate))
        if t >= seconds:
            break
        txn = str(rng.choice(names, p=weights))
        if txn == "place_order":
            lines: list[dict[str, Any]] = []
            for _ in range(int(rng.integers(2, 5))):
                p = product()
                lines.append(
                    {
                        "p": p,
                        "q": int(rng.integers(1, 4)),
                        "unit_price": str(pools.get("prices", {}).get(p, "10.00")),
                    }
                )
            total = sum((Decimal(line["unit_price"]) * line["q"] for line in lines), Decimal(0))
            params = {
                "customer_id": int(rng.choice(customers, p=customer_weights)),
                "store_id": int(rng.choice(stores)),
                "lines": lines,
                "total": str(total),
                "payment_mode": str(rng.choice(["upi", "card", "cod", "wallet"])),
            }
        elif txn == "stock_decrement":
            params = {"s": int(rng.choice(stores)), "p": product(), "q": int(rng.integers(1, 3))}
        elif txn in {"update_status", "order_status"}:
            if not eligible:
                raise BadRequest("Scheduled status transitions exhaust the active pool")
            if txn == "update_status":
                position = int(rng.integers(len(eligible)))
                index = eligible[position]
                update_budget[index] -= 1
                if update_budget[index] == 0:
                    eligible[position] = eligible[-1]
                    eligible.pop()
            else:
                index = int(rng.integers(len(update_budget)))
            params = {"pool_index": index, "pool_source": "active"}
            if index in created_by_pool:
                params["created_arrival"] = created_by_pool[index]
                params["pool_source"] = "recent"
        else:
            params = {"store_id": int(rng.choice(stores))}
        if txn == "place_order":
            logical = len(update_budget)
            created_by_pool[logical] = len(arrivals)
            eligible.append(logical)
            update_budget.append(3)
        arrivals.append(Arrival(t, txn, params))
    return Schedule(arrivals, seconds, profile, seed, pools)


def prepare_clone(dbname: str, profile: ProfileSpec, seed: int) -> dict:
    """Perform identical trusted fixture setup only on validated sandbox clones."""
    _validate(dbname, get_settings())
    start = perf_counter()
    with connect(dbname) as conn:
        with conn.transaction():
            conn.execute(
                "/* dpa:sys */ INSERT INTO store_stock "
                "(store_id,product_id,qty,reserved,updated_at) "
                "SELECT s.store_id,p.product_id,1000000,0,"
                "(SELECT value::timestamptz FROM meta WHERE key='as_of') "
                "FROM stores s CROSS JOIN products p WHERE p.product_id<=50 "
                "ON CONFLICT(store_id,product_id) DO UPDATE SET qty=1000000"
            )
            inserted = 0
            if profile.pre_sale_load:
                row = conn.execute("/* dpa:sys */ SELECT max(product_id) FROM products").fetchone()
                if row is None or row[0] is None:
                    raise BadRequest("Products must be seeded")
                first = int(row[0]) + 1
                conn.execute(
                    "/* dpa:sys */ INSERT INTO products "
                    "(product_id,sku,name,category_id,brand,price,is_active,created_at) "
                    "SELECT g,'SALE'||g,'sale_product_'||g,1+(g%%40),'sale',"
                    "10+((g+%s)%%490),true,(SELECT value::timestamptz FROM meta WHERE key='as_of') "
                    "FROM generate_series(%s::int,%s::int) g",
                    (seed, first, first + 1999),
                )
                conn.execute(
                    "/* dpa:sys */ INSERT INTO store_stock "
                    "(store_id,product_id,qty,reserved,updated_at) "
                    "SELECT s.store_id,p.product_id,1000000,0,"
                    "(SELECT value::timestamptz FROM meta WHERE key='as_of') "
                    "FROM (SELECT store_id FROM stores ORDER BY store_id LIMIT 20) s "
                    "CROSS JOIN products p WHERE p.product_id BETWEEN %s AND %s",
                    (first, first + 1999),
                )
                conn.execute(
                    "/* dpa:sys */ UPDATE products SET price=price*1.05 WHERE product_id%7=0"
                )
                inserted = 2000
        if profile.pre_sale_load and profile.analyze_after_load:
            conn.execute("/* dpa:sys */ ANALYZE")
    return {
        "profile": profile.name,
        "seed": seed,
        "new_products": inserted,
        "hot_qty": 1000000,
        "hot_set_max": 50,
        "analyzed": bool(profile.pre_sale_load and profile.analyze_after_load),
        "autovacuum": "left enabled; may analyze asynchronously",
        "seconds": perf_counter() - start,
    }


def run_block(
    dbname: str,
    schedule: list[Arrival],
    workers: int,
    *,
    stop_event: threading.Event | None = None,
    record_timeline: bool = False,
) -> BlockResult:
    """Run open-loop traffic, recording finish-target latency and invalidity evidence."""
    s = get_settings()
    if workers < 1 or dbname in {s.golden_db, s.maint_db, "template0", "template1"}:
        raise BadRequest("Invalid workload target or worker count")
    duration = (
        schedule.seconds
        if isinstance(schedule, Schedule)
        else max(1.0, max((a.t for a in schedule), default=1.0))
    )
    profile = schedule.profile if isinstance(schedule, Schedule) else load_profiles()["normal"]
    pools = load_pools(dbname, profile)
    # A/B schedules retain identical initial pool snapshots; no live reread can
    # silently change pool-index meaning between sides.
    if isinstance(schedule, Schedule):
        pools = schedule.pools
    if any(a.txn not in ORDER_TXNS or not math.isfinite(a.t) or a.t < 0 for a in schedule):
        raise BadRequest("Invalid scheduled arrival")
    if any(a.t > b.t for a, b in zip(schedule, schedule[1:], strict=False)):
        raise BadRequest("Scheduled arrivals must be ordered by target time")
    cancelled = threading.Event()
    external = stop_event or current_event() or threading.Event()
    jobs: Queue = Queue()
    ready = threading.Barrier(workers + 1)
    lock = threading.Lock()
    completed_condition = threading.Condition(lock)
    latencies: dict[str, list[float]] = {name: [] for name in ORDER_TXNS}
    errors = {name: 0 for name in ORDER_TXNS}
    oos = {name: 0 for name in ORDER_TXNS}
    connections: list[psycopg.Connection] = []
    startup_errors = []
    created: dict[int, int] = {}
    completed = set()
    timeline = []
    queue_lags = []
    dispatcher_lags = []
    deadline = 0.0
    t0 = 0.0

    def worker() -> None:
        nonlocal t0
        conn = None
        try:
            conn = connect(dbname, autocommit=False)
            with lock:
                connections.append(conn)
        except Exception as exc:
            with lock:
                startup_errors.append(type(exc).__name__)
        try:
            ready.wait(timeout=45)
            if conn is None:
                return
            while not cancelled.is_set():
                if external.is_set():
                    cancelled.set()
                    break
                try:
                    job = jobs.get(timeout=0.05)
                except Empty:
                    continue
                if job is None:
                    break
                index, arrival = job
                start = perf_counter()
                params = dict(arrival.params)
                status = "ok"
                try:
                    if arrival.txn in {"order_status", "update_status"} and "id" not in params:
                        active = pools["active_order_ids"]
                        if "created_arrival" in params:
                            target = params["created_arrival"]
                            with completed_condition:
                                while target not in completed and not cancelled.is_set():
                                    remaining = deadline - perf_counter()
                                    if remaining <= 0:
                                        raise Conflict("Creation dependency missed block deadline")
                                    completed_condition.wait(min(remaining, 0.1))
                                if target not in created:
                                    raise Conflict("Referenced order was not created")
                                params["id"] = created[target]
                        else:
                            params["id"] = active[params["pool_index"] % len(active)]
                    if arrival.txn == "place_order":
                        params["total"] = Decimal(params["total"])
                        params["lines"] = [
                            dict(line, unit_price=Decimal(line["unit_price"]))
                            for line in params["lines"]
                        ]
                    result = run_transaction(conn, arrival.txn, params)
                    if arrival.txn == "place_order":
                        with lock:
                            created[index] = result
                except OutOfStock:
                    conn.rollback()
                    status = "oos"
                    with lock:
                        errors[arrival.txn] += 1
                        oos[arrival.txn] += 1
                except Exception as exc:
                    conn.rollback()
                    status = type(exc).__name__
                    with lock:
                        errors[arrival.txn] += 1
                finish = perf_counter()
                with lock:
                    latencies[arrival.txn].append(max((finish - (t0 + arrival.t)) * 1000, 1e-3))
                    completed.add(index)
                    completed_condition.notify_all()
                    queue_lags.append(max(0, (start - (t0 + arrival.t)) * 1000))
                    if record_timeline:
                        timeline.append(
                            {
                                "index": index,
                                "txn": arrival.txn,
                                "target_s": arrival.t,
                                "start_s": start - t0,
                                "finish_s": finish - t0,
                                "status": status,
                            }
                        )
        finally:
            if conn:
                conn.close()

    threads = [
        threading.Thread(target=worker, name=f"dpa-worker-{i}", daemon=True) for i in range(workers)
    ]
    for thread in threads:
        thread.start()
    try:
        ready.wait(timeout=45)
    except threading.BrokenBarrierError:
        cancelled.set()
        raise BadRequest("Workload connection startup timed out") from None
    if startup_errors:
        cancelled.set()
        for thread in threads:
            thread.join(2)
        return BlockResult(
            duration,
            latencies,
            errors,
            len(schedule),
            {"valid": False, "startup_errors": startup_errors},
        )
    t0 = perf_counter()
    deadline = t0 + 3 * duration
    dashboard_stop = threading.Event()

    def dashboard() -> None:
        try:
            with connect(dbname) as conn:
                as_of = load_as_of(conn)
                while not dashboard_stop.wait(profile.dashboard_refresh_s):
                    for shape_id in ("hourly_sales", "stockouts_15m"):
                        query = render(REPORT_SHAPES[shape_id], conn, {"as_of": as_of.isoformat()})
                        start = perf_counter()
                        conn.execute(query).fetchall()
                        with lock:
                            latencies.setdefault("report:" + shape_id, []).append(
                                (perf_counter() - start) * 1000
                            )
        except Exception as exc:
            with lock:
                errors["dashboard"] = errors.get("dashboard", 0) + 1
            _LOG.warning("Dashboard query failed: %s", type(exc).__name__)

    report_thread = threading.Thread(target=dashboard, name="dpa-dashboard", daemon=True)
    report_thread.start()
    try:
        for index, arrival in enumerate(schedule):
            if external.is_set() or cancelled.is_set():
                break
            remaining = t0 + arrival.t - perf_counter()
            if remaining > 0 and external.wait(remaining):
                break
            lag = max(0, perf_counter() - (t0 + arrival.t))
            dispatcher_lags.append(lag * 1000)
            if lag > 0.05 * duration:
                cancelled.set()
                _LOG.error("Invalid workload: dispatcher lag %.3f ms", lag * 1000)
                break
            jobs.put((index, arrival))
        external.wait(max(0, t0 + duration - perf_counter()))
        for _ in threads:
            jobs.put(None)
        for thread in threads:
            thread.join(max(0, deadline - perf_counter()))
    finally:
        dashboard_stop.set()
        if any(thread.is_alive() for thread in threads) or external.is_set():
            cancelled.set()
            with lock:
                active_connections = list(connections)
            for conn in active_connections:
                with suppress(psycopg.Error):
                    conn.cancel()
            for thread in threads:
                thread.join(2)
        report_thread.join(2)
        if any(thread.is_alive() for thread in threads) or report_thread.is_alive():
            raise BadRequest("Workload threads did not stop; measurement invalid")
    if created:
        with connect(dbname) as conn:
            conn.execute(
                "/* dpa:txn=update_status */ UPDATE orders SET status='packing' "
                "WHERE order_id=ANY(%s) AND status='placed'",
                (list(created.values()),),
            )
    dropped = len(schedule) - len(completed)
    return BlockResult(
        duration,
        latencies,
        errors,
        dropped,
        {
            "valid": (
                not cancelled.is_set()
                and not external.is_set()
                and dropped == 0
                and not errors.get("dashboard", 0)
            ),
            "profile": profile.name,
            "seed": schedule.seed if isinstance(schedule, Schedule) else None,
            "scheduled": len(schedule),
            "completed": len(completed),
            "created_orders": len(created),
            "oos": oos,
            "workload_lag_ms": max(dispatcher_lags, default=0),
            "queue_lag_p95_ms": float(np.quantile(queue_lags, 0.95)) if queue_lags else None,
            "actual_seconds": perf_counter() - t0,
            "timeline": sorted(timeline, key=lambda r: r["index"]) if record_timeline else None,
        },
    )
