import { Search } from "lucide-react";
import { routeUrl, type Route } from "../context";
import { AccountMenu, type Who } from "./AccountMenu";
import { Wordmark } from "./Brand";
import { ThemeToggle } from "./ThemeToggle";
import { Tuner, type Readouts } from "./Tuner";

const MAC = /mac/i.test(navigator.platform);

/**
 * The top plate (DESIGN.md, Shell): the wordmark and theme switch on the rail, the tuning scale across the rest, search and the
 * account in its corner. Under 900px the scale moves to a strip along the bottom (see Shell).
 */
export function TopBar({
  route,
  go,
  needs,
  readouts,
  who,
  phone,
  onSearch,
  onSignOut,
}: {
  route: Route;
  go: (route: Route) => void;
  needs: number;
  readouts: Readouts;
  who: Who;
  /** True under 900px. */
  phone: boolean;
  onSearch: () => void;
  onSignOut: () => void;
}) {
  return (
    <header className="ax-bar">
      <div className="ax-rail">
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
          <Wordmark />
        </a>
        <ThemeToggle />
      </div>
      {!phone && <Tuner route={route} go={go} needs={needs} readouts={readouts} phone={false} />}
      <div className="ax-tools">
        <button type="button" className="ax-kbd" aria-label="Search" title="Search" onClick={onSearch}>
          {phone ? <Search size={16} aria-hidden="true" /> : MAC ? "⌘K" : "Ctrl K"}
        </button>
        <AccountMenu who={who} onSignOut={onSignOut} />
      </div>
    </header>
  );
}
