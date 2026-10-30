// Small pieces the work pages (Overview, Jobs, Applications, Needs you, the job drawer) share: the status that maps the
// product's states onto the Pill tones, a search box, filter text, a company-and-role cell, an overflow menu for row
// actions and a two-step confirm button. Tokens only; the CSS is styles/components.css.
import { MoreHorizontal, Search } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { stateInfo, type Tone } from "./model";
import { Button, Pill, type PillTone } from "./ui/index";

// model.ts tones onto the primitives' tones, one for one, so components.css can give each its own look.
const TONE: Record<Tone, PillTone> = {
  ok: "ok",
  run: "brand",
  ask: "warn",
  hold: "info",
  fix: "fix",
  bad: "bad",
  idle: "neutral",
};

export function StatusPill({ state, label, title }: { state: string; label?: string; title?: string }) {
  const info = stateInfo(state);
  return (
    <Pill tone={TONE[info.tone]} dot title={title}>
      {label || info.label}
    </Pill>
  );
}

export function SearchBox({
  value,
  onChange,
  label,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  label: string;
  placeholder: string;
}) {
  return (
    <label className="ax-search">
      <Search size={15} aria-hidden="true" />
      <input
        type="search"
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

export type ChipItem = { id: string; label: string; count?: number };

/** One quiet row of text filters with counts, acting as one choice (aria-pressed on each). */
export function Chips({
  items,
  value,
  onChange,
  label,
}: {
  items: ChipItem[];
  value: string;
  onChange: (id: string) => void;
  label: string;
}) {
  return (
    <div className="ax-chips" role="group" aria-label={label}>
      {items.map((c) => (
        <button key={c.id} type="button" className="ax-chip" aria-pressed={value === c.id} onClick={() => onChange(c.id)}>
          {c.label}
          {c.count !== undefined && <span className="ax-chip-n">{c.count.toLocaleString()}</span>}
        </button>
      ))}
    </div>
  );
}

/** Company and the role beside it in muted text, on one line that ends in an ellipsis. Opens the job. */
export function JobCell({
  company,
  role,
  title,
  onOpen,
}: {
  company: string;
  role: string;
  title?: string;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      className="ax-job"
      title={title}
      onClick={(e) => {
        e.stopPropagation();
        onOpen();
      }}
    >
      <strong>{company}</strong>
      <span>{role}</span>
    </button>
  );
}

/** A muted one-line note (a reason, a platform) that shows its full text on hover. */
export function Note({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className="ax-note" title={title ?? (typeof children === "string" ? children : undefined)}>
      {children}
    </span>
  );
}

// ---------- overflow menu ----------
export type MenuAction = {
  id: string;
  label: string;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
  /** A question to ask first ("Delete this job?"): the menu swaps to a Cancel / confirm pair. */
  confirm?: string;
  confirmLabel?: string;
};

/**
 * The row's "more" menu. The menu is a fixed-position portal, so a table's own scrolling never clips it. Arrow keys,
 * Home and End move; Esc closes and returns focus to the button; a destructive action asks inside the menu.
 */
export function RowMenu({
  label,
  actions,
  busy,
  trigger,
}: {
  label: string;
  actions: MenuAction[];
  busy?: boolean;
  /** Replaces the three-dot button (the drawer uses a labelled "More" button). */
  trigger?: (props: { open: boolean }) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [asking, setAsking] = useState<MenuAction | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  const close = useCallback((refocus = true) => {
    setOpen(false);
    setAsking(null);
    if (refocus) button.current?.focus();
  }, []);

  useLayoutEffect(() => {
    if (!open || !button.current || !menu.current) return;
    const r = button.current.getBoundingClientRect();
    const w = menu.current.offsetWidth;
    const h = menu.current.offsetHeight;
    const left = Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w));
    const below = r.bottom + 4;
    const top = below + h > window.innerHeight - 8 ? Math.max(8, r.top - h - 4) : below;
    setPos({ top, left });
  }, [open, asking]);

  // Focus moves in once the menu is placed: a hidden (not yet positioned) menu cannot take focus.
  useEffect(() => {
    if (open && pos) menu.current?.querySelector<HTMLElement>("button:not(:disabled)")?.focus();
  }, [open, pos, asking]);
  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => {
      const t = e.target as Node;
      if (menu.current?.contains(t) || button.current?.contains(t)) return;
      close(false);
    };
    const leave = () => close(false);
    document.addEventListener("pointerdown", away, true);
    window.addEventListener("resize", leave);
    // Any scroll under the menu (the table, the page) would leave it floating over the wrong row.
    document.addEventListener("scroll", leave, true);
    return () => {
      document.removeEventListener("pointerdown", away, true);
      window.removeEventListener("resize", leave);
      document.removeEventListener("scroll", leave, true);
    };
  }, [open, close]);

  const move = (e: React.KeyboardEvent) => {
    const items = [...(menu.current?.querySelectorAll<HTMLElement>("button:not(:disabled)") ?? [])];
    const at = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close();
    } else if (e.key === "Tab") close(false);
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

  if (!actions.length) return null;
  return (
    <>
      <button
        ref={button}
        type="button"
        className={trigger ? "ax-btn is-sm" : "ax-icon-btn ax-more"}
        aria-label={trigger ? undefined : label}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={busy}
        onClick={(e) => {
          e.stopPropagation();
          setPos(null);
          setOpen((v) => !v);
          setAsking(null);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && !open) {
            e.preventDefault();
            setPos(null);
            setOpen(true);
          }
        }}
      >
        {trigger ? trigger({ open }) : <MoreHorizontal size={16} aria-hidden="true" />}
      </button>
      {open &&
        createPortal(
          <div
            ref={menu}
            className="ax-menu"
            role="menu"
            aria-label={label}
            style={{ top: pos?.top ?? 0, left: pos?.left ?? 0, visibility: pos ? "visible" : "hidden" }}
            onKeyDown={move}
            onClick={(e) => e.stopPropagation()}
          >
            {asking ? (
              <div className="ax-menu-ask" role="group" aria-label="Confirm">
                <p>{asking.confirm}</p>
                <div>
                  <Button size="sm" variant="ghost" onClick={() => setAsking(null)} autoFocus>
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    variant={asking.danger ? "danger" : "primary"}
                    onClick={() => {
                      const run = asking.onSelect;
                      close(false);
                      run();
                    }}
                  >
                    {asking.confirmLabel || asking.label}
                  </Button>
                </div>
              </div>
            ) : (
              actions.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  role="menuitem"
                  className={"ax-menu-item" + (a.danger ? " is-danger" : "")}
                  disabled={a.disabled}
                  onClick={() => {
                    if (a.confirm) setAsking(a);
                    else {
                      close(false);
                      a.onSelect();
                    }
                  }}
                >
                  {a.label}
                </button>
              ))
            )}
          </div>,
          document.body,
        )}
    </>
  );
}

/** A button that asks once ("Remove 5 jobs?") before it acts; Esc or Cancel backs out. */
export function ConfirmButton({
  label,
  question,
  confirmLabel,
  onConfirm,
  loading,
  disabled,
  danger = true,
  size = "sm",
}: {
  label: string;
  question: string;
  confirmLabel: string;
  onConfirm: () => unknown;
  loading?: boolean;
  disabled?: boolean;
  danger?: boolean;
  size?: "sm" | "md";
}) {
  const [asking, setAsking] = useState(false);
  const yes = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (asking) yes.current?.focus();
  }, [asking]);
  if (!asking)
    return (
      <Button size={size} variant={danger ? "danger" : "secondary"} disabled={disabled} loading={loading} onClick={() => setAsking(true)}>
        {label}
      </Button>
    );
  return (
    <span
      className="ax-ask"
      role="group"
      aria-label={question}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          setAsking(false);
        }
      }}
    >
      <span className="ax-ask-text">{question}</span>
      <button
        ref={yes}
        type="button"
        className={"ax-btn is-sm " + (danger ? "is-danger" : "is-primary")}
        onClick={async () => {
          await onConfirm();
          setAsking(false);
        }}
      >
        {confirmLabel}
      </button>
      <Button size="sm" variant="ghost" onClick={() => setAsking(false)}>
        Cancel
      </Button>
    </span>
  );
}

/** A single plain sentence with an action ("3 questions need you"). warn and bad add a small dot; nothing else is coloured. */
export function Notice({
  tone = "info",
  children,
  action,
}: {
  tone?: "info" | "warn" | "bad";
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className={"ax-notice is-" + tone} role="status">
      <span>{children}</span>
      {action}
    </div>
  );
}
