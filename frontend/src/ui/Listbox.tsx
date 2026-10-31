import { Check, ChevronDown } from "lucide-react";
import { useCallback, useRef, useState, type ReactNode } from "react";
import { Popover } from "./Popover";

export type ListboxOption<T extends string> = { value: T; label: string; count?: number };

/**
 * A filter or small choice as a designed menu: use it in toolbars and anywhere a choice stands on its own. A plain `Select` is for
 * long forms and for phones' native pickers. Arrow keys, Home, End and typing a letter move; Enter or a click chooses; Esc closes.
 */
export function Listbox<T extends string>({
  value,
  options,
  onChange,
  label,
  prefix,
  size = "md",
  align = "start",
  disabled,
  icon,
}: {
  value: T;
  options: ListboxOption<T>[];
  onChange: (value: T) => void;
  /** Its accessible name, and the menu's. */
  label: string;
  /** Shown before the value in the button ("Rows 25"). */
  prefix?: string;
  size?: "sm" | "md";
  align?: "start" | "end";
  disabled?: boolean;
  icon?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);
  const current = options.find((o) => o.value === value);
  const choose = (v: T) => {
    onChange(v);
    setOpen(false);
    button.current?.focus();
  };
  return (
    <>
      <button
        ref={button}
        type="button"
        className={"ax-btn ax-listbox" + (size === "sm" ? " is-sm" : "")}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${label}: ${current?.label ?? value}`}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => {
          if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !open) {
            e.preventDefault();
            setOpen(true);
          }
        }}
      >
        {icon}
        {prefix && <span className="ax-listbox-prefix">{prefix}</span>}
        <span>{current?.label ?? value}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      <Popover anchor={button} open={open} onClose={close} label={label} role="listbox" align={align} match>
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            role="option"
            className="ax-menu-item"
            aria-selected={o.value === value}
            onClick={() => choose(o.value)}
            onKeyDown={(e) => {
              // Typing a letter jumps to the next option that starts with it.
              if (e.key.length !== 1 || e.metaKey || e.ctrlKey || e.key === " ") return;
              const items = [...e.currentTarget.parentElement!.querySelectorAll<HTMLElement>('[role="option"]')];
              const at = items.indexOf(e.currentTarget);
              const next = [...items.slice(at + 1), ...items.slice(0, at)].find((el) => el.textContent?.trim().toLowerCase().startsWith(e.key.toLowerCase()));
              next?.focus();
            }}
          >
            <span>{o.label}</span>
            <span className="ax-menu-item-end">
              {o.count !== undefined && o.count.toLocaleString()}
              {o.value === value ? <Check size={14} aria-hidden="true" /> : <span style={{ width: 14 }} />}
            </span>
          </button>
        ))}
      </Popover>
    </>
  );
}
