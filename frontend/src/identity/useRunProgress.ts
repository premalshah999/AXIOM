import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { progressFrom, type Progress } from "./stages";

// GET /api/runs/{id} is the only place a run's events are, and it is heavy (40 KB for a young run), so this asks for one run
// at a time: at once for a run it knows nothing about, and otherwise refreshes the oldest every few seconds. Only while the
// page is visible. A run moves between rungs over minutes, so a channel a little behind is fine and starving the server is not.
const NEW_RUN_MS = 1200;
const REFRESH_MS = 6000;

export function useRunProgress(runIds: readonly string[]): Record<string, Progress> {
  const [told, setTold] = useState<Record<string, Progress>>({});
  const ids = useRef(runIds);
  ids.current = runIds;
  const asked = useRef(new Map<string, number>());

  useEffect(() => {
    let stopped = false;
    let timer = 0;
    const tick = async () => {
      const current = ids.current;
      for (const id of [...asked.current.keys()]) if (!current.includes(id)) asked.current.delete(id);
      const unknown = current.find((id) => !asked.current.has(id));
      const oldest = [...current].sort((a, b) => (asked.current.get(a) ?? 0) - (asked.current.get(b) ?? 0))[0];
      const id = unknown ?? oldest;
      if (id && document.visibilityState === "visible") {
        asked.current.set(id, Date.now());
        try {
          const detail = await api<{ events?: { kind: string; message?: string }[] }>(`/runs/${id}`);
          const progress = progressFrom(detail.events ?? []);
          if (!stopped && progress) setTold((was) => ({ ...was, [id]: progress }));
        } catch {
          /* the channel keeps what it last knew */
        }
      }
      if (!stopped) timer = window.setTimeout(tick, unknown ? NEW_RUN_MS : REFRESH_MS);
    };
    timer = window.setTimeout(tick, 0);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, []);

  return told;
}
