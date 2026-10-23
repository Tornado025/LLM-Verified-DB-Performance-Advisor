# Step 16 — Frontend foundation (Vite + React + TypeScript)

**Goal (end state):** `npm run dev` serves an app with routing, a typed API client generated from the backend's OpenAPI schema,
an SSE hook, global design tokens and layout shell, and a passing `npm run build` + `npm run test`. No pages are complete yet (step 17).

**Prerequisites:** step 15 (OpenAPI available). Node 20+.

## 16.1 Project creation

```
cd frontend
npm create vite@latest . -- --template react-ts
npm i react-router-dom recharts
npm i -D vitest @testing-library/react @testing-library/jest-dom jsdom openapi-typescript
```
`vite.config.ts`: `server.proxy = { "/api": "http://localhost:8000" }` (so no CORS during dev), `test.environment = "jsdom"`, `test.setupFiles = "./src/test/setup.ts"`.
`package.json` scripts: `dev`, `build`, `preview`, `test`, `gen:api` = `openapi-typescript http://localhost:8000/openapi.json -o src/api.d.ts`, `typecheck` = `tsc --noEmit`.
`frontend/Dockerfile`: multi-stage (node build → nginx serve with `/api` proxy to `backend:8000`, SSE-safe: `proxy_buffering off; proxy_read_timeout 1h;`).

## 16.2 Design direction (decide once, apply everywhere)

**Purpose:** an operations console for people **without a DBA** who must decide quickly whether an AI-suggested database change is safe.
**Tone:** calm, precise, trustworthy; "lab instrument" rather than "marketing dashboard". The **memorable element** is the **CI bar** (section 17.2): every verdict is drawn as a confidence interval against a vertical δ line.

Tokens (CSS custom properties in `src/styles/tokens.css`; light theme primary, dark via `prefers-color-scheme`):

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#EEF1F3` | `#0F1519` | page |
| `--surface` | `#FFFFFF` | `#172027` | cards |
| `--surface-2` | `#E3E8EB` | `#1F2A32` | table stripes, wells |
| `--ink` | `#14202B` | `#E6EDF1` | text |
| `--ink-2` | `#51606C` | `#9BAAB5` | secondary text |
| `--line` | `#CBD3D9` | `#2C3A44` | borders |
| `--pass` | `#0E7C5A` | `#3DCF9B` | PASS |
| `--fail` | `#B3261E` | `#FF7A70` | FAIL / harmful |
| `--hold` | `#B7791F` | `#F2B75B` | undetermined / BLOCK / weak |
| `--accent` | `#0E5E7C` | `#5CC3E8` | links, focus, primary action |
Never rely on colour alone: every state also has an icon (✓ ✕ ◐) **and** a text label.

Typography (load from Google Fonts with `font-display: swap` and system fallbacks):
* Headings: **"Bricolage Grotesque"** (600/700), fallback `system-ui, sans-serif`.
* Body/UI: **"Instrument Sans"** (400/500/600), fallback `system-ui, sans-serif`.
* Numbers/code/SQL: **"JetBrains Mono"** with `font-variant-numeric: tabular-nums`.
Scale: 12/14/16/20/28/40 px; body 15–16 px; line-height 1.5; max prose width 70ch.
Spacing scale: 4/8/12/16/24/32/48. Radius 6 px (cards 10 px). Borders over shadows; one soft shadow for the evidence card.
Motion: ≤ 150 ms ease-out for hover/focus and a 250 ms reveal for the stepper; **respect `prefers-reduced-motion`** (disable transitions).
Accessibility: visible `:focus-visible` outline 2 px `--accent`; min target 40 px; labels for every input; `aria-live="polite"` for the event log; tables with `<th scope>`.

## 16.3 Source layout

```
src/
  main.tsx  App.tsx  api.ts  api.d.ts(generated)  types.ts  styles/{tokens.css,base.css}
  hooks/{useApi.ts,useEventStream.ts,useJob.ts,usePolling.ts}
  lib/{format.ts,ci.ts,classnames.ts}
  components/ (Stepper, EventLog, CandidatesTable, EvidenceCard, GuardGrid, CIBar, PlanDiff, Chips, Badge, Button, Field, EmptyState, ErrorBanner, Skeleton, Toast)
  pages/ (Overview, NewRun, Runs, RunDetail, Applied, Experiments, NotFound)
  test/setup.ts
```

## 16.4 `api.ts`

* `async function api<T>(path, init?)`: prefixes `/api`, JSON in/out, throws `ApiError {code, message, detail, status}` parsed from the error envelope; network failure ⇒ `ApiError("network")`.
* Typed wrappers: `getHealth`, `getShapes`, `getProfiles`, `getObserve`, `createJob`, `listJobs`, `getJob`, `cancelJob`, `getEvidence`, `getCandidate`, `approve`, `blockCandidate`, `listApplied`, `revert`, `traffic`, `getRq2/4/5`.
* `types.ts` re-exports generated types plus UI helpers (`StateName`, `CandidateStatus`).

## 16.5 Hooks

* `useEventStream(jobId)`: opens `EventSource('/api/jobs/{id}/events')`; keeps events in state (cap 2000), de-duplicates by id, reconnects with `Last-Event-ID` automatically (browser) and falls back to polling `/events.json` after 3 failures; closes on `end`. Returns `{events, status, connected, error}`.
* `useJob(jobId)`: polls `getJob` every 2 s while non-terminal; stops when terminal.
* `usePolling(fn, ms, enabled)`: generic; pauses when the tab is hidden (`document.visibilityState`).
* `useApi(fn, deps)`: `{data, error, loading, reload}` with abort on unmount.

## 16.6 Layout shell (`App.tsx`)

* Top bar: product name "DB Performance Advisor", health chips (Postgres, HypoPG, pg_stat_statements, LLM), theme follows system.
* Left nav (collapses to top on narrow screens): Overview · New run · Runs · Applied · Experiments.
* `<main>` max width 1280 px; page title area with breadcrumb.
* Global `ErrorBanner` for health failures: "PostgreSQL is not reachable — start it with `make up`".
* Routes: `/`, `/new`, `/runs`, `/runs/:jobId`, `/applied`, `/experiments`, `*`.
* Toasts for approve/revert results (aria-live).

## 16.7 `lib/ci.ts` helpers (used by CIBar and tests)

```ts
export function relToX(rel: number, domain: [number, number], width: number): number
export function clampDomain(bounds: {lower:number; upper:number}[], delta: number): [number, number]   // always includes 0 and delta, padded 10%
export function verdictOf(b: {lower:number; upper:number}, delta: number): "PASS"|"FAIL"|"HOLD"
export function fmtPct(x: number, digits?: number): string      // 0.0123 -> "+1.2%"
```
`verdictOf`: `upper < delta` ⇒ PASS; `lower > delta` ⇒ FAIL; else HOLD.

## 16.8 Tests (vitest)
* `ci.test.ts` for each helper (edge: infinite bounds clamp; equal bounds).
* `useEventStream` with a fake `EventSource` (dedupe, reconnect fallback).
* `api.test.ts`: error envelope parsing, network error.
* Smoke test rendering `<App/>` with mocked fetch: nav present, health chips render.

## 16.9 Done checklist
- [ ] `npm run build`, `npm run typecheck`, `npm run test` pass
- [ ] `npm run gen:api` produces types with the backend running
- [ ] Tokens and fonts applied; dark mode works; keyboard focus visible
- [ ] Commit `step 16: frontend foundation`
