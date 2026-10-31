import type { ReactNode } from "react";
import { Button } from "./Button";

/** A quiet "still loading" line. Pages keep their own layout while it shows. */
export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="ax-empty" role="status">
      <span className="ax-spinner" aria-hidden="true" />
      {label}
    </p>
  );
}

/** A refusal or failed load: one line, with "Try again" when the caller can retry. */
export function ErrorLine({ error, retry }: { error: Error | string | ReactNode | null; retry?: () => void }) {
  if (!error) return null;
  return (
    <div className="ax-notice is-bad" role="alert">
      <span>{error instanceof Error ? error.message : error}</span>
      {retry && (
        <Button size="sm" variant="ghost" onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  );
}
