import type { ReactNode } from "react";
export function Badge({
  state,
  tone,
  children,
}: {
  state?: "pass" | "fail" | "hold" | "info" | "muted";
  tone?: "pass" | "fail" | "hold" | "info" | "muted";
  children: ReactNode;
}) {
  state = state ?? tone ?? "muted";
  return (
    <span className="badge" data-state={state}>
      <span aria-hidden="true">
        {state === "pass"
          ? "✓"
          : state === "fail"
            ? "✕"
            : state === "hold"
              ? "◐"
              : "ⓘ"}
      </span>
      {children}
    </span>
  );
}
