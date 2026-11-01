import type { ReactNode } from "react";
import { platformName } from "../format";
import { useNow } from "../hooks";
import { Monogram } from "../ui/Monogram";
import { RUNGS, type Progress } from "./stages";

/** One application under way. */
export type LiveRow = {
  id: string;
  company: string;
  url?: string;
  role: string;
  platform: string;
  state: string;
  since?: string;
  progress: Progress;
  open?: () => void;
  /** The row's Pause, when it can be paused now. */
  pause?: ReactNode;
  /** A run whose browser is released while it waits on an emailed code: real work, in no slot. */
  parked?: boolean;
};

const clockOf = (since: string | undefined, now: number) => {
  const t = since ? Date.parse(since) : NaN;
  if (!Number.isFinite(t)) return "";
  const s = Math.max(0, Math.floor((now - t) / 1000));
  const m = Math.floor(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}` : `${m}:${String(s % 60).padStart(2, "0")}`;
};

/** Six segments of dots, one for each step: the ones behind it dim, the one it is on full and pulsing, the ones ahead dark. */
function Track({ rung, pulsing }: { rung: number; pulsing: boolean }) {
  return (
    <span className="ov-track" aria-hidden="true">
      {RUNGS.map((_, i) => {
        const step = i + 1;
        return <i key={i} className={step < rung ? "is-past" : step === rung ? "is-now" + (pulsing ? " is-pulse" : "") : ""} />;
      })}
    </span>
  );
}

function Row({ row, now }: { row: LiveRow; now: number }) {
  const who = (
    <>
      <b title={row.company}>{row.company}</b>
      <span title={row.role}>{row.role}</span>
    </>
  );
  return (
    <li className={"ov-row" + (row.parked ? " is-parked" : "")}>
      <Monogram name={row.company} url={row.url} size="md" />
      {row.open ? (
        <button type="button" className="ov-who" onClick={row.open}>
          {who}
        </button>
      ) : (
        <div className="ov-who">{who}</div>
      )}
      <div className="ov-step">
        <Track rung={row.progress.rung} pulsing={!row.parked && row.state !== "pausing"} />
        <span className="ov-doing" title={row.progress.doing}>
          {row.progress.doing}
        </span>
      </div>
      <div className="ov-data">
        <time>{clockOf(row.since, now)}</time>
        <span>{platformName(row.platform)}</span>
      </div>
      <div className="ov-row-act">{row.pause}</div>
    </li>
  );
}

/**
 * Live work: what is really running, richly, in the dark window. Idle capacity is one figure and a row of dark dots, never a column
 * of empty channels, so the panel reads complete at one busy or nine. `empty` says why nothing is running; `foot` is what else
 * waits, as links to the lists.
 */
export function LiveWork({
  rows,
  parked,
  busy,
  limit,
  checking,
  empty,
  foot,
}: {
  rows: LiveRow[];
  parked: LiveRow[];
  busy: number;
  limit: number;
  /** Slot status hasn't arrived: claim nothing about idle. */
  checking: boolean;
  empty: ReactNode;
  foot?: ReactNode;
}) {
  const now = useNow(1000);
  return (
    <section className="ax-window ov-live" aria-label="Live work">
      <header className="ov-live-head">
        <h2>Applying now</h2>
        {checking && <p>Checking…</p>}
      </header>
      {rows.length + parked.length > 0 ? (
        <>
          <div className="ov-legend" aria-hidden="true">
            {RUNGS.map((r) => (
              <span key={r}>{r === "submit" ? "Send" : r.charAt(0).toUpperCase() + r.slice(1)}</span>
            ))}
          </div>
          <ol className="ov-rows">
            {rows.map((r) => (
              <Row key={r.id} row={r} now={now} />
            ))}
            {parked.map((r) => (
              <Row key={r.id} row={r} now={now} />
            ))}
          </ol>
        </>
      ) : (
        <p className="ov-live-empty">{empty}</p>
      )}
      {foot && <footer className="ov-live-foot">{foot}</footer>}
    </section>
  );
}
