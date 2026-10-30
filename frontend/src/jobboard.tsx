import { useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "./api";
import { ApplyOutcomes, useApplyMany, useSelection } from "./bulk";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { Pagination, type Paging } from "./pagination";
import { TopActions } from "./shell/Shell";
import type { Board, Listing } from "./types";
import { ago, plural, stamp } from "./format";
import { Button, Empty, Table, type Column } from "./ui/index";
import { Chips, JobCell, LoadError, Notice, Note, SearchBox, StatusPill } from "./worktools";

// A listing in any of these states is already owned or protected.
const LOCKED = new Set([
  "paused",
  "queued",
  "running",
  "submitting",
  "confirmed",
  "submission_unknown",
  "already_applied",
  "archived",
]);
// A listing in one of these states is already owned or protected; "pending:" is an Apply just made.
const locked = (status: string) => LOCKED.has(status) || status.startsWith("pending:");
const RUN_STATES = new Set(["queued", "running", "submitting", "confirmed", "submission_unknown", "already_applied"]);

// "Why isn't this applying?": the batch's last reason for a Ready job it did not start. The server keeps codes such
// as `generic_careers_page` as the reason for some guards, so they are read as words.
function whyNot(r: NonNullable<Listing["rejection"]>) {
  const text = /\s/.test(r.reason) ? r.reason : r.reason.replace(/_/g, " ");
  const label = r.outcome === "deferred" || !r.outcome ? "Waiting" : "Not started";
  return `${label}: ${text.charAt(0).toLowerCase()}${text.slice(1)}`;
}

const FITS = [
  ["all", "Any fit"],
  ["eligible", "Eligible"],
  ["uncertain", "Uncertain"],
  ["unranked", "Not ranked"],
  ["below", "Below fit"],
] as const;

// Jobs › Discovered pages, filters and counts come from the server (GET /api/job-board?page=): the dashboard used to
// receive 500 listings (577 KB) every 20 s and filter them itself, so larger boards showed wrong totals and missed
// rows (2026-10-22). Below-fit jobs can be ticked, a page or every match selected, and applied to in one request.
export function DiscoveredJobs() {
  const { snapshot, route, go, openJob, version, marks, counts } = useWorkspace();
  const countsKey = `${counts.discovered}:${counts.pipeline}`;
  const { pending, run } = useAction();
  const now = useNow(30000);
  const [query, setQuery] = useDraft("board-query", "");
  const [ats, setAts] = useDraft("board-ats", "all");
  const [level, setLevel] = useDraft("board-level", "all");
  const [q, setQ] = useState(query.trim());
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const fit = route.filter || "all";
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => setPage(1), [q, ats, level, fit, size]);
  const key = new URLSearchParams({
    include_sources: "false",
    q,
    ats,
    level,
    fit,
    page: String(page),
    size: String(size),
  }).toString();
  const board = usePoll<Board & { key: string }>(
    async (signal) => ({ ...(await api<Board>(`/job-board?${key}`, "GET", undefined, signal)), key }),
    10000,
    [key, version, countsKey],
  );
  const selection = useSelection();
  const bulk = useApplyMany();
  const settings = board.data?.settings;
  const jobs = useMemo(
    () =>
      (board.data?.jobs || []).map((j) => {
        const mark = marks[j.id];
        return mark && mark.at > board.requestedAt ? { ...j, status: mark.state } : j;
      }),
    [board.data, marks, board.requestedAt],
  );
  const paging: Paging = {
    items: jobs,
    total: board.data?.total ?? jobs.length,
    page: board.data?.page ?? page,
    pages: board.data?.pages ?? 1,
    size,
    initialSize: 25,
    setPage,
    setSize,
  };
  const blockers = [
    !snapshot.config.workers_enabled && "application workers are disabled",
    !snapshot.control.auto_submit && "automatic submission is off (Settings › System)",
    snapshot.control.paused && "new applications are paused (resume them on Overview)",
  ].filter(Boolean) as string[];
  const canApply = blockers.length === 0;

  const applySelected = async () => {
    if (await bulk.apply(selection.ids)) selection.clear();
  };

  // One search: the employer boards and the internet-wide new-grad lists together. Two buttons and a second
  // status panel (polled every 10 s) did the same job with different words (2026-10-23).
  const sweep = usePoll<{ state?: string }>(
    (signal) => api("/v2/discovery/feeds", "GET", undefined, signal),
    30000,
    [version],
  );
  const searching = (board.data?.scanning ?? 0) > 0 || sweep.data?.state === "running";
  const scan = () =>
    run(
      "scan",
      async () => {
        const r = await api<{ queued_sources: number; total_sources: number }>("/job-board/scrape", "POST");
        await api("/v2/discovery/feeds/refresh", "POST").catch(() => undefined);
        await Promise.all([board.refresh(), sweep.refresh()]);
        return r;
      },
      (r) =>
        r &&
        `Searching ${plural(r.queued_sources, "employer board")} and the new-grad lists. New jobs appear here as they arrive.`,
    );

  const columns: Column<Listing>[] = [
    {
      key: "job",
      header: "Job",
      width: 400,
      truncate: true,
      cell: (j) => (
        <JobCell
          company={j.company}
          role={j.title}
          title={j.rejection && j.status === "ready" ? whyNot(j.rejection) : undefined}
          onOpen={() => openJob(j.id)}
        />
      ),
    },
    { key: "where", header: "Location", hideBelow: 600, width: 200, truncate: true, cell: (j) => <Note>{j.location || "United States"}</Note> },
    {
      key: "posted",
      header: "Posted",
      hideBelow: 600,
      cell: (j) => (
        <time dateTime={j.posted_at} title={`${j.date_basis || "posted"} ${stamp(j.posted_at)}`}>
          {ago(j.posted_at, now)}
        </time>
      ),
    },
    {
      key: "fit",
      header: "Fit",
      num: true,
      cell: (j) => <span title={j.reason || j.match_reason}>{j.score == null ? "—" : j.score}</span>,
    },
    {
      key: "status",
      header: "Status",
      cell: (j) => (
        <>
          <StatusPill state={RUN_STATES.has(j.status) || j.status.startsWith("pending:") ? j.status : "job:" + j.status} />
          {j.rejection && j.status === "ready" && (
            <Note title={`${j.rejection.code || "reason"} · ${stamp(j.rejection.at)} · ${whyNot(j.rejection)}`}> · held</Note>
          )}
        </>
      ),
    },
    {
      key: "act",
      header: <span className="sr-only">Actions</span>,
      width: "1%",
      pin: true,
      cell: (j) =>
        locked(j.status) ? null : (
          <Button size="sm" className="ax-lead" disabled={!canApply || bulk.applying} onClick={() => bulk.apply([j.id])}>
            Apply
          </Button>
        ),
    },
  ];

  const b = board.data;
  const fresh = b ? (b.fresh_total ?? b.jobs.length) : 0;
  const span = b?.max_age_hours === 720 ? "30 days" : b?.max_age_hours === 168 ? "7 days" : "48 hours";
  const platforms = b?.platforms || [...new Set((b?.jobs || []).map((j) => j.ats))].sort();
  const chips = FITS.filter(([id]) => id === "all" || b?.fits?.[id] !== 0 || fit === id).map(([id, label]) => ({
    id,
    label,
    count: b?.fits?.[id],
  }));
  const current = !!b && b.key === key;
  return (
    <>
      <TopActions>
        <Button
          size="sm"
          loading={searching}
          disabled={pending !== null || !settings?.enabled}
          onClick={scan}
          icon={<RefreshCw size={14} aria-hidden="true" />}
        >
          {searching ? "Searching…" : "Search now"}
        </Button>
      </TopActions>
      {b && (
        <p className="ax-lede">
          {plural(fresh, "job")} not applied to yet, US roles posted in the last {span}
          {searching ? " · searching now" : b.last_requested_at ? ` · last search ${ago(b.last_requested_at, now)}` : ""}
          {!settings?.auto_apply && " · automatic applications are off"}.{" "}
          <button type="button" className="ax-link" onClick={() => go({ view: "settings", tab: "discovery" })}>
            Discovery settings
          </button>
        </p>
      )}
      {b && !settings?.enabled && (
        <Notice
          tone="warn"
          action={
            <button type="button" className="ax-link" onClick={() => go({ view: "settings", tab: "discovery" })}>
              Turn it on
            </button>
          }
        >
          Discovery is off, so no new listings arrive.
        </Notice>
      )}
      {!canApply && <Notice tone="warn">Applying from this list is unavailable: {blockers.join("; ")}.</Notice>}
      <div className="ax-toolbar">
        <SearchBox value={query} onChange={setQuery} label="Search discovered jobs" placeholder="Role, company or city" />
        <select className="ax-control ax-inline-select" aria-label="Platform" value={ats} onChange={(e) => setAts(e.target.value)}>
          <option value="all">All platforms</option>
          {platforms.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <select className="ax-control ax-inline-select" aria-label="Career level" value={level} onChange={(e) => setLevel(e.target.value)}>
          <option value="all">All levels</option>
          <option>New grad</option>
          <option>Early career</option>
          <option>Experience unspecified</option>
        </select>
        <Chips
          items={chips}
          value={fit}
          label="Filter by fit"
          onChange={(id) => go({ view: "jobs", tab: "discovered", filter: id === "all" ? undefined : id })}
        />
      </div>
      {bulk.last && <ApplyOutcomes result={bulk.last} onClose={bulk.dismiss} />}
      <Table
        fill
        label="Discovered jobs"
        columns={columns}
        rows={jobs}
        rowKey={(j) => j.id}
        onRowClick={(j) => openJob(j.id)}
        selected={selection.chosen}
        onSelectedChange={selection.replace}
        canSelect={(j) => !locked(j.status)}
        rowLabel={(j) => `${j.title} at ${j.company}`}
        loading={!b && !board.error}
        error={board.error && !b ? <LoadError error={board.error} retry={board.refresh} /> : undefined}
        busy={!!b && !current}
        bulk={
          <>
            {(b?.selectable_total ?? 0) > jobs.length && (
              <Button
                size="sm"
                variant="ghost"
                disabled={selection.full}
                onClick={() => selection.setMany(b?.selectable_ids || [], true)}
              >
                {(b?.selectable_total ?? 0) > selection.limit ? `Select the first ${selection.limit}` : `Select all ${(b?.selectable_total ?? 0).toLocaleString()}`}
              </Button>
            )}
            {selection.full && <span className="ax-note">{selection.limit} at a time</span>}
            <Button variant="primary" size="sm" disabled={!canApply} loading={bulk.applying} onClick={applySelected}>
              {`Apply to selected (${selection.ids.length})`}
            </Button>
          </>
        }
        empty={
          <Empty
            title={
              fresh ? "No jobs match these filters" : searching ? "Searching for new jobs" : "Nothing new to apply to right now"
            }
            hint={
              fresh
                ? "Clear a filter to see more."
                : searching
                  ? "Employer boards and new-grad lists are being checked. Jobs appear as they arrive."
                  : `Jobs you applied to are in Pipeline. Only US roles posted in the last ${span} with a known date are listed; the next scheduled search checks again.`
            }
          />
        }
      />
      <Pagination paging={paging} label="Listing pages" />
    </>
  );
}
