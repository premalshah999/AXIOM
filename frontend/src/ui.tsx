import React, { useEffect, useId, useRef, useState } from "react";
import { X } from "lucide-react";
import { stateInfo } from "./model";

// ---------- formatting ----------
export const words = (s: string) => s.replaceAll("_", " ");
export const shortId = (id: string) => id.slice(0, 8);
export const plural = (n: number, one: string, many = one + "s") =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

export function ago(value?: string | number | null, now = Date.now()) {
  if (!value) return "—";
  const t = typeof value === "number" ? value : new Date(value).getTime();
  if (Number.isNaN(t)) return "—";
  const s = Math.round((now - t) / 1000);
  if (s < 0) return "just now";
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 7) return `${Math.round(s / 86400)}d ago`;
  return day(value);
}
export function day(value?: string | number | null) {
  if (!value) return "—";
  return new Date(value).toLocaleDateString([], {
    month: "short",
    day: "numeric",
    year:
      new Date(value).getFullYear() === new Date().getFullYear()
        ? undefined
        : "numeric",
  });
}
export function stamp(value?: string | number | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}
export function clock(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}
export function elapsed(from?: string | null, now = Date.now()) {
  if (!from) return "";
  return seconds((now - new Date(from).getTime()) / 1000);
}
export function seconds(total: number) {
  const s = Math.max(0, Math.round(total));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}
export const host = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};

// ---------- primitives ----------
export function StatePill({ state, label }: { state: string; label?: string }) {
  const info = stateInfo(state);
  return (
    <span className={"pill tone-" + info.tone}>
      <i aria-hidden="true" />
      {label || info.label}
    </span>
  );
}

export function Dot({ tone }: { tone: string }) {
  return <i className={"dot tone-" + tone} aria-hidden="true" />;
}

export function Field({
  label,
  hint,
  children,
  wide,
}: {
  label: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <label className={"field" + (wide ? " wide" : "")}>
      <span className="field-label">{label}</span>
      {children}
      {hint && <small className="field-hint">{hint}</small>}
    </label>
  );
}

export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" aria-hidden="true" />
      {label}
    </div>
  );
}

export function ErrorLine({
  error,
  retry,
}: {
  error: Error | string | null;
  retry?: () => void;
}) {
  if (!error) return null;
  return (
    <div className="error-line" role="alert">
      <span>{typeof error === "string" ? error : error.message}</span>
      {retry && (
        <button className="btn sm" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}

// Two-step inline confirmation for consequential actions.
export function Confirm({
  label,
  confirm,
  onConfirm,
  pending,
  disabled,
  danger = true,
  note,
}: {
  label: string;
  confirm: string;
  onConfirm: () => unknown;
  pending?: boolean;
  disabled?: boolean;
  danger?: boolean;
  note?: string;
}) {
  const [asking, setAsking] = useState(false);
  const yes = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (asking) yes.current?.focus();
  }, [asking]);
  if (!asking)
    return (
      <button
        type="button"
        className={"btn sm" + (danger ? " danger" : "")}
        disabled={disabled || pending}
        onClick={() => setAsking(true)}
      >
        {label}
      </button>
    );
  return (
    <span className="confirm" role="group" aria-label={note || confirm}>
      {note && <span className="confirm-note">{note}</span>}
      <button
        ref={yes}
        type="button"
        className={"btn sm" + (danger ? " danger-solid" : " primary")}
        disabled={pending}
        onClick={async () => {
          await onConfirm();
          setAsking(false);
        }}
      >
        {pending ? "Working…" : confirm}
      </button>
      <button
        type="button"
        className="btn sm quiet"
        onClick={() => setAsking(false)}
      >
        Cancel
      </button>
    </span>
  );
}

// Native modal dialog: focus containment, Escape and inert background for free.
export function Dialog({
  open,
  onClose,
  title,
  children,
  variant = "center",
  labelledBy,
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  variant?: "center" | "drawer";
  labelledBy?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const opener = useRef<Element | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) {
      opener.current = document.activeElement;
      el.showModal();
    } else if (!open && el.open) {
      el.close();
    }
  }, [open]);
  useEffect(
    () => () => {
      const target = opener.current as HTMLElement | null;
      if (target && document.contains(target)) target.focus?.();
    },
    [],
  );
  return (
    <dialog
      ref={ref}
      className={"dialog " + variant}
      aria-labelledby={labelledBy || (title ? titleId : undefined)}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
      onClose={() => {
        const target = opener.current as HTMLElement | null;
        if (target && document.contains(target)) target.focus?.();
      }}
    >
      <div className="dialog-body">
        {title && (
          <header className="dialog-head">
            <h2 id={titleId}>{title}</h2>
            <button
              className="icon-btn"
              aria-label="Close"
              type="button"
              onClick={onClose}
            >
              <X size={18} />
            </button>
          </header>
        )}
        {open && children}
      </div>
    </dialog>
  );
}

// One cell per target application: filled cells are confirmed submissions.
export function Tape({
  confirmed,
  target,
}: {
  confirmed: number;
  target: number;
}) {
  const cells = Math.max(1, Math.min(500, target));
  const done = Math.min(confirmed, cells);
  const rows = Math.ceil(cells / 100);
  return (
    <div
      className={"tape" + (rows > 1 ? " multi" : "")}
      role="img"
      aria-label={`${confirmed} of ${target} applications confirmed`}
    >
      {Array.from({ length: rows }, (_, r) => {
        const start = r * 100;
        const count = Math.min(100, cells - start);
        return (
          <div className="tape-row" key={r}>
            {Array.from({ length: count }, (_, i) => (
              <i key={i} className={start + i < done ? "on" : ""} />
            ))}
          </div>
        );
      })}
    </div>
  );
}

export function KeyValues({
  items,
}: {
  items: [React.ReactNode, React.ReactNode][];
}) {
  return (
    <dl className="kv">
      {items.map(([k, v], i) => (
        <div key={i}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function External({
  href,
  children,
  className,
}: {
  href: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <a
      className={"external " + (className || "")}
      href={href}
      target="_blank"
      rel="noreferrer noopener"
    >
      {children}
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

/** "examplecorp · boards.greenhouse.io" for a pasted link whose title isn't known until ranking. */
export function linkHost(url?: string) {
  try {
    const u = new URL(url || "");
    const first = u.pathname.split("/").filter(Boolean)[0] || "";
    const host = u.hostname.replace(/^www\./, "");
    const tenant = host.split(".")[0];
    // Shared ATS hosts name the employer in the path or the subdomain.
    if (/greenhouse\.io$|lever\.co$|ashbyhq\.com$/.test(host) && first)
      return `${first} · ${host}`;
    if (/myworkdayjobs\.com$|icims\.com$|oraclecloud\.com$/.test(host))
      return `${tenant} · ${host.split(".").slice(1).join(".")}`;
    return host;
  } catch {
    return "";
  }
}
