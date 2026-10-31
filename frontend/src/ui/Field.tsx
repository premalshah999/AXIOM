import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

type Meta = { label: string; hint?: ReactNode; error?: ReactNode };

function Wrap({
  id,
  label,
  hint,
  error,
  className,
  children,
}: Meta & { id: string; className?: string; children: ReactNode }) {
  return (
    <div className={"ax-field " + (className || "")}>
      <label className="ax-label" htmlFor={id}>
        {label}
      </label>
      {children}
      {error ? (
        <p className="ax-error" id={id + "-msg"} role="alert">
          {error}
        </p>
      ) : (
        hint && (
          <p className="ax-hint" id={id + "-msg"}>
            {hint}
          </p>
        )
      )}
    </div>
  );
}

const describe = (id: string, m: Meta) => (m.error || m.hint ? id + "-msg" : undefined);

/** A labelled text input with an optional hint and error. */
export function Input({
  label,
  hint,
  error,
  className,
  ...rest
}: Meta & InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <Wrap id={id} label={label} hint={hint} error={error} className={className}>
      <input
        id={id}
        className="ax-control"
        aria-invalid={error ? true : undefined}
        aria-describedby={describe(id, { label, hint, error })}
        {...rest}
      />
    </Wrap>
  );
}

/** A labelled select; pass <option> children. */
export function Select({
  label,
  hint,
  error,
  className,
  children,
  ...rest
}: Meta & SelectHTMLAttributes<HTMLSelectElement>) {
  const id = useId();
  return (
    <Wrap id={id} label={label} hint={hint} error={error} className={className}>
      <select
        id={id}
        className="ax-control"
        aria-invalid={error ? true : undefined}
        aria-describedby={describe(id, { label, hint, error })}
        {...rest}
      >
        {children}
      </select>
    </Wrap>
  );
}

/** A labelled textarea. `mono` sets it in the code face (JSON, LaTeX). */
export function Textarea({
  label,
  hint,
  error,
  className,
  mono,
  ...rest
}: Meta & { mono?: boolean } & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const id = useId();
  return (
    <Wrap id={id} label={label} hint={hint} error={error} className={className}>
      <textarea
        id={id}
        className={"ax-control" + (mono ? " code" : "")}
        aria-invalid={error ? true : undefined}
        aria-describedby={describe(id, { label, hint, error })}
        {...rest}
      />
    </Wrap>
  );
}

/**
 * A label over any control (a list input, a secret input, two inputs side by side). The label wraps the control, so a
 * click on it focuses the control and no id is needed. Use Input, Select or Textarea when the control is one of those.
 */
export function Field({ label, hint, error, children, className }: Meta & { children: ReactNode; className?: string }) {
  return (
    <label className={"ax-field " + (className || "")}>
      <span className="ax-label">{label}</span>
      {children}
      {error ? (
        <span className="ax-error" role="alert">
          {error}
        </span>
      ) : (
        hint && <span className="ax-hint">{hint}</span>
      )}
    </label>
  );
}

/** A checkbox with its name and, only where a person could get it wrong, one line of what it does. */
export function Check({
  checked,
  onChange,
  label,
  note,
  disabled,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: ReactNode;
  note?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className="ax-check">
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="ax-check-text">
        <span>{label}</span>
        {note && <small className="ax-hint">{note}</small>}
      </span>
    </label>
  );
}
