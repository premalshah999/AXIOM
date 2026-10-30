import type { ReactNode } from "react";

export type PillTone = "neutral" | "brand" | "ok" | "warn" | "bad" | "info";

/**
 * A status as plain muted text. Only warn and bad (needs an answer, failed) get a small coloured dot and full-strength
 * text; success and everything else stay quiet. `dot` is accepted for older call sites and adds nothing on its own.
 */
export function Pill({
  tone = "neutral",
  children,
  title,
}: {
  tone?: PillTone;
  children: ReactNode;
  title?: string;
  dot?: boolean;
}) {
  const attention = tone === "warn" || tone === "bad";
  return (
    <span className={"ax-pill" + (attention ? " is-" + tone : "")} title={title}>
      {attention && <i className="ax-pill-dot" aria-hidden="true" />}
      {children}
    </span>
  );
}
