import { createContext, useContext } from "react";
import type { JobRow, Marks } from "./model";
import type { Counts, Snapshot } from "./types";

export type View = "batch" | "jobs" | "ledger" | "setup" | "editor";
export type Route = {
  view: View;
  tab?: string;
  filter?: string;
  job?: string;
  run?: string;
  artifact?: string;
};

// Earlier dashboard links (?view=Applications, ?page=job-board, ?gmail=…) keep working.
const LEGACY: Record<string, Route> = {
  overview: { view: "batch" },
  "needs help": { view: "batch" },
  applications: { view: "jobs", tab: "pipeline" },
  "job board": { view: "jobs", tab: "discovered" },
  archive: { view: "jobs", tab: "archive" },
  successful: { view: "ledger" },
  accounts: { view: "setup", tab: "accounts" },
  resumes: { view: "setup", tab: "resumes" },
  "knowledge base": { view: "setup", tab: "profile" },
  sources: { view: "setup", tab: "discovery" },
  settings: { view: "setup", tab: "system" },
};
const VIEWS: View[] = ["batch", "jobs", "ledger", "setup", "editor"];

export function parseRoute(href = location.href): Route {
  const url = new URL(href);
  const q = url.searchParams;
  if (q.has("gmail")) return { view: "setup", tab: "email" };
  if (q.get("page") === "job-board") return { view: "jobs", tab: "discovered" };
  // Path-style links (/setup, /jobs) work as well as ?view=.
  const pathView = url.pathname.replace(/^\/+|\/+$/g, "").toLowerCase();
  const raw = (q.get("view") || (VIEWS.includes(pathView as View) ? pathView : "")).toLowerCase();
  const base: Route = VIEWS.includes(raw as View)
    ? { view: raw as View }
    : LEGACY[raw] || { view: "batch" };
  return {
    ...base,
    tab: q.get("tab") || base.tab,
    filter: q.get("filter") || undefined,
    job: q.get("job") || undefined,
    run: q.get("run") || undefined,
    artifact: q.get("artifact") || undefined,
  };
}

export function routeUrl(route: Route) {
  const url = new URL(location.href);
  // A path-style entry (/setup) would otherwise outrank the batch view, which has no ?view=.
  if (VIEWS.includes(url.pathname.replace(/^\/+|\/+$/g, "").toLowerCase() as View)) url.pathname = "/";
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
  go: (route: Route) => void;
  openJob: (job: string, run?: string) => void;
};

export const NO_COUNTS: Counts = { groups: {}, states: {}, pipeline: 0, jobs: 0 };

export const WorkspaceContext = createContext<Workspace | null>(null);
export function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) throw new Error("Workspace context missing");
  return value;
}
