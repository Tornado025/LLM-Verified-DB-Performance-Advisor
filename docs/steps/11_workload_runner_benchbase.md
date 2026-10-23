# Step 11 — Workload runner, flash-sale profiles, calibration, BenchBase

**Goal (end state):** `workload.py` runs deterministic open-loop blocks of order transactions against any database
(`qc_live` or a clone) under the four profiles and returns per-transaction latency lists; the base rate is
**calibrated** so `flash20` uses about 60–70 % of baseline saturation; `benchbase.py` + four CH-benCHmark configs can run
the same profiles. A paired A/A guard-style run shows ~0 % mean relative change.

**Prerequisites:** steps 02–05, 07.

## 11.1 Profiles (`backend/data/profiles.json` + `ProfileSpec`)

| name | rate_mult | zipf_s | hot_set_size | hot_share | status_poll_mult | dashboard_refresh_s | pre_sale_load | analyze_after_load |
|---|---|---|---|---|---|---|---|---|
| normal | 1 | 0.8 | 50 | 0.15 | 1 | 10 | false | – |
| flash5 | 5 | 1.1 | 20 | 0.40 | 2 | 5 | true | true |
| flash10 | 10 | 1.2 | 15 | 0.55 | 3 | 3 | true | true |
| flash20 | 20 | 1.3 | 10 | 0.70 | 4 | 2 | true | **false** (stale statistics case) |

All values are **experiment parameters**, not claims about any company; document the calibration sources in the README
(Zepto ≈ 2.5K orders/store/day normal; Zomato NYE ramp ≈ 1.6× over two hours; Instamart Holi 444 packs/min hot-SKU; Alibaba 122× spike) and sweep the multipliers.

## 11.2 Schedule construction (precomputed ⇒ common random numbers)

```python
def build_schedule(profile, seconds, seed, base_rate, pools) -> list[Arrival]
```
1. `rng = np.random.default_rng(seed)`; total rate `λ = base_rate × profile.rate_mult`.
2. Arrival times: cumulative sums of `rng.exponential(1/λ)` until `seconds`.
3. Transaction type per arrival: categorical with weights from `ORDER_TXNS` (`order_status` weight multiplied by `status_poll_mult`, then renormalised).
4. Parameters per arrival (all drawn from `rng` at schedule-build time, so **A and B receive identical parameters**):
   * `place_order`: `customer_id` (Zipf over customer pool), `store_id` uniform, 2–4 lines; each line's `product_id`: with probability `hot_share` choose from the hot set (Zipf with `zipf_s`), else uniform over all products; qty 1–3; `payment_mode`.
   * `stock_decrement`: store uniform, product as above, qty 1–2.
   * `update_status`: a **pool index** (resolved at execution time to an actual order id from the shared active pool).
   * `order_status`: pool index over recently created or seeded active orders.
   * `picker_queue_read`: store uniform.
5. `Arrival(t, txn, params)`; the schedule is a pure function of `(profile, seconds, seed, base_rate, pools)`.
6. **Pools** loaded once per clone before the block: `active_order_ids` (≤ 2000 from `status IN ('placed','packing','out_for_delivery')`), `customer_ids`, `store_ids`, `hot_product_ids` (the `hot_set_size` lowest product ids — consistent with the seeded skew).

## 11.3 Block execution (open loop)

```python
def run_block(dbname, schedule, workers, *, stop_event=None, record_timeline=False) -> BlockResult
```
1. Open `workers` connections (autocommit False; `SET statement_timeout`). Create a shared `deque` of arrivals and a shared list for created order ids.
2. `t0 = time.perf_counter()`. Each worker loop:
   * pop the next arrival (lock-protected index); `target = t0 + arrival.t`; sleep until `target` if early (use `time.sleep` for > 2 ms, spin for the final < 2 ms is **not** needed).
   * execute the transaction; **latency = finish − target** (includes queueing delay; this is deliberate: saturation shows up as latency growth).
   * on exception: rollback, increment `errors[txn]`; on `oos` count separately. Never raise out of the worker.
3. Run until all arrivals are processed or `stop_event`. The block's `seconds` = schedule duration; wait for stragglers up to `2 × seconds` then abandon (counted in `dropped`).
4. Return `BlockResult(seconds, latencies={txn: [ms…]}, errors, dropped)`.
5. Background **dashboard thread** (one extra connection): every `dashboard_refresh_s` run 2 report shapes (`hourly_sales`, `stockouts_15m`); record latencies under `report:<shape>`; **never** part of the guard.
6. After the block, `update_status` transitions created orders to keep pools meaningful.

Rate sanity: if the scheduler thread cannot keep up (Python overhead), log `workload_lag_ms`; abort the experiment if lag > 5 % of block time (measurement invalid).

## 11.4 Preparing a clone for a block

```python
def prepare_clone(dbname, profile, seed) -> dict
```
* Always: `UPDATE store_stock SET qty = 1000000 WHERE product_id <= <hot_set_size_max>` (so the hot set never runs out; identical on A and B).
* If `profile.pre_sale_load`: bulk-insert `N_new = 2000` products (+`store_stock` rows for 20 stores each) and `UPDATE products SET price = price * 1.05 WHERE product_id % 7 = 0` to mimic a catalogue/price refresh.
  If `profile.analyze_after_load` run `ANALYZE`; else **skip it** (stale-statistics scenario). Record `{"analyzed": bool}` in the evidence.
* `VACUUM (ANALYZE)` is **not** run here; autovacuum is left to PostgreSQL (document).
* Return a dict describing what was done (goes into the guard result).

## 11.5 Calibration (`scripts/calibrate_rate.py`)

Goal: choose `BASE_RATE_TPS` so that `flash20` ≈ 60–70 % of the **baseline saturation throughput** (the highest rate at which p95 of `place_order` stays < 4× its low-load p95).
1. Create one clone. Using profile `normal` shape with rate multiplier 1, run 20 s blocks at increasing absolute rates (5, 10, 20, 40, 80, 160, 320 tps) until p95(`place_order`) exceeds the threshold; saturation = last good rate.
2. `BASE_RATE_TPS = floor(0.65 × saturation / 20)`; clamp to [3, 60].
3. Write `data/calibration.json` and print the recommended `.env` value. Re-run after changing the dataset scale or hardware.
4. Also record low-load p95 per transaction (the guard needs ≥ `MIN_SAMPLES_PER_BLOCK` samples per type per block: with weights 0.05 for `picker_queue_read`, `BLOCK_SECONDS × base_rate × 0.05 ≥ 5` ⇒ `BLOCK_SECONDS ≥ 100 / base_rate`; compute and warn).

## 11.6 A/A validation (acceptance for the runner)
* Create two identical clones A, B. For 12 block pairs (alternating order) run `normal` and `flash5` with the same seed.
* Per transaction type compute `d = ln(mean_B / mean_A)`; assert `|mean(d)| < 0.03` and that `stats.verdict` returns `PASS` for **every** type within `MAX_BLOCKS`.
  If not, the runner is too noisy: increase `BLOCK_SECONDS`, lower rate, or fix a bug. Record numbers in `PROGRESS.md`.

## 11.7 `app/benchbase.py` and `benchbase/configs/`

* Build BenchBase per its README (needs a JDK; check the required version in the repo). Keep the built jar path in `BENCHBASE_JAR` (env, optional).
* Copy the CH-benCHmark sample config for PostgreSQL from the BenchBase repo (`config/postgres/…chbenchmark…xml`; check the exact filename) to `benchbase/configs/ch_normal.xml`, then edit:
  `<url>` (database `qc_ch` created from a CH-benCHmark load, **separate from the quick-commerce DB**), `<username>/<password>`, `<scalefactor>` (warehouses, 10 for dev, 50–200 for final), `<terminals>`, and `<works>`:
  * normal: one phase, `<time>120</time>`, `<rate>R</rate>`, weights `45,43,4,4,4` (NewOrder, Payment, OrderStatus, Delivery, StockLevel).
  * flash5/10/20: same with `<rate>` multiplied by 5/10/20 (and `<arrival>` Poisson if your BenchBase version supports it — verify).
* `benchbase.py`:
  ```python
  def load_ch(db: str, scale: int) -> None            # runs --create=true --load=true
  def run_profile(db: str, profile: str, out_dir: Path, seconds: int) -> dict   # runs --execute=true, returns parsed latencies
  def parse_results(out_dir: Path) -> dict[str, dict]    # per transaction type: mean, p50, p95, p99, count, errors
  ```
  Command (verify flags for your version): `java -jar benchbase.jar -b chbenchmark -c <config.xml> --create=true --load=true --execute=true -d <out_dir>`.
* The CH-benCHmark database `qc_ch` has its **own** golden/clone cycle: `sandbox.create_clone(template="qc_ch_golden")`.
* In the guard (step 12) a `--workload ch` option calls `benchbase.run_profile` on clone A then clone B (alternating), and feeds per-transaction mean ratios into the same `stats.verdict`.
* TPC-C spreads across warehouses (little hot-row contention); to approximate a flash sale also run with **fewer warehouses touched** (set `terminals` > warehouses and reduce `<scalefactor>` of the *active* range if your version allows) and rely on the custom workload for hot-stock contention. Label results "CH-benCHmark / TPC-C-derived", never "TPC-C certified".

## 11.8 Tests (`tests/test_workload.py`)
* `test_schedule_deterministic`: same inputs ⇒ identical schedule; different seed ⇒ different.
* `test_schedule_rate`: observed arrivals ≈ `λ × seconds` (±10 %); type frequencies match weights (χ² p > 0.01).
* `test_hot_share`: share of lines in the hot set ≈ `hot_share` (±5 %).
* `@db test_run_block_smoke`: 5 s block on a clone at 5 tps finishes, all five types have latencies.
* `@db test_errors_counted_not_raised`: inject a bad parameter ⇒ `errors[txn] > 0`, no exception.
* `@db @slow test_aa_validation`: section 11.6.
* `test_prepare_clone_skip_analyze`: for `flash20`, `pg_stat_user_tables.last_analyze` unchanged for `products`.

## 11.9 Done checklist
- [ ] `calibration.json` produced; `.env` base rate set; A/A validation numbers recorded
- [ ] BenchBase built; `ch_*.xml` run for 60 s each; parsed results saved to `experiments/results/ch_smoke.json`
- [ ] Commit `step 11: workload runner and benchbase`
