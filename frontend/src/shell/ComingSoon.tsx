import { Pill } from "../ui/index";
import type { NavItem } from "./nav";

/** The calm page behind a coming-soon sidebar item: the name, one sentence on what it will do, and nothing else. */
export function ComingSoon({ item }: { item: NavItem }) {
  const Icon = item.icon;
  return (
    <section className="ax-soon-page" aria-labelledby="soon-title">
      <Icon size={28} strokeWidth={1.6} aria-hidden="true" />
      <p className="ax-soon-name" id="soon-title">
        {item.label} <Pill tone="brand">Soon</Pill>
      </p>
      <p className="ax-soon-about">{item.about}</p>
    </section>
  );
}
