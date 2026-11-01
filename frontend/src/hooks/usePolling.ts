import { useEffect, useRef } from "react";
export function usePolling(
  fn: (signal: AbortSignal) => Promise<unknown>,
  ms: number,
  enabled = true,
) {
  const latest = useRef(fn);
  latest.current = fn;
  useEffect(() => {
    if (!enabled) return;
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    let controller: AbortController | undefined;
    const tick = async () => {
      if (stopped || document.visibilityState === "hidden") return;
      controller = new AbortController();
      try {
        await latest.current(controller.signal);
      } catch {
        /* Caller owns error state. */
      } finally {
        if (!stopped) timer = setTimeout(tick, ms);
      }
    };
    const visibility = () => {
      clearTimeout(timer);
      controller?.abort();
      if (document.visibilityState === "visible") void tick();
    };
    document.addEventListener("visibilitychange", visibility);
    void tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [ms, enabled]);
}
