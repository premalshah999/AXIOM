import { useMemo } from "react";
import "./identity.css";

export type Confirmation = { at: string; company?: string; title?: string };

// In the chart's own units (the SVG scales to its box).
const W = 312;
const FLOOR = 88; // the ground the dots stack up from
const CEILING = 6;
const LEFT = 6;
const RIGHT = W - 6;
// Tried largest first: 37 dots read as stars and 300 still fit, and no dot is ever merged, dropped or moved in time.
const RADII = [3, 2.6, 2.2, 1.9, 1.6, 1.35, 1.1, 0.9];
const GAP = 0.9;

const clock = (d: Date, withMinutes: boolean) =>
  d
    .toLocaleTimeString("en-US", { hour: "numeric", minute: withMinutes ? "2-digit" : undefined, hour12: true })
    .toLowerCase()
    .replace(/\s+/g, " ");

export function midnightBefore(now: number): number {
  const d = new Date(now);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/** Confirmations since local midnight, oldest first. Unreadable or future timestamps are not today's. */
export function confirmedToday<T extends { at: string }>(items: readonly T[], now: number): T[] {
  const start = midnightBefore(now);
  return items
    .map((item) => ({ item, t: Date.parse(item.at) }))
    .filter(({ t }) => Number.isFinite(t) && t >= start && t <= now)
    .sort((a, b) => a.t - b.t)
    .map(({ item }) => item);
}

/** A swarm: x is the true time, y is only the lowest place that does not touch an earlier dot. */
export function swarm(xs: readonly number[], r: number): number[] {
  const d = 2 * r + GAP;
  const placed: { x: number; y: number }[] = [];
  const ys: number[] = [];
  for (const x of xs) {
    const near = placed.filter((p) => Math.abs(p.x - x) < d);
    const candidates = [0, ...near.map((p) => p.y + Math.sqrt(d * d - (p.x - x) * (p.x - x)))].sort((a, b) => a - b);
    const y = candidates.find((c) => near.every((p) => (p.x - x) * (p.x - x) + (p.y - c) * (p.y - c) >= d * d - 1e-6)) ?? 0;
    placed.push({ x, y });
    ys.push(y);
  }
  return ys;
}

/** Evenly spaced hours that fit, with the current time at the end. */
function ticks(start: number, end: number): { t: number; label: string }[] {
  const hours = (end - start) / 3_600_000;
  const step = hours <= 4 ? 1 : hours <= 8 ? 2 : hours <= 16 ? 4 : 6;
  const out: { t: number; label: string }[] = [];
  for (let h = 0; h * 3_600_000 < end - start; h += step) {
    // Too close to the time at the end: 12 units of label each side.
    if ((end - (start + h * 3_600_000)) / (end - start) < 0.22) break;
    out.push({ t: start + h * 3_600_000, label: clock(new Date(start + h * 3_600_000), false) });
  }
  return out;
}

/**
 * One dot per confirmed application since midnight, at the moment it was confirmed. It only draws what it is given: fed from
 * GET /api/successful-applications (`applied_at`, `company`, `title`). With nothing confirmed there are no dots and it says so.
 * `alive` lets the newest dot breathe, and only when the machine is actually running.
 */
export function SkyChart({ confirmed, now, alive }: { confirmed: readonly Confirmation[]; now: number; alive?: boolean }) {
  const { dots, r, tickRows, busiest, count, nowX } = useMemo(() => {
    const today = confirmedToday(confirmed, now);
    const start = midnightBefore(now);
    // An hour at least, so the first dots of the day do not fill the whole axis.
    const end = Math.max(now, start + 3_600_000);
    const x = (t: number) => LEFT + ((t - start) / (end - start)) * (RIGHT - LEFT);
    const xs = today.map((c) => x(Date.parse(c.at)));
    const room = FLOOR - CEILING;
    let r = RADII[RADII.length - 1];
    let ys: number[] = [];
    for (const candidate of RADII) {
      ys = swarm(xs, candidate);
      r = candidate;
      if (Math.max(0, ...ys) + candidate * 2 <= room) break;
    }
    // Past the smallest dot, the stacks are pressed to fit: every dot is still there, at its true time.
    const top = Math.max(0, ...ys) + r * 2;
    const squeeze = top > room ? room / top : 1;
    const dots = today.map((c, i) => {
      const at = new Date(c.at);
      return {
        key: i,
        cx: xs[i],
        cy: FLOOR - r - ys[i] * squeeze,
        title: clock(at, true) + (c.company ? ", " + c.company : "") + (c.title ? ", " + c.title : ""),
      };
    });
    const perHour = new Map<number, number>();
    for (const c of today) {
      const h = new Date(c.at).getHours();
      perHour.set(h, (perHour.get(h) ?? 0) + 1);
    }
    const peak = [...perHour.entries()].sort((a, b) => b[1] - a[1])[0];
    return {
      dots,
      r,
      count: today.length,
      nowX: x(now),
      busiest: peak ? clock(new Date(new Date(now).setHours(peak[0], 0, 0, 0)), false) : "",
      tickRows: ticks(start, end).map((t) => ({ x: x(t.t), label: t.label })),
    };
  }, [confirmed, now]);

  const said =
    count === 0
      ? "Nothing confirmed since midnight"
      : `${count} ${count === 1 ? "application" : "applications"} confirmed since midnight, busiest around ${busiest}`;
  return (
    <figure className="ax-skychart">
      <figcaption>{count ? "Each dot, one confirmed since midnight" : "Nothing confirmed yet today"}</figcaption>
      <svg viewBox={`0 0 ${W} 108`} role="img" aria-label={said}>
        {dots.map((d, i) => (
          <circle key={d.key} className={"ax-star" + (alive && i === dots.length - 1 ? " is-last" : "")} cx={d.cx} cy={d.cy} r={r}>
            <title>{d.title}</title>
          </circle>
        ))}
        {tickRows.map((t) => (
          <text key={t.label} x={t.x} y={103}>
            {t.label}
          </text>
        ))}
        <text x={Math.min(nowX, W)} y={103} textAnchor="end">
          {clock(new Date(now), true)}
        </text>
      </svg>
    </figure>
  );
}
