// Per-viewer conveniences kept in localStorage: the theme.
// Every read and write is wrapped: private windows and blocked site data throw on access, and a preference is never worth
// failing a render. The page renders correctly without any of it.
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

type ThemeId = "dark" | "light";

const THEME_KEY = "axiom.theme";

// Old stored values from the five-theme menu: maroon and ivory were light; navy, slate and system (which followed the
// device, and now takes the default) become dark. index.html does the same before first paint.
function readTheme(): ThemeId {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "maroon" || v === "ivory" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

// One current theme for the whole app, so the top bar and the palette never disagree.
let current: ThemeId = readTheme();
const listeners = new Set<() => void>();

// The browser's own chrome follows the page's ground, read from the token so tokens.css stays the only place with colours.
// main.tsx imports the CSS before this module runs.
function syncThemeColor() {
  const ground = getComputedStyle(document.documentElement).getPropertyValue("--ground").trim();
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", ground);
}
syncThemeColor();

// The sidebar went on 2026-10-30, and its remembered collapsed state with it.
try {
  localStorage.removeItem("axiom.sidebar.collapsed");
} catch {
  /* nothing to clear */
}

function setTheme(next: ThemeId) {
  current = next;
  document.documentElement.dataset.theme = next;
  syncThemeColor();
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch {
    /* lasts for this tab */
  }
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => void listeners.delete(listener);
}

/** The current theme and the toggle between the two. */
export function useTheme() {
  const theme = useSyncExternalStore(subscribe, () => current);
  const toggle = useCallback(() => setTheme(current === "dark" ? "light" : "dark"), []);
  return [theme, toggle] as const;
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
