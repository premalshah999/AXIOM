// Where everything lives (DESIGN.md, Navigation). Nine pages, then the coming-soon group. Each item maps onto the route table in
// context.ts, which is where old URLs are resolved.
import {
  Briefcase,
  CheckCheck,
  FileText,
  Gauge,
  Inbox,
  ListChecks,
  ListOrdered,
  Mail,
  Mic,
  Network,
  Settings2,
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
  /** Under the label, for a name that doesn't say what it is. */
  note?: string;
  /** A coming-soon item: it opens a page of one sentence. */
  soon?: boolean;
  /** The one sentence for the coming-soon page. */
  about?: string;
};

export const ALL_ITEMS: NavItem[] = [
  { id: "overview", label: "Overview", icon: Gauge, route: { view: "batch" } },
  { id: "review", label: "Review a batch", icon: ListChecks, route: { view: "review" } },
  { id: "queue", label: "Queue", icon: ListOrdered, route: { view: "queue" } },
  { id: "board", label: "Job board", icon: Briefcase, route: { view: "board" } },
  { id: "needs", label: "Needs you", icon: Inbox, route: { view: "needs" }, badge: "needs" },
  { id: "applications", label: "Applications", icon: CheckCheck, route: { view: "ledger" } },
  { id: "resumes", label: "Resumes", icon: FileText, route: { view: "resumes" } },
  { id: "profile", label: "Profile", icon: UserRound, route: { view: "profile" } },
  { id: "settings", label: "Settings", icon: Settings2, route: { view: "settings" } },
  {
    id: "axe",
    label: "Axe",
    note: "AI interviewer",
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
];

// Each page's tabs. The palette offers them as places to jump to; the pages render from these.
export const PAGE_TABS: Partial<Record<View, TabItem[]>> = {
  ledger: [
    { id: "confirmed", label: "Confirmed" },
    { id: "problems", label: "What went wrong" },
    { id: "failed", label: "Couldn't apply" },
    { id: "not", label: "Not applying" },
    { id: "archive", label: "Archive" },
  ],
  profile: [
    { id: "identity", label: "Identity" },
    { id: "experience", label: "Experience" },
    { id: "knowledge", label: "Knowledge" },
  ],
  settings: [
    { id: "connections", label: "Connections" },
    { id: "email", label: "Mailbox" },
    { id: "discovery", label: "Job search" },
    { id: "applying", label: "Applying" },
    { id: "accounts", label: "Employer accounts" },
    { id: "account", label: "Your account" },
    { id: "database", label: "Database" },
    { id: "system", label: "Advanced" },
  ],
};

/** Settings' sections. The license key is one of them only in a build that asks for a key (license.tsx). */
export function settingsTabs(licensed: boolean): TabItem[] {
  const tabs = PAGE_TABS.settings!;
  if (!licensed) return tabs;
  const after = tabs.findIndex((t) => t.id === "account") + 1;
  return [...tabs.slice(0, after), { id: "license", label: "License key" }, ...tabs.slice(after)];
}

/** Whether `item` is where `route` is. Routes are canonical, so one page is lit per view; the editor belongs to Resumes. */
export function isActive(item: NavItem, route: Route): boolean {
  return item.route.view === (route.view === "editor" ? "resumes" : route.view === "tailor" ? "queue" : route.view);
}

/** The page's own name, for the page header and the document title. */
export function titleFor(route: Route): string {
  if (route.view === "editor") return "Resume editor";
  if (route.view === "tailor") return "Tailored resume";
  return ALL_ITEMS.find((i) => isActive(i, route))?.label ?? "AXIOM";
}
