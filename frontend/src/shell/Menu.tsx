import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * A small dropdown from the top bar: a button and a menu under it. Closes on an outside click or Esc, focus moves to the first
 * item on opening and back to the button on Esc, and arrow keys move. Items are role="menuitem" in `children`.
 */
export function Menu({
  label,
  button,
  buttonClass,
  title,
  children,
}: {
  /** The menu's name and, when the button shows no text, the button's. */
  label: string;
  button: ReactNode;
  buttonClass: string;
  title?: string;
  children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

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
    <div className="ax-bar-menu" ref={box}>
      <button type="button" className={buttonClass} aria-haspopup="menu" aria-expanded={open} aria-label={label} title={title} onClick={() => setOpen((v) => !v)}>
        {button}
      </button>
      {open && (
        <div className="ax-pop ax-bar-pop" role="menu" aria-label={label} onKeyDown={arrows}>
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}
