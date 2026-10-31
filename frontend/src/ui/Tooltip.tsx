import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * A short explanation on hover or keyboard focus: what a number counts, why a status reads as it does, what an icon button does.
 * Never the only place something essential is said; a phone has no hover.
 */
export function Tooltip({ label, children, className }: { label: ReactNode; children: ReactNode; className?: string }) {
  const [show, setShow] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const anchor = useRef<HTMLSpanElement>(null);
  const tip = useRef<HTMLDivElement>(null);
  const timer = useRef(0);
  const id = useId();

  useLayoutEffect(() => {
    if (!show || !anchor.current || !tip.current) return setPos(null);
    const r = anchor.current.getBoundingClientRect();
    const w = tip.current.offsetWidth;
    const h = tip.current.offsetHeight;
    const above = r.top - h - 6;
    setPos({
      top: above < 8 ? r.bottom + 6 : above,
      left: Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2)),
    });
  }, [show, label]);

  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => {
    if (!show) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setShow(false);
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [show]);

  if (!label) return <>{children}</>;
  const hide = () => {
    window.clearTimeout(timer.current);
    setShow(false);
  };
  return (
    <span
      ref={anchor}
      className={"ax-tipped " + (className || "")}
      aria-describedby={show ? id : undefined}
      onMouseEnter={() => {
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => setShow(true), 250);
      }}
      onMouseLeave={hide}
      onFocus={(e) => {
        if ((e.target as HTMLElement).matches(":focus-visible")) setShow(true);
      }}
      onBlur={hide}
    >
      {children}
      {show &&
        createPortal(
          <div
            ref={tip}
            id={id}
            role="tooltip"
            className="ax-tip"
            style={{ top: pos?.top ?? 0, left: pos?.left ?? 0, visibility: pos ? "visible" : "hidden" }}
          >
            {label}
          </div>,
          document.body,
        )}
    </span>
  );
}
