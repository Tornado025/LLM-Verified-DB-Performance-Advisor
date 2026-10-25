# DB Performance Advisor

A research framework for verifying LLM-proposed PostgreSQL query tuning under
normal and flash-sale workloads. Suggestions must pass sandbox verification before
human approval, application, monitoring, and automatic rollback on regression.

## Status

Steps 01–03 are implemented and verified. Step 02 provides the original
generated quick-commerce schema and deterministic seeder. Research datasets complement later experiments;
they are not imported into the operational commerce tables. Backend configuration, connection helpers, models, logging and SQLite metadata
storage are implemented. The tuning pipeline, API and frontend are not implemented.
See [project guidance](AGENTS.md), [contracts](docs/steps/00_contracts.md), and the
[progress log](docs/PROGRESS.md). Implementation follows steps 01–21 in order.

## Development setup

Requires Python 3.11, Docker with Compose v2, Git, and make. Node 20+ is needed
when the frontend is introduced in step 16.

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
