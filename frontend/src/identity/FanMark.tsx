import type { FanState } from "./stages";
import "./identity.css";

// Six panes and a hub, left to right. Drawn with hairline gaps for small sizes and edge to edge for a window, whose frame is
// the stroke. The paths are the concept's and are not computed, so nothing here runs on every render.
const GLYPH = [
  "M0.52 14.80A14.5 14.5 0 0 1 2.11 8.86L10.91 13.39A4.6 4.6 0 0 0 10.41 15.28Z",
  "M2.80 7.66A14.5 14.5 0 0 1 7.16 3.30L12.51 11.63A4.6 4.6 0 0 0 11.13 13.01Z",
  "M8.36 2.61A14.5 14.5 0 0 1 14.30 1.02L14.78 10.91A4.6 4.6 0 0 0 12.89 11.41Z",
  "M15.70 1.02A14.5 14.5 0 0 1 21.64 2.61L17.11 11.41A4.6 4.6 0 0 0 15.22 10.91Z",
  "M22.84 3.30A14.5 14.5 0 0 1 27.20 7.66L18.87 13.01A4.6 4.6 0 0 0 17.49 11.63Z",
  "M27.89 8.86A14.5 14.5 0 0 1 29.48 14.80L19.59 15.28A4.6 4.6 0 0 0 19.09 13.39Z",
];
const GLYPH_HUB = "M12 15.5A3 3 0 0 1 18 15.5Z";
const WINDOW = [
  "M0 60A60 60 0 0 1 8.04 30L47.01 52.5A15 15 0 0 0 45 60Z",
  "M8.04 30A60 60 0 0 1 30 8.04L52.5 47.01A15 15 0 0 0 47.01 52.5Z",
  "M30 8.04A60 60 0 0 1 60 0L60 45A15 15 0 0 0 52.5 47.01Z",
  "M60 0A60 60 0 0 1 90 8.04L67.5 47.01A15 15 0 0 0 60 45Z",
  "M90 8.04A60 60 0 0 1 111.96 30L72.99 52.5A15 15 0 0 0 67.5 47.01Z",
  "M111.96 30A60 60 0 0 1 120 60L75 60A15 15 0 0 0 72.99 52.5Z",
];
const WINDOW_HUB = "M45 60A15 15 0 0 1 75 60Z";

/** The pane's class: panes before the stage are lit, the stage's own pane says how it is going, the rest are dark. */
function pane(i: number, fan: FanState): string {
  const n = i + 1;
  if (fan.tone === "stop") return "off";
  if (n < fan.stage) return "on";
  if (n > fan.stage) return "off";
  switch (fan.tone) {
    case "work":
      return "now";
    case "ask":
      return "ask";
    case "fix":
      return "fix";
    case "unc":
      return "unc";
    case "done":
      return "on";
    default:
      return "held";
  }
}

/**
 * The fanlight. `size` is its width in px; omit it to fill the parent, as in a window. Small on a row (`variant="glyph"`, the
 * default) it takes its colour from the state; in a window (`variant="window"`) from the window around it. Decorative when
 * the row says the state in words: pass `label={false}`.
 */
export function FanMark({
  fan,
  variant = "glyph",
  size,
  label,
}: {
  fan: FanState;
  variant?: "glyph" | "window";
  size?: number;
  label?: string | false;
}) {
  const panes = variant === "window" ? WINDOW : GLYPH;
  const text = label === false ? undefined : (label ?? fan.label);
  return (
    <svg
      className={"ax-fan is-" + variant + " tone-" + fan.tone}
      viewBox={variant === "window" ? "0 0 120 60" : "0 0 30 16"}
      width={size}
      role={text ? "img" : undefined}
      aria-label={text}
      aria-hidden={text ? undefined : true}
    >
      {text && <title>{text}</title>}
      {panes.map((d, i) => (
        <path key={i} className={"w " + pane(i, fan)} d={d} />
      ))}
      <path className="hub" d={variant === "window" ? WINDOW_HUB : GLYPH_HUB} />
    </svg>
  );
}

/**
 * A window the size of a row: the fan is how far it got, the pane below is how it is doing (lamplit, hot, striped for repair,
 * low for no receipt, dark glass when confirmed, slats when stopped). For a Jobs row or a filter.
 */
export function MiniWindow({ fan, size = 34, label }: { fan: FanState; size?: number; label?: string | false }) {
  const text = label === false ? undefined : (label ?? fan.label);
  return (
    <span
      className={"ax-mini tone-" + fan.tone}
      style={{ "--mini": size + "px" } as React.CSSProperties}
      role={text ? "img" : undefined}
      aria-label={text}
      aria-hidden={text ? undefined : true}
      title={text}
    >
      <FanMark fan={fan} variant="window" label={false} />
      <i />
    </span>
  );
}
