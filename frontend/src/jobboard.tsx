// Job board: which real, open postings the app found, and why each one fits or doesn't (owner, 2026-11-01: "make the job board good
// and legit"). Pages, filters and exact counts come from the server (GET /api/board, jobpilot/board_view.py), the same reading the
// Queue is made from, so In queue 4 here is number 4 there. Closed, stale and duplicate rows never reach it.
import { ExternalLink, FilterX, Plus, Radar, RefreshCw, SearchX } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { addToQueue, api, moveToFront } from "./api";
import { useSelection } from "./bulk";
import { useWorkspace } from "./context";
import { ago, platformName, plural, stamp } from "./format";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { OPEN_JOB_LINK } from "./jobintake";
import { Pagination, type Paging } from "./pagination";
import { TopActions, TopLine } from "./shell/Shell";
import type { BoardFacet, BoardPage, BoardRow } from "./types";
import { Button, Empty, Listbox, Monogram, Table, type Column } from "./ui/index";
import { LoadError, Notice, SearchBox } from "./worktools";
import "./styles/queue.css";

const webLink = (url: string) => (/^https?:\/\//i.test(url) ? url : "");

const SORTS = [
  { value: "fit", label: "Best fit" },
  { value: "newest", label: "Newest" },
  { value: "company", label: "Company A to Z" },
] as const;
type Sort = (typeof SORTS)[number]["value"];
type Fit = "match" | "other" | "all";

// A row's place in one short phrase, the same word it has on every other screen.
function StateCell({ r }: { r: BoardRow }) {
  if (r.state === "queue")
    return (
      <span className="bd-state">
        In queue <b>{r.position}</b>
      </span>
    );
  if (r.state === "approval") return <span className="bd-state">Needs you</span>;
  if (r.state === "checking") return <span className="bd-state is-idle">Checking fit</span>;
  if (r.state === "ready") return <span className="bd-state">Ready</span>;
  return <span className="bd-state is-idle">Not applying</span>;
}

function When({ r, now }: { r: BoardRow; now: number }) {
  const when = ago(r.posted_at, now);
  return (
    <time className="bd-when" dateTime={r.posted_at} title={`${r.dated ? "Posted" : "First found"} ${stamp(r.posted_at)}${r.dated ? "" : ". The posting gives no date."}`}>
      <span>{r.dated ? when : `Found ${when}`}</span>
      <small title={`${platformName(r.ats)} · ${r.source}`}>
        {platformName(r.ats)} · {r.source}
      </small>
    </time>
  );
}

export function BoardView() {
  const { snapshot, route, go, openJob, version, refresh } = useWorkspace();
  const now = useNow(30000);
  const { pending, run } = useAction();
  const [query, setQuery] = useDraft("board-query", route.q || "");
  const [q, setQ] = useState(query.trim());
  const [fit, setFit] = useDraft<Fit>("board-fit", "match");
  const [role, setRole] = useDraft("board-role", "all");
  const [company, setCompany] = useDraft("board-company", "all");
  const [place, setPlace] = useDraft("board-place", "all");
  const [posted, setPosted] = useDraft("board-posted", "all");
  const [platform, setPlatform] = useDraft("board-platform", "all");
  const [sort, setSort] = useDraft<Sort>("board-sort", "fit");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  useEffect(() => {
    if (route.q) setQuery(route.q);
  }, [route.q, setQuery]);
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => setPage(1), [q, fit, role, company, place, posted, platform, sort, size]);
  const key = new URLSearchParams({ q, fit, role, company, place, posted, platform, sort, page: String(page), size: String(size) }).toString();
  const board = usePoll<BoardPage & { key: string }>(
    async (signal) => ({ ...(await api<BoardPage>(`/board?${key}`, "GET", undefined, signal)), key }),
    15000,
    [key, version],
  );
  const b = board.data;
  const planning = !!b?.planning;
  const reread = board.refresh;
  // A cold start: the first screening is still under way, so ask again soon rather than in fifteen seconds.
  useEffect(() => {
    if (!planning) return;
    const timer = window.setTimeout(reread, 1500);
    return () => window.clearTimeout(timer);
  }, [planning, b, reread]);
  const rows = useMemo(() => b?.rows ?? [], [b]);
  const selection = useSelection();
  const paging: Paging = { items: rows, total: b?.total ?? 0, page: b?.page ?? page, pages: b?.pages ?? 1, size, initialSize: 25, setPage, setSize };
  const filtered = !!q || fit !== "match" || role !== "all" || company !== "all" || place !== "all" || posted !== "all" || platform !== "all";
  const clear = () => {
    setQuery("");
    setFit("match");
    setRole("all");
    setCompany("all");
    setPlace("all");
    setPosted("all");
    setPlatform("all");
  };
  const blockers = [
    !snapshot.config.workers_enabled && "application workers are disabled",
    !snapshot.control.auto_submit && "automatic submission is off (Settings › Applying)",
    snapshot.control.paused && "AXIOM is stopped (press Resume applications at the top)",
  ].filter(Boolean) as string[];
  const canApply = blockers.length === 0;

  const again = async () => {
    await board.refresh();
    void refresh();
  };
  const add = (r: BoardRow) => run("add:" + r.id, async () => { const out = await addToQueue(r.id); await again(); return out; }, (out) => out?.message);
  const front = (r: BoardRow) => run("add:" + r.id, async () => { const out = await moveToFront(r.id); await again(); return out; }, (out) => out?.message);
  const addSelected = () =>
    run(
      "many",
      async () => {
        const out = await api<{ added: number; refused: number }>("/queue/add-many", "POST", { job_ids: selection.ids });
        selection.clear();
        await again();
        return out;
      },
      (out) => out && `${plural(out.added, "job")} added to the queue.${out.refused ? ` ${out.refused} could not be added.` : ""}`,
    );
  const scan = () =>
    run(
      "scan",
      async () => {
        const r = await api<{ queued_sources: number }>("/job-board/scrape", "POST");
        await api("/v2/discovery/feeds/refresh", "POST").catch(() => undefined);
        await board.refresh();
        return r;
      },
      (r) => r && `Searching ${plural(r.queued_sources, "employer board")} and the new-grad lists. New jobs appear here as they arrive.`,
    );
  const searching = (b?.scanning ?? 0) > 0;
  const span = !b ? "" : b.window >= 720 ? "30 days" : b.window >= 168 ? "7 days" : "48 hours";

  const columns: Column<BoardRow>[] = [
    {
      key: "job",
      header: "Job",
      width: 420,
      truncate: true,
      min: 220,
      cell: (r) => (
        <button
          type="button"
          className="bd-job"
          onClick={(e) => {
            e.stopPropagation();
            openJob(r.id);
          }}
        >
          <Monogram name={r.company} url={r.url} size="md" employer />
          <span className="bd-title" title={r.title}>
            {r.title}
          </span>
          <span className="bd-sub" title={`${r.company}${r.location ? " · " + r.location : ""}`}>
            {r.company}
            {r.location ? ` · ${r.location}` : " · Location not listed"}
          </span>
          <span className="bd-meta-line">{r.line}</span>
          <span className="bd-meta">
            {r.score != null && <span className="mono">Fit {r.score}</span>}
            <span>{r.dated ? ago(r.posted_at, now) : `Found ${ago(r.posted_at, now)}`}</span>
            <span>{platformName(r.ats)}</span>
            <StateCell r={r} />
          </span>
        </button>
      ),
    },
    { key: "fit", header: "Fit", num: true, hideBelow: 600, cell: (r) => (r.score == null ? "—" : r.score) },
    {
      key: "why",
      header: fit === "match" ? "Why it fits" : "Why",
      hideBelow: 600,
      width: 340,
      truncate: true,
      min: 180,
      cell: (r) => (
        <span className="bd-line" title={r.line}>
          {r.line}
        </span>
      ),
    },
    { key: "when", header: "Posted, and where from", hideBelow: 860, cell: (r) => <When r={r} now={now} /> },
    { key: "state", header: "Status", hideBelow: 600, cell: (r) => <StateCell r={r} /> },
    {
      key: "act",
      header: <span className="sr-only">Posting and queue</span>,
      width: "1%",
      pin: true,
      cell: (r) => {
        const link = webLink(r.url);
        return (
          <div className="ax-act" onClick={(e) => e.stopPropagation()}>
            {r.state === "queue" && r.position !== 1 && (
              <Button size="sm" className="ax-lead" disabled={pending !== null} loading={pending === "add:" + r.id} onClick={() => front(r)}>
                To front
              </Button>
            )}
            {r.state === "approval" && (
              <Button size="sm" className="ax-lead" onClick={() => go({ view: "needs", filter: r.approval || "reserved" })}>
                Review
              </Button>
            )}
            {r.can_add && (
              <Button size="sm" className="ax-lead" disabled={pending !== null} loading={pending === "add:" + r.id} icon={<Plus size={13} aria-hidden="true" />} onClick={() => add(r)}>
                Queue
              </Button>
            )}
            {link && (
              <a className="ax-btn is-sm ax-lead bd-link" href={link} target="_blank" rel="noreferrer noopener" title="Open the employer's posting in your browser">
                Posting <ExternalLink size={12} aria-hidden="true" />
                <span className="sr-only"> (opens in your browser)</span>
              </a>
            )}
          </div>
        );
      },
    },
  ];

  const all = (label: string, facet?: BoardFacet[], rename?: (f: BoardFacet) => string) => [
    { value: "all", label },
    ...(facet ?? []).map((f) => ({ value: f.value, label: rename ? rename(f) : f.label, count: f.count })),
  ];
  const keep = (value: string, options: { value: string; label: string; count?: number }[]) =>
    value === "all" || options.some((o) => o.value === value) ? options : [...options, { value, label: value, count: 0 }];
  return (
    <div className="ax-page ax-page-fill">
      <TopActions>
        <Button variant="secondary" icon={<Plus size={15} aria-hidden="true" />} onClick={() => window.dispatchEvent(new Event(OPEN_JOB_LINK))}>
          Add a link
        </Button>
        <Button loading={searching || pending === "scan"} disabled={pending !== null || !b?.enabled || !snapshot.config.workers_enabled} onClick={scan} icon={<RefreshCw size={14} aria-hidden="true" />}>
          {searching ? "Searching…" : "Search now"}
        </Button>
      </TopActions>
      {b && !planning && (
        <TopLine>
          {b.all.toLocaleString()} open US postings from the last {span}, with closed ones and duplicates left out
          {b.searched_at ? `. Last searched ${ago(b.searched_at, now)}.` : "."}{" "}
          <button type="button" className="ax-link" onClick={() => go({ view: "settings", tab: "discovery" })}>
            What it looks for
          </button>
        </TopLine>
      )}
      {b && !b.enabled && (
        <Notice tone="warn" action={<button type="button" className="ax-link" onClick={() => go({ view: "settings", tab: "discovery" })}>Turn it on</button>}>
          Job search is off, so no new postings arrive.
        </Notice>
      )}
      <div className="bd-search">
        <div className="bd-toolbar" role="group" aria-label="Filter the job board">
          <SearchBox value={query} onChange={setQuery} label="Search the job board" placeholder="Role, company or city" />
          <Listbox label="Fit" value={fit} options={(b?.facets.fit?.length ? b.facets.fit : [{ value: "match", label: "Good fits" }]) as { value: Fit; label: string; count?: number }[]} onChange={setFit} />
          <Listbox prefix="Role" label="Role" value={role} options={keep(role, all("Any", b?.facets.role))} onChange={setRole} />
          <Listbox prefix="Company" label="Company" value={company} options={keep(company, all("Any", b?.facets.company))} onChange={setCompany} />
          <Listbox prefix="Location" label="Location" value={place} options={keep(place, all("Any", b?.facets.place))} onChange={setPlace} />
          <Listbox prefix="Posted" label="Posted" value={posted} options={all("Any", b?.facets.posted)} onChange={setPosted} />
          <Listbox prefix="Platform" label="Platform" value={platform} options={keep(platform, all("Any", b?.facets.platform, (f) => platformName(f.value)))} onChange={setPlatform} />
          {filtered && (
            <Button variant="ghost" size="sm" icon={<FilterX size={14} aria-hidden="true" />} onClick={clear}>
              Clear
            </Button>
          )}
          <span className="bd-sort">
            <Listbox prefix="Sort" label="Sort" align="end" value={sort} options={SORTS as unknown as { value: Sort; label: string }[]} onChange={setSort} />
          </span>
        </div>
      </div>
      {board.error && b && <LoadError error={board.error} retry={board.refresh} />}
      <Table
        fill
        label="Job board"
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        onRowClick={(r) => openJob(r.id)}
        selected={selection.chosen}
        onSelectedChange={rows.some((r) => r.can_add) ? selection.replace : undefined}
        canSelect={(r) => r.can_add}
        rowLabel={(r) => `${r.title} at ${r.company}`}
        loading={(!b || planning) && !board.error}
        error={board.error && !b ? <LoadError error={board.error} retry={board.refresh} /> : undefined}
        busy={!!b && b.key !== key}
        footer={paging.total > 0 ? <Pagination paging={paging} label="Job board pages" /> : undefined}
        bulk={
          <Button variant="primary" size="sm" loading={pending === "many"} disabled={pending !== null} onClick={addSelected}>
            {`Add to queue (${selection.ids.length})`}
          </Button>
        }
        empty={
          <Empty
            icon={filtered ? SearchX : Radar}
            title={filtered ? "No jobs match these filters" : searching ? "Searching for new jobs" : "No open postings right now"}
            hint={filtered ? "Clear a filter to see more." : searching ? "Employer boards and new-grad lists are being read. Jobs appear as they arrive." : `Only US roles posted in the last ${span} are listed. The next scheduled search checks again.`}
            action={filtered ? <Button size="sm" onClick={clear}>Clear the filters</Button> : undefined}
          />
        }
      />
    </div>
  );
}
