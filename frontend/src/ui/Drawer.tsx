import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

/**
 * A right-side drawer over the page with its own scroll. Esc, the scrim and the close button close it; focus moves in
 * on open and returns to the opener on close, and Tab stays inside. The page behind does not scroll (it never does:
 * the document is fixed), and the body of the drawer is the one scroll region while it is open.
 */
export function Drawer({
  open,
  onClose,
  title,
  children,
  footer,
  width = 600,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    const node = panel.current;
    node?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      } else if (e.key === "Tab" && node) {
        const items = [...node.querySelectorAll<HTMLElement>('a[href],button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])')].filter((el) => el.offsetParent !== null);
        if (items.length === 0) return;
        const first = items[0]!;
        const last = items[items.length - 1]!;
        if (e.shiftKey && (document.activeElement === first || document.activeElement === node)) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      opener?.focus?.();
    };
  }, [open, onClose]);
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
          <h2>{title}</h2>
          <button type="button" className="ax-icon-btn" aria-label="Close" onClick={onClose}>
            <X size={16} />
          </button>
        </header>
        <div className="ax-drawer-body">{children}</div>
        {footer && <footer className="ax-drawer-foot">{footer}</footer>}
      </div>
    </div>
  );
}
