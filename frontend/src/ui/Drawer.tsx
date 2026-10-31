import { X } from "lucide-react";
import { useRef, type ReactNode } from "react";
import { IconButton } from "./Button";
import { useOverlay } from "./overlay";

/**
 * A right-side drawer over the page with its own scroll: a record you opened from a list. Esc, the scrim and the close button close
 * it; focus moves in on open and returns on close, and Tab stays inside. The page behind does not scroll (it never does: the
 * document is fixed). `lead` sits before the title (a monogram), `sub` under it.
 */
export function Drawer({
  open,
  onClose,
  title,
  sub,
  lead,
  children,
  footer,
  width = 600,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  sub?: ReactNode;
  lead?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useOverlay(open, panel, onClose);
  if (!open) return null;
  return (
    <div className="ax-drawer-root">
      <button type="button" className="ax-scrim" aria-label="Close" tabIndex={-1} onClick={onClose} />
      <div
        ref={panel}
        className="ax-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        tabIndex={-1}
        style={{ "--drawer-w": width + "px" } as React.CSSProperties}
      >
        <header className="ax-drawer-head">
          {lead}
          <h2>
            {title}
            {sub && <span className="ax-drawer-sub">{sub}</span>}
          </h2>
          <IconButton label="Close" icon={<X size={16} aria-hidden="true" />} onClick={onClose} />
        </header>
        <div className="ax-drawer-body">{children}</div>
        {footer && <footer className="ax-drawer-foot">{footer}</footer>}
      </div>
    </div>
  );
}
