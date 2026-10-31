import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

// Abortable, non-overlapping polling that pauses while the tab is hidden and
// keeps the last good data on transient errors.
//
// A background poll whose answer is unchanged keeps the same data object, so nothing re-renders, and the next poll
// waits longer (up to 3× the interval) until something changes. The dashboard re-rendered every view each poll
// whether or not anything had changed (2026-10-22). `refresh()` and changed deps always fetch now and commit.
// `requestedAt` is when the request behind the shown data started: optimistic marks made after it still apply.
export function usePoll<T>(
  load: (signal: AbortSignal) => Promise<T>,
  interval: number,
  deps: unknown[] = [],
  enabled = true,
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [failures, setFailures] = useState(0);
  const [updatedAt, setUpdatedAt] = useState(0);
  const [requestedAt, setRequestedAt] = useState(0);
  const inflight = useRef<AbortController | null>(null);
  const loader = useRef(load);
  const last = useRef("");
  const lastGood = useRef(0);
  const delay = useRef(interval);
  loader.current = load;

  const run = useCallback(
    async (force: boolean) => {
      if (inflight.current) {
        if (!force) return;
        inflight.current.abort();
      }
      const controller = new AbortController();
      inflight.current = controller;
      const started = Date.now();
      try {
        const result = await loader.current(controller.signal);
        if (controller.signal.aborted) return;
        let text = "";
        try {
          text = JSON.stringify(result);
        } catch {
          text = "";
        }
        const same = text !== "" && text === last.current;
        last.current = text;
        lastGood.current = Date.now();
        delay.current = same && !force ? Math.min(delay.current * 1.5, interval * 3) : interval;
        if (!same || force) {
          setData(result);
          setUpdatedAt(lastGood.current);
          setRequestedAt(started);
        }
        setError(null);
        setFailures(0);
      } catch (e) {
        if (controller.signal.aborted || (e as Error).name === "AbortError")
          return;
        delay.current = interval;
        setError(e as Error);
        setUpdatedAt(lastGood.current);
        setFailures((n) => n + 1);
      } finally {
        if (inflight.current === controller) inflight.current = null;
      }
    },
    [interval],
  );

  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let timer = 0;
    const schedule = () => {
      if (!stopped) timer = window.setTimeout(tick, delay.current);
    };
    const tick = () => {
      if (document.visibilityState === "visible") run(false).finally(schedule);
      else schedule();
    };
    delay.current = interval;
    run(true).finally(schedule);
    const visible = () => {
      if (document.visibilityState !== "visible") return;
      delay.current = interval;
      run(false);
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
      inflight.current?.abort();
      inflight.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, interval, ...deps]);

  const refresh = useCallback(() => run(true), [run]);
  return { data, error, failures, updatedAt, requestedAt, refresh, setData };
}

// In-memory drafts survive polling re-renders and view switches within the
// session. Nothing is written to browser storage.
const drafts = new Map<string, unknown>();
export function useDraft<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() =>
    drafts.has(key) ? (drafts.get(key) as T) : initial,
  );
  const set = useCallback(
    (next: T | ((current: T) => T)) =>
      setValue((current) => {
        const v =
          typeof next === "function"
            ? (next as (c: T) => T)(current)
            : next;
        drafts.set(key, v);
        return v;
      }),
    [key],
  );
  const clear = useCallback(() => {
    drafts.delete(key);
    setValue(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return [value, set, clear] as const;
}

// Toasts: short outcome messages, errors stay until dismissed.
export type Toast = { id: number; tone: "ok" | "error" | "info"; text: string };
type Notify = (tone: Toast["tone"], text: string) => void;
export const ToastContext = createContext<Notify>(() => {});
export const useNotify = () => useContext(ToastContext);

// Per-control pending state; outcomes go to toasts, never a global spinner.
export function useAction() {
  const notify = useNotify();
  const [pending, setPending] = useState<string | null>(null);
  const live = useRef(true);
  // Set true again on mount: StrictMode mounts, cleans up and mounts again, and a cleanup alone left it false.
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  const run = useCallback(
    async <T>(
      key: string,
      fn: () => Promise<T>,
      success?: string | ((result: T) => string | undefined),
    ): Promise<T | undefined> => {
      setPending(key);
      try {
        const result = await fn();
        const text = typeof success === "function" ? success(result) : success;
        if (text) notify("ok", text);
        return result;
      } catch (e) {
        if ((e as Error).name !== "AbortError")
          notify("error", (e as Error).message);
        return undefined;
      } finally {
        if (live.current) setPending(null);
      }
    },
    [notify],
  );
  return { pending, run };
}

export function useNow(interval = 15000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), interval);
    return () => window.clearInterval(timer);
  }, [interval]);
  return now;
}
