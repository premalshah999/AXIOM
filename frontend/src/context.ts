import { createContext, useContext } from "react";
import type { JobRow, Marks } from "./model";
import type { Counts, Snapshot } from "./types";

// The pages (batch is Overview, ledger is Applications), the resume editor and the coming-soon pages.
export type View =
  | "batch"
  | "review"
  | "queue"
  | "board"
  | "needs"
  | "ledger"
  | "resumes"
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
  /** A list's search, so a number elsewhere can open the list behind it ("Workday" on Overview opens Jobs searched for it). */
  q?: string;
};
/** A route as code or an old link may still spell it: "setup" and "jobs" are old pages, resolved by canonical(). */
export type AnyRoute = Omit<Route, "view"> & { view: View | "setup" | "jobs" };

const VIEWS: View[] = [
  "batch",
  "review",
  "queue",
  "board",
  "needs",
  "ledger",
  "resumes",
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
  resumes: { view: "resumes", tab: undefined },
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
  jobs: { view: "jobs" },
  pipeline: { view: "queue" },
  discovered: { view: "board" },
  "job board": { view: "board" },
  "job-board": { view: "board" },
  reserved: { view: "needs", filter: "reserved" },
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

// Jobs was five tabs and three sub-filters until 2026-11-01 (owner: "the job board is confusing, don't know where my queue is"). Each
// old address opens the page that now answers its question: what is next is Queue, what was found is the Job board, what needs a
// decision is Needs you, what happened is Applications.
function fromJobs(route: AnyRoute): Route {
  const { tab, filter, q } = route;
  const rest = { job: route.job, run: route.run, artifact: route.artifact, q };
  if (filter === "needs" || tab === "needs") return { ...rest, view: "needs" };
  if (tab === "overlimit") return { ...rest, view: "needs", filter: "over" };
  if (tab === "reserved") return { ...rest, view: "needs", filter: filter || "reserved" };
  if (tab === "failed") return { ...rest, view: "ledger", tab: "failed" };
  if (tab === "not") return { ...rest, view: "ledger", tab: "not" };
  if (tab === "applying" || filter === "active" || filter === "repair") return { ...rest, view: "queue" };
  // Overview's "tried" and a platform's row: applications that started.
  if (tab === "pipeline") return { ...rest, view: "ledger" };
  if (filter === "ready") return { ...rest, view: "queue" };
  if (filter === "held" || filter?.startsWith("held:")) return { ...rest, view: "queue", filter };
  return { ...rest, view: "board" };
}

/** A route's one current spelling. go() and routeUrl() both pass through here, so old values never reach a page. */
export function canonical(route: AnyRoute): Route {
  const { view, tab, filter } = route;
  if (view === "jobs") return fromJobs(route);
  if (view === "setup") {
    const section = tab === "profile" && filter ? PROFILE_SECTIONS[filter] : undefined;
    if (section) return { ...route, filter: undefined, ...section };
    // Setup opened on Connections when no section was named.
    return { ...route, ...(SETUP_TABS[tab || ""] ?? SETUP_TABS.connections!) };
  }
  // Resumes was a tab of Profile until 2026-10-31.
  if (view === "profile" && tab === "resumes") return { ...route, view: "resumes", tab: undefined };
  // Settings › Storage was removed; its address opens Connections.
  if (view === "settings" && tab === "storage") return { ...route, tab: "connections" } as Route;
  return route as Route;
}

const pathName = (url: URL) => url.pathname.replace(/^\/+|\/+$/g, "").toLowerCase();
const isPageName = (name: string) => VIEWS.includes(name as View) || name in OLD_NAMES;

export function parseRoute(href = location.href): Route {
  const url = new URL(href);
  const q = url.searchParams;
  if (q.has("gmail")) return { view: "settings", tab: "email" };
  if (q.get("page") === "job-board") return { view: "board" };
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
    q: q.get("q") || undefined,
  });
}

export function routeUrl(raw: AnyRoute) {
  const route = canonical(raw);
  const url = new URL(location.href);
  // A path-style entry (/settings) would otherwise outrank the batch view, which has no ?view=.
  if (isPageName(pathName(url))) url.pathname = "/";
  for (const key of ["view", "tab", "filter", "job", "run", "artifact", "q", "page"])
    url.searchParams.delete(key);
  if (route.view !== "batch") url.searchParams.set("view", route.view);
  for (const key of ["tab", "filter", "job", "run", "artifact", "q"] as const)
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
  // The snapshot link: lost after two failed reads in a row. `updatedAt` is when the data on screen was fetched.
  link: { lost: boolean; updatedAt: number };
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
