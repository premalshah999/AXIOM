// Small shared pieces for Profile and Settings, built on the ax- primitives (src/ui/index). Nothing here knows a page.
import { Eye, EyeOff } from "lucide-react";
import { useEffect, useId, useState, type InputHTMLAttributes, type ReactNode } from "react";
import "./styles/setup.css";
import { api } from "./api";
import { useAction } from "./hooks";
import { Button, ConfirmButton } from "./ui/index";

/** On a page, one column of labelled rows divided by hairlines; in a drawer, stacked fields. */
export function FormGrid({ children }: { children: ReactNode }) {
  return <div className="ax-grid">{children}</div>;
}

/** A section's actions row, left-aligned on a page, right-aligned in a drawer. */
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

/** A switch with its name and what it does. */
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
        className="ax-switch"
        disabled={disabled}
        onClick={onChange}
      >
        <i />
      </button>
    </div>
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

/**
 * Worker counts are sized when the app starts. This says so and restarts now: new starts pause, a final submit in
 * progress finishes, then the app comes back on its own in under a minute and this page reloads.
 */
export function RestartNotice({ message }: { message: string }) {
  const { pending, run } = useAction();
  const [restarting, setRestarting] = useState(false);
  const restart = () =>
    run(
      "restart",
      async () => {
        await api("/system/restart", "POST");
        setRestarting(true);
        const started = Date.now();
        const wait = async () => {
          await new Promise((r) => setTimeout(r, 4000));
          try {
            await api("/runtime-health", "GET");
            if (Date.now() - started > 8000) return location.reload();
          } catch {
            /* still restarting */
          }
          if (Date.now() - started < 180000) void wait();
        };
        void wait();
      },
      "Restarting. Running applications resume after the restart.",
    );
  return (
    <div className="ax-notice is-warn" role="status">
      <span>{restarting ? "Restarting. This page reloads when the app is back." : message}</span>
      {!restarting && (
        <ConfirmButton
          label="Restart now"
          confirm="Restart"
          note="Applications pause, final submits finish, then it comes back."
          variant="secondary"
          size="md"
          pending={pending === "restart"}
          onConfirm={restart}
        />
      )}
    </div>
  );
}
