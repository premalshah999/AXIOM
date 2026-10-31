import { Empty } from "../ui/Empty";
import type { NavItem } from "./nav";

/** The page behind a coming-soon item: its name is the page title, and this says what it will be. */
export function ComingSoon({ item }: { item: NavItem }) {
  return (
    <div className="ax-page">
      <div className="ax-panel">
        <Empty icon={item.icon} title="Coming soon" hint={item.about} />
      </div>
    </div>
  );
}
