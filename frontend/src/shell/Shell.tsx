import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { Route } from "../context";
import type { Who } from "./AccountMenu";
import { PageBoundary } from "./ErrorBoundary";
import { titleFor } from "./nav";
import { useMedia } from "./prefs";
import { PhoneBar, PhoneTabs, Sidebar, type Status } from "./Sidebar";
import { StopButton, StopLine, useStopControl } from "./StopControl";

const SlotContext = createContext<{ actions: HTMLElement | null; line: HTMLElement | null }>({ actions: null, line: null });

/**
 * The page's actions, at the right of its header: `<TopActions><Button>Export</Button></TopActions>`. They unmount with the page. A
 * phone wraps them under the title. At most one primary.
 */
export function TopActions({ children }: { children: ReactNode }) {
  const slot = useContext(SlotContext).actions;
  return slot ? createPortal(children, slot) : null;
}

/** The one short line under the page title, only if it helps. */
export function TopLine({ children }: { children: ReactNode }) {
  const slot = useContext(SlotContext).line;
  return slot ? createPortal(children, slot) : null;
}

/**
 * The application frame (DESIGN.md, Shell): the sidebar beside the page sheet. The sheet is a column of the page header, banners and
 * ONE scroll region (.ax-scroll) holding the page. The document itself never scrolls (base.css), so every scrolling bug has one
 * place to look. Wide tables scroll sideways inside themselves; drawers and menus own their scroll.
 */
export function Shell({
  route,
  go,
  needs,
  status,
  who,
  onSignOut,
  onSearch,
  banners,
  children,
}: {
  route: Route;
  go: (route: Route) => void;
  needs: number;
  /** The live status line at the sidebar's foot. */
  status: Status;
  who: Who;
  onSignOut: () => void;
  onSearch: () => void;
  /** One-line notices under the page header (setup unfinished, connection lost). */
  banners?: ReactNode;
  children: ReactNode;
}) {
  const phone = useMedia("(max-width: 899px)");
  const [actions, setActions] = useState<HTMLElement | null>(null);
  const [line, setLine] = useState<HTMLElement | null>(null);
  const stop = useStopControl();
  const scroll = useRef<HTMLDivElement>(null);

  // Leaving a page starts the next one at its top. Profile's and Settings' tabs are whole pages, so they reset too; the lists under
  // Jobs and Applications share their page's place.
  const page = route.view === "profile" || route.view === "settings" ? route.view + ":" + (route.tab || "") : route.view;
  useEffect(() => {
    scroll.current?.scrollTo({ top: 0 });
  }, [page]);

  return (
    <SlotContext.Provider value={{ actions, line }}>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <div className="ax-app">
        {phone ? (
          <PhoneBar go={go} status={status} onSearch={onSearch} />
        ) : (
          <Sidebar route={route} go={go} needs={needs} status={status} who={who} onSearch={onSearch} onSignOut={onSignOut} />
        )}
        <div className={"ax-main view-" + route.view}>
          <header className="ax-head">
            <div className="ax-head-text">
              <h1>{titleFor(route)}</h1>
              <div className="ax-head-line" ref={setLine} />
            </div>
            <div className="ax-top-actions" ref={setActions} />
            <StopButton control={stop} />
          </header>
          <StopLine control={stop} />
          {banners}
          <div className={"ax-scroll" + (route.view === "editor" ? " is-fixed" : "")} ref={scroll}>
            <main id="main" className={"main view-" + route.view} tabIndex={-1}>
              <PageBoundary key={page}>{children}</PageBoundary>
            </main>
          </div>
        </div>
        {phone && <PhoneTabs route={route} go={go} needs={needs} who={who} onSignOut={onSignOut} />}
      </div>
    </SlotContext.Provider>
  );
}
