import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useNow } from "../hooks";
import type { Awake } from "./awake";
import { FanMark } from "./FanMark";
import { Sign } from "./Sign";
import type { FanState } from "./stages";
import "./identity.css";

/** One busy worker. facadeWorkers (adapters.ts) builds these from the real API. */
export type FacadeWorker = {
  /** The run id. */
  id: string;
  /** Which window: 1 is the first. */
  slot: number;
  jobId?: string | null;
  company: string;
  role: string;
  /** What it is doing, in plain words (doingFor). */
  doing: string;
  startedAt?: string | null;
  fan: FanState;
};

// Below this a window cannot hold a company and a role, so idle windows give up their place.
const MIN_WINDOW = 136;
// Windows are this wide or more when only busy ones are shown.
const COMFORT_WINDOW = 172;
// An idle window only has to say "Idle".
const MIN_IDLE_WINDOW = 84;
const GAP = 12;

function soFar(startedAt: string | null | undefined, now: number): string {
  const t = startedAt ? Date.parse(startedAt) : NaN;
  if (!Number.isFinite(t)) return "";
  const min = Math.max(0, Math.floor((now - t) / 60000));
  if (min < 1) return "Just started";
  if (min < 60) return `${min} min so far`;
  return `${Math.floor(min / 60)} hr ${min % 60} min so far`;
}

function fits(width: number, n: number, each: number) {
  return Math.max(1, Math.floor((width + GAP) / (each + GAP))) >= n;
}

/**
 * The building: one arched window per worker slot, lit while a worker has a run, with the blade sign on its corner. Reflows
 * for 1 to 30 slots. Every slot gets a window when they all fit at a readable width; when they do not, the busy ones keep
 * theirs and the idle ones become a row of tiny arches with a count. Columns depend on the width alone, so a worker going
 * idle between polls never resizes its neighbours.
 *
 * `slots` is application_workers normal + specialist + custom. `workers` are the slots with a run. `paused` dims the lights
 * on runs that are only finishing. `notice` is one plain line on the wall: setup unfinished, no workers.
 */
export function Facade({
  slots,
  workers,
  awake,
  paused,
  loading,
  notice,
  onOpen,
}: {
  slots: number;
  workers: readonly FacadeWorker[];
  awake: Awake;
  paused?: boolean;
  /** Worker status has not arrived yet: frames only, and it claims nothing about any of them. */
  loading?: boolean;
  notice?: ReactNode;
  onOpen?: (worker: FacadeWorker) => void;
}) {
  const now = useNow(30000);
  const floors = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    if (floors.current) setWidth(floors.current.clientWidth);
  }, []);
  // Fires when the wall changes size, not on a timer.
  useEffect(() => {
    const el = floors.current;
    if (!el) return;
    const seen = new ResizeObserver(() => setWidth(el.clientWidth));
    seen.observe(el);
    return () => seen.disconnect();
  }, []);

  const bySlot = new Map(workers.map((w) => [w.slot, w]));
  const busy = workers.length;
  const count = Math.max(slots, 0);
  const idleCount = Math.max(0, count - busy);
  const compactIdle = busy === 0 || loading;
  const allFit = width === 0 || fits(width, count, compactIdle ? MIN_IDLE_WINDOW : MIN_WINDOW);
  const showAll = allFit || compactIdle;
  const perRow = Math.max(2, Math.floor((width + GAP) / (COMFORT_WINDOW + GAP)));
  const columns = allFit ? Math.max(1, count) : compactIdle ? Math.max(2, Math.floor((width + GAP) / (MIN_IDLE_WINDOW + GAP))) : perRow;
  const shown = Array.from({ length: count }, (_, i) => i + 1).filter((slot) => showAll || bySlot.has(slot));
  const lit = shown.filter((slot) => bySlot.has(slot));
  // The lamplight on the pavement, under the lit windows of the bottom floor.
  const lastRow = Math.floor((shown.length - 1) / columns);
  const spill = shown
    .map((slot, i) => ({ slot, col: i % columns, row: Math.floor(i / columns) }))
    .filter((w) => w.row === lastRow && bySlot.has(w.slot));

  return (
    <div
      className={"ax-facade" + (paused ? " is-paused" : "") + (loading ? " is-loading" : "") + (count === 0 ? " is-empty" : "")}
      aria-busy={loading || undefined}
    >
      <div className="ax-cornice" aria-hidden="true" />
      <Sign awake={awake} />
      {notice && (
        <p className="ax-wall-notice" role="status">
          {notice}
        </p>
      )}
      <div className="ax-wall">
        <div
          ref={floors}
          className={"ax-floors" + (compactIdle ? " is-idle-row" : "")}
          aria-label={loading ? "Workers, loading" : `${count} ${count === 1 ? "worker" : "workers"}, ${busy} busy`}
          role="group"
          style={{ "--cols": columns } as React.CSSProperties}
        >
          {shown.map((slot) => {
            const w = bySlot.get(slot);
            return w ? (
              <Lit key={slot} worker={w} now={now} onOpen={onOpen} />
            ) : (
              <article key={slot} className="ax-win is-idle" aria-label={loading ? undefined : `Worker ${slot} is idle`}>
                <FanMark fan={IDLE_FAN} variant="window" label={false} />
                <div className="ax-win-pane">{!loading && <p className="ax-win-meta">Idle</p>}</div>
              </article>
            );
          })}
        </div>
      </div>
      {!showAll && (
        <div className="ax-idle-strip" role="img" aria-label={`${count} workers: ${busy} busy, ${idleCount} idle`}>
          <span className="ax-tinies" aria-hidden="true">
            {Array.from({ length: count }, (_, i) => (
              <i key={i} className={bySlot.has(i + 1) ? "is-on" : undefined} />
            ))}
          </span>
          {idleCount > 0 ? <span>{idleCount} idle, ready for the next job</span> : <span>All at work</span>}
        </div>
      )}
      <div className="ax-plinth" aria-hidden="true" />
      {lit.length > 0 && !paused && (
        <svg className="ax-spill" viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <linearGradient id="ax-spill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" className="ax-spill-a" />
              <stop offset=".5" className="ax-spill-b" />
              <stop offset="1" className="ax-spill-c" />
            </linearGradient>
          </defs>
          {spill.map((w) => {
            const a = (w.col / columns) * 1000;
            const b = ((w.col + 1) / columns) * 1000;
            return <polygon key={w.slot} points={`${a},0 ${b},0 ${b + 26},100 ${a + 10},100`} fill="url(#ax-spill)" />;
          })}
        </svg>
      )}
    </div>
  );
}

const IDLE_FAN: FanState = { stage: 0, tone: "idle", label: "Idle" };

function Lit({ worker, now, onOpen }: { worker: FacadeWorker; now: number; onOpen?: (w: FacadeWorker) => void }) {
  const time = soFar(worker.startedAt, now);
  return (
    <article className="ax-win is-lit">
      <FanMark fan={worker.fan} variant="window" />
      <div className="ax-win-pane">
        <h3 className="ax-win-co" title={worker.company}>
          {onOpen ? (
            <button type="button" onClick={() => onOpen(worker)}>
              {worker.company}
            </button>
          ) : (
            worker.company
          )}
        </h3>
        <p className="ax-win-role" title={worker.role}>
          {worker.role}
        </p>
        <div className="ax-win-doing">
          <p className="ax-win-act" title={worker.doing}>
            {worker.doing}
          </p>
          {time && <p className="ax-win-meta">{time}</p>}
        </div>
      </div>
    </article>
  );
}
