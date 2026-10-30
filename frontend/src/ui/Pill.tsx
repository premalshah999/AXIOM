import type { ReactNode } from "react";

export type PillTone = "neutral" | "brand" | "ok" | "warn" | "bad" | "info" | "fix";

/**
 * A short status. Every tone and the dot are always in the markup (is-<tone>, has-dot when `dot` is passed), and
 * components.css decides what each one looks like, so changing the look never means changing this file.
 */
export function Pill({
  tone = "neutral",
  children,
  title,
  dot,
}: {
  tone?: PillTone;
  children: ReactNode;
  title?: string;
  dot?: boolean;
}) {
  return (
    <span className={"ax-pill is-" + tone + (dot ? " has-dot" : "")} title={title}>
      <i className="ax-pill-dot" aria-hidden="true" />
      {children}
    </span>
  );
}
