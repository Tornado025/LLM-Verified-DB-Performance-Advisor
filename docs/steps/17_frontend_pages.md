# Step 17 — Frontend pages and components

**Goal (end state):** all five pages work against the real API (with the mock LLM): you can start a run, watch the pipeline live,
inspect an evidence card with CI bars and the guard grid, approve/block, watch applied changes and revert, and read the experiment charts.
A keyboard-only user can complete a full run → approve flow.

**Prerequisites:** step 16, backend (15) running with `LLM_PROVIDER=mock`.

## 17.1 Shared components

| Component | Props | Behaviour |
|---|---|---|
| `Badge` | `tone: "pass"|"fail"|"hold"|"info"|"muted"`, `children` | icon + text; ✓ ✕ ◐ ⓘ |
| `Chips` | list of `{label, ok}` | health chips |
| `Button` | variant primary/secondary/danger, `loading`, `disabled` | spinner only when loading; `aria-busy` |
| `Field` | label, hint, error, children | associates `label`/`aria-describedby` |
| `EmptyState` | title, body, action | for no data |
| `ErrorBanner` | `error: ApiError` | shows code + message + "Details" disclosure |
| `Skeleton` | lines | loading placeholder |
| `Toast` | via context | success/error, 5 s, aria-live |

## 17.2 `CIBar` (signature component)

SVG, responsive width (`viewBox` + `width: 100%`), height 28 px.
Props: `lower`, `upper`, `mean`, `delta`, `domain?`, `label?` (accessible name), `state?`, `showAxis?`.
* Draw a thin horizontal baseline; a vertical **δ line** (dashed, label "δ 5%" on first instance or when `showAxis`), a vertical **zero line**.
* The interval is a rounded bar from `lower` to `upper`; a dot at `mean`. Infinite bounds draw an arrow tip at the edge.
* Colour from `verdictOf`: PASS (`--pass`), FAIL (`--fail`), HOLD (`--hold`); also a 10-px icon at the right.
* `role="img"`, `aria-label` like "place_order, flash10: +1.2% (95 % bound −0.4% to +3.1%), within δ 5%".
* `domain` defaults from `clampDomain` over all bars in the same grid so scales are comparable within one view.
* Tooltip on hover/focus: n, mean, bounds, state, metric.
* Unit tests: render PASS/FAIL/HOLD, infinite bound, accessibility label text.

## 17.3 `GuardGrid`

Table: rows = transaction types, columns = profiles (`normal`, `flash5`, `flash10`, `flash20`). Each cell: `CIBar` (compact, no axis) + text `+1.2%` + state icon.
* Header row shows the profile with its multiplier ("flash10 · 10× orders").
* A cell with state `CONTINUE/BLOCK` is labelled "not proven (n=18)". Missing cell (profile not run) is "—".
* Highlight the **verdict-flip** pattern: if `normal` is PASS and any flash column is FAIL/BLOCK, add a banner above the grid: "Passes on a normal day, fails under flash-sale load" (this is RQ5 made visible).
* Below the grid: a one-line summary "Worst bound: place_order @ flash20 = +7.3% (limit 5%)".
* Keyboard: grid is a real `<table>`; cells focusable with tooltips.

## 17.4 `Stepper`

Steps: Observe · Propose · Filter · Equivalence · Race · Guard · Decide · Apply (Apply shows only after approval).
State per step derived from events (`pending`, `running`, `done`, `skipped`, `error`); when a step is running show the latest event message and a small progress hint
(e.g. "sample 3 of ≤10 · 2 new plans", "round 4 · 3 candidates alive", "flash10 · block 7/40 · worst bound +6.1%").
A numbered sequence is appropriate here (it is a real ordered process). Reduced motion: no animation, just state changes.

## 17.5 `EventLog`
Virtualised list (or capped to the last 300 rows), monospace, level icon, relative time; filters (All / Warn+Error / Results); "Pause autoscroll" toggle; `aria-live="polite"` on the container but **rate-limited** (announce only `warn/error/result`).

## 17.6 `CandidatesTable`
Columns: Kind (badge), Candidate (truncated SQL, expandable `<details>`), Status (badge with tooltip reason), Speedup (CIBar compact in negative domain), Equivalence (label + tier + weak warning), Guard (✓/✕/◐ + worst bound), Actions.
Sorting by status group then speedup; row click selects the candidate for the evidence panel. Status → tone mapping: `selected` pass; `rejected_*`, `failed_guard` fail; `blocked_unproven`, `dropped_race` hold; `skipped_*` muted; `proposed` info.

## 17.7 `PlanDiff`
Two-column read-only view of simplified plan trees (node type, relation, index name, rows estimate) with changed nodes highlighted; fingerprint shown as `ab12cd` with "same plan" / "different plan" badge. Use `<pre>` with semantic list markup for the tree; copy-SQL button for the candidate.

## 17.8 `EvidenceCard` (for the selected candidate and the decision)

Sections (collapsible `<details>`, all open by default for the selected candidate):
1. **Decision banner** — action (Apply rewrite / Apply index / Block) with reason; Approve and Block buttons (`Approve and apply` / `Block this change`); weak-rewrite confirmation checkbox.
2. **What changed** — original vs candidate SQL (side by side, with sqlglot-free simple line diff), rationale.
3. **Plan** — `PlanDiff`.
4. **Equivalence** — tier table (0–3), rewrite class, counterexample database count, non-vacuous fraction, and the **counterexample viewer** (tz, rows) when rejected.
5. **Report speed** — CIBar domain negative to 0, n runs, metric.
6. **Order-processing guard** — `GuardGrid`.
7. **Budget** — seconds, paired runs, LLM calls/cost, clones.
Approve flow: `Approve and apply` ⇒ confirm dialog (focus-trapped) showing the exact DDL/`CONCURRENTLY` statement and that it will run on `qc_live` ⇒ POST approve ⇒ toast + link to Applied.

## 17.9 Pages

**Overview** — health summary; table of observed slow statements (`/api/observe`, tags shown as chips); cards for the 8 report shapes (question, baseline time if known, "Tune this query" → `/new?shape=...`); note when `pg_stat_statements` unavailable.

**New run** — form with sections: Shapes (multi-select with select-all), Verification (δ slider 1–20 % with numeric input, α, metric radio), Load profiles (checkboxes with multiplier), Budget (minutes), LLM (provider select, "mock" default, model shown read-only from settings), Advanced (k_max, patience, prefer ratio, seed, workload custom/ch, noise, dry run). Live estimate: "Estimated guard time per index candidate ≈ X min" (from `estimate_guard_seconds` via `/api/settings`). Validation inline; submit → `/runs/:id`.

**Runs** — list (status, shapes, started, duration, decision summary), filters, empty state.

**Run detail** — header (job id, status badge, cancel button), `Stepper`, two-column layout: left `EventLog`, right candidates for the selected shape (shape tabs if multiple) + `EvidenceCard`. Live updates via `useEventStream`; after `end` refetch evidence. Show a "No candidate proven safe" empty-success state clearly (blocking is a valid outcome).

**Applied** — table of applied changes (kind, DDL/rewrite, applied at, state badge, last window); detail drawer with baseline vs current mean per statement group (Recharts line chart of window means with a δ band) and Revert button (confirm). Live traffic controls (start/stop generator, profile select) with a warning banner when running.

**Experiments** — tabs: RQ2 (line chart: x = noise 0/5/10 %, y = false-apply %, one line per decider, DB Performance Advisor highlighted; second chart false-block and mean runs; toggle "simulation / real"), RQ5 (bar chart: verdict-flip rate by multiplier; table by candidate kind), RQ4 (shape counts per day, speedup vs k with saturation marker), Equivalence (table: false-accept per tier). Each chart has a text table alternative and download-CSV button. If a result file is missing show an `EmptyState` explaining which command creates it.

## 17.10 Copy and microcopy rules
Sentence case; specific verbs ("Start verification", "Approve and apply", "Revert change"); errors say what failed and what to try ("Could not reach PostgreSQL. Start it with `make up` and reload."); never "Oops"; units always shown (ms, %, tps).

## 17.11 Responsive and accessibility acceptance
* Works from 360 px to 1600 px; wide tables scroll inside their container; nav collapses.
* Lighthouse accessibility ≥ 95 on Overview, Run detail, Experiments.
* Full keyboard path: Overview → "Tune this query" → New run → Start → Run detail → select candidate → Approve (dialog) → Applied.
* Contrast ≥ 4.5:1 for text in both themes (check tokens).

## 17.12 Tests
* Component tests: `CIBar`, `GuardGrid` (flip banner appears for the planted pattern), `Stepper` state derivation from a fixture event list, `CandidatesTable` status/tone mapping, `EvidenceCard` approve dialog flow with mocked API.
* One Playwright (optional) e2e against the running stack with the mock LLM: start dry-run job ⇒ stepper completes ⇒ evidence visible.
* Fixture files in `src/test/fixtures/{events.json,evidence.json,experiments.json}` generated from real backend output.

## 17.13 Done checklist
- [x] All pages usable against the real API; screenshots saved to `docs/paper/figures/ui_*.png`
- [x] `npm run build && npm run test` green; Lighthouse notes in `PROGRESS.md`
- [ ] Commit `step 17: frontend pages`
