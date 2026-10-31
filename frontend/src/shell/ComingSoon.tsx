import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";
import { useWorkspace } from "../context";
import { Button } from "../ui/index";
import type { NavItem } from "./nav";
import { TopLine } from "./Shell";
import "../styles/screens.css";

/** What each page will let a person do, in three plain lines. `about` (nav.ts) is the one sentence above them. */
const WILL: Record<string, string[]> = {
  axe: [
    "Practice with questions drawn from the roles you apply to.",
    "Answer out loud, as you would in the real interview.",
    "Get notes on what to tighten before the next one.",
  ],
  network: [
    "See other people applying to the same companies.",
    "Ask for an introduction, or offer one.",
    "Share what you learn about a team, with the people you choose.",
  ],
  emails: [
    "Write a first note to a recruiter or hiring manager.",
    "Follow up on your schedule, and stop when they reply.",
    "Keep every message beside the application it belongs to.",
  ],
};

// A picture of the idea in the page's own tokens: a lit mark is never used, because nothing here is live.
function Picture({ id }: { id: string }) {
  if (id === "axe")
    return (
      <svg viewBox="0 0 320 200" role="img" aria-label="A question, then a spoken answer drawn as a waveform">
        <rect className="s-card" x="24" y="24" width="200" height="56" rx="8" />
        <rect className="s-bar" x="40" y="40" width="120" height="6" rx="3" />
        <rect className="s-bar is-soft" x="40" y="56" width="164" height="6" rx="3" />
        <rect className="s-card" x="96" y="104" width="200" height="72" rx="8" />
        {Array.from({ length: 22 }, (_, i) => {
          const h = 8 + Math.abs(Math.sin(i * 1.7) * 30) + (i % 3) * 4;
          return <rect key={i} className="s-wave" x={112 + i * 8} y={140 - h / 2} width="3" height={h} rx="1.5" />;
        })}
      </svg>
    );
  if (id === "network") {
    const nodes: [number, number][] = [
      [160, 100],
      [70, 52],
      [250, 56],
      [58, 148],
      [262, 148],
      [160, 28],
      [160, 176],
    ];
    return (
      <svg viewBox="0 0 320 200" role="img" aria-label="People connected through shared companies">
        {nodes.slice(1).map(([x, y], i) => (
          <line key={i} className="s-line" x1="160" y1="100" x2={x} y2={y} />
        ))}
        <line className="s-line" x1="70" y1="52" x2="160" y2="28" />
        <line className="s-line" x1="250" y1="56" x2="160" y2="28" />
        <line className="s-line" x1="58" y1="148" x2="160" y2="176" />
        <line className="s-line" x1="262" y1="148" x2="160" y2="176" />
        {nodes.map(([x, y], i) => (
          <g key={i}>
            <circle className={"s-node" + (i === 0 ? " is-core" : "")} cx={x} cy={y} r={i === 0 ? 18 : 12} />
            <circle className="s-dot" cx={x} cy={y} r={i === 0 ? 5 : 3} />
          </g>
        ))}
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 320 200" role="img" aria-label="Three messages in a row, connected in sequence">
      <line className="s-line" x1="52" y1="34" x2="52" y2="166" />
      {[0, 1, 2].map((i) => (
        <g key={i}>
          <circle className="s-node" cx="52" cy={42 + i * 58} r="9" />
          <circle className="s-dot" cx="52" cy={42 + i * 58} r="3" />
          <rect className="s-card" x="84" y={20 + i * 58} width={i === 2 ? 168 : 212} height="44" rx="8" />
          <rect className="s-bar" x="100" y={34 + i * 58} width={i === 2 ? 76 : 96} height="6" rx="3" />
          <rect className="s-bar is-soft" x="100" y={46 + i * 58} width={i === 2 ? 120 : 148} height="6" rx="3" />
        </g>
      ))}
    </svg>
  );
}

/**
 * The page behind a coming-soon item (DESIGN.md §3). It is plainly not switched on: a dashed lamp and the word "Soon", then what
 * it will do and a picture of the idea. Nothing here is real data and nothing on it can be clicked except the way back.
 */
export function ComingSoon({ item }: { item: NavItem }) {
  const { go } = useWorkspace();
  const lines = WILL[item.id] ?? [];
  const Icon = item.icon;
  const line: ReactNode = item.note;
  return (
    <div className="ax-page">
      {line && <TopLine>{line}</TopLine>}
      <section className="ax-soon">
        <div className="ax-soon-copy">
          <span className="ax-soon-state">
            <span className="ax-lamp is-hold" aria-hidden="true" />
            Coming soon
          </span>
          <span className="ax-soon-icon">
            <Icon size={20} aria-hidden="true" />
          </span>
          <h2>{item.about}</h2>
          <ul>
            {lines.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
          <p className="ax-hint">It isn't switched on yet, and nothing here changes your applications.</p>
          <div>
            <Button icon={<ArrowLeft size={15} aria-hidden="true" />} onClick={() => go({ view: "batch" })}>
              Back to Overview
            </Button>
          </div>
        </div>
        <div className="ax-soon-pic" aria-hidden={false}>
          <Picture id={item.id} />
        </div>
      </section>
    </div>
  );
}
