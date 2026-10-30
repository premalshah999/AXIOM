import type { NavItem } from "./nav";

/** The page behind a coming-soon sidebar item: the name is in the top bar, and this is its one sentence. */
export function ComingSoon({ item }: { item: NavItem }) {
  return (
    <div className="ax-page">
      <p className="ax-soon-about">{item.about}</p>
    </div>
  );
}
