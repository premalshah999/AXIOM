import { PanelLeft, Search } from "lucide-react";
import { useEffect, useRef } from "react";
import { routeUrl, type Route } from "../context";
import { AccountMenu, type Who } from "./AccountMenu";
import { BrandMark } from "./Brand";
import { isActive, NAV, type NavItem } from "./nav";

const MAC = /mac/i.test(navigator.platform);

/**
 * The sidebar (DESIGN.md, Navigation): six pages, a quiet "Coming soon" list, and the account row. 220px open, 56px
 * collapsed to an icon rail with the labels as tooltips; the choice is remembered by the caller. Under 900px it is a
 * slide-over drawer opened from the top bar.
 */
export function Sidebar({
  route,
  go,
  needs,
  collapsed,
  onToggle,
  drawer,
  open,
  onClose,
  onSearch,
  who,
  onSignOut,
}: {
  route: Route;
  go: (route: Route) => void;
  needs: number;
  collapsed: boolean;
  onToggle: () => void;
  /** True under 900px: the sidebar is a drawer, always shown with labels. */
  drawer: boolean;
  open: boolean;
  onClose: () => void;
  onSearch: () => void;
  who: Who;
  onSignOut: () => void;
}) {
  const rail = collapsed && !drawer;
  const side = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  close.current = onClose;

  // The drawer: Esc closes, focus moves in on open and returns to the opener, Tab stays inside. Only opening moves focus:
  // the shell re-renders on every poll.
  useEffect(() => {
    if (!drawer || !open) return;
    const opener = document.activeElement as HTMLElement | null;
    (side.current?.querySelector<HTMLElement>(".ax-nav[aria-current]") ?? side.current?.querySelector<HTMLElement>("a"))?.focus();
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
  }, [drawer, open]);

  const hidden = drawer && !open;
  return (
    <>
      {drawer && open && <button type="button" className="ax-scrim ax-side-scrim" aria-label="Close menu" tabIndex={-1} onClick={onClose} />}
      <aside
        ref={side}
        className={["ax-side", rail ? "is-rail" : "", drawer ? "is-drawer" : "", drawer && open ? "is-open" : ""].filter(Boolean).join(" ")}
        aria-label="Main"
        // A closed drawer is off-screen: keep it out of the tab order and the accessibility tree.
        inert={hidden || undefined}
      >
        <div className="ax-side-head">
          <a
            className="ax-brand"
            href={routeUrl({ view: "batch" })}
            aria-label="AXIOM overview"
            onClick={(e) => {
              e.preventDefault();
              go({ view: "batch" });
            }}
          >
            <BrandMark size={18} />
            {!rail && <span>AXIOM</span>}
          </a>
          {!rail && !drawer && (
            <>
              <button type="button" className="ax-icon-btn ax-side-toggle" aria-label="Collapse sidebar" title="Collapse sidebar" onClick={onToggle}>
                <PanelLeft size={15} aria-hidden="true" />
              </button>
              <button type="button" className="ax-kbd" aria-label="Search" title="Search" onClick={onSearch}>
                {MAC ? "⌘K" : "Ctrl K"}
              </button>
            </>
          )}
        </div>

        <div className="ax-side-nav">
          {rail && (
            <button type="button" className="ax-nav" aria-label="Search" title={MAC ? "Search (⌘K)" : "Search (Ctrl K)"} onClick={onSearch}>
              <Search size={16} strokeWidth={1.75} aria-hidden="true" />
            </button>
          )}
          {NAV.map((section, i) => (
            <div key={section.label ?? i} className="ax-side-group">
              {section.label && (rail ? <div className="ax-side-rule" /> : <p className="ax-side-label">{section.label}</p>)}
              <ul>
                {section.items.map((item) => (
                  <li key={item.id}>
                    <NavRow item={item} route={route} go={go} rail={rail} needs={needs} />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="ax-side-foot">
          {rail && (
            <button type="button" className="ax-nav" aria-label="Expand sidebar" title="Expand sidebar" onClick={onToggle}>
              <PanelLeft size={16} strokeWidth={1.75} aria-hidden="true" />
            </button>
          )}
          <AccountMenu who={who} rail={rail} onSignOut={onSignOut} />
        </div>
      </aside>
    </>
  );
}

function NavRow({ item, route, go, rail, needs }: { item: NavItem; route: Route; go: (r: Route) => void; rail: boolean; needs: number }) {
  const active = isActive(item, route);
  const count = item.badge === "needs" ? needs : 0;
  const Icon = item.icon;
  const tip = rail ? item.label + (item.soon ? " (coming soon)" : count ? ` (${count})` : "") : undefined;
  return (
    <a
      className={"ax-nav" + (active ? " is-active" : "") + (item.soon ? " is-soon" : "")}
      href={routeUrl(item.route)}
      aria-current={active ? "page" : undefined}
      title={tip}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
        e.preventDefault();
        go(item.route);
      }}
    >
      {/* The coming-soon list is muted text: its icons appear only where the rail has nothing else to show. */}
      {(rail || !item.soon) && <Icon size={16} strokeWidth={1.75} aria-hidden="true" />}
      {!rail && <span className="ax-nav-label">{item.label}</span>}
      {!rail && count > 0 && (
        <span className="ax-nav-count">
          {count > 99 ? "99+" : count}
          <span className="sr-only"> question{count === 1 ? "" : "s"} need you</span>
        </span>
      )}
      {rail && count > 0 && <i className="ax-nav-dot" aria-hidden="true" />}
      {rail && (
        <span className="sr-only">
          {item.label}
          {count > 0 ? `, ${count} need you` : ""}
        </span>
      )}
    </a>
  );
}
