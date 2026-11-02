// The place for how a job is applied to: "Apply" or "Apply with tailored resume". It sits in a Job board row and in a review batch row,
// and renders nothing until that choice exists. The choice belongs to the job, so whatever it saves is read when the job's turn comes.
//
// A list wraps its table in <ApplyChoices ids={...}>: one request gives every row's state, and a row that changes updates its own entry without
// refetching the page. Rows that are still being tailored are re-read together, one request per tick. Without a wrapper (a row on its own) the control asks for itself.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "./api";
import { loadChoices, mergeChoices } from "./tailorbatch";
import { TailorChoice, type Mode, type TailorState } from "./tailoring";

type Held = { choices: Record<string, TailorState>; modes: Mode[] | null };
const Ctx = createContext<{ held: Held; set: (jobId: string, s: TailorState | null) => void } | null>(null);

const read = (ids: string[]) => loadChoices<TailorState, Mode>(ids, (path) => api(path));

export function ApplyChoices({ ids, children }: { ids: string[]; children: ReactNode }) {
  const key = ids.join(",");
  const [held, setHeld] = useState<Held>({ choices: {}, modes: null });
  const current = useRef(held);
  current.current = held;
  // The same ids again (a poll that changed nothing about which rows are shown) asks for nothing.
  useEffect(() => {
    if (!ids.length) return;
    let stale = false;
    read(ids)
      .then((r) => !stale && setHeld((was) => ({ choices: mergeChoices(was.choices, ids, r.choices), modes: r.modes.length ? r.modes : was.modes })))
      .catch(() => undefined);
    return () => {
      stale = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const preparing = useMemo(() => ids.filter((id) => held.choices[id]?.state === "preparing"), [key, held.choices]);
  const preparingKey = preparing.join(",");
  useEffect(() => {
    if (!preparing.length) return;
    const t = setInterval(() => {
      const asked = preparing.filter((id) => current.current.choices[id]?.state === "preparing");
      if (asked.length) read(asked).then((r) => setHeld((was) => ({ ...was, choices: mergeChoices(was.choices, asked, r.choices) }))).catch(() => undefined);
    }, 2500);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preparingKey]);
  const set = useCallback((jobId: string, s: TailorState | null) => {
    setHeld((was) => ({ ...was, choices: mergeChoices(was.choices, [jobId], s ? { [jobId]: s } : {}) }));
  }, []);
  const value = useMemo(() => ({ held, set }), [held, set]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function ApplyChoice({ jobId }: { jobId: string }) {
  const list = useContext(Ctx);
  // In a row it has no label beside it, so hovering says what it chooses.
  return (
    <span title="How to apply: with your resume as it is, or a tailored one">
      {list ? <TailorChoice jobId={jobId} compact listed known={list.held.choices[jobId] ?? null} modes={list.held.modes ?? undefined} onChange={(s) => list.set(jobId, s)} /> : <TailorChoice jobId={jobId} compact />}
    </span>
  );
}
