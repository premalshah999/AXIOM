// Small shared pieces for the Setup pages, built on the ax- primitives (src/ui/index). Nothing here knows a page.
import { Eye, EyeOff } from "lucide-react";
import { useEffect, useId, useRef, useState, type InputHTMLAttributes, type ReactNode } from "react";
import "./styles/setup.css";
import { Button } from "./ui/index";

/** A muted paragraph under a panel's title: what the panel is for. */
export function Lede({ children }: { children: ReactNode }) {
  return <p className="ax-lede">{children}</p>;
}

/** Fields in two columns (one on a phone). Give a child className="is-wide" to span both. */
export function FormGrid({ children }: { children: ReactNode }) {
  return <div className="ax-grid">{children}</div>;
}

/** The panel's actions row: right-aligned, primary last. `note` sits at the left. */
export function Actions({ children, note, start }: { children: ReactNode; note?: ReactNode; start?: boolean }) {
  return (
    <div className={"ax-actions" + (start ? " is-start" : "")}>
      {note && <span className="ax-actions-note">{note}</span>}
      {children}
    </div>
  );
}

/**
 * Save for a form that spans a long page: stays at the foot of the scroll region while something is unsaved, so the
 * button is never a screen away. Discard returns the page to what is saved.
 */
export function SaveBar({
  dirty,
  saving,
  onSave,
  onDiscard,
  label = "Save changes",
  note,
  disabled,
}: {
  dirty: boolean;
  saving?: boolean;
  onSave: () => void;
  onDiscard?: () => void;
  label?: string;
  note?: ReactNode;
  disabled?: boolean;
}) {
  // Nothing to save, nothing to show: the toast already said "saved".
  if (!dirty && !saving) return null;
  return (
    <div className={"ax-savebar" + (dirty ? " is-dirty" : "")} role="group" aria-label="Save">
      <span className="ax-savebar-note" role="status">
        {saving ? "Saving…" : dirty ? (note ?? "You have unsaved changes.") : "All changes saved."}
      </span>
      {onDiscard && (
        <Button variant="ghost" disabled={!dirty || saving} onClick={onDiscard}>
          Discard
        </Button>
      )}
      <Button variant="primary" loading={saving} disabled={!dirty || disabled} onClick={onSave}>
        {label}
      </Button>
    </div>
  );
}

/** A switch with its name and what it does. Matches the legacy .switch look until that class is retired. */
export function SwitchRow({
  label,
  note,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  note?: ReactNode;
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="ax-switch-row">
      <span className="ax-row-main" id={id}>
        <strong>{label}</strong>
        {note && <small>{note}</small>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={id}
        className="switch"
        disabled={disabled}
        onClick={onChange}
      >
        <i />
      </button>
    </div>
  );
}

/**
 * A destructive action asks first, in place: the button becomes "Cancel / Yes, <verb>". Esc cancels, focus moves to the
 * confirming button. `onConfirm` may be async; the confirm closes when it settles.
 */
export function ConfirmButton({
  label,
  confirm,
  note,
  onConfirm,
  pending,
  disabled,
  variant = "danger",
  size = "sm",
}: {
  label: string;
  confirm: string;
  note?: string;
  onConfirm: () => unknown;
  pending?: boolean;
  disabled?: boolean;
  variant?: "danger" | "secondary";
  size?: "sm" | "md";
}) {
  const [asking, setAsking] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  const was = useRef(false);
  // Focus follows the swap: to the confirming button when it opens, back to the first button when it closes.
  useEffect(() => {
    if (asking || was.current) root.current?.querySelector("button")?.focus();
    was.current = asking;
  }, [asking]);
  return (
    <span
      ref={root}
      className="ax-confirm"
      role={asking ? "group" : undefined}
      aria-label={asking ? note || confirm : undefined}
      onKeyDown={(e) => {
        if (asking && e.key === "Escape") {
          e.stopPropagation();
          setAsking(false);
        }
      }}
    >
      {!asking ? (
        <Button variant={variant} size={size} disabled={disabled || pending} onClick={() => setAsking(true)}>
          {label}
        </Button>
      ) : (
        <>
          {note && <span className="ax-confirm-note">{note}</span>}
          <Button
            variant="danger"
            size={size}
            loading={pending}
            onClick={async () => {
              try {
                await onConfirm();
              } finally {
                setAsking(false);
              }
            }}
          >
            {confirm}
          </Button>
          <Button variant="ghost" size={size} onClick={() => setAsking(false)}>
            Cancel
          </Button>
        </>
      )}
    </span>
  );
}

/** A masked input with a Show/Hide toggle. Used for secrets the user is typing (never for saved ones: the API masks those). */
export function SecretInput({
  value,
  onChange,
  placeholder,
  label,
  id,
  describedBy,
  extra,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  label: string;
  id?: string;
  describedBy?: string;
  extra?: ReactNode;
}) {
  const [shown, setShown] = useState(false);
  return (
    <div className="ax-secret">
      <input
        id={id}
        className="ax-control"
        type={shown ? "text" : "password"}
        autoComplete="off"
        spellCheck={false}
        aria-describedby={describedBy}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <Button
        size="sm"
        variant="ghost"
        aria-label={(shown ? "Hide " : "Show ") + label}
        aria-pressed={shown}
        onClick={() => setShown((s) => !s)}
        icon={shown ? <EyeOff size={15} aria-hidden="true" /> : <Eye size={15} aria-hidden="true" />}
      />
      {extra}
    </div>
  );
}

const splitList = (text: string) =>
  text
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

/**
 * A comma-separated list in one input. It keeps what is typed (the comma and the space after it included) and reports
 * the finished list; it follows the list only when that changes from outside (Discard, a reload).
 */
export function ListInput({
  value,
  onChange,
  ...rest
}: { value: string[]; onChange: (value: string[]) => void } & Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "value" | "onChange"
>) {
  const [text, setText] = useState(value.join(", "));
  useEffect(() => {
    setText((t) => (JSON.stringify(splitList(t)) === JSON.stringify(value) ? t : value.join(", ")));
  }, [value]);
  return (
    <input
      {...rest}
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        onChange(splitList(e.target.value));
      }}
    />
  );
}

/** Rows of label / value for a read-only summary. */
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
