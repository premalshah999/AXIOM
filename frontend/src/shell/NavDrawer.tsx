import { LogOut } from "lucide-react";
import { useEffect, useRef } from "react";
import type { Route } from "../context";
import type { Who } from "./AccountMenu";
import { NAV } from "./nav";
import { NavLink } from "./TopBar";

const [PAGES, SOON] = NAV;

/**
 * The pages on a phone: a slide-over on the building's dark green, whichever hour it is. Esc and the scrim close it, focus moves
 * in on opening and back to the opener, and Tab stays inside. Only opening moves focus: the shell re-renders on every poll.
 */
export function NavDrawer({
  route,
  go,
  needs,
  who,
  open,
  onClose,
  onSignOut,
}: {
  route: Route;
  go: (route: Route) => void;
  needs: number;
  who: Who;
  open: boolean;
  onClose: () => void;
  onSignOut: () => void;
}) {
  const side = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    (side.current?.querySelector<HTMLElement>(".ax-sheet-link[aria-current]") ?? side.current?.querySelector<HTMLElement>("a"))?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") close.current();
      else if (e.key === "Tab" && side.current) {
        const items = [...side.current.querySelectorAll<HTMLElement>("a[href],button:not(:disabled)")].filter((el) => el.offsetParent !== null);
        const first = items[0];
        const last = items[items.length - 1];
        if (!first || !last) return;
        if (e.shiftKey && document.activeElement === first) {
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
  }, [open]);

  const name = who.name || who.email || "Signed in";
  return (
    <>
      {open && <button type="button" className="ax-scrim ax-sheet-scrim" aria-label="Close menu" tabIndex={-1} onClick={onClose} />}
      <aside
        ref={side}
        className={"ax-sheet" + (open ? " is-open" : "")}
        aria-label="Main"
        // A closed drawer is off-screen: keep it out of the tab order and the accessibility tree.
        inert={!open || undefined}
      >
        <nav className="ax-sheet-pages" aria-label="Pages">
          {PAGES.items.map((item) => (
            <NavLink key={item.id} item={item} route={route} go={go} needs={needs} className="ax-sheet-link" />
          ))}
        </nav>
        <nav className="ax-sheet-soon" aria-label={SOON.label}>
          <p>{SOON.label}</p>
          {SOON.items.map((item) => (
            <NavLink key={item.id} item={item} route={route} go={go} needs={0} className="ax-sheet-soon-link" />
          ))}
        </nav>
        <div className="ax-sheet-foot">
          <span className="ax-sheet-who">{name}</span>
          <button type="button" className="ax-sheet-out" onClick={onSignOut}>
            <LogOut size={15} aria-hidden="true" />
            Sign out
          </button>
        </div>
      </aside>
    </>
  );
}
