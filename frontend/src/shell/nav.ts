// Where everything lives (DESIGN.md, Navigation). Six pages, then the coming-soon group. Each item maps onto the route
// table in context.ts, which is where old URLs are resolved.
import {
  Briefcase,
  House,
  Inbox,
  Mail,
  Mic,
  Network,
  Send,
  Settings,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import type { Route, View } from "../context";
import type { TabItem } from "../ui/Tabs";

export type NavItem = {
  id: string;
  label: string;
  icon: LucideIcon;
  route: Route;
  /** Which count to show, named so the nav does not know what it counts. */
  badge?: "needs";
  /** Lives under the quiet "Coming soon" label and opens a page of one sentence. */
  soon?: boolean;
  /** The one sentence for the coming-soon page. */
  about?: string;
};
export type NavSection = { label?: string; items: NavItem[] };

export const NAV: NavSection[] = [
  {
    items: [
      { id: "overview", label: "Overview", icon: House, route: { view: "batch" } },
      { id: "jobs", label: "Jobs", icon: Briefcase, route: { view: "jobs" } },
      { id: "needs", label: "Needs you", icon: Inbox, route: { view: "needs" }, badge: "needs" },
      { id: "applications", label: "Applications", icon: Send, route: { view: "ledger" } },
      { id: "profile", label: "Profile", icon: UserRound, route: { view: "profile" } },
      { id: "settings", label: "Settings", icon: Settings, route: { view: "settings" } },
    ],
  },
  {
    label: "Coming soon",
    items: [
      {
        id: "axe",
        label: "Axe",
        icon: Mic,
        route: { view: "soon-axe" },
        soon: true,
        about: "An AI interviewer that runs mock interviews for the roles you apply to.",
      },
      {
        id: "network",
        label: "Network",
        icon: Network,
        route: { view: "soon-network" },
        soon: true,
        about: "Connects users and affiliates, so people applying to the same companies can introduce each other.",
      },
      {
        id: "emails",
        label: "Emails",
        icon: Mail,
        route: { view: "soon-emails" },
        soon: true,
        about: "Outreach and follow-up emails that send themselves on your schedule.",
      },
    ],
  },
];

export const ALL_ITEMS = NAV.flatMap((s) => s.items);

// Each page's tabs. The palette offers them as places to jump to; Profile and Settings render from these.
export const PAGE_TABS: Partial<Record<View, TabItem[]>> = {
  jobs: [
    { id: "pipeline", label: "Pipeline" },
    { id: "discovered", label: "Discovered" },
    { id: "reserved", label: "Reserved" },
  ],
  ledger: [
    { id: "confirmed", label: "Confirmed" },
    { id: "unconfirmed", label: "Unconfirmed" },
    { id: "archive", label: "Archive" },
  ],
  profile: [
    { id: "identity", label: "Identity" },
    { id: "experience", label: "Experience" },
    { id: "knowledge", label: "Knowledge" },
    { id: "resumes", label: "Resumes" },
  ],
  settings: [
    { id: "connections", label: "Connections" },
    { id: "storage", label: "Storage" },
    { id: "accounts", label: "Accounts" },
    { id: "email", label: "Email" },
    { id: "discovery", label: "Discovery" },
    { id: "system", label: "System" },
  ],
};

/** Whether `item` is where `route` is. Routes are canonical, so one page is lit per view; the editor belongs to Profile. */
export function isActive(item: NavItem, route: Route): boolean {
  return item.route.view === (route.view === "editor" ? "profile" : route.view);
}

/** The page's own name, for the top bar and the document title. */
export function titleFor(route: Route): string {
  if (route.view === "editor") return "Resume editor";
  return ALL_ITEMS.find((i) => isActive(i, route))?.label ?? "AXIOM";
}
