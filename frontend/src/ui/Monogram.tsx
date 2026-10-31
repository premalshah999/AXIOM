// A small square with the employer's initial, so a list scans like a product. Deterministic: the same name always gets the same
// letter and tone. Logos are never fetched: asking a third party for them would leak the job list.
const TONES = 8;

function hash(text: string) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function monogramOf(name: string | undefined) {
  const clean = (name || "").trim().toLowerCase();
  // "The Home Depot" and "the home depot, inc" should read as H and agree on a tone.
  const key = clean.replace(/^(the|a|an)\s+/, "").replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  const initial = key.slice(0, 1).toUpperCase() || "?";
  return { initial, tone: hash(key.replace(/\s+(inc|llc|ltd|corp|corporation|company|co)$/, "")) % TONES };
}

/** `size`: sm 24 (a row), md 32, lg 40 (a drawer's head). Hidden from screen readers: the name always stands beside it. */
export function Monogram({ name, size = "sm" }: { name?: string; size?: "sm" | "md" | "lg" }) {
  const { initial, tone } = monogramOf(name);
  return (
    <span className={"ax-mono" + (size === "sm" ? "" : " is-" + size)} data-tone={tone} aria-hidden="true">
      {initial}
    </span>
  );
}

/** Several employers behind one thing (one question holding up six applications): up to three squares and a count of the rest. */
export function MonogramStack({ names, total, max = 3 }: { names: (string | undefined)[]; total?: number; max?: number }) {
  const shown = names.slice(0, max);
  const rest = (total ?? names.length) - shown.length;
  return (
    <span className="ax-mono-stack" aria-hidden="true">
      {shown.map((n, i) => (
        <Monogram key={i} name={n} />
      ))}
      {rest > 0 && <span className="ax-mono ax-mono-more">+{rest > 99 ? 99 : rest}</span>}
    </span>
  );
}
