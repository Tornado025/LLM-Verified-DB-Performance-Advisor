# AGENTS.md — DB Performance Advisor (project overview for Codex)

> This file is the entry point. Read it fully, then read `docs/steps/00_contracts.md`, then work through
> `docs/steps/01_*.md` … `21_*.md` **in order**. Do not skip ahead. Each step file ends with an
> acceptance check; a step is finished only when every check passes.

## 1. What this project is (one paragraph)

**DB Performance Advisor** (research acronym **MARS** — *Make AI Recommendations Safe*) is an
LLM-driven **verification framework** for slow-query tuning on **quick-commerce (dark-store) PostgreSQL
databases** that face **flash-sale** load. An LLM proposes fixes for a slow recurring **report query**
(rewrite the SQL, add an index, or refresh statistics). The system does **not trust** the LLM. Every
suggestion is verified on isolated database clones before a human can apply it:

1. **Rewrites** must pass a tiered **equivalence check** (same rows, including across time zones).
2. **Indexes / statistics** must pass a **sandbox replay guard**: every order-processing transaction
   (place order, decrement stock, update status, …) must stay within **δ = 5 %** of baseline latency,
   under a **normal-day** profile **and** **flash-sale** profiles (5×, 10×, 20× order rate, hot products).
3. The cheapest verified fix wins: a verified rewrite (zero write cost) if good enough, else a guarded
   index, else **block**.
4. A human approves; the change is applied, **monitored**, and **auto-reverted** on regression.
5. Every decision produces an **evidence card** (what was proposed, measured, decided, and why).

The research output is a **measured study** (RQ1–RQ5), not just a tool: validation cost, how often the
verdicts are wrong under noise, and how often a normal-day "pass" flips to "fail" under flash-sale load.

## 2. The three new techniques (the novelty — implement exactly)

| # | Technique | One-line definition | Where |
|---|---|---|---|
| 1 | **Plan-saturation stopping** | Keep sampling the LLM until new samples stop producing new query plans (not a fixed best-of-k) | `propose.py` |
| 2 | **Safe racing** | Test candidates in rounds; drop clear losers or clearly unsafe ones early; spend the budget on survivors | `race.py` |
| 3 | **Non-inferiority guard** | Pass only if the *upper confidence bound* of the relative slowdown is below δ; too few runs ⇒ **block** (fail closed) | `stats.py`, `guard.py` |

Plus: the **rewrite-or-index decision** (`decide.py`) and the **flash-sale verdict-flip measurement** (RQ5).

## 3. Research questions (every feature exists to answer one of these)

| RQ | Question | Primary output |
|---|---|---|
| RQ1 | How often can a verified rewrite replace an index, and what order-processing cost does that save? | table + chart |
| RQ2 | Wrong-result rate of each equivalence tier **and** false-apply/false-block rate of the guard at 0/5/10 % injected noise vs simple rules | the **RQ2 chart** (noise vs false-apply, MARS flat) |
| RQ3 | Are buffer reads steadier decision signals than wall-clock time? | verdict-flip counts |
| RQ4 | How many query shapes does the (text-to-SQL) workload produce, how stable, and how does best verified speedup grow with LLM samples k? | shape stats + speedup-vs-k curve |
| RQ5 | How often does a change that passes on a normal day fail under a flash-sale profile, at what spike size? | verdict-flip rate vs multiplier |

## 4. Scope

**In scope:** PostgreSQL 16 (single node, Docker); B-tree indexes (plain, partial, expression, covering);
`ANALYZE`; `CREATE STATISTICS`; rewrites of exactly **3 classes** — (1) index-friendly predicates
(function on a column → range), (2) correlated subquery → join, (3) removing redundant
`DISTINCT` / `ORDER BY` / joins.

**Out of scope (never build):** schema redesign, partitioning, sharding, caches (Redis), config knobs
(`work_mem` …) except as a clearly-marked stretch, `DROP INDEX` suggestions from the LLM, NoSQL/columnar,
scraping Blinkit/Zepto/Instamart, claiming a rewrite is "proven" (label is `tested`, human approval always).

## 5. Tech stack

| Layer | Choice |
|---|---|
| DB | PostgreSQL 16 in Docker (named volume `pgdata`), extensions `pg_stat_statements`, `hypopg` |
| Backend | Python 3.11, FastAPI, psycopg 3, sqlglot, numpy/scipy, httpx, SQLite (metadata) |
| Frontend | React + Vite + TypeScript, React Router, Recharts, SSE (`EventSource`) |
| LLM | **OpenRouter** (OpenAI-compatible HTTP API) + Ollama (local) + **Mock** provider (default for dev/tests) |
| Benchmarks | Own quick-commerce workload runner **and** CMU **BenchBase** CH-benCHmark (TPC-C + TPC-H-style on shared tables) |
| Tests | pytest (unit + `@pytest.mark.db` integration), ruff, mypy (light), vitest for frontend |

## 6. Repository layout (target)

```
db-performance-advisor/
├── AGENTS.md  README.md  LICENSE  .gitignore  .env.example  Makefile  docker-compose.yml
├── docker/        postgres.Dockerfile  postgres.conf  init/01-extensions.sql
├── backend/
│   ├── Dockerfile  requirements.txt  pytest.ini  pyproject.toml(ruff/mypy)
│   ├── sql/       schema.sql  seed_helpers.sql
│   ├── scripts/   seed.py generate_questions.py build_rewrite_testset.py analyse_traces.py
│   │              rewrite_prevalence.py load_instacart.py run_experiments.py calibrate_rate.py env_report.py
│   │              noise_report.py demo_revert.py
│   ├── data/      questions.json rewrite_pairs.json profiles.json external/(git-ignored)
│   ├── app/       main.py api.py config.py pg.py store.py models.py errors.py shapes.py observe.py
│   │              llm/{base,openrouter,ollama,mock,prompts}.py safety.py propose.py plans.py
│   │              equivalence.py sandbox.py measure.py workload.py benchbase.py noise.py
│   │              stats.py guard.py race.py decide.py pipeline.py monitor.py simulate.py
│   └── tests/     test_*.py  conftest.py
├── benchbase/     README.md  configs/ch_{normal,flash5,flash10,flash20}.xml
├── experiments/   rq1_cost/ rq2_noise/ rq3_metrics/ rq4_shift/ rq5_flash/ baselines/ results/
├── frontend/      Dockerfile package.json tsconfig.json vite.config.ts index.html src/{pages,components}
└── docs/          steps/*.md  PROGRESS.md  evidence_card.schema.json  demo_script.md  paper/
```

## 7. The pipeline (what the code does end to end)

```
OBSERVE → PROPOSE (k-sampling, plan saturation) → FILTER (EXPLAIN/HypoPG; drop no-plan-change & duplicates)
   → EQUIVALENCE (rewrites only, tiers 0–3) → RACE (paired report-speed runs, early drops)
   → GUARD (indexes/stats: paired blocks of order transactions × {normal, flash5, flash10, flash20})
   → DECIDE (verified rewrite ≥ prefer_ratio × best? else first guarded index that passes? else BLOCK)
   → EVIDENCE CARD → human APPROVE → APPLY → MONITOR (pg_stat_statements windows) → AUTO-REVERT
```

Databases on one server: `qc_golden` (seeded; **no connections ever**, used as template), `qc_live`
(clone of golden = "production"), `qc_sbx_*` (short-lived sandbox clones).

## 8. Working rules for Codex (read carefully)

1. **Order matters.** Follow the step files by number. Do not start step N+1 until step N's acceptance
   commands pass. If a check cannot pass, fix the cause; do not weaken the check.
2. **Contracts are law.** Names, signatures, enums, env vars and JSON shapes in
   `docs/steps/00_contracts.md` must be used exactly. If you must change one, change the contract file
   in the same commit and fix every user.
3. **One commit per step** (message `step NN: <title>`). Append a dated entry to `docs/PROGRESS.md`
   (what was done, commands run, results, open issues).
4. **Fail closed.** Anywhere a safety decision is made (guard, equivalence, safety allowlist), uncertainty
   means **block / reject**, never pass.
5. **Never trust the LLM.** All LLM output goes through `safety.check` before anything touches a database.
   LLM text is data, never executed directly outside the allowlist.
6. **Never connect to `qc_golden`** except inside `seed.py` while seeding, and `sandbox.create_clone`
   (which uses the admin connection to a maintenance DB, not golden).
7. **Never run destructive SQL against `qc_live`** except through `monitor.apply/revert` after an explicit
   approval record exists.
8. **No hidden network calls in tests.** Default `LLM_PROVIDER=mock`. Tests marked `@pytest.mark.llm`
   (real API) are skipped unless `RUN_LLM_TESTS=1`.
9. **Determinism.** Every random process takes an explicit seed (`numpy.random.Generator`, never the
   global RNG). Log the seed in results.
10. **Honest numbers.** Every number in a result file must come from a logged run. Null results are
    reported. Never hard-code expected results.
11. **Secrets** only in `.env` (git-ignored). Never print API keys or DSN passwords in logs.
12. **Style:** Python 3.11, type hints, `ruff` (line length 100), no wildcard imports, docstrings on
    public functions, `logging` not `print` in the app.
13. **When unsure about a PostgreSQL/BenchBase/OpenRouter detail,** verify it against the tool's own
    documentation or by running a small experiment, and write the finding in `docs/PROGRESS.md`.

## 9. Glossary

| Term | Meaning |
|---|---|
| **δ (delta)** | Max tolerated slowdown of any protected order transaction (0.05) |
| **Shape** | Query template run with different parameter values; tuning targets shapes |
| **Protected transactions** | Order-processing transactions the guard must keep within δ |
| **Non-inferiority** | Proving "no worse than δ" (upper CI bound < δ), not proving "better" |
| **False apply / false block** | Letting a harmful change through / rejecting a harmless one |
| **Plan saturation** | New LLM samples stop yielding new plan fingerprints |
| **HOT update** | Heap-only-tuple update; skipped index maintenance; lost when an indexed column changes |
| **Common random numbers** | Identical arrival schedule/keys for A and B blocks to cancel variance |
| **Frozen clock (`as_of`)** | Report shapes use a fixed seeded timestamp instead of `now()` so data never goes stale |
| **Evidence card** | JSON record of everything measured and decided for one shape |

## 10. Step index (definite goal of each step)

| # | File | End-state goal |
|---|---|---|
| 00 | `00_contracts.md` | Shared names/signatures/enums/JSON defined (read-only reference) |
| 01 | `01_scaffold_environment.md` | `make up` starts Postgres(+extensions); `make test` runs an empty green suite |
| 02 | `02_schema_and_seeding.md` | `make seed` builds `qc_golden` + `qc_live` with verified skew and row counts |
| 03 | `03_backend_core.md` | config/pg/store/models/errors done; event store works; unit tests green |
| 04 | `04_shapes_and_transactions.md` | 8 report shapes + 5 order transactions run on `qc_live`; sampler + as_of tested |
| 05 | `05_stats_engine.md` | `stats.py` passes type-I-error and power tests |
| 06 | `06_simulation_rq2.md` | `python -m app.simulate` writes the RQ2 result JSON; MARS flat vs rules |
| 07 | `07_sandbox_measure_noise.md` | Clones, paired measurement, A/A noise report, noise injector all work |
| 08 | `08_plans_and_filter.md` | Plan fingerprinting + HypoPG filter drop duplicates/no-op candidates |
| 09 | `09_llm_safety_propose.md` | Providers, safety allowlist, plan-saturation proposer work with the mock |
| 10 | `10_equivalence.md` | Tiered equivalence rejects every trap in the labelled set |
| 11 | `11_workload_runner_benchbase.md` | Open-loop runner + profiles + calibrated rate; BenchBase configs run |
| 12 | `12_guard_race_decide.md` | Guard, racing and decision produce correct verdicts on planted cases |
| 13 | `13_pipeline_evidence.md` | A job runs end to end with the mock LLM and saves evidence cards |
| 14 | `14_apply_monitor_revert.md` | Approve → apply → monitor → auto-revert demonstrated on live traffic |
| 15 | `15_api_layer.md` | All REST + SSE endpoints implemented and tested |
| 16 | `16_frontend_foundation.md` | Vite app builds; design tokens, router, API client, SSE hook ready |
| 17 | `17_frontend_pages.md` | All 5 pages + components work against the real API |
| 18 | `18_datasets_analysis.md` | Redset/Snowset/SkyServer/SQLShare/SEDE analyses and the labelled rewrite set exist |
| 19 | `19_experiments.md` | RQ1–RQ5 result files + baselines reproduced by one command |
| 20 | `20_testing_ci.md` | Full test matrix + CI green |
| 21 | `21_docs_demo_release.md` | README, demo script, paper skeleton, tagged release `v1.0` |

## 11. Quick commands (once step 01 is done)

```
make up          # docker compose up -d db (+ backend/frontend when they exist)
make seed        # python backend/scripts/seed.py --scale 1
make test        # pytest -m "not db and not llm"
make test-db     # pytest -m db   (needs `make up`)
make api         # uvicorn app.main:app --reload --port 8000   (from backend/)
make web         # npm run dev     (from frontend/)
make experiments # python backend/scripts/run_experiments.py --all
```
