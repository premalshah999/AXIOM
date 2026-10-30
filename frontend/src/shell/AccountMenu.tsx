import { LogOut, Moon, Sun } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTheme } from "./prefs";

export type Who = { name?: string; email?: string };

/**
 * The sidebar's footer: avatar and name. Clicking opens a small menu with the theme toggle and sign out. Opens upward,
 * closes on an outside click or Esc, and arrow keys move. The toggle leaves the menu open so the change is visible.
 */
export function AccountMenu({ who, rail, onSignOut }: { who: Who; rail: boolean; onSignOut: () => void }) {
  const [theme, toggleTheme] = useTheme();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const name = who.name || who.email || "Signed in";

  useEffect(() => {
    if (!open) return;
    box.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      setOpen(false);
      box.current?.querySelector<HTMLElement>("button[aria-haspopup]")?.focus();
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc, true);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc, true);
    };
  }, [open]);

  const arrows = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = [...(box.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
    const at = items.indexOf(document.activeElement as HTMLElement);
    items[(at + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
  };

  return (
    <div className="ax-account" ref={box}>
      {open && (
        <div className="ax-pop ax-account-pop" role="menu" aria-label="Account" onKeyDown={arrows}>
          <button type="button" role="menuitem" className="ax-menu-item" onClick={toggleTheme}>
            {theme === "dark" ? <Sun size={15} aria-hidden="true" /> : <Moon size={15} aria-hidden="true" />}
            {theme === "dark" ? "Light theme" : "Dark theme"}
          </button>
          <button
            type="button"
            role="menuitem"
            className="ax-menu-item"
            onClick={() => {
              setOpen(false);
              onSignOut();
            }}
          >
            <LogOut size={15} aria-hidden="true" />
            Sign out
          </button>
        </div>
      )}
      <button
        type="button"
        className="ax-account-row"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={rail ? "Account: " + name : undefined}
        title={rail ? name : who.email}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="ax-avatar" aria-hidden="true">
          {name.slice(0, 1).toUpperCase()}
        </span>
        {!rail && <span className="ax-account-name">{name}</span>}
      </button>
    </div>
  );
}
