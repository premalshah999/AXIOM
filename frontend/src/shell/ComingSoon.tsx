import type { NavItem } from "./nav";

/** The page behind a coming-soon item: its name is the page title, and this is its one sentence. */
export function ComingSoon({ item }: { item: NavItem }) {
  return (
    <div className="ax-page">
      <p className="ax-soon-about">{item.about}</p>
    </div>
  );
}
