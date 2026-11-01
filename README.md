# DB Performance Advisor

A research framework for verifying LLM-proposed PostgreSQL tuning on a
quick-commerce database under normal and flash-sale traffic.

The LLM proposes a SQL rewrite, B-tree index, or statistics change. The advisor
checks the suggestion on isolated database clones, records an evidence card,
and requires approval before applying it. Applied changes can be monitored and
reverted on regression. Rewrite correctness is labelled **tested**, never proven.

## Current status

Implementation through Step 17 includes the verification pipeline, REST/SSE API,
and frontend: start runs, watch progress, inspect evidence, approve/block changes,
and view applied changes and available experiment results. Frontend redesign and
further research evaluation remain planned work.

Dedicated dataset analyses and the full RQ1–RQ5 experiment suite are still pending
(Steps 18–19). Existing simulations and pilot runs are not a completed study.
See [progress](docs/PROGRESS.md) and [deferred investigations](docs/POST_21_FOLLOWUPS.md).

## Verification flow

```text
Observe → Propose → Filter → Equivalence → Race → Guard → Decide
                                                    ↓
                          Evidence → Approval → Apply → Monitor / Revert
```

- **Plan saturation:** stop sampling when new proposals stop producing new plans.
- **Equivalence:** test rewrites against the original query, including time zones
  and generated counterexamples.
- **Safe racing:** eliminate clearly weaker candidates early.
- **Non-inferiority guard:** protect order transactions under normal, 5×, 10×,
  and 20× configured order-arrival rates.
- **Decision:** prefer a sufficiently good verified rewrite, otherwise a guarded
  index, otherwise block.

The default tolerated slowdown is **δ = 5%**. PASS requires an upper confidence
bound below δ; FAIL indicates measured unacceptable slowdown; BLOCK means safety
has not been established. A configured traffic multiplier is not achieved throughput.

## Requirements and platform status

| Component | Required version / role |
|---|---|
| Python | 3.11, backend and measurement scripts |
| Node.js | 24, frontend development/build |
| Docker | Compose v2; PostgreSQL 16 and extensions are built by the project |
| Git and Make | Repository access and development commands |
| Java | Optional pinned Java 23 runtime for BenchBase |

| Platform | Development route | Validation status |
|---|---|---|
| macOS | Native Python/Node + Docker Desktop | Used for recorded project checks |
| Linux Mint | Native Python/Node + Docker Engine and Compose | Teammate setup pending validation |
| Windows | Ubuntu on WSL2 + Docker Desktop integration | Teammate setup pending validation |

Allow disk space for the seeded database and temporary clones. Larger datasets
and benchmarks need more resources than a basic UI check. The repository is
private; your GitHub account needs access before cloning.

## macOS setup

Install Git, Make, Python 3.11, Node.js 24, and Docker Desktop. Start Docker Desktop.
Run these commands in Terminal:

```sh
git clone https://github.com/Tornado025/LLM-Verified-DB-Performance-Advisor.git
cd LLM-Verified-DB-Performance-Advisor
cp .env.example .env
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
make up
docker compose ps
make seed
make api
```

**`make seed` replaces development databases.** Use it for initial setup or an
intentional reset, not every startup. Keep the API terminal running.

Open another terminal at the repository root:

```sh
cd frontend
npm ci
npm run dev
```

Open the [frontend](http://localhost:5173) and
[API documentation](http://localhost:8000/docs).

## Linux Mint setup

Install Git, Make, Python **3.11** with venv support, Node.js **24**, and Docker
Engine with the Compose v2 plugin. Check the actual versions; the distribution's
default Python/Node packages may differ from the required versions.

For Docker installation, use the distribution guidance in the
[official Ubuntu installation documentation](https://docs.docker.com/engine/install/ubuntu/).
Linux Mint is an Ubuntu derivative and is not officially supported by that Docker
installation guide; follow its derivative-specific caveats and use the matching
Ubuntu base release, not the Mint release name.

Confirm Docker is running and accessible from your user account:

```sh
python3.11 --version
node --version
git --version
make --version
docker compose version
docker info
```

Then, in a regular terminal:

```sh
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/Tornado025/LLM-Verified-DB-Performance-Advisor.git
cd LLM-Verified-DB-Performance-Advisor
cp .env.example .env
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
make up
docker compose ps
make seed
make api
```

Seeding replaces development databases. Keep this terminal running. In a second
terminal at the repository root:

```sh
cd frontend
npm ci
npm run dev
```

Open the [frontend](http://localhost:5173) and
[API documentation](http://localhost:8000/docs). This project setup still needs
validation on your Mint machine. BenchBase additionally needs the portability
change described below.

## Windows setup — WSL2

Use Ubuntu inside WSL2 for Python, Node, Git, Make, and project commands. Native
PowerShell development is not the documented backend workflow.

### Prepare WSL and Docker

In Administrator PowerShell:

```powershell
wsl --install -d Ubuntu
```

Restart if prompted, open Ubuntu, and complete its initial setup. Check that the
distribution uses WSL2 with `wsl --list --verbose` in PowerShell.
See [Microsoft's WSL installation guide](https://learn.microsoft.com/en-us/windows/wsl/install).

Install Docker Desktop for Windows, use its WSL2 backend, and enable Ubuntu under
**Settings → Resources → WSL Integration**. See
[Docker's WSL guide](https://docs.docker.com/desktop/features/wsl/).

### Install and run inside Ubuntu

Install Python **3.11** with venv support, Node.js **24**, Git, and Make **inside
Ubuntu**. A Windows installation of Python or Node is not a substitute for the
Linux environment. Verify:

```sh
python3.11 --version
node --version
npm --version
git --version
make --version
docker compose version
docker info
```

Keep the repository in the WSL filesystem, outside OneDrive:

```sh
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/Tornado025/LLM-Verified-DB-Performance-Advisor.git
cd LLM-Verified-DB-Performance-Advisor
cp .env.example .env
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
make up
docker compose ps
make seed
make api
```

Seeding replaces development databases. Keep this Ubuntu terminal running. Open
another Ubuntu terminal:

```sh
cd ~/projects/LLM-Verified-DB-Performance-Advisor/frontend
npm ci
npm run dev
```

Open the [frontend](http://localhost:5173) and
[API documentation](http://localhost:8000/docs) in your Windows browser.
Windows/WSL project validation is pending; BenchBase has an additional Java-path
limitation described below.

## Configuration and everyday use

Defaults in `.env.example` use local development credentials and
`LLM_PROVIDER=mock`, with no paid model calls. Keep secrets in ignored `.env`.
The host-based setup uses `PG_HOST=localhost`; Compose overrides it for the
containerized backend.

On later starts, activate `.venv`, start Docker, run `make up`, then `make api`
and `make web` in separate terminals. There is no need to seed again.

An alternative to the local API process is:

```sh
docker compose up -d --build backend
```

Do not run both API variants on port 8000. `make up` currently starts only the
database, and the frontend is started separately.

### First run

1. Open Overview and choose **Tune this query**.
2. Keep the provider set to Mock and review the policy, profiles, and budget.
3. Start verification and inspect its events and evidence card.
4. Approve only a selected change after reviewing the exact statement.

Advanced settings include a **Dry run** option. It stops after filtering and
equivalence and does not establish performance safety or allow application.
Rewrite approval registers SQL for application adoption; it does not replace
queries in another application automatically.

### Database lifecycle

- `qc_golden`: seeded template, closed to ordinary connections.
- `qc_live`: development database representing the live application.
- `qc_sbx_*`: disposable verification clones.

```sh
make seed SCALE=3    # replace development databases with a larger generated fixture
make reset-live      # replace only live from golden
make down            # stop Docker services, retain the data volume
```

**`make clean` deletes the PostgreSQL Docker volume.** Never connect directly to
golden for normal querying. Live changes go through the approval/apply/revert path.

## Tests and checks

From the repository root, with `.venv` active (Ubuntu terminal on Windows):

```sh
make test       # backend tests excluding DB and real-LLM tests
make lint       # ruff, format check, mypy
make test-db    # DB integration tests; requires running, seeded PostgreSQL
```

Integration tests exercise clones, equivalence, measurements, pipeline, and
apply/revert behavior. Some take substantial time or depend on host noise.

Frontend:

```sh
cd frontend
npm run typecheck
npm run test
npm run build
```

With the backend running, regenerate declarations after API changes using
`npm run gen:api`. Frontend checks also include real-API browser smoke tests,
keyboard approval/revert, responsive layouts, and Lighthouse accessibility.
Recorded checks are in `experiments/results/step17_ui_checks.json`.

### Noise simulation and equivalence

From `backend/` with the virtual environment active:

```sh
python -m app.simulate --trials 400 --seed 1234 --borderline --out ../experiments/results/rq2_sim.json
```

This uses synthetic observations and needs no DB or LLM. From the repository
root, run the DB-backed labelled rewrite evaluation with:

```sh
python backend/scripts/equivalence_report.py
```

The equivalence report uses disposable databases, generated counterexamples, and
multiple time zones. Simulation results are separate from real DB measurements.

## Quick-commerce workload and TPC-derived benchmarks

### Main quick-commerce workload

The primary evaluation database contains **generated commerce records**, not
research trace metadata. Eight report shapes run alongside protected order
transactions. Profiles configure normal, 5×, 10×, and 20× order rates, including
hot-product contention.

With PostgreSQL seeded and `.venv` active, from the repository root:

```sh
python backend/scripts/calibrate_rate.py
python backend/scripts/validate_workload_aa.py
```

Calibration writes `backend/data/calibration.json`; review it and set the
recommended `BASE_RATE_TPS` in `.env`. Recalibrate after hardware or scale changes.
A/A compares identical clones to assess measurement reliability and can take
tens of minutes. Inconclusive evidence never becomes a safety PASS.

### BenchBase CH-benCHmark

The separate BenchBase evaluation combines **TPC-C-derived order transactions**
with **TPC-H-style analytical queries** on shared tables. It is not a certified
TPC-C/TPC-H result, nor a standalone official TPC-H evaluation.

Follow [the BenchBase setup guide](benchbase/README.md) for the pinned source
revision, Java **23** build, distribution, and `BENCHBASE_JAR` configuration.
Benchmark source/build outputs are not included in a fresh clone.

**Platform limitation:** the adapter currently hard-codes the recorded macOS ARM
Java executable. Mint and Windows/WSL require a Linux Java 23 installation and a
portable Java-path change before these adapter commands will work. Setting
`JAVA_HOME` alone does not currently fix the adapter. Main application setup and
mock/unit tests do not require BenchBase.

After completing the BenchBase setup and resolving the platform path if needed:

```sh
PYTHONPATH=backend python -c "from app.benchbase import load_ch; load_ch('qc_ch_golden', 10)"
python backend/scripts/benchbase_smoke.py
```

Loading refuses an existing target. Smoke scenarios use disposable clones,
separate from quick-commerce live data. Results are recorded in
`experiments/results/ch_smoke.json`; raw logs remain ignored.

Recorded normal and 5× runs had no unexpected SQL errors. Higher-load scenarios
reported PostgreSQL predicate-lock shared-memory errors; the cause is not yet
confirmed. They are not error-free performance validation. See
[deferred investigations](docs/POST_21_FOLLOWUPS.md).

## Research datasets and their testing role

Research datasets complement the evaluation; they are not combined into one
commerce database or directly replayed against the quick-commerce schema.

| Dataset | Inspected contents | Role in the research |
|---|---|---|
| Redset | Anonymized execution metadata; no SQL text | Arrivals, repetition, read/write mix, runtimes, workload drift |
| Snowset | Execution timings and resource metadata; no SQL text | Burstiness, duration, and concurrency analysis |
| SQLShare | SQL, views, heterogeneous table rows | SQL-pattern and rewrite-opportunity analysis |
| SEDE | SQL, descriptions, schema metadata | Query-pattern analysis and example selection |
| Downloaded SkyServer bundle | Numeric/binary benchmark material | Further format/source validation; not the expected SQL log |

Redset and Snowset are traces, not executable transaction generators like
BenchBase. SQLShare/SEDE queries need dialect/schema adaptation; their pairs are
not automatically equivalence ground truth. Dataset analysis will support
experimental workload choices and expand the labelled rewrite evaluation.
See [the inspected dataset gap report](docs/dataset_gap_report.md).

### Storage on all platforms

The expected, Git-ignored location is `backend/data/external/`. Raw data is not
included in Git or application images. A fresh clone does not contain the local
dataset symlink or downloads; obtain the documented source copies separately.
Respect each source's licence and retain verification/provenance records.

Keep large datasets outside iCloud/OneDrive. On macOS, Mint, or inside WSL, if the
expected path does not already exist, link an existing populated directory:

```sh
ln -s "$HOME/datasets/db-performance-advisor-external" backend/data/external
```

Use your actual directory and capitalization. This creates a link, not a download.
Existing local copies include a `VERIFICATION.md` file. Installing datasets is
not required for starting the app or running ordinary unit tests.

The current offline inventory can inspect local data:

```sh
python backend/scripts/analyse_inventory.py --dataset all --sample-rows 0
```

This full scan can be expensive and rewrites the inventory/gap report. It does
not import research data into PostgreSQL or test every query through the advisor.
Dedicated Step 18 analyses and the complete Step 19 runner remain pending.
**`make experiments` is a reserved target whose script is not implemented yet.**

## LLM providers

Use `LLM_PROVIDER=mock` for free, deterministic development. For explicit
OpenRouter calls, configure `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, and
`LLM_PROVIDER=openrouter` in `.env`. Calls consume provider credits. Ollama is
also supported through `OLLAMA_URL`, `OLLAMA_MODEL`, and `LLM_PROVIDER=ollama`.

Real API tests use the `llm` marker and require `RUN_LLM_TESTS=1`; routine tests
mock network calls. Never commit keys or print them in logs. LLM text is untrusted
and goes through safety screening before database evaluation.

## Research questions

| RQ | Question |
|---|---|
| RQ1 | When can a verified rewrite replace an index, and what write cost does it avoid? |
| RQ2 | How do equivalence and guard error rates change under noise? |
| RQ3 | Are buffer reads steadier signals than wall-clock time? |
| RQ4 | How stable are query shapes, and how does verified speedup grow with samples? |
| RQ5 | How often does a normal-day PASS become a flash-sale FAIL? |

## Troubleshooting

| Problem | Check |
|---|---|
| Frontend unavailable | Start `npm run dev` in `frontend/`; use port 5173. |
| API unavailable | Start `make api` or the Docker backend; use port 8000. |
| DB unavailable | Start Docker, run `make up`, inspect `docker compose ps`. |
| Missing tables | Run the initial development seed deliberately. |
| Missing pytest/ruff/uvicorn | Activate `.venv` and install backend requirements. |
| Wrong Python or Node | Check versions inside the terminal/environment running the project. |
| Docker unavailable in WSL | Check Docker Desktop and Ubuntu WSL integration. |
| Empty experiment chart | Its result file may not have been generated yet. |
| Guard BLOCK | Inspect bounds, samples, noise, and budget; do not bypass the guard. |
| BenchBase Java error | Check pinned Java 23, distribution, and the adapter's platform-specific path. |

## Documentation

- [Project architecture and working rules](AGENTS.md)
- [Shared contracts](docs/steps/00_contracts.md)
- [Progress and measured results](docs/PROGRESS.md)
- [API guide](docs/api.md)
- [Frontend guide](frontend/README.md)
- [BenchBase guide](benchbase/README.md)
- [Dataset gap analysis](docs/dataset_gap_report.md)
- [Deferred investigations](docs/POST_21_FOLLOWUPS.md)

## Licence

Project code is licensed under [MIT](LICENSE). Datasets and third-party benchmark
components retain their respective licences.
