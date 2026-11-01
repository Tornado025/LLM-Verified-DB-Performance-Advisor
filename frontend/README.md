# Frontend verification console

With the backend running on port 8000:

```sh
npm ci
npm run gen:api
npm run dev
```

Open http://localhost:5173/ for the frontend. Port 8000 serves the backend API
and interactive API documentation. Vite proxies `/api` requests to that backend.

`npm run build`, `npm run typecheck`, and `npm run test` verify the frontend.
`src/api.d.ts` is generated from the backend's OpenAPI schema; regenerate it
after API changes. TypeScript 5.9 is pinned for generator compatibility.

The pages use the real API for observed statements, new verification runs, live
events, evidence cards, approvals, applied changes, traffic controls, and experiment
charts. Missing experiment files show an explicit empty state. Theme follows the
system preference. Charts include text tables and CSV downloads.

Accessibility audit summaries and remaining acceptance checks are recorded in
`experiments/results/step17_ui_checks.json` and `docs/PROGRESS.md`.

The multi-stage Dockerfile builds static assets and serves them through Nginx.
Its `/api` proxy expects a backend service named `backend` on the same Docker
network and disables buffering for event streams.
