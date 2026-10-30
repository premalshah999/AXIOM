import type { ReactNode } from "react";

/** A plain titled section on a hairline, not a card. `actions` sits at the right of the title; `flush` is for a table inside. */
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
