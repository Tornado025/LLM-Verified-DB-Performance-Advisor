# Step 21 — README, demo, paper skeleton, reproducibility, release

**Goal (end state):** the repository is publishable: a README that gets a stranger to a working demo in 15 minutes, a 5-minute demo script and recorded backup video,
a paper/poster skeleton mapped to the RQs, a reproducibility checklist, a license/citation file, and a tagged release `v1.0` with a frozen results bundle.

**Prerequisites:** steps 01–20.

## 21.1 `README.md` (structure)

1. **Title + one-line pitch:** "DB Performance Advisor — let an LLM suggest database fixes, then verify them before they touch a flash-sale store."
2. **Why:** the problem in 5 lines with the three headline numbers from the literature (32 % wrong rewrites, 175 min validation, etc.) *only if you re-verified the sources*; otherwise omit numbers.
3. **What it does:** the pipeline diagram (reuse AGENTS.md section 7) and a screenshot of the evidence card with CI bars.
4. **Quick start (≤ 10 commands):** prerequisites; `cp .env.example .env`; `make up`; `make seed`; backend + frontend start; open `http://localhost:5173`; run a dry run with the mock LLM.
5. **Using a real LLM:** OpenRouter env vars; pin model; cost note.
6. **Docker storage and disk budget:** named volume, 3–5× rule, `docker compose down -v` warning.
7. **Experiments:** how to reproduce each figure/table (`make experiments`), expected runtime.
8. **Project layout** (tree) and a pointer to `docs/steps/`.
9. **Safety model:** what the allowlist and gates guarantee and **do not** guarantee (testing ≠ proof; human approval always).
10. **Limitations:** synthetic data (no public quick-commerce order DB), single node, PostgreSQL 16 only, laptop noise, Python workload runner limits, 3 rewrite classes.
11. **Citing / License / Acknowledgements** (RVCE EL project, SDG 9 alignment: 9.4, 9.1, 9.5, supporting 9.b).

## 21.2 Demo (5 minutes) — `docs/demo_script.md`

Pre-demo checklist: `make up`, data seeded, backend + frontend running, mock LLM (deterministic) or a pre-warmed real model, screen resolution 1080p, notifications off, a **recorded backup video** ready.
Script (time-boxed):
1. (0:00) Overview page: slow `recent_orders` — baseline seconds, plan "Seq Scan".
2. (0:30) New run → select `recent_orders`, `delivered_yesterday`, `top_products_store`; keep δ = 5 %; profiles all; start.
3. (1:00) Stepper: propose (plan saturation message), filter (one skipped, one invalid DDL rejected), equivalence (the wrong time-zone rewrite rejected with the Kolkata counterexample).
4. (2:00) Race + guard: show a CI bar crossing the δ line on a flash profile; the verdict-flip banner ("passes on a normal day, fails at 10×").
5. (3:00) Decision: selected index/rewrite with evidence card; click **Approve and apply**; show `CREATE INDEX CONCURRENTLY`.
6. (3:30) Applied page: start flash traffic; (pre-staged) planted bad index auto-reverted — show the window table.
7. (4:15) Experiments: RQ2 chart — MARS flat while single-run climbs; one sentence on the cost (false blocks).
8. (4:45) Close: three novelties + what it does not claim.
Rehearse twice; keep the backup video under 3 minutes.

## 21.3 Paper / poster skeleton (`docs/paper/`)

IEEE conference format. Sections ↔ artefacts:
| Section | Content | Source |
|---|---|---|
| Abstract | problem, approach, 3 results, limitation | `summary.md` |
| 1 Introduction | LLM tuning is useful but unstable; validation is expensive and noise-blind; flash sales change the answer | AGENTS.md, Part 1 notes |
| 2 Background & related work | LLM rewrite/index tuning; Azure auto-indexing; equivalence checking; noise-aware benchmarking; flash-sale databases (X-Engine, PolarDB-X hotspot docs) | literature notes (re-verify every citation) |
| 3 Problem & threat model | δ, protected transactions, fail-closed, human approval | contracts |
| 4 Method | pipeline, plan saturation, safe racing, non-inferiority guard, rewrite-or-index, equivalence tiers | steps 05, 09–12 |
| 5 Implementation | PostgreSQL 16, HypoPG, sandboxing, workload runner, BenchBase | steps 07, 11 |
| 6 Evaluation | setup (env.json), datasets (synthetic + CH-benCHmark), RQ1–RQ5 results, baselines, ablations | `experiments/results/` |
| 7 Discussion | what the verdicts cost, false blocks, flash-sale flips, limitations (synthetic data, single node, noise) | |
| 8 Conclusion & future work | drift monitoring, knobs, joint rewrite+index, cheaper clones (COW/ZFS), more rewrite classes | |
Figures: pipeline, CI-bar illustration, RQ2 chart, RQ5 flip chart, guard grid, speedup-vs-k, equivalence per-tier bars, UI screenshots.
Rule: **every number in the text must trace to a result file**; keep a `docs/paper/numbers.md` mapping sentence → file → key.
Claims to avoid: "first", "proves", "guarantees" (use "tested", "bounded with confidence level"), "nobody reports cost". State clearly that no public quick-commerce order database exists and the workload is synthetic and calibrated.

## 21.4 Reproducibility checklist
- [ ] `docker compose up -d db && make seed` recreates the databases deterministically (seed, scale printed)
- [ ] `make experiments` regenerates all result JSON from scratch; runtime documented
- [ ] Software versions pinned (`requirements.txt` frozen, `package-lock.json`, Docker image tags)
- [ ] `env.json` committed with each result bundle
- [ ] LLM model slug, temperature and date recorded; raw LLM outputs cached in `experiments/results/raw/llm_cache.jsonl` (so results can be re-evaluated without paying again)
- [ ] Random seeds listed in README
- [ ] Known sources of non-determinism documented (host noise, LLM backend routing, autovacuum timing)

## 21.5 Final hardening pass
* Re-run the full test suite; run `make experiments --quick`; run the demo script end to end on a clean clone.
* Security review: allowlist tests, no secrets in git history (`git log -p | grep -i key`), `.env` ignored.
* Accessibility pass on the UI; update screenshots.
* Delete leftover `qc_sbx_*` databases; verify `docker compose down -v && make up && make seed` works from scratch.

## 21.6 Release
1. `CHANGELOG.md` (steps → features), `CITATION.cff`, `LICENSE`.
2. Tag `v1.0`; create a GitHub release with attached `results_bundle.zip` (all result JSON + env.json + figures) and `demo.mp4` (or link).
3. Repository description: "LLM-driven verification framework for query and index optimization on quick-commerce PostgreSQL under flash-sale workloads." Topics: `postgresql`, `llm`, `database-tuning`, `query-optimization`, `fastapi`, `react`.
4. Archive (optional): Zenodo DOI for the results bundle.

## 21.7 Final acceptance (project definition of done)

- [ ] A stranger can follow the README to a working demo in ≤ 15 minutes (test with a teammate)
- [ ] All step acceptance checklists (01–20) ticked in `docs/PROGRESS.md`
- [ ] Evidence cards show all four verdict types: selected rewrite, selected index, failed guard, rejected equivalence
- [ ] RQ2 chart exists from simulation **and** real database; RQ5 verdict-flip result exists (even if the answer is "rarely")
- [ ] The three novel techniques each have an ablation result
- [ ] Release `v1.0` tagged; backup demo video recorded
