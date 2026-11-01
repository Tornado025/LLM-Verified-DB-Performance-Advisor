import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, afterEach, it, expect, vi } from "vitest";
import { CIBar } from "./CIBar";
import { GuardGrid } from "./GuardGrid";
import { stepStates } from "./Stepper";
import { statusTone } from "./CandidatesTable";
import { EvidenceCard } from "./EvidenceCard";
import type { Evidence, JobEvent } from "../types";
import cards from "../test/fixtures/evidence.json";
import events from "../test/fixtures/events.json";
import * as api from "../api";
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
});
afterEach(() => vi.restoreAllMocks());
it.each([
  [0, 0.03, "PASS"],
  [0.06, 0.1, "FAIL"],
  [0, 0.07, "HOLD"],
])("renders accessible interval verdict %s to %s", (lower, upper, state) => {
  render(
    <CIBar
      lower={Number(lower)}
      upper={Number(upper)}
      mean={0.02}
      delta={0.05}
      label="stock_decrement, flash10"
    />,
  );
  const bar = screen.getByRole("img");
  expect(bar).toHaveAccessibleName(/stock_decrement, flash10/);
  expect(bar.textContent).toContain(
    state === "PASS" ? "✓" : state === "FAIL" ? "✕" : "◐",
  );
});
it("renders infinite bounds without invalid SVG positions", () => {
  render(<CIBar lower={-Infinity} upper={Infinity} mean={null} delta={0.05} />);
  expect(screen.getByRole("img").innerHTML).not.toMatch(/(?:NaN|Infinity)[" ]/);
  expect(screen.getByRole("img")).toHaveAccessibleName(/not proven/);
});
it("distinguishes real flash failure from uncertainty", () => {
  render(
    <GuardGrid
      delta={0.05}
      guard={{
        passed: false,
        profiles: {
          normal: {
            cells: {
              place_order: {
                n: 20,
                mean_rel: 0,
                lower: -0.01,
                upper: 0.02,
                state: "PASS",
              },
            },
          },
          flash10: {
            cells: {
              place_order: {
                n: 20,
                mean_rel: 0.1,
                lower: 0.08,
                upper: 0.12,
                state: "FAIL",
              },
            },
          },
        },
      }}
    />,
  );
  expect(
    screen.getByText("Passes on a normal day, fails under flash-sale load"),
  ).toBeInTheDocument();
});
it("derives terminal step states from logged events", () => {
  const states = stepStates(events as JobEvent[], true);
  expect(states.find((s) => s.step === "decide")?.state).toBe("done");
  expect(states.every((s) => s.state !== "running")).toBe(true);
  expect(statusTone("blocked_unproven")).toBe("hold");
  expect(statusTone("rejected_equiv")).toBe("fail");
});
it("requires an explicit dialog confirmation before approval", async () => {
  const card = cards.find((c) => c.shape_id === "payout_report") as Evidence;
  const candidate = card.candidates.find(
    (c) => c.id === card.decision.candidate_id,
  )!;
  vi.spyOn(api, "getCandidate").mockResolvedValue({
    ...candidate,
    apply_preview: candidate.sql,
    target_database: "qc_live",
  });
  const apply = vi
    .spyOn(api, "approve")
    .mockResolvedValue({ id: "applied" } as never);
  render(
    <MemoryRouter>
      <EvidenceCard card={card} />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Approve and apply" }));
  await screen.findByRole("dialog");
  expect(apply).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirm approval" }));
  await waitFor(() => expect(apply).toHaveBeenCalledWith(candidate.id, false));
});
