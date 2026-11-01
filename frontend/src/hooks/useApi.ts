import { useCallback, useEffect, useRef, useState } from "react";
import type { DependencyList } from "react";
export function useApi<T>(
  fn: (signal: AbortSignal) => Promise<T>,
  deps: DependencyList = [],
) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const latest = useRef(fn);
  latest.current = fn;
  const reload = useCallback(() => setRevision((n) => n + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(undefined);
    latest
      .current(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [...deps, revision]);
  return { data, error, loading, reload };
}
