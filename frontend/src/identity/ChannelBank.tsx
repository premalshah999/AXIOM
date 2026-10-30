import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { RUNGS, type Progress } from "./stages";

/** One slot with a run. */
export type Channel = {
  /** 1 is the first. */
  slot: number;
  /** The run id. */
  id: string;
  company: string;
  role: string;
  platform: string;
  state: string;
  since?: string;
  progress: Progress;
  open?: () => void;
  /** The row's Pause, when it can be paused now. */
  pause?: ReactNode;
};

// Below this a channel cannot hold its reading and a label column: the bank turns into a row of ladders over a list of the
// busy ones, which is also what a phone gets.
const MIN_CHANNEL = 120;

/** Four dots wide, eighteen high: three rows to a rung, and the front two rows of the current rung lit full. */
function Ladder({ rung, lit }: { rung: number; lit: boolean }) {
  // Kept when the channel goes dark, so it fades from where it was rather than dropping first.
  const last = useRef(rung);
  if (lit) last.current = rung;
  const rows = last.current * 3 - 1;
  const top = 144 - rows * 8;
  return (
    <svg className="ax-lad" viewBox="0 0 32 144" aria-hidden="true">
      <rect width="32" height="144" fill="url(#d8-off)" />
      <g className="ax-lad-lit">
        <rect y={top + 16} width="32" height={Math.max(0, 144 - top - 16)} fill="url(#d8-trail)" />
        <rect y={top + 8} width="32" height="8" fill="url(#d8-on)" />
        <rect className="ax-lad-front" y={top} width="32" height="8" fill="url(#d8-on)" />
      </g>
    </svg>
  );
}

const clockOf = (since: string | undefined, now: number) => {
  const t = since ? Date.parse(since) : NaN;
  if (!Number.isFinite(t)) return "";
  const s = Math.max(0, Math.floor((now - t) / 1000));
  const m = Math.floor(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}` : `${m}:${String(s % 60).padStart(2, "0")}`;
};

function CompanyName({ channel }: { channel: Channel }) {
  return channel.open ? (
    <button type="button" className="ax-ch-open" title={channel.company} onClick={channel.open}>
      {channel.company}
    </button>
  ) : (
    <span title={channel.company}>{channel.company}</span>
  );
}

/**
 * The channel bank: one dot-matrix channel per worker slot, 1 to 30, in a window that stays dark in both themes. A channel lights
 * to the rung its worker is on and goes dark when the worker has no run, so pausing darkens them one at a time as each run
 * really stops. `slots` is application_workers normal + specialist + custom; `channels` come from runtime-health's workers.
 * `checking` means worker status has not arrived, so idle channels claim nothing.
 */
export function ChannelBank({
  slots,
  channels,
  now,
  checking,
}: {
  slots: number;
  channels: readonly Channel[];
  now: number;
  checking?: boolean;
}) {
  const root = useRef<HTMLElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    if (root.current) setWidth(root.current.clientWidth);
  }, []);
  // Fires when the panel changes size, not on a timer.
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const seen = new ResizeObserver(() => setWidth(el.clientWidth));
    seen.observe(el);
    return () => seen.disconnect();
  }, []);

  const bySlot = new Map(channels.map((c) => [c.slot, c]));
  const list = Array.from({ length: slots }, (_, i) => i + 1);
  const rail = parseFloat(root.current ? getComputedStyle(root.current).getPropertyValue("--rail") : "") || 96;
  const compact = width > 0 && (width - rail) / Math.max(1, slots) < MIN_CHANNEL;
  const busy = channels.length;
  const said = slots
    ? `${slots} channels, ${busy} busy. ` +
      channels.map((c) => `Channel ${c.slot} ${c.company}, ${RUNGS[c.progress.rung - 1]}.`).join(" ")
    : "No workers are configured.";
  const idleWord = checking ? "checking" : "idle";

  if (!slots)
    return (
      <section className="ax-bank" ref={root} aria-label="Workers">
        <div className="ax-window ax-ladders is-empty">
          <p>No workers are configured.</p>
        </div>
      </section>
    );

  return (
    <section className={"ax-bank" + (compact ? " is-compact" : "")} ref={root} aria-label="Workers" style={{ "--slots": slots } as React.CSSProperties}>
      <div className="ax-window ax-ladders" role="img" aria-label={said}>
        {!compact && (
          <ul className="ax-stages" aria-hidden="true">
            {[...RUNGS].reverse().map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
        {list.map((slot) => {
          const c = bySlot.get(slot);
          const order = c ? channels.indexOf(c) : 0;
          return (
            <div key={slot} className={"ax-ch" + (c ? " is-busy" : "")} style={{ "--o": order } as React.CSSProperties}>
              <span className="ax-ch-head">{compact && slots > 12 ? slot : `ch ${slot}`}</span>
              <Ladder rung={c?.progress.rung ?? 1} lit={!!c} />
              {c && !compact && (
                <span className="ax-reading" style={{ "--rung": c.progress.rung } as React.CSSProperties}>
                  <em>{c.state === "pausing" ? "pausing" : RUNGS[c.progress.rung - 1]}</em>
                  <span>{clockOf(c.since, now)}</span>
                </span>
              )}
            </div>
          );
        })}
      </div>
      {!compact ? (
        <div className="ax-labels">
          <Lead busy={busy} slots={slots} />
          {list.map((slot) => {
            const c = bySlot.get(slot);
            return c ? (
              <div key={slot} className="ax-lb">
                <b>
                  <CompanyName channel={c} />
                </b>
                <span title={c.role}>{c.role}</span>
                <span className="ax-lb-do" title={c.progress.doing}>
                  {c.progress.doing}
                </span>
                <span className="ax-lb-data">{c.platform}</span>
                {c.pause}
              </div>
            ) : (
              <div key={slot} className="ax-lb is-idle">
                <b>{idleWord}</b>
              </div>
            );
          })}
        </div>
      ) : (
        <>
          <Lead busy={busy} slots={slots} />
          {busy > 0 && (
            <ol className="ax-chlist">
              {channels.map((c) => (
                <li key={c.id}>
                  <span className="ax-chlist-ch">ch {c.slot}</span>
                  <div className="ax-chlist-who">
                    <b>
                      <CompanyName channel={c} />
                    </b>
                    <span title={c.role}>{c.role}</span>
                    <span className="ax-lb-do" title={c.progress.doing}>
                      {c.progress.doing}
                    </span>
                  </div>
                  <div className="ax-chlist-data">
                    <span>{clockOf(c.since, now)}</span>
                    <span>{c.platform}</span>
                    {c.pause}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </section>
  );
}

function Lead({ busy, slots }: { busy: number; slots: number }) {
  return (
    <div className="ax-lead">
      <span className="ax-read">{busy}</span>
      <span className="ax-engraved">of {slots} busy</span>
    </div>
  );
}

