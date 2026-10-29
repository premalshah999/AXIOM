import type { ReactNode } from "react";

export type PillTone = "neutral" | "brand" | "ok" | "warn" | "bad" | "info";

/** A short status chip. Sentence case; `dot` adds a leading dot in the tone colour. */
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
    <span className={"ax-pill" + (tone === "neutral" ? "" : " is-" + tone)} title={title}>
      {dot && <i className="ax-pill-dot" aria-hidden="true" />}
      {children}
    </span>
  );
}
