import type { ButtonHTMLAttributes } from "react";

/** The round key: the one thing on the panel you press. Its face says one word; `aria-label` says what it does in full. */
export function MasterKey({
  word,
  icon,
  busy,
  ...rest
}: { word: string; icon: "pause" | "start"; busy?: boolean } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type="button" className="ax-masterkey" aria-busy={busy || undefined} {...rest}>
      {busy ? (
        <span className="ax-spinner" aria-hidden="true" />
      ) : icon === "pause" ? (
        <svg width="18" height="20" viewBox="0 0 18 20" aria-hidden="true">
          <rect x="1" width="5" height="20" />
          <rect x="12" width="5" height="20" />
        </svg>
      ) : (
        <svg width="18" height="20" viewBox="0 0 18 20" aria-hidden="true">
          <path d="M1 0l16 10L1 20z" />
        </svg>
      )}
      <span>{word}</span>
    </button>
  );
}
