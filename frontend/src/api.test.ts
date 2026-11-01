import { afterEach, it, expect, vi } from "vitest";
import { api, ApiError } from "./api";
afterEach(() => vi.unstubAllGlobals());
it("parses structured API errors", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "conflict",
            message: "Needs evidence",
            detail: { candidate: "x" },
          },
        }),
        { status: 409 },
      ),
    ),
  );
  await expect(api("/test")).rejects.toMatchObject({
    code: "conflict",
    status: 409,
    message: "Needs evidence",
    detail: { candidate: "x" },
  });
});
it("labels network failures", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
  await expect(api("/test")).rejects.toBeInstanceOf(ApiError);
  await expect(api("/test")).rejects.toMatchObject({ code: "network" });
});
it("preserves cancellation", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new DOMException("aborted", "AbortError")),
  );
  await expect(api("/test")).rejects.toMatchObject({ name: "AbortError" });
});
