// Per-viewer conveniences kept in localStorage: the theme, whether the sidebar is collapsed.
// Every read and write is wrapped: private windows and blocked site data throw on access, and a preference is never worth
// failing a render. The page renders correctly without any of it.
import { useCallback, useEffect, useState } from "react";

export const THEMES = [
  { id: "navy", label: "Navy", note: "Dark, blue accent", swatch: "#5b8cff", ground: "#070b14" },
  { id: "maroon", label: "Maroon", note: "Light, maroon accent", swatch: "#8c1d40", ground: "#f8f6f5" },
  { id: "slate", label: "Slate", note: "Dark, teal accent", swatch: "#2dd4bf", ground: "#0a0d10" },
  { id: "ivory", label: "Ivory", note: "Light, green accent", swatch: "#2f6f4e", ground: "#faf8f3" },
  { id: "system", label: "System", note: "Follows your device", swatch: "#8b95ab", ground: "#070b14" },
] as const;
export type ThemeId = (typeof THEMES)[number]["id"];

export const THEME_KEY = "axiom.theme";
const IDS = THEMES.map((t) => t.id as string);

export function readTheme(): ThemeId {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v && IDS.includes(v) ? (v as ThemeId) : "navy";
  } catch {
    return "navy";
  }
}

// Applied before first paint by the inline script in index.html; this keeps it in step afterwards.
function applyTheme(id: ThemeId) {
  const root = document.documentElement;
  root.dataset.theme = id;
  const t = THEMES.find((x) => x.id === id)!;
  const dark = id === "system" ? matchMedia("(prefers-color-scheme: dark)").matches : id === "navy" || id === "slate";
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", id === "system" ? (dark ? "#070b14" : "#f8f6f5") : t.ground);
}

export function useTheme() {
  const [theme, setTheme] = useState<ThemeId>(readTheme);
  useEffect(() => applyTheme(theme), [theme]);
  const set = useCallback((next: ThemeId) => {
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* lasts for this tab */
    }
    setTheme(next);
  }, []);
  return [theme, set] as const;
}

/** State that survives a reload. */
export function usePersisted<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem("axiom." + key);
      return raw == null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("axiom." + key, JSON.stringify(value));
    } catch {
      /* private mode */
    }
  }, [key, value]);
  return [value, setValue] as const;
}

/** Whether a media query matches, kept current as the window changes. */
export function useMedia(query: string): boolean {
  const [hit, setHit] = useState(() => matchMedia(query).matches);
  useEffect(() => {
    const m = matchMedia(query);
    const on = () => setHit(m.matches);
    on();
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [query]);
  return hit;
}
