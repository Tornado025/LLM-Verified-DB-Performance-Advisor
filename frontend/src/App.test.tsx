import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, it, expect, vi } from "vitest";
import { App } from "./App";
afterEach(() => vi.unstubAllGlobals());
it("renders navigation and real health states", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            url.includes("/shapes")
              ? { reports: [], txns: [] }
              : url.includes("/observe")
                ? { available: true, statements: [] }
                : {
                    status: "ok",
                    postgres: true,
                    hypopg: true,
                    pg_stat_statements: true,
                    llm: { ok: true, provider: "mock" },
                  },
          ),
          { status: 200 },
        ),
      ),
    ),
  );
  render(
    <MemoryRouter>
      <App />
    </MemoryRouter>,
  );
  expect(screen.getByRole("navigation")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument();
  await screen.findByText("Postgres");
  expect(screen.getByText("HypoPG").closest(".badge")).toHaveAttribute(
    "data-state",
    "pass",
  );
});
