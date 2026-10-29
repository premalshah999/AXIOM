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

/** A labelled textarea. */
export function Textarea({
  label,
  hint,
  error,
  className,
  ...rest
}: Meta & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const id = useId();
  return (
    <Wrap id={id} label={label} hint={hint} error={error} className={className}>
      <textarea
        id={id}
        className="ax-control"
        aria-invalid={error ? true : undefined}
        aria-describedby={describe(id, { label, hint, error })}
        {...rest}
      />
    </Wrap>
  );
}
