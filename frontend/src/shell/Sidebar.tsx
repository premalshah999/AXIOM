import { LogOut, PanelLeftClose, PanelLeftOpen, Search } from "lucide-react";
import { useEffect, useRef } from "react";
import { routeUrl, type Route } from "../context";
import { BrandMark } from "./Brand";
import { isActive, NAV, type NavItem } from "./nav";
import { ThemeMenu } from "./ThemeMenu";

export type Status = { tone: "ok" | "warn" | "bad" | "idle"; text: string; sub: string };
export type Who = { name?: string; email?: string };

/**
 * The sidebar (DESIGN.md §3 and §4). 240px open, 64px collapsed to an icon rail with the labels as tooltips; the choice
 * is remembered by the caller. Under 900px it is a slide-over drawer opened from the top bar. Every group stays open.
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
  status,
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
  status: Status;
  who: Who;
  onSignOut: () => void;
}) {
  const rail = collapsed && !drawer;
  const side = useRef<HTMLElement>(null);

  // The drawer: Esc closes, focus moves in on open and returns to the opener, Tab stays inside.
  useEffect(() => {
    if (!drawer || !open) return;
    const opener = document.activeElement as HTMLElement | null;
    side.current?.querySelector<HTMLElement>("a[aria-current], a")?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
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
  }, [drawer, open, onClose]);

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
            <BrandMark />
            {!rail && <span>AXIOM</span>}
          </a>
          {!rail && !drawer && (
            <button type="button" className="ax-icon-btn" aria-label="Collapse sidebar" title="Collapse sidebar" onClick={onToggle}>
              <PanelLeftClose size={16} />
            </button>
          )}
        </div>

        <div className="ax-side-search">
          <button type="button" className="ax-search-btn" onClick={onSearch} aria-label="Search" title={rail ? "Search (⌘K)" : undefined}>
            <Search size={rail ? 17 : 15} aria-hidden="true" />
            {!rail && (
              <>
                <span>Search</span>
                <kbd>⌘K</kbd>
              </>
            )}
          </button>
        </div>

        <div className="ax-side-nav">
          {NAV.map((section, i) => (
            <div key={section.label ?? i} className="ax-side-group">
              {section.label &&
                (rail ? <div className="ax-side-rule" /> : <p className="ax-side-label">{section.label}</p>)}
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

        {rail && (
          <div className="ax-side-expand">
            <button type="button" className="ax-icon-btn" aria-label="Expand sidebar" title="Expand sidebar" onClick={onToggle}>
              <PanelLeftOpen size={16} />
            </button>
          </div>
        )}

        <div className="ax-side-foot">
          <a
            className="ax-foot-row ax-status"
            href={routeUrl({ view: "batch" })}
            title={rail ? status.text + ". " + status.sub : undefined}
            onClick={(e) => {
              e.preventDefault();
              go({ view: "batch" });
            }}
          >
            <i className={"ax-dot is-" + status.tone} aria-hidden="true" />
            {!rail && (
              <span className="ax-foot-stack">
                <span>{status.text}</span>
                <small>{status.sub}</small>
              </span>
            )}
            {rail && <span className="sr-only">{status.text}</span>}
          </a>
          <ThemeMenu collapsed={rail} />
          <div className="ax-user">
            {!rail && (
              <>
                <span className="ax-avatar" aria-hidden="true">
                  {(who.name || who.email || "?").slice(0, 1).toUpperCase()}
                </span>
                <span className="ax-foot-stack" title={who.email}>
                  <span>{who.name || who.email || "Signed in"}</span>
                </span>
              </>
            )}
            <button type="button" className="ax-icon-btn" aria-label="Sign out" title="Sign out" onClick={onSignOut}>
              <LogOut size={15} />
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}

function NavRow({ item, route, go, rail, needs }: { item: NavItem; route: Route; go: (r: Route) => void; rail: boolean; needs: number }) {
  const active = isActive(item, route);
  const count = item.badge === "needs" ? needs : 0;
  const Icon = item.icon;
  const tip = rail ? item.label + (item.soon ? " (soon)" : count ? ` (${count})` : "") : undefined;
  return (
    <a
      className={"ax-nav" + (active ? " is-active" : "")}
      href={routeUrl(item.route)}
      aria-current={active ? "page" : undefined}
      title={tip}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
        e.preventDefault();
        go(item.route);
      }}
    >
      <Icon size={17} strokeWidth={active ? 2.1 : 1.8} aria-hidden="true" />
      {!rail && <span className="ax-nav-label">{item.label}</span>}
      {!rail && item.soon && <span className="ax-soon">Soon</span>}
      {!rail && count > 0 && (
        <b className="ax-badge">
          {count > 99 ? "99+" : count}
          <span className="sr-only"> question{count === 1 ? "" : "s"} need you</span>
        </b>
      )}
      {rail && count > 0 && <i className="ax-badge-dot" aria-hidden="true" />}
      {rail && <span className="sr-only">{item.label}{count > 0 ? `, ${count} need you` : ""}</span>}
    </a>
  );
}
