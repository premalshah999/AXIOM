// A small square with the employer's initial, so a list scans like a product. Deterministic: the same name always gets the same
// letter and tone. An employer's square (`employer`) shows the company's icon once this computer has found one; the letter stays
// underneath, so a miss, a slow network or the setting being off changes nothing and nothing moves.
//
// The icons are asked for once per company, not once per row: every square on a screen joins one batched GET /api/logos (names in,
// states out, answered from memory), and only the companies that have an icon load it (GET /api/logo, kept a year by the browser,
// lazy offscreen). A company not looked up yet is queued on the server; the answer is asked again a few times, then dropped.
import { useEffect, useState, useSyncExternalStore } from "react";

type Logo = { state: "ready" | "pending" | "none"; v?: number };

const known = new Map<string, Logo>();
const listeners = new Set<() => void>();
const wanted = new Set<string>();
const tries = new Map<string, number>();
const mounted = new Map<string, number>();
const RETRY_MS = [3000, 5000, 8000, 12000, 20000];
let flushing = 0;
let off = false;

function publish(next: Record<string, Logo>) {
  for (const [name, logo] of Object.entries(next)) known.set(name, logo);
  listeners.forEach((l) => l());
}

async function flush() {
  flushing = 0;
  const names = [...wanted].slice(0, 100);
  names.forEach((n) => wanted.delete(n));
  if (!names.length || off) return;
  try {
    const response = await fetch("/api/logos?" + names.map((n) => "name=" + encodeURIComponent(n)).join("&"), { credentials: "same-origin" });
    if (!response.ok) throw new Error(String(response.status));
    const body = (await response.json()) as { off: boolean; logos: Record<string, Logo> };
    if (body.off) {
      off = true;
      publish(Object.fromEntries(names.map((n) => [n, { state: "none" } as Logo])));
      return;
    }
    publish(body.logos);
    for (const name of names) {
      if (body.logos[name]?.state !== "pending") continue;
      const n = tries.get(name) ?? 0;
      tries.set(name, n + 1);
      if (n >= RETRY_MS.length) publish({ [name]: { state: "none" } });
      else setTimeout(() => ask(name), RETRY_MS[n]);
    }
  } catch {
    publish(Object.fromEntries(names.map((n) => [n, { state: "none" } as Logo])));
  }
  if (wanted.size) schedule();
}

function schedule() {
  if (!flushing) flushing = window.setTimeout(flush, 40);
}

function ask(name: string) {
  const got = known.get(name);
  if (off || (got && got.state !== "pending") || !mounted.get(name)) return;
  wanted.add(name);
  schedule();
}

function useLogo(name: string | undefined, enabled: boolean): Logo | undefined {
  const target = enabled && name ? name : "";
  useEffect(() => {
    if (!target) return;
    mounted.set(target, (mounted.get(target) ?? 0) + 1);
    ask(target);
    return () => {
      mounted.set(target, (mounted.get(target) ?? 1) - 1);
    };
  }, [target]);
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => (target ? known.get(target) : undefined),
  );
}

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

/** `size`: sm 24 (a row), md 32, lg 40 (a drawer's head). Hidden from screen readers: the name always stands beside it.
 *  `employer`: the name is a company, so its icon replaces the letter once found (`url` is the old way to say so). */
export function Monogram({ name, size = "sm", url, employer }: { name?: string; size?: "sm" | "md" | "lg"; url?: string; employer?: boolean }) {
  const { initial, tone } = monogramOf(name);
  const state = useLogo(name, !!(employer || url));
  const logo = name && state?.state === "ready" ? `/api/logo?company=${encodeURIComponent(name)}&v=${state.v ?? 0}` : "";
  const [shown, setShown] = useState("");
  const [missing, setMissing] = useState("");
  return (
    <span className={"ax-mono" + (size === "sm" ? "" : " is-" + size) + (logo && shown === logo ? " has-logo" : "")} data-tone={tone} aria-hidden="true">
      {initial}
      {logo && missing !== logo && (
        <img src={logo} alt="" loading="lazy" decoding="async" onLoad={() => setShown(logo)} onError={() => setMissing(logo)} />
      )}
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
        <Monogram key={i} name={n} employer />
      ))}
      {rest > 0 && <span className="ax-mono ax-mono-more">+{rest > 99 ? 99 : rest}</span>}
    </span>
  );
}
