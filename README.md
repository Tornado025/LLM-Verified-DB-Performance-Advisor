# DB Performance Advisor (MARS)

A research framework for verifying LLM-proposed PostgreSQL query tuning under
normal and flash-sale workloads. Suggestions must pass sandbox verification before
human approval, application, monitoring, and automatic rollback on regression.

## Status

Step 01 scaffold is complete and its acceptance checks pass. The advisor, API, and frontend are not implemented.
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
Step 02 will create `qc_golden` and `qc_live`; health checks never connect to golden.
Validate the scaffold database with:

```sh
docker compose ps
docker compose exec db psql -U advisor -d postgres -c '\dx'
docker compose exec db psql -U advisor -d postgres -c 'SELECT * FROM hypopg_reset();'
docker compose exec db psql -U advisor -d postgres -c 'SHOW shared_preload_libraries;'
```

`make test` allows an empty suite during scaffolding. Real LLM tests require
`RUN_LLM_TESTS=1`. DB tests skip when the maintenance database is unavailable.
`make seed`, `make api`, `make web`, and `make experiments` become available in
later steps. `make down` preserves the data volume; `make clean` deletes it.

Licensed under the MIT License.
