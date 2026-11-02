import type { ReactNode } from "react";
import { plainError } from "../format";
import { Button } from "./Button";

/** A quiet "still loading" line. Lists use Skeleton instead, so the page keeps its shape. */
export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="ax-loading" role="status">
      <span className="ax-spinner" aria-hidden="true" />
      {label}
    </p>
  );
}

/** Rows of placeholder in the shape of a list of jobs: a monogram and two lengths of text. */
export function Skeleton({ rows = 5, mono = true }: { rows?: number; mono?: boolean }) {
  return (
    <div className="ax-skel-rows" role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="ax-skel-row">
          {mono && <span className="ax-skeleton is-mono" />}
          <span className="ax-skeleton" style={{ width: `${22 - (i % 3) * 4}%` }} />
          <span className="ax-skeleton" style={{ width: `${44 - ((i * 2) % 5) * 5}%` }} />
        </div>
      ))}
    </div>
  );
}

/** A refusal or failed load: one line, with "Try again" when the caller can retry. */
export function ErrorLine({ error, retry }: { error: Error | string | ReactNode | null; retry?: () => void }) {
  if (!error) return null;
  return (
    <div className="ax-notice is-bad" role="alert">
      <span>{error instanceof Error ? plainError(error.message) : error}</span>
      {retry && (
        <Button size="sm" variant="ghost" onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  );
}
