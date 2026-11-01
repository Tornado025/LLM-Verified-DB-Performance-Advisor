import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, it, expect, vi } from "vitest";
import { useEventStream } from "./useEventStream";
class Source {
  static instances: Source[] = [];
  listeners = new Map<string, ((event: Event) => void)[]>();
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    Source.instances.push(this);
  }
  addEventListener(name: string, fn: (event: Event) => void) {
    this.listeners.set(name, [...(this.listeners.get(name) ?? []), fn]);
  }
  close() {
    this.closed = true;
  }
  send(name: string, data: unknown) {
    this.listeners
      .get(name)
      ?.forEach((fn) =>
        fn(new MessageEvent(name, { data: JSON.stringify(data) })),
      );
  }
}
afterEach(() => {
  vi.unstubAllGlobals();
  Source.instances = [];
});
it("deduplicates events, keeps order, closes at end", () => {
  vi.stubGlobal("EventSource", Source);
  const { result, unmount } = renderHook(() => useEventStream("job"));
  const s = Source.instances[0];
  act(() => {
    s.onopen?.();
    s.send("observe", { id: 2, message: "b" });
    s.send("system", { id: 1, message: "a" });
    s.send("observe", { id: 2, message: "b" });
  });
  expect(result.current.events.map((e) => e.id)).toEqual([1, 2]);
  act(() => s.send("end", { status: "done" }));
  expect(result.current.status).toBe("done");
  expect(s.closed).toBe(true);
  unmount();
});
it("falls back to polling after three stream failures", async () => {
  vi.stubGlobal("EventSource", Source);
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (input: string) =>
        new Response(
          JSON.stringify(
            input.includes("events.json")
              ? { events: [{ id: 3, message: "fallback" }], last_id: 3 }
              : { status: "done" },
          ),
          { status: 200 },
        ),
    ),
  );
  const { result } = renderHook(() => useEventStream("job"));
  const s = Source.instances[0];
  act(() => {
    s.onerror?.();
    s.onerror?.();
    s.onerror?.();
  });
  await waitFor(() => expect(result.current.status).toBe("done"));
  expect(result.current.events[0].id).toBe(3);
  expect(s.closed).toBe(true);
});
