import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

const ITEMS = '[role^="menuitem"]:not(:disabled),[role="option"]';

/**
 * A menu's floating part: placed under its anchor (over it when there is no room below), in a portal so a table's scrolling or the
 * sidebar's edge never clips it. An outside click, Esc, a resize or a scroll under it closes it. Focus moves to the selected or the
 * first item once it is placed; arrow keys, Home and End move between items. Esc returns focus to the anchor.
 */
export function Popover({
  anchor,
  open,
  onClose,
  label,
  role = "menu",
  align = "end",
  match,
  children,
}: {
  anchor: RefObject<HTMLElement | null>;
  open: boolean;
  onClose: () => void;
  label: string;
  role?: "menu" | "listbox";
  align?: "start" | "end";
  /** At least as wide as the anchor. */
  match?: boolean;
  children: ReactNode;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number; minWidth?: number } | null>(null);

  const place = useCallback(() => {
    if (!anchor.current || !box.current) return;
    const r = anchor.current.getBoundingClientRect();
    const w = Math.max(box.current.offsetWidth, match ? r.width : 0);
    const h = box.current.offsetHeight;
    const left = Math.max(8, Math.min(window.innerWidth - w - 8, align === "end" ? r.right - w : r.left));
    const below = r.bottom + 4;
    const top = below + h > window.innerHeight - 8 ? Math.max(8, r.top - h - 4) : below;
    setPos({ top, left, minWidth: match ? r.width : undefined });
  }, [anchor, align, match]);

  useLayoutEffect(() => {
    if (open) place();
    else setPos(null);
  }, [open, place]);
  // The content can change while open (a menu that asks first).
  useLayoutEffect(() => {
    if (open && box.current) place();
  }, [children]); // eslint-disable-line react-hooks/exhaustive-deps

  const placed = !!pos;
  useEffect(() => {
    if (!open || !placed || !box.current) return;
    const el = box.current;
    (el.querySelector<HTMLElement>('[aria-selected="true"]') ?? el.querySelector<HTMLElement>(ITEMS) ?? el.querySelector<HTMLElement>("button:not(:disabled)"))?.focus();
  }, [open, placed]);

  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => {
      const t = e.target as Node;
      if (box.current?.contains(t) || anchor.current?.contains(t)) return;
      onClose();
    };
    const scrolled = (e: Event) => {
      if (!box.current?.contains(e.target as Node)) onClose();
    };
    document.addEventListener("pointerdown", away, true);
    window.addEventListener("resize", onClose);
    document.addEventListener("scroll", scrolled, true);
    return () => {
      document.removeEventListener("pointerdown", away, true);
      window.removeEventListener("resize", onClose);
      document.removeEventListener("scroll", scrolled, true);
    };
  }, [open, onClose, anchor]);

  if (!open) return null;

  const keys = (e: React.KeyboardEvent) => {
    const items = [...(box.current?.querySelectorAll<HTMLElement>(ITEMS) ?? [])];
    const at = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      onClose();
      anchor.current?.focus();
    } else if (e.key === "Tab") onClose();
    else if (e.key === "ArrowDown") {
      e.preventDefault();
      items[(at + 1) % items.length]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      items[(at - 1 + items.length) % items.length]?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      items[0]?.focus();
    } else if (e.key === "End") {
      e.preventDefault();
      items[items.length - 1]?.focus();
    }
  };

  return createPortal(
    <div
      ref={box}
      className="ax-menu"
      role={role}
      aria-label={label}
      style={{ top: pos?.top ?? 0, left: pos?.left ?? 0, minWidth: pos?.minWidth, visibility: pos ? "visible" : "hidden" }}
      onKeyDown={keys}
      onClick={(e) => e.stopPropagation()}
    >
      {children}
    </div>,
    document.body,
  );
}
