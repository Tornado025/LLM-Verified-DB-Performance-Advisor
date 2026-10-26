# DB Performance Advisor

A research framework for verifying LLM-proposed PostgreSQL query tuning under
normal and flash-sale workloads. Suggestions must pass sandbox verification before
human approval, application, monitoring, and automatic rollback on regression.

## Status

Steps 01–11 are implemented and verified (step 07's host-noise variance test remains inconclusive). Step 02 provides the original
generated quick-commerce schema and deterministic seeder. Research datasets complement later experiments;
they are not imported into the operational commerce tables. Backend configuration, connection helpers, models, logging and SQLite metadata
storage are implemented, along with eight report shapes and five protected order
transactions. The statistics engine provides conservative sequential safety and
improvement tests. A reproducible RQ2 simulation compares decision rules under
noise. Disposable clones, read-only paired measurements and a real A/A noise
report are available. Plan fingerprinting, hypothetical-index screening, schema
metadata and slow-query observation are implemented. The tuning pipeline, API and
frontend are not implemented. LLM providers, safety screening and plan-saturation
proposal sampling are implemented; 228 generated report questions are available.
Rewrite equivalence checks include generated counterexamples and full-data
comparisons; the 22-case seed catalogue passes. Results are tested, never proven.
See [project guidance](AGENTS.md), [contracts](docs/steps/00_contracts.md), and the
[progress log](docs/PROGRESS.md). Implementation follows steps 01–21 in order.

## Development setup

Requires Python 3.11, Docker with Compose v2, Git, and make. Node 20+ is needed
when the frontend is introduced in step 16.

Step 11 acceptance is complete. It adds seeded normal/flash-sale traffic, local
rate calibration and a separate BenchBase CH workload. The BenchBase flash10 and
flash20 observations include PostgreSQL predicate-lock errors; the noise
injector variance check from Step 07 is inconclusive. Both are recorded for
review after Step 21 in [deferred follow-ups](docs/POST_21_FOLLOWUPS.md) and do
not interrupt the numbered implementation sequence.
With the environment active, `python backend/scripts/calibrate_rate.py` measures
the host's saturation and writes `backend/data/calibration.json`; set its
recommended `BASE_RATE_TPS` in `.env`. Rerun after changing hardware or seed scale.
`python backend/scripts/validate_workload_aa.py` compares identical clones with
counterbalanced ordering and checkpoints every pair. It can take tens of minutes;
a failed or inconclusive check does not become a safety PASS. The slow DB test
recomputes acceptance from the raw saved pairs, or runs the experiment if absent.
After an interruption, add `--resume` to retain completed pairs and the original
40-pair budget. Resume checks the policy, durations, seeds, ordering and raw block
validity, then rebuilds the paired observations; it rejects incompatible evidence.
See [BenchBase setup](benchbase/README.md) for the separate benchmark build and
60-second profile commands. Profile values are experiment parameters; calibration
comes from measured database traffic on this host.

```sh
cp .env.example .env
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
make up
make test
make lint
```

The development database credentials in `.env.example` are local defaults.
Keep secrets in the ignored `.env` file. The default LLM provider is `mock`.
Python dependencies are pinned from the first successful Python 3.11 installation.

PostgreSQL 16 initializes extensions in the maintenance database (`postgres`).
Seeding creates `qc_golden` and `qc_live`; health checks never connect to golden.
Validate the scaffold database with:

```sh
docker compose ps
docker compose exec db psql -U advisor -d postgres -c '\dx'
docker compose exec db psql -U advisor -d postgres -c 'SELECT * FROM hypopg_reset();'
docker compose exec db psql -U advisor -d postgres -c 'SHOW shared_preload_libraries;'
```

`make test` allows an empty suite during scaffolding. Real LLM tests require
`RUN_LLM_TESTS=1`. DB tests skip when the maintenance database is unavailable.
`make api`, `make web`, and `make experiments` become available in later steps.
`make down` preserves the data volume; `make clean` deletes it.

## Seed the development databases

The seeder generates the controlled quick-commerce dataset specified by the
original plan. These are synthetic records. With the virtual environment active
and PostgreSQL running:

```sh
make seed                  # rebuild golden and live, scale 1, seed 1234
make test-db               # verify the seeded live database
make reset-live            # rebuild only live from the idle golden template
make seed SCALE=3          # larger development dataset, above 256 MiB shared_buffers
```

Seed and reset commands delete their development target databases and disconnect
clients. Golden permits no direct connections after seeding; reset clones it from
the maintenance database. All verification queries run against live.

The seeder prints a measured JSON report and returns nonzero if verification fails.
Scale 1 is a quick fixture; its measured size is below shared_buffers on this setup.
Use a larger scale for timing experiments and check the reported size. Final
experiments may need a dataset larger than Docker's RAM.

For a different random seed or to refuse replacing existing databases:

```sh
python backend/scripts/seed.py --scale 1 --seed 42
python backend/scripts/seed.py --no-drop
```

Seeds range from -1000000 to 1000000 and map directly to PostgreSQL `setseed`.
The frozen clock is UTC noon on the seeding date. Identical seed, scale, date,
and PostgreSQL environment reproduce the data; `seeded_at` records the actual run time.

Licensed under the MIT License.


## Run the noise simulation

With the virtual environment active, from `backend/`:

```sh
python -m app.simulate --trials 400 --seed 1234 --borderline --out ../experiments/results/rq2_sim.json
```

This uses synthetic measurements and needs no database or LLM. Trials are per
true-effect value per noise level. The JSON includes error-rate confidence
intervals and measurement costs; borderline effects are reported separately.


## LLM development and real calls

`LLM_PROVIDER=mock` is the free development/test default. For explicit real calls,
configure `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` only in ignored `.env` and
select `LLM_PROVIDER=openrouter`. GPT-4.1 Nano is the current low-cost testing choice;
API key/account limits remain the user's responsibility. Real API tests require
`RUN_LLM_TESTS=1` and the `llm` marker. Routine tests mock HTTP completely.

The saved questions were generated with GPT-4.1 Mini before switching to Nano;
`backend/data/questions_meta.json` preserves model/usage/provenance. Queries passed
safety and EXPLAIN checks, which do not establish that their answers match the
English questions or that a rewrite is equivalent.


## Re-run rewrite equivalence measurements

With PostgreSQL running and the virtual environment active:

```sh
python backend/scripts/equivalence_report.py
```

This uses disposable clones, 50 seeded scratch populations per eligible pair and
three timezones. It requires no model calls and writes measured verdicts to
`experiments/results/equivalence_report.json`. The seed catalogue contains ten
known traps and twelve equivalent pairs; later analysis expands it.
