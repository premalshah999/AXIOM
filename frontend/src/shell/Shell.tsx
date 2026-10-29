import { Menu, Search } from "lucide-react";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { Route } from "../context";
import { PageBoundary } from "./ErrorBoundary";
import { titleFor } from "./nav";
import { usePersisted, useMedia } from "./prefs";
import { Sidebar, type Status, type Who } from "./Sidebar";

const SlotContext = createContext<HTMLElement | null>(null);

/**
 * Put controls in the top bar's actions slot (right of the page title) from inside any page:
 * `<TopActions><Button>Export</Button></TopActions>`. They unmount with the page.
 */
export function TopActions({ children }: { children: ReactNode }) {
  const slot = useContext(SlotContext);
  return slot ? createPortal(children, slot) : null;
}

/**
 * The application frame (DESIGN.md §3): a grid of [sidebar | main]. Main is a column of the top bar, banners, and ONE
 * scroll region (.ax-scroll) holding the page. The document itself never scrolls (base.css), so every scrolling bug has
 * one place to look. Wide tables scroll sideways inside their own panel; modals and drawers own their scroll.
 */
export function Shell({
  route,
  go,
  needs,
  status,
  who,
  onSignOut,
  onSearch,
  paletteOpen,
  end,
  banners,
  children,
}: {
  route: Route;
  go: (route: Route) => void;
  needs: number;
  status: Status;
  who: Who;
  onSignOut: () => void;
  onSearch: () => void;
  paletteOpen: boolean;
  /** Global top-bar controls (add a job link). */
  end: ReactNode;
  /** Full-width notices under the top bar (setup unfinished, connection lost). */
  banners?: ReactNode;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = usePersisted("sidebar.collapsed", false);
  const drawer = useMedia("(max-width: 899px)");
  const [open, setOpen] = useState(false);
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const title = titleFor(route);

  // Leaving a page starts the next one at its top; a tab inside the same page keeps its place (Setup's sections
  // are pages of the sidebar, so they reset too).
  const page = route.view === "setup" ? "setup:" + (route.tab || "") : route.view;
  useEffect(() => {
    scroll.current?.scrollTo({ top: 0 });
  }, [page]);
  // Choosing a page closes the drawer; growing the window past 900px leaves no stale open state behind.
  useEffect(() => setOpen(false), [route.view, route.tab, drawer]);
  useEffect(() => {
    if (paletteOpen) setOpen(false);
  }, [paletteOpen]);

  return (
    <SlotContext.Provider value={slot}>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <div className={"ax-app" + (collapsed && !drawer ? " is-rail" : "")}>
        <Sidebar
          route={route}
          go={go}
          needs={needs}
          collapsed={collapsed}
          onToggle={() => setCollapsed((v) => !v)}
          drawer={drawer}
          open={open}
          onClose={() => setOpen(false)}
          onSearch={onSearch}
          status={status}
          who={who}
          onSignOut={onSignOut}
        />
        <div className="ax-main">
          <header className="ax-top">
            {drawer && (
              <button type="button" className="ax-icon-btn ax-top-menu" aria-label="Open menu" aria-expanded={open} onClick={() => setOpen(true)}>
                <Menu size={18} />
              </button>
            )}
            <h1 className="ax-top-title">{title}</h1>
            <div className="ax-top-actions" ref={setSlot} />
            <div className="ax-top-end">
              {drawer && (
                <button type="button" className="ax-icon-btn" aria-label="Search" onClick={onSearch}>
                  <Search size={17} />
                </button>
              )}
              {end}
            </div>
          </header>
          {banners}
          <div className={"ax-scroll" + (route.view === "editor" ? " is-fixed" : "")} ref={scroll}>
            <main id="main" className={"main view-" + route.view} tabIndex={-1}>
              <PageBoundary key={page}>{children}</PageBoundary>
            </main>
          </div>
        </div>
      </div>
    </SlotContext.Provider>
  );
}
