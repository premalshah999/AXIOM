// Small pieces the work pages (Overview, Jobs, Applications, Needs you, the job drawer) share: the status that maps the
// product's states onto the Pill tones, a search box, filter text, a company-and-role cell, an overflow menu for row
// actions and a two-step confirm button. Tokens only; the CSS is styles/components.css.
import { MoreHorizontal, Search } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { plainError } from "./format";
import { stateInfo, type Tone } from "./model";
import { Button, Monogram, Pill, Popover, type PillTone } from "./ui/index";

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

export function StatusPill({ state, label, title, tone }: { state: string; label?: string; title?: string; tone?: Tone }) {
  const info = stateInfo(state);
  return (
    <Pill tone={TONE[tone || info.tone]} dot title={title}>
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

type ChipItem = { id: string; label: string; count?: number };

/** A row of filter keys with counts, acting as one choice (aria-pressed on each). */
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

/**
 * A job in a table: its monogram, the employer and the role beside it, on one line that ends in an ellipsis. Opens the job. In a
 * narrow table they stack, and `meta` (the status and time the table has dropped) shows under them.
 */
export function JobCell({
  company,
  url,
  role,
  title,
  meta,
  hint,
  unread,
  onOpen,
}: {
  company: string;
  /** The posting's address: with it, the company's icon can replace its letter. */
  url?: string;
  role: string;
  title?: string;
  meta?: ReactNode;
  /** The posting's host, shown muted after the role when the employer or role was not read from the posting. */
  hint?: string;
  /** The role is a stand-in ("Role not read yet"), so it reads quieter. */
  unread?: boolean;
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
      <Monogram name={company} url={url} />
      <strong title={company}>{company}</strong>
      <span className={"ax-job-role" + (unread ? " is-unread" : "")} title={hint ? `${role} · ${hint}` : role}>
        {role}
        {hint && <span className="ax-job-hint">{hint}</span>}
      </span>
      {meta && <span className="ax-job-meta">{meta}</span>}
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
 * The row's "more" menu. Arrow keys, Home and End move; Esc closes and returns focus to the button; a destructive action asks inside
 * the menu.
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
  const button = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => {
    setOpen(false);
    setAsking(null);
  }, []);
  if (!actions.length) return null;
  return (
    <>
      <button
        ref={button}
        type="button"
        className={trigger ? "ax-btn" : "ax-icon-btn ax-more"}
        aria-label={trigger ? undefined : label}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={busy}
        onClick={(e) => {
          e.stopPropagation();
          setAsking(null);
          setOpen((v) => !v);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" && !open) {
            e.preventDefault();
            setOpen(true);
          }
        }}
      >
        {trigger ? trigger({ open }) : <MoreHorizontal size={16} aria-hidden="true" />}
      </button>
      <Popover anchor={button} open={open} onClose={close} label={label}>
        {asking ? (
          <div className="ax-menu-ask" role="group" aria-label="Confirm">
            <p>{asking.confirm}</p>
            <div>
              <Button size="sm" variant="ghost" onClick={() => setAsking(null)} autoFocus>
                Cancel
              </Button>
              <Button
                size="sm"
                variant={asking.danger ? "danger-solid" : "primary"}
                onClick={() => {
                  const run = asking.onSelect;
                  close();
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
                  close();
                  a.onSelect();
                }
              }}
            >
              {a.label}
            </button>
          ))
        )}
      </Popover>
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
        className={"ax-btn is-sm " + (danger ? "is-danger-solid" : "is-primary")}
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

/**
 * A single plain sentence with an action ("3 questions need you"). warn and bad add a small dot; nothing else is
 * coloured. `lead` is the sentence a page is about (Overview's state): body size and full-strength text.
 */
export function Notice({
  tone = "info",
  children,
  action,
  lead,
}: {
  tone?: "info" | "warn" | "bad";
  children: ReactNode;
  action?: ReactNode;
  lead?: boolean;
}) {
  return (
    <div className={"ax-notice is-" + tone + (lead ? " is-lead" : "")} role="status">
      <span>{children}</span>
      {action}
    </div>
  );
}

/** Rows of label and value for a read-only summary (the same markup as setup-kit's Facts, without its stylesheet). */
export function Facts({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="ax-kv">
      {items.map(([k, v], i) => (
        <div key={i}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A list that could not be read: the server's words and a way to try again. */
export function LoadError({ error, retry }: { error: Error; retry: () => unknown }) {
  return (
    <Notice
      tone="bad"
      action={
        <Button size="sm" onClick={() => void retry()}>
          Try again
        </Button>
      }
    >
      {plainError(error.message)}
    </Notice>
  );
}
