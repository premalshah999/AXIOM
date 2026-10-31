import { ChevronsUpDown, LogOut, Moon, Sun } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { Popover } from "../ui/Popover";
import { useTheme } from "./prefs";

export type Who = { name?: string; email?: string };

/** Who is signed in, the two themes, and sign out. `Theme` and `sign out` are also what the phone's More sheet shows. */
export function ThemeChoice() {
  const [theme, toggle] = useTheme();
  const option = (id: "dark" | "light", label: string, icon: React.ReactNode) => (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={theme === id}
      aria-label={label + " theme"}
      onClick={() => {
        if (theme !== id) toggle();
      }}
    >
      {icon}
      {label}
    </button>
  );
  return (
    <div className="ax-theme-row">
      <span>Theme</span>
      <div className="ax-theme-seg">
        {option("dark", "Dark", <Moon size={13} aria-hidden="true" />)}
        {option("light", "Light", <Sun size={13} aria-hidden="true" />)}
      </div>
    </div>
  );
}

export function AccountMenu({ who, onSignOut }: { who: Who; onSignOut: () => void }) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);
  const name = who.name || who.email || "Signed in";
  return (
    <>
      <button
        ref={button}
        type="button"
        className="ax-account"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={"Account: " + name}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="ax-account-mark" aria-hidden="true">
          {name.slice(0, 1).toUpperCase()}
        </span>
        <span className="ax-account-name">{name}</span>
        <ChevronsUpDown size={14} aria-hidden="true" />
      </button>
      <Popover anchor={button} open={open} onClose={close} label="Account" align="start" match>
        {who.email && who.email !== name && <p className="ax-menu-who">{who.email}</p>}
        <ThemeChoice />
        <div className="ax-menu-rule" />
        <button
          type="button"
          role="menuitem"
          className="ax-menu-item"
          onClick={() => {
            close();
            onSignOut();
          }}
        >
          <LogOut size={15} aria-hidden="true" />
          Sign out
        </button>
      </Popover>
    </>
  );
}
