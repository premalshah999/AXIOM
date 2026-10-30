import { Menu as MenuIcon, Search } from "lucide-react";
import { routeUrl, type Route } from "../context";
import { AccountMenu, type Who } from "./AccountMenu";
import { BrandMark } from "./Brand";
import { HourToggle } from "./HourToggle";
import { Menu } from "./Menu";
import { isActive, NAV, type NavItem } from "./nav";

const MAC = /mac/i.test(navigator.platform);
const [PAGES, SOON] = NAV;

/** A link that goes through `go`, and still opens in a new tab when asked to. */
export function NavLink({
  item,
  route,
  go,
  needs,
  className,
  role,
  onDone,
}: {
  item: NavItem;
  route: Route;
  go: (r: Route) => void;
  needs: number;
  className: string;
  role?: string;
  onDone?: () => void;
}) {
  const active = isActive(item, route);
  const count = item.badge === "needs" ? needs : 0;
  return (
    <a
      className={className}
      href={routeUrl(item.route)}
      role={role}
      aria-current={active ? "page" : undefined}
      title={item.about}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
        e.preventDefault();
        go(item.route);
        onDone?.();
      }}
    >
      {item.barLabel ?? item.label}
      {count > 0 && (
        <span className="ax-nav-count">
          {count > 99 ? "99+" : count}
          <span className="sr-only"> question{count === 1 ? "" : "s"} need you</span>
        </span>
      )}
    </a>
  );
}

/**
 * The top bar (DESIGN.md, Shell): the six pages on the left, the wordmark at the centre, then coming soon, search, the hour and
 * the account on the right. Under 1240px the wordmark moves left and coming soon folds into a menu; under 900px the pages move
 * to a slide-over opened from here (NavDrawer).
 */
export function TopBar({
  route,
  go,
  needs,
  who,
  drawer,
  menuOpen,
  onMenu,
  onSearch,
  onSignOut,
}: {
  route: Route;
  go: (route: Route) => void;
  needs: number;
  who: Who;
  /** True under 900px. */
  drawer: boolean;
  menuOpen: boolean;
  onMenu: () => void;
  onSearch: () => void;
  onSignOut: () => void;
}) {
  return (
    <header className="ax-bar">
      {drawer ? (
        <button type="button" className="ax-icon-btn ax-bar-burger" aria-label="Open menu" aria-expanded={menuOpen} onClick={onMenu}>
          <MenuIcon size={19} aria-hidden="true" />
        </button>
      ) : (
        <nav className="ax-bar-nav" aria-label="Main">
          {PAGES.items.map((item) => (
            <NavLink key={item.id} item={item} route={route} go={go} needs={needs} className="ax-nav" />
          ))}
        </nav>
      )}
      <a
        className="ax-brand"
        href={routeUrl({ view: "batch" })}
        aria-label="AXIOM, overview"
        onClick={(e) => {
          if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
          e.preventDefault();
          go({ view: "batch" });
        }}
      >
        <BrandMark />
        <span>AXIOM</span>
      </a>
      <div className="ax-bar-end">
        {!drawer && (
          <>
            <nav className="ax-soon" aria-label={SOON.label}>
              <span>{SOON.label}</span>
              {SOON.items.map((item) => (
                <NavLink key={item.id} item={item} route={route} go={go} needs={0} className="ax-soon-link" />
              ))}
            </nav>
            <div className="ax-soon-fold">
              <Menu label={SOON.label!} buttonClass="ax-soon-button" button={SOON.label}>
                {(close) =>
                  SOON.items.map((item) => (
                    <NavLink key={item.id} item={item} route={route} go={go} needs={0} className="ax-menu-item" role="menuitem" onDone={close} />
                  ))
                }
              </Menu>
            </div>
          </>
        )}
        {drawer ? (
          <button type="button" className="ax-icon-btn" aria-label="Search" onClick={onSearch}>
            <Search size={18} aria-hidden="true" />
          </button>
        ) : (
          <button type="button" className="ax-kbd" aria-label="Search" title="Search" onClick={onSearch}>
            {MAC ? "⌘K" : "Ctrl K"}
          </button>
        )}
        <HourToggle />
        {!drawer && <AccountMenu who={who} onSignOut={onSignOut} />}
      </div>
    </header>
  );
}
