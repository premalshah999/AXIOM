import { useEffect, useLayoutEffect, useRef } from "react";
import { routeUrl, type Route } from "../context";
import { ALL_ITEMS, isActive, type NavItem } from "./nav";

/** What a station reads under its name, by nav item id. */
export type Readouts = Partial<Record<string, string>>;

function Station({
  item,
  route,
  go,
  needs,
  readout,
}: {
  item: NavItem;
  route: Route;
  go: (r: Route) => void;
  needs: number;
  readout?: string;
}) {
  const count = item.badge === "needs" ? needs : 0;
  const value = count ? (count > 999 ? "999+" : String(count)) : (readout ?? item.barLabel);
  return (
    <a
      className={"ax-st" + (item.soon ? " is-soon" : "") + (count ? " is-hot" : "")}
      href={routeUrl(item.route)}
      aria-current={isActive(item, route) ? "page" : undefined}
      title={item.about}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
        e.preventDefault();
        go(item.route);
      }}
    >
      <span className="ax-st-name">{item.label}</span>
      <span className="ax-st-val" title={value}>
        {value}
        {count > 0 && <span className="sr-only"> question{count === 1 ? "" : "s"} need you</span>}
      </span>
    </a>
  );
}

/**
 * The navigation: nine stations on a tuning scale, and a needle that moves to where you are. On a phone it is a strip along the
 * bottom that slides under a fixed needle, with real names at a real size rather than a tiny scale.
 */
export function Tuner({
  route,
  go,
  needs,
  readouts,
  phone,
}: {
  route: Route;
  go: (route: Route) => void;
  needs: number;
  readouts: Readouts;
  phone: boolean;
}) {
  const strip = useRef<HTMLElement>(null);
  const at = Math.max(0, ALL_ITEMS.findIndex((item) => isActive(item, route)));
  const placed = useRef(false);

  // The phone's strip brings the current station to the needle: at once on arrival, then with a slide.
  const centre = (behavior: ScrollBehavior) => {
    const el = strip.current;
    const here = el?.querySelector<HTMLElement>("[aria-current]");
    if (el && here) el.scrollTo({ left: here.offsetLeft - el.clientWidth / 2, behavior });
  };
  useLayoutEffect(() => {
    if (!phone || placed.current) return;
    placed.current = true;
    centre("auto");
  }, [phone]);
  useEffect(() => {
    if (phone) centre(matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth");
  }, [phone, at]);

  return (
    <div className={"ax-tuner" + (phone ? " is-phone" : "")} style={{ "--i": at } as React.CSSProperties}>
      <nav ref={strip} className="ax-stations" aria-label="Main">
        {ALL_ITEMS.map((item) => (
          <Station key={item.id} item={item} route={route} go={go} needs={needs} readout={readouts[item.id]} />
        ))}
      </nav>
      {!phone && (
        <span className="ax-band" aria-hidden="true">
          coming soon
        </span>
      )}
      <div className="ax-scale" aria-hidden="true" />
      <div className="ax-needle" aria-hidden="true" />
    </div>
  );
}
