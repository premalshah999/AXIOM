import { Check, Palette } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { THEMES, useTheme } from "./prefs";

/**
 * The theme chooser in the sidebar footer: Navy, Maroon, Slate, Ivory or System. Opens upward, closes on an outside
 * click or Esc, and the choice is remembered (localStorage axiom.theme, applied before first paint by index.html).
 */
export function ThemeMenu({ collapsed }: { collapsed: boolean }) {
  const [theme, setTheme] = useTheme();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const current = THEMES.find((t) => t.id === theme)!;

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
        box.current?.querySelector<HTMLElement>("button[aria-haspopup]")?.focus();
      }
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc, true);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc, true);
    };
  }, [open]);

  // Focus the chosen theme when the menu opens, so arrow keys and Enter work at once.
  useEffect(() => {
    if (open) box.current?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
  }, [open]);

  const arrows = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = [...(box.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"]') ?? [])];
    const at = items.indexOf(document.activeElement as HTMLElement);
    items[(at + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
  };

  return (
    <div className="ax-theme" ref={box}>
      {open && (
        <div className="ax-pop ax-theme-pop" role="menu" aria-label="Theme" onKeyDown={arrows}>
          {THEMES.map((t) => (
            <button
              key={t.id}
              type="button"
              role="menuitemradio"
              aria-checked={theme === t.id}
              className="ax-theme-item"
              onClick={() => {
                setTheme(t.id);
                setOpen(false);
              }}
            >
              <i className="ax-swatch" style={{ background: t.swatch, borderColor: t.id === "system" ? "var(--line-strong)" : t.swatch }} aria-hidden="true" />
              <span className="ax-theme-name">
                {t.label}
                <small>{t.note}</small>
              </span>
              {theme === t.id && <Check size={14} aria-hidden="true" />}
            </button>
          ))}
        </div>
      )}
      <button
        type="button"
        className="ax-foot-row"
        aria-haspopup="menu"
        aria-expanded={open}
        title={collapsed ? "Theme: " + current.label : undefined}
        onClick={() => setOpen((v) => !v)}
      >
        <Palette size={16} aria-hidden="true" />
        {!collapsed && (
          <>
            <span className="ax-foot-label">Theme</span>
            <span className="ax-foot-value">{current.label}</span>
          </>
        )}
      </button>
    </div>
  );
}
