import { useMemo, useState } from "react";
import { clock } from "../format";

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
    if (Math.max(0, ...counts) <= bin.rows) return { bin, counts, shown: counts, each: 1 };
  }
  // Past that, one dot stands for a few. The caption says how many, so it is still true.
  const bin = BINS[1]!;
  const counts = columnsFor(bin);
  const each = Math.ceil(Math.max(...counts) / bin.rows);
  return { bin, counts, shown: counts.map((n) => Math.ceil(n / each)), each };
}

/**
 * The day so far. `times` are the confirmations since local midnight (epoch ms, from GET /api/successful-applications'
 * `applied_at`); with none, the matrix is simply unlit. The newest columns and the now line take --live, which is vermilion only
 * while the machine runs. Pointing at a column says what it holds.
 */
export function DayChart({ times, now }: { times: readonly number[]; now: number }) {
  const start = new Date(now).setHours(0, 0, 0, 0);
  const { bin, counts, shown, each } = useMemo(() => layout(times, start), [times, start]);
  const [hover, setHover] = useState<number | null>(null);
  const nowCol = Math.min(bin.cols - 1, Math.floor((now - start) / (bin.minutes * 60000)));
  const nowX = (((now - start) / 86400000) * W).toFixed(1);
  const off = bin.pitch === 10 ? "d10" : "d5";
  const said = times.length
    ? `${times.length} confirmed since midnight${each > 1 ? `, each dot is ${each}` : ""}.`
    : "Nothing confirmed yet today.";
  const from = hover === null ? 0 : start + hover * bin.minutes * 60000;
  return (
    <figure className="ax-day" onMouseLeave={() => setHover(null)}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={said}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setHover(Math.max(0, Math.min(bin.cols - 1, Math.floor(((e.clientX - r.left) / r.width) * bin.cols))));
        }}
      >
        <rect width={W} height={H} fill={`url(#${off}-off)`} />
        {hover !== null && <rect className="ax-day-col" x={hover * bin.pitch} width={bin.pitch} height={H} />}
        {shown.map((n, i) =>
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
        <line className="ax-day-now" x1={nowX} x2={nowX} y1={0} y2={H + 6} />
      </svg>
      {hover !== null && (
        <div className="ax-day-tip" style={{ left: `${((hover + 0.5) / bin.cols) * 100}%` }}>
          <b>{counts[hover]}</b> confirmed · {clock(new Date(from).toISOString())}
        </div>
      )}
      <figcaption className="ax-hours" aria-hidden="true">
        {[0, 3, 6, 9, 12, 15, 18, 21].map((h) => {
          // An hour the "now" mark would sit on gives way to it.
          const past = h - (now - start) / 3600000;
          const hidden = (past > -1.4 && past < 0.7) || (each > 1 && h >= 18);
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

/** The dot patterns the day chart is drawn from. Rendered once. */
export function DotPatterns() {
  const dot = (id: string, pitch: number, r: number, fill: string) => (
    <pattern id={id} width={pitch} height={pitch} patternUnits="userSpaceOnUse">
      <circle cx={pitch / 2} cy={pitch / 2} r={r} style={{ fill: `var(${fill})` }} />
    </pattern>
  );
  return (
    <svg className="ax-defs" width="0" height="0" aria-hidden="true" focusable="false">
      <defs>
        {dot("d10-off", 10, 1.9, "--dot-plate")}
        {dot("d10-on", 10, 3.4, "--dot-lit")}
        {dot("d10-new", 10, 3.4, "--live")}
        {dot("d5-off", 5, 1, "--dot-plate")}
        {dot("d5-on", 5, 1.8, "--dot-lit")}
        {dot("d5-new", 5, 1.8, "--live")}
      </defs>
    </svg>
  );
}
