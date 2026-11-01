import { useEffect, useMemo, useState } from "react";
import { RefreshCw, Radar, SearchX } from "lucide-react";
import { api } from "./api";
import { ApplyOutcomes, useApplyMany, useSelection } from "./bulk";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { Pagination, type Paging } from "./pagination";
import { TopActions, TopLine } from "./shell/Shell";
import type { Board, Listing, Startable } from "./types";
import { ago, jobWords, listedWhen, plainReason, platformName, plural, shownFit, stamp } from "./format";
import { Button, Empty, Listbox, Table, Tooltip, type Column } from "./ui/index";
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
  "closed",
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

// "Ready" is a job the batch can start now and "Held" one it will not, each with its reason (jobpilot/startable.py): the same two
// numbers Overview's sentence quotes. "Everything found" adds jobs below your fit and ones not ranked yet. No counts on the chips:
// the tab says how many are ready, and a count that changes meaning is worse than none.
const FITS = [
  ["ready", "Ready"],
  ["held", "Held"],
  ["all", "Everything found"],
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
  const ats = "all";
  const level = "all";
  const [q, setQ] = useState(query.trim());
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  // "held:internship" is the Held list narrowed to one reason.
  const [fit, hold = ""] = (route.filter || "ready").split(":") as [string, string?];
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => setPage(1), [q, ats, level, fit, hold, size]);
  const key = new URLSearchParams({
    include_sources: "false",
    q,
    ats,
    level,
    fit,
    hold,
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
  // Keeps the server's reading of which Ready jobs can start warm, so rows and the Ready and Held counts have it.
  usePoll<Startable>((signal) => api("/startable", "GET", undefined, signal), 30000, [version]);
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
    !snapshot.control.auto_submit && "automatic submission is off (Settings › Applying)",
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

  // What is happening to a found job, in one sentence: why it is held, why it is ruled out, or that it starts by itself.
  const sentenceOf = (j: Listing) =>
    j.hold
      ? j.hold.reason
      : j.status === "skipped" || j.status === "closed"
        ? plainReason(j.reason) || "Below your minimum fit."
        : j.status === "ready"
          ? "Ready. Starts by itself."
          : j.status === "new"
            ? "Being checked against your profile."
            : plainReason(j.reason);
  // The pill is the vocabulary word: nothing on a Ready row (the list is "To apply"), "Not applying" for a held or ruled-out one.
  const statusOf = (j: Listing) => {
    if (j.hold || j.status === "skipped" || j.status === "closed") return <StatusPill state="job:skipped" />;
    if (fit === "ready") return null;
    return <StatusPill state={RUN_STATES.has(j.status) || j.status.startsWith("pending:") ? j.status : "job:" + j.status} />;
  };
  const columns: Column<Listing>[] = [
    {
      key: "job",
      header: "Job",
      width: 520,
      truncate: true,
      cell: (j) => (
        <JobCell
          {...jobWords(j)}
          title={j.hold ? `${j.hold.reason} ${j.hold.next}` : undefined}
          onOpen={() => openJob(j.id)}
          meta={
            <>
              {statusOf(j)}
              <span>{sentenceOf(j)}</span>
              {shownFit(j) != null && <span className="mono">Fit {shownFit(j)}</span>}
            </>
          }
        />
      ),
    },
    { key: "where", header: "Location", hideBelow: 860, width: 170, truncate: true, cell: (j) => <Note>{j.location || "United States"}</Note> },
    {
      key: "posted",
      header: "Posted",
      hideBelow: 600,
      cell: (j) => {
        const when = listedWhen(j, now);
        return (
          <time dateTime={j.posted_at} title={when.title}>
            {when.text}
          </time>
        );
      },
    },
    {
      key: "fit",
      header: "Fit",
      num: true,
      hideBelow: 600,
      cell: (j) => (
        <Tooltip label={plainReason(j.reason) || j.match_reason}>
          <span>{shownFit(j) == null ? "—" : shownFit(j)}</span>
        </Tooltip>
      ),
    },
    {
      key: "status",
      header: "What is happening",
      hideBelow: 600,
      width: 330,
      truncate: true,
      cell: (j) => <Note title={j.hold ? `${j.hold.reason} ${j.hold.next}` : undefined}>{sentenceOf(j)}</Note>,
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
  const chips = FITS.map(([id, label]) => ({ id, label }));
  const current = !!b && b.key === key;
  return (
    <>
      <TopActions>
        <Button
          loading={searching}
          disabled={pending !== null || !settings?.enabled}
          onClick={scan}
          icon={<RefreshCw size={14} aria-hidden="true" />}
        >
          {searching ? "Searching…" : "Search now"}
        </Button>
      </TopActions>
      {b && (fit === "ready" || fit === "held") && (
        <TopLine>
          {fit === "ready"
            ? "Jobs AXIOM can apply to now. A running batch starts them by itself."
            : "Jobs AXIOM will not apply to by itself, and why. Apply starts one yourself where the reason allows it."}
        </TopLine>
      )}
      {b && fit !== "ready" && fit !== "held" && (
        <TopLine>
          Found and not applied to yet: US roles posted in the last {span}
          {searching ? ", searching now" : b.last_requested_at ? `, last searched ${ago(b.last_requested_at, now)}` : ""}.{!settings?.auto_apply && " Automatic applications are off."}{" "}
          <button type="button" className="ax-link" onClick={() => go({ view: "settings", tab: "discovery" })}>
            Discovery settings
          </button>
        </TopLine>
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
        <Chips
          items={chips}
          value={fit}
          label="Show"
          onChange={(id) => go({ view: "jobs", tab: "discovered", filter: id === "ready" ? undefined : id })}
        />
      </div>
      {fit === "held" && b?.holds && b.holds.length > 1 && (
        <div className="ax-toolbar">
          <Chips
            items={[
              { id: "held", label: "Any reason" },
              ...b.holds.map((h) => ({ id: `held:${h.code}`, label: h.phrase.charAt(0).toUpperCase() + h.phrase.slice(1) })),
            ]}
            value={hold ? `held:${hold}` : "held"}
            label="Filter by reason"
            onChange={(id) => go({ view: "jobs", tab: "discovered", filter: id })}
          />
        </div>
      )}
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
        footer={paging.total > 0 ? <Pagination paging={paging} label="Listing pages" /> : undefined}
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
            icon={fresh ? SearchX : Radar}
            title={
              fresh ? "No jobs match these filters" : searching ? "Searching for new jobs" : "Nothing new to apply to right now"
            }
            hint={
              fresh
                ? "Clear a filter to see more."
                : searching
                  ? "Employer boards and new-grad lists are being checked. Jobs appear as they arrive."
                  : `Jobs you applied to are under Applied. Only US roles posted in the last ${span} with a known date are listed; the next scheduled search checks again.`
            }
          />
        }
      />
    </>
  );
}
