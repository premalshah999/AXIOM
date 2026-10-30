import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { Route } from "../context";
import type { Who } from "./AccountMenu";
import { PageBoundary } from "./ErrorBoundary";
import { titleFor } from "./nav";
import { NavDrawer } from "./NavDrawer";
import { useMedia } from "./prefs";
import { TopBar } from "./TopBar";

const SlotContext = createContext<HTMLElement | null>(null);

/**
 * Put a control at the right of the page title (a row under the bar on a phone) from inside any page:
 * `<TopActions><Button>Export</Button></TopActions>`. It unmounts with the page. Use it for at most one or two
 * things a page really needs; the bar has no controls of its own.
 */
export function TopActions({ children }: { children: ReactNode }) {
  const slot = useContext(SlotContext);
  return slot ? createPortal(children, slot) : null;
}

/**
 * The application frame (DESIGN.md, Shell): the top bar over main. Main is a column of the page title, banners, and ONE scroll
 * region (.ax-scroll) holding the page. The document itself never scrolls (base.css), so every scrolling bug has one place to
 * look. Wide tables scroll sideways inside themselves; drawers and menus own their scroll.
 */
export function Shell({
  route,
  go,
  needs,
  who,
  onSignOut,
  onSearch,
  paletteOpen,
  banners,
  children,
}: {
  route: Route;
  go: (route: Route) => void;
  needs: number;
  who: Who;
  onSignOut: () => void;
  onSearch: () => void;
  paletteOpen: boolean;
  /** One-line notices under the page title (setup unfinished, connection lost). */
  banners?: ReactNode;
  children: ReactNode;
}) {
  const drawer = useMedia("(max-width: 899px)");
  const [open, setOpen] = useState(false);
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const [rowSlot, setRowSlot] = useState<HTMLElement | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const title = titleFor(route);
  // Overview's sentence is its title; every other page names itself.
  const titled = route.view !== "batch";

  // Leaving a page starts the next one at its top. Profile's and Settings' tabs are whole pages, so they reset too; the
  // lists under Jobs and Applications share their page's place.
  const page = route.view === "profile" || route.view === "settings" ? route.view + ":" + (route.tab || "") : route.view;
  useEffect(() => {
    scroll.current?.scrollTo({ top: 0 });
  }, [page]);
  // Choosing a page closes the drawer; growing the window past 900px leaves no stale open state behind.
  useEffect(() => setOpen(false), [route.view, route.tab, drawer]);
  useEffect(() => {
    if (paletteOpen) setOpen(false);
  }, [paletteOpen]);

  return (
    <SlotContext.Provider value={drawer ? rowSlot : slot}>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <div className="ax-app">
        <TopBar route={route} go={go} needs={needs} who={who} drawer={drawer} menuOpen={open} onMenu={() => setOpen(true)} onSearch={onSearch} onSignOut={onSignOut} />
        {drawer && <NavDrawer route={route} go={go} needs={needs} who={who} open={open} onClose={() => setOpen(false)} onSignOut={onSignOut} />}
        <div className={"ax-main view-" + route.view}>
          <div className={"ax-head" + (titled ? "" : " is-untitled")}>
            <h1 className={"ax-top-title" + (titled ? "" : " sr-only")}>{title}</h1>
            {/* Under 900px the bar has no room for a page's buttons beside its title: they go in a row below it. */}
            <div className="ax-top-actions" ref={setSlot} />
          </div>
          <div className="ax-top-row" ref={setRowSlot} />
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
