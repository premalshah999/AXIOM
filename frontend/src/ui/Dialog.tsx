import { X } from "lucide-react";
import { useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { IconButton } from "./Button";
import { useOverlay } from "./overlay";

/**
 * A small centred dialog for one short task (add a job, answer one question, confirm something that cannot be undone). `footer`
 * holds its buttons, the primary last. A long form or a record belongs in a Drawer or on a page.
 */
export function Dialog({
  open,
  onClose,
  title,
  sub,
  children,
  footer,
  width = 480,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  sub?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useOverlay(open, panel, onClose);
  if (!open) return null;
  return createPortal(
    <div className="ax-dialog-root">
      <button type="button" className="ax-scrim" aria-label="Close" tabIndex={-1} onClick={onClose} />
      <div
        ref={panel}
        className="ax-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        tabIndex={-1}
        style={{ "--dialog-w": width + "px" } as React.CSSProperties}
      >
        <header className="ax-dialog-head">
          <h2>
            {title}
            {sub && <p>{sub}</p>}
          </h2>
          <IconButton label="Close" icon={<X size={16} aria-hidden="true" />} onClick={onClose} />
        </header>
        <div className="ax-dialog-body">{children}</div>
        {footer && <footer className="ax-dialog-foot">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}
