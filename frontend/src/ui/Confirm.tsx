import { useEffect, useRef, useState } from "react";
import { Button } from "./Button";

/**
 * A destructive action asks first, in place: the button becomes "Yes, <verb> / Cancel". Esc cancels, focus moves to the
 * confirming button. `onConfirm` may be async; the question closes when it settles.
 */
export function ConfirmButton({
  label,
  confirm,
  note,
  onConfirm,
  pending,
  disabled,
  variant = "danger",
  size = "sm",
}: {
  label: string;
  confirm: string;
  note?: string;
  onConfirm: () => unknown;
  pending?: boolean;
  disabled?: boolean;
  variant?: "danger" | "secondary";
  size?: "sm" | "md";
}) {
  const [asking, setAsking] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  const was = useRef(false);
  // Focus follows the swap: to the confirming button when it opens, back to the first button when it closes.
  useEffect(() => {
    if (asking || was.current) root.current?.querySelector("button")?.focus();
    was.current = asking;
  }, [asking]);
  return (
    <span
      ref={root}
      className="ax-ask"
      role={asking ? "group" : undefined}
      aria-label={asking ? note || confirm : undefined}
      onKeyDown={(e) => {
        if (asking && e.key === "Escape") {
          e.stopPropagation();
          setAsking(false);
        }
      }}
    >
      {!asking ? (
        <Button variant={variant} size={size} disabled={disabled || pending} onClick={() => setAsking(true)}>
          {label}
        </Button>
      ) : (
        <>
          {note && <span className="ax-ask-text">{note}</span>}
          <Button
            variant="danger"
            size={size}
            loading={pending}
            onClick={async () => {
              try {
                await onConfirm();
              } finally {
                setAsking(false);
              }
            }}
          >
            {confirm}
          </Button>
          <Button variant="ghost" size={size} onClick={() => setAsking(false)}>
            Cancel
          </Button>
        </>
      )}
    </span>
  );
}
