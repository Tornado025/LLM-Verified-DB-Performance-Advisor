import { lazy, Suspense } from "react";
import {
  BrowserRouter,
  Link,
  NavLink,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { getHealth } from "./api";
import { useApi } from "./hooks/useApi";
import { usePolling } from "./hooks/usePolling";
import { Badge } from "./components/Badge";
import { ErrorBanner } from "./components/ErrorBanner";
import { ToastProvider } from "./components/Toast";
import "./styles/base.css";
import "./styles/pages.css";
import Overview from "./pages/Overview";
import NewRun from "./pages/NewRun";
import Runs from "./pages/Runs";
import RunDetail from "./pages/RunDetail";
const Applied = lazy(() => import("./pages/Applied"));
const Experiments = lazy(() => import("./pages/Experiments"));
const links = [
  ["/", "Overview", "◫"],
  ["/new", "New run", "+"],
  ["/runs", "Runs", "≡"],
  ["/applied", "Applied", "✓"],
  ["/experiments", "Experiments", "⌁"],
];
function Shell() {
  const health = useApi(getHealth);
  usePolling(async () => {
    health.reload();
  }, 15000);
  const location = useLocation();
  const title =
    links.find(([path]) => path === location.pathname)?.[1] ??
    (location.pathname.startsWith("/runs/") ? "Run detail" : "Not found");
  return (
    <>
      <header className="topbar">
        <Link className="brand" to="/">
          <span className="brand-mark" aria-hidden="true">
            ≋
          </span>
          DB Performance Advisor
        </Link>
        <div className="health" aria-label="Service health">
          {[
            ["Postgres", health.data?.postgres],
            ["HypoPG", health.data?.hypopg],
            ["pg_stat_statements", health.data?.pg_stat_statements],
            ["LLM", health.data?.llm.ok],
          ].map(([label, ok]) => (
            <Badge
              key={String(label)}
              state={ok === undefined ? "hold" : ok ? "pass" : "fail"}
            >
              {String(label)}
              {ok === undefined ? " · checking" : ""}
            </Badge>
          ))}
        </div>
      </header>
      <div className="workspace">
        <aside className="sidebar">
          <div>
            <div className="nav-label">Workspace</div>
            <nav aria-label="Main navigation">
              {links.map(([path, label, icon]) => (
                <NavLink key={path} to={path} end={path === "/"}>
                  <span className="nav-icon" aria-hidden="true">
                    {icon}
                  </span>
                  {label}
                </NavLink>
              ))}
            </nav>
          </div>
          <div className="sidebar-note">
            <div className="eyebrow">Safety by design</div>
            <p>
              Every suggestion needs evidence.
              <br />
              Every change needs approval.
            </p>
            <span className="mono">δ = 5% slowdown limit</span>
          </div>
        </aside>
        <main>
          <div className="breadcrumb">
            <span>Workspace</span>
            <span aria-hidden="true">/</span>
            <span>{title}</span>
          </div>
          {(health.error || health.data?.postgres === false) && (
            <ErrorBanner message="PostgreSQL is not reachable — start it with make up" />
          )}
          <Suspense fallback={<p role="status">Loading page…</p>}>
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/new" element={<NewRun />} />
              <Route path="/runs" element={<Runs />} />
              <Route path="/runs/:jobId" element={<RunDetail />} />
              <Route path="/applied" element={<Applied />} />
              <Route path="/experiments" element={<Experiments />} />
              <Route
                path="*"
                element={
                  <>
                    <h1>Page not found</h1>
                    <Link className="button" to="/">
                      Return to overview
                    </Link>
                  </>
                }
              />
            </Routes>
          </Suspense>
          <footer className="app-footer">
            DB PERFORMANCE ADVISOR <span aria-hidden="true"> / </span>{" "}
            VERIFICATION CONSOLE
          </footer>
        </main>
      </div>
    </>
  );
}
export function App() {
  return (
    <ToastProvider>
      <Shell />
    </ToastProvider>
  );
}
export default function Root() {
  return (
    <BrowserRouter>
      <App />
    </BrowserRouter>
  );
}
