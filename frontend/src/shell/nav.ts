// Where everything lives (DESIGN.md §4). Every item maps onto the route model in context.ts (view + tab), so each
// existing URL keeps working and the sidebar is only another way to reach it. Groups are always open.
import {
  BookOpen,
  Briefcase,
  ClipboardCheck,
  Compass,
  FileText,
  KeyRound,
  LayoutDashboard,
  Mail,
  MailPlus,
  MessageCircleQuestion,
  Mic,
  Network,
  Plug,
  Radar,
  Settings,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import type { Route } from "../context";

export type NavItem = {
  id: string;
  label: string;
  icon: LucideIcon;
  route: Route;
  /** Which badge to attach, named so the nav does not know what it counts. */
  badge?: "needs";
  /** A "Soon" pill and a calm page instead of a feature. */
  soon?: boolean;
  /** One sentence for the coming-soon page. */
  about?: string;
};
export type NavSection = { label?: string; items: NavItem[] };

const SETUP_TABS = ["profile", "answers", "resumes", "accounts", "email", "discovery", "connections", "system"];

export const NAV: NavSection[] = [
  {
    items: [
      { id: "overview", label: "Overview", icon: LayoutDashboard, route: { view: "batch" } },
      { id: "jobs", label: "Jobs", icon: Briefcase, route: { view: "jobs", tab: "pipeline" } },
      { id: "needs", label: "Needs you", icon: MessageCircleQuestion, route: { view: "needs" }, badge: "needs" },
      { id: "discovered", label: "Discovered", icon: Compass, route: { view: "jobs", tab: "discovered" } },
      { id: "applications", label: "Applications", icon: ClipboardCheck, route: { view: "ledger" } },
    ],
  },
  {
    label: "Setup",
    items: [
      { id: "profile", label: "Profile", icon: UserRound, route: { view: "setup", tab: "profile" } },
      { id: "knowledge", label: "Knowledge", icon: BookOpen, route: { view: "setup", tab: "answers" } },
      { id: "resumes", label: "Resumes", icon: FileText, route: { view: "setup", tab: "resumes" } },
      { id: "accounts", label: "Accounts", icon: KeyRound, route: { view: "setup", tab: "accounts" } },
      { id: "email", label: "Email", icon: Mail, route: { view: "setup", tab: "email" } },
      { id: "discovery", label: "Discovery", icon: Radar, route: { view: "setup", tab: "discovery" } },
      { id: "connections", label: "Connections", icon: Plug, route: { view: "setup", tab: "connections" } },
      { id: "system", label: "System", icon: Settings, route: { view: "setup", tab: "system" } },
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
        about: "Axe is an AI interviewer. It will run mock interviews for the roles you are applying to and tell you what to tighten.",
      },
      {
        id: "network",
        label: "Network",
        icon: Network,
        route: { view: "soon-network" },
        soon: true,
        about: "Network will connect users and affiliates, so people applying to the same companies can introduce each other.",
      },
      {
        id: "emails",
        label: "Emails",
        icon: MailPlus,
        route: { view: "soon-emails" },
        soon: true,
        about: "Emails will run email automation pipelines: outreach and follow-ups that send themselves on your schedule.",
      },
    ],
  },
];

export const ALL_ITEMS = NAV.flatMap((s) => s.items);

/** Whether `item` is where `route` is. Setup defaults to its first section (Connections) when no tab is given. */
export function isActive(item: NavItem, route: Route): boolean {
  const r = item.route;
  // Discovered, Applications and Jobs share the Jobs view: the tab picks which row is lit.
  if (r.view === "jobs") {
    if (route.view !== "jobs") return false;
    const tab = route.tab || "pipeline";
    if (item.id === "discovered") return tab === "discovered";
    return tab !== "discovered" && tab !== "archive";
  }
  if (item.id === "applications") return route.view === "ledger" || (route.view === "jobs" && route.tab === "archive");
  if (r.view === "setup") {
    if (route.view === "editor") return item.id === "resumes";
    if (route.view !== "setup") return false;
    const tab = SETUP_TABS.includes(route.tab || "") ? route.tab : "connections";
    return tab === r.tab;
  }
  return route.view === r.view;
}

/** The page's own name, for the top bar and the document title. */
export function titleFor(route: Route): string {
  if (route.view === "editor") return "Resume editor";
  // Jobs on the Archive tab reads as Applications in the nav, but the page is still Jobs › Archive.
  if (route.view === "jobs" && route.tab === "archive") return "Archive";
  return ALL_ITEMS.find((i) => isActive(i, route))?.label ?? "AXIOM";
}
