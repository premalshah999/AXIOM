import { createContext, useContext } from "react";
import type { JobRow, Marks } from "./model";
import type { Counts, Snapshot } from "./types";

// The six sidebar pages (batch is Overview, ledger is Applications), the resume editor and the coming-soon pages.
export type View =
  | "batch"
  | "jobs"
  | "needs"
  | "ledger"
  | "profile"
  | "settings"
  | "editor"
  | "soon-axe"
  | "soon-network"
  | "soon-emails";
export type Route = {
  view: View;
  tab?: string;
  filter?: string;
  job?: string;
  run?: string;
  artifact?: string;
};
/** A route as code or an old link may still spell it: "setup" is the old combined page, resolved by canonical(). */
export type AnyRoute = Omit<Route, "view"> & { view: View | "setup" };

const VIEWS: View[] = [
  "batch",
  "jobs",
  "needs",
  "ledger",
  "profile",
  "settings",
  "editor",
  "soon-axe",
  "soon-network",
  "soon-emails",
];

// ---------- The route table: every URL the dashboard has ever had resolves here (DESIGN.md, Navigation) ----------

// ?view=setup&tab=… : Setup's old sections.
const SETUP_TABS: Record<string, Route> = {
  connections: { view: "settings", tab: "connections" },
  // Storage held a placeholder sentence (Supabase is on hold): old links open Connections.
  storage: { view: "settings", tab: "connections" },
  accounts: { view: "settings", tab: "accounts" },
  email: { view: "settings", tab: "email" },
  discovery: { view: "settings", tab: "discovery" },
  system: { view: "settings", tab: "system" },
  profile: { view: "profile", tab: "identity" },
  answers: { view: "profile", tab: "knowledge" },
  knowledge: { view: "profile", tab: "knowledge" },
  resumes: { view: "profile", tab: "resumes" },
};

// ?view=setup&tab=profile&filter=… : the old Profile page's sections, now spread over Profile's tabs.
const PROFILE_SECTIONS: Record<string, Route> = {
  identity: { view: "profile", tab: "identity" },
  experience: { view: "profile", tab: "experience" },
  preferences: { view: "profile", tab: "experience", filter: "preferences" },
  facts: { view: "profile", tab: "knowledge", filter: "facts" },
  policies: { view: "profile", tab: "knowledge", filter: "policies" },
  rules: { view: "profile", tab: "knowledge", filter: "rules" },
};

// ?view=<name> or /<name>: page names from every earlier dashboard, matched in lower case.
const OLD_NAMES: Record<string, AnyRoute> = {
  overview: { view: "batch" },
  "needs help": { view: "needs" },
  "needs you": { view: "needs" },
  "needs-you": { view: "needs" },
  questions: { view: "needs" },
  pipeline: { view: "jobs", tab: "pipeline" },
  discovered: { view: "jobs", tab: "discovered" },
  "job board": { view: "jobs", tab: "discovered" },
  "job-board": { view: "jobs", tab: "discovered" },
  reserved: { view: "jobs", tab: "reserved" },
  applications: { view: "ledger" },
  successful: { view: "ledger" },
  confirmed: { view: "ledger" },
  unconfirmed: { view: "ledger", tab: "unconfirmed" },
  archive: { view: "ledger", tab: "archive" },
  setup: { view: "setup" },
  connections: SETUP_TABS.connections!,
  storage: SETUP_TABS.storage!,
  accounts: SETUP_TABS.accounts!,
  email: SETUP_TABS.email!,
  discovery: SETUP_TABS.discovery!,
  sources: SETUP_TABS.discovery!,
  system: SETUP_TABS.system!,
  identity: PROFILE_SECTIONS.identity!,
  experience: PROFILE_SECTIONS.experience!,
  knowledge: SETUP_TABS.knowledge!,
  "knowledge base": SETUP_TABS.knowledge!,
  answers: SETUP_TABS.knowledge!,
  resumes: SETUP_TABS.resumes!,
};

/** A route's one current spelling. go() and routeUrl() both pass through here, so old values never reach a page. */
export function canonical(route: AnyRoute): Route {
  const { view, tab, filter } = route;
  if (view === "setup") {
    const section = tab === "profile" && filter ? PROFILE_SECTIONS[filter] : undefined;
    if (section) return { ...route, filter: undefined, ...section };
    // Setup opened on Connections when no section was named.
    return { ...route, ...(SETUP_TABS[tab || ""] ?? SETUP_TABS.connections!) };
  }
  // Settings › Storage was removed; its address opens Connections.
  if (view === "settings" && tab === "storage") return { ...route, tab: "connections" } as Route;
  // The archive moved from Jobs to Applications; the Pipeline's old "Not started" list is Discovered.
  if (view === "jobs" && tab === "archive") return { ...route, view: "ledger" };
  if (view === "jobs" && (!tab || tab === "pipeline") && filter === "new") return { ...route, view, tab: "discovered", filter: undefined };
  return route as Route;
}

const pathName = (url: URL) => url.pathname.replace(/^\/+|\/+$/g, "").toLowerCase();
const isPageName = (name: string) => VIEWS.includes(name as View) || name in OLD_NAMES;

export function parseRoute(href = location.href): Route {
  const url = new URL(href);
  const q = url.searchParams;
  if (q.has("gmail")) return { view: "settings", tab: "email" };
  if (q.get("page") === "job-board") return { view: "jobs", tab: "discovered" };
  // Path-style links (/settings, /jobs) work as well as ?view=.
  const path = pathName(url);
  const raw = (q.get("view") || (isPageName(path) ? path : "")).toLowerCase();
  const base: AnyRoute = VIEWS.includes(raw as View) ? { view: raw as View } : OLD_NAMES[raw] || { view: "batch" };
  return canonical({
    ...base,
    tab: q.get("tab") || base.tab,
    filter: q.get("filter") || base.filter,
    job: q.get("job") || undefined,
    run: q.get("run") || undefined,
    artifact: q.get("artifact") || undefined,
  });
}

export function routeUrl(raw: AnyRoute) {
  const route = canonical(raw);
  const url = new URL(location.href);
  // A path-style entry (/settings) would otherwise outrank the batch view, which has no ?view=.
  if (isPageName(pathName(url))) url.pathname = "/";
  for (const key of ["view", "tab", "filter", "job", "run", "artifact", "page"])
    url.searchParams.delete(key);
  if (route.view !== "batch") url.searchParams.set("view", route.view);
  for (const key of ["tab", "filter", "job", "run", "artifact"] as const)
    if (route[key]) url.searchParams.set(key, route[key]!);
  return url.pathname + url.search + url.hash;
}

export type Workspace = {
  snapshot: Snapshot;
  // Live rows only (applying, waiting, questions, recent outcomes); lists page through GET /api/jobs.
  rows: JobRow[];
  // Exact counts over every job; zeros until the server sends them.
  counts: Counts;
  // Re-reads the snapshot; paged lists re-read too (they watch `version`).
  refresh: () => Promise<void>;
  version: number;
  // Optimistic: show these jobs in `state` until a list fetched after now arrives.
  marks: Marks;
  markJobs: (ids: string[], state: string) => void;
  route: Route;
  go: (route: AnyRoute) => void;
  openJob: (job: string, run?: string) => void;
};

export const NO_COUNTS: Counts = { groups: {}, states: {}, pipeline: 0, jobs: 0 };

export const WorkspaceContext = createContext<Workspace | null>(null);
export function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("Workspace context missing");
  return value;
}
