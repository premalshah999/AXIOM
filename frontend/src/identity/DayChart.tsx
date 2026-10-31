import { useMemo } from "react";

// A matrix of dots, one column per stretch of the day: every lit dot is one confirmed application, at the time it was confirmed.
// Half-hour columns while they fit six high; quarter-hour columns at half the pitch when a busy day needs them.
const W = 480;
const H = 60;

type Bin = { cols: number; pitch: number; rows: number; minutes: number };
const BINS: Bin[] = [
  { cols: 48, pitch: 10, rows: 6, minutes: 30 },
  { cols: 96, pitch: 5, rows: 12, minutes: 15 },
];

function layout(times: readonly number[], start: number) {
  const columnsFor = (bin: Bin) => {
    const counts = new Array<number>(bin.cols).fill(0);
    for (const t of times) counts[Math.min(bin.cols - 1, Math.floor((t - start) / (bin.minutes * 60000)))]++;
    return counts;
  };
  for (const bin of BINS) {
    const counts = columnsFor(bin);
    if (Math.max(0, ...counts) <= bin.rows) return { bin, counts, each: 1 };
  }
  // Past that, one dot stands for a few. The caption says how many, so it is still true.
  const bin = BINS[1];
  const counts = columnsFor(bin);
  const each = Math.ceil(Math.max(...counts) / bin.rows);
  return { bin, counts: counts.map((n) => Math.ceil(n / each)), each };
}

/**
 * The day so far. `times` are the confirmations since local midnight (epoch ms, from GET /api/successful-applications'
 * `applied_at`); with none, the matrix is simply unlit. `live` lets the newest column and the now line take the lamp colour.
 */
export function DayChart({ times, now }: { times: readonly number[]; now: number }) {
  const start = new Date(now).setHours(0, 0, 0, 0);
  const { bin, counts, each } = useMemo(() => layout(times, start), [times, start]);
  const nowCol = Math.min(bin.cols - 1, Math.floor((now - start) / (bin.minutes * 60000)));
  const nowX = (((now - start) / 86400000) * W).toFixed(1);
  const off = bin.pitch === 10 ? "d10" : "d5";
  const said = times.length
    ? `${times.length} confirmed since midnight${each > 1 ? `, each dot is ${each}` : ""}.`
    : "Nothing confirmed yet today.";
  return (
    <figure className="ax-day">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={said}>
        <title>{said}</title>
        <rect width={W} height={H} fill={`url(#${off}-off)`} />
        {counts.map((n, i) =>
          n ? (
            <rect
              key={i}
              x={i * bin.pitch}
              y={H - n * bin.pitch}
              width={bin.pitch}
              height={n * bin.pitch}
              fill={`url(#${off}-${i >= nowCol - 1 ? "new" : "on"})`}
            />
          ) : null,
        )}
        <line className="ax-day-now" x1={nowX} x2={nowX} y1={-5} y2={H + 6} />
      </svg>
      <figcaption className="ax-hours" aria-hidden="true">
        {[0, 3, 6, 9, 12, 15, 18, 21].map((h) => {
          // An hour the "now" mark would sit on gives way to it.
          const past = h - ((now - start) / 3600000);
          const hidden = (past >= 0 && past < 1.6) || (each > 1 && h === 21);
          return <span key={h}>{hidden ? "" : String(h).padStart(2, "0")}</span>;
        })}
        <i className="ax-hours-now" style={{ left: `${(Number(nowX) / W) * 100}%` }}>
          now
        </i>
        {each > 1 && <em>each dot is {each}</em>}
      </figcaption>
    </figure>
  );
}

/** The dot patterns everything on the panel is drawn from. Rendered once. */
export function DotPatterns() {
  const dot = (id: string, pitch: number, r: number, fill: string) => (
    <pattern id={id} width={pitch} height={pitch} patternUnits="userSpaceOnUse">
      <circle cx={pitch / 2} cy={pitch / 2} r={r} style={{ fill: `var(${fill})` }} />
    </pattern>
  );
  return (
    <svg className="ax-defs" width="0" height="0" aria-hidden="true" focusable="false">
      <defs>
        {dot("d8-off", 8, 3, "--dot-off")}
        {dot("d8-trail", 8, 1.7, "--led")}
        {dot("d8-on", 8, 3.2, "--led")}
        {dot("d10-off", 10, 2.1, "--line")}
        {dot("d10-on", 10, 3.6, "--ink")}
        {dot("d10-new", 10, 3.6, "--live")}
        {dot("d5-off", 5, 1.1, "--line")}
        {dot("d5-on", 5, 1.9, "--ink")}
        {dot("d5-new", 5, 1.9, "--live")}
      </defs>
    </svg>
  );
}
