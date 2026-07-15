import { useCallback, useEffect, useRef, useState } from "react";

/** Fetch-on-mount async hook with manual reload. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  const requestIdRef = useRef(0);
  const inFlightRef = useRef<{ id: number; promise: Promise<void> } | null>(null);
  fnRef.current = fn;

  const execute = useCallback((force = false): Promise<void> => {
    if (!force && inFlightRef.current) return inFlightRef.current.promise;

    const requestId = ++requestIdRef.current;
    setLoading(true);

    const promise = (async () => {
      try {
        const next = await fnRef.current();
        if (requestIdRef.current === requestId) {
          setData(next);
          setError(null);
        }
      } catch (cause) {
        if (requestIdRef.current === requestId) {
          setError(cause instanceof Error ? cause.message : String(cause));
        }
      } finally {
        if (requestIdRef.current === requestId) setLoading(false);
        if (inFlightRef.current?.id === requestId) inFlightRef.current = null;
      }
    })();

    inFlightRef.current = { id: requestId, promise };
    return promise;
  }, []);

  const reload = useCallback((): Promise<void> => execute(), [execute]);

  useEffect(() => {
    void execute(true);
    return () => {
      requestIdRef.current += 1;
      inFlightRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps]);

  return { data, error, loading, reload };
}

export function useInterval(cb: () => void, ms: number | null) {
  const ref = useRef(cb);
  ref.current = cb;
  useEffect(() => {
    if (ms == null) return;
    const id = setInterval(() => ref.current(), ms);
    return () => clearInterval(id);
  }, [ms]);
}
