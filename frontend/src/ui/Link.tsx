import type { ReactNode } from "react";

/** A link to another site: opens a new tab and says so to a screen reader. */
export function External({ href, children, className = "" }: { href: string; children: ReactNode; className?: string }) {
  return (
    <a className={"ax-link " + className} href={href} target="_blank" rel="noreferrer noopener">
      {children}
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}
