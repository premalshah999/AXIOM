import type { ReactNode } from "react";

/**
 * A raised surface that groups something, with a title and `actions` at its right; inside a drawer, a dialog or another panel it is
 * a plain section. `flush` drops the padding for a list or table that brings its own rules.
 */
export function Panel({
  title,
  actions,
  children,
  flush,
  className = "",
  id,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  className?: string;
  id?: string;
}) {
  return (
    <section className={"ax-panel " + className} id={id}>
      {(title !== undefined || actions) && (
        <header className="ax-panel-head">
          {title !== undefined && <h2>{title}</h2>}
          {actions && <div className="ax-panel-actions">{actions}</div>}
        </header>
      )}
      <div className={"ax-panel-body" + (flush ? " is-flush" : "")}>{children}</div>
    </section>
  );
}
