import { Ellipsis, LogOut, Search } from "lucide-react";
import { useRef, useState } from "react";
import { routeUrl, type Route } from "../context";
import { useOverlay } from "../ui/overlay";
import { IconButton } from "../ui/Button";
import { AccountMenu, ThemeChoice, type Who } from "./AccountMenu";
import { Wordmark } from "./Brand";
import { ALL_ITEMS, isActive, type NavItem } from "./nav";

const MAC = /mac/i.test(navigator.platform);

export type Status = { text: string; lamp: "live" | "ask" | "off" | "ok" };

type Nav = { route: Route; go: (route: Route) => void; needs: number };

/** Real links, so middle-click and copy-link work; a plain click stays in the app. */
function navClick(go: (route: Route) => void, to: Route, after?: () => void) {
  return (e: React.MouseEvent) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
    e.preventDefault();
    go(to);
    after?.();
  };
}

function Count({ n }: { n: number }) {
  return n > 0 ? (
    <span className="ax-nav-count">
      {n > 999 ? "999+" : n}
      <span className="sr-only"> question{n === 1 ? "" : "s"} waiting</span>
    </span>
  ) : null;
}

function NavLink({ item, route, go, needs, after }: Nav & { item: NavItem; after?: () => void }) {
  return (
    <a
      className={"ax-nav-item" + (item.soon ? " is-soon" : "")}
      href={routeUrl(item.route)}
      aria-current={isActive(item, route) ? "page" : undefined}
      onClick={navClick(go, item.route, after)}
    >
      <item.icon size={16} aria-hidden="true" />
      <span className="ax-nav-label">{item.label}</span>
      {item.note && <span className="ax-nav-note">{item.note}</span>}
      {item.badge === "needs" && <Count n={needs} />}
    </a>
  );
}

function StatusRow({ status, go, after }: { status: Status; go: (route: Route) => void; after?: () => void }) {
  return (
    <a className="ax-side-status" href={routeUrl({ view: "batch" })} onClick={navClick(go, { view: "batch" }, after)}>
      <i className={"ax-lamp is-" + status.lamp} aria-hidden="true" />
      <span>{status.text}</span>
    </a>
  );
}

/** The desktop navigation: always there, short, with the live status and the account at its foot. */
export function Sidebar({ route, go, needs, status, who, onSearch, onSignOut }: Nav & { status: Status; who: Who; onSearch: () => void; onSignOut: () => void }) {
  return (
    <aside className="ax-side">
      <a className="ax-brand" href={routeUrl({ view: "batch" })} aria-label="AXIOM, overview" onClick={navClick(go, { view: "batch" })}>
        <Wordmark />
      </a>
      <button type="button" className="ax-side-search" onClick={onSearch}>
        <Search size={14} aria-hidden="true" />
        <span>Search</span>
        <kbd>{MAC ? "⌘K" : "Ctrl K"}</kbd>
      </button>
      <nav className="ax-nav" aria-label="Main">
        {ALL_ITEMS.filter((i) => !i.soon).map((item) => (
          <NavLink key={item.id} item={item} route={route} go={go} needs={needs} />
        ))}
      </nav>
      <div className="ax-nav ax-nav-soon" role="group" aria-label="Coming soon">
        <p className="ax-nav-group">Coming soon</p>
        {ALL_ITEMS.filter((i) => i.soon).map((item) => (
          <NavLink key={item.id} item={item} route={route} go={go} needs={needs} />
        ))}
      </div>
      <div className="ax-side-foot">
        <StatusRow status={status} go={go} />
        <AccountMenu who={who} onSignOut={onSignOut} />
      </div>
    </aside>
  );
}

// The places a phone goes most; everything else is under More.
const TABS = ["overview", "queue", "board", "needs"];

/** Under 900px: a slim bar above (logo, live status, search) and a tab bar below, with the rest of the pages in a More sheet. */
export function PhoneBar({ go, status, onSearch }: { go: (route: Route) => void; status: Status; onSearch: () => void }) {
  return (
    <header className="ax-phonebar">
      <a className="ax-brand" href={routeUrl({ view: "batch" })} aria-label="AXIOM, overview" onClick={navClick(go, { view: "batch" })}>
        <Wordmark width={68} />
      </a>
      <StatusRow status={status} go={go} />
      <IconButton label="Search" size="md" icon={<Search size={18} aria-hidden="true" />} onClick={onSearch} />
    </header>
  );
}

export function PhoneTabs({ route, go, needs, who, onSignOut }: Nav & { who: Who; onSignOut: () => void }) {
  const [more, setMore] = useState(false);
  const sheet = useRef<HTMLDivElement>(null);
  useOverlay(more, sheet, () => setMore(false));
  const main = ALL_ITEMS.filter((i) => TABS.includes(i.id));
  const rest = ALL_ITEMS.filter((i) => !TABS.includes(i.id));
  const inMore = rest.some((i) => isActive(i, route));
  const name = who.name || who.email || "Signed in";
  const close = () => setMore(false);
  return (
    <>
      <nav className="ax-tabbar" aria-label="Main">
        {main.map((item) => (
          <a key={item.id} className="ax-tabbar-item" href={routeUrl(item.route)} aria-current={isActive(item, route) ? "page" : undefined} onClick={navClick(go, item.route, close)}>
            <span className="ax-tabbar-icon">
              <item.icon size={20} aria-hidden="true" />
              {item.badge === "needs" && <Count n={needs} />}
            </span>
            {item.label}
          </a>
        ))}
        <button type="button" className="ax-tabbar-item" aria-expanded={more} aria-current={inMore ? "page" : undefined} onClick={() => setMore((v) => !v)}>
          <span className="ax-tabbar-icon">
            <Ellipsis size={20} aria-hidden="true" />
          </span>
          More
        </button>
      </nav>
      {more && (
        <div className="ax-sheet-root">
          <button type="button" className="ax-scrim" aria-label="Close" tabIndex={-1} onClick={close} />
          <div ref={sheet} className="ax-sheet" role="dialog" aria-modal="true" aria-label="More" tabIndex={-1}>
            <nav className="ax-nav" aria-label="More pages">
              {rest.filter((i) => !i.soon).map((item) => (
                <NavLink key={item.id} item={item} route={route} go={go} needs={needs} after={close} />
              ))}
            </nav>
            <div className="ax-nav" role="group" aria-label="Coming soon">
              <p className="ax-nav-group">Coming soon</p>
              {rest.filter((i) => i.soon).map((item) => (
                <NavLink key={item.id} item={item} route={route} go={go} needs={needs} after={close} />
              ))}
            </div>
            <div className="ax-sheet-foot">
              <p className="ax-menu-who">{name}</p>
              <ThemeChoice />
              <button
                type="button"
                className="ax-menu-item"
                onClick={() => {
                  close();
                  onSignOut();
                }}
              >
                <LogOut size={15} aria-hidden="true" />
                Sign out
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
