// Job board: which real, open postings the app found, and why each one fits or doesn't (owner, 2026-11-01: "make the job board good
// and legit"). Pages, filters and exact counts come from the server (GET /api/board, jobpilot/board_view.py), the same reading the
// Queue is made from, so In queue 4 here is number 4 there. Closed, stale and duplicate rows never reach it.
import { ExternalLink, FilterX, ListChecks, Plus, Radar, RefreshCw, SearchX, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { addManyToQueue, addToQueue, api, frontManyInQueue, moveToFront, type QueueMany } from "./api";
import { ApplyChoice, ApplyChoices } from "./applychoice";
import { ConfirmQueue, Refused, useSelection } from "./bulk";
import { useWorkspace } from "./context";
import { ago, platformName, plural, stamp } from "./format";
import { useAction, useDraft, useNow, usePoll, useRowActions } from "./hooks";
import { OPEN_JOB_LINK } from "./jobintake";
import { Pagination, type Paging } from "./pagination";
import { TopActions, TopLine } from "./shell/Shell";
import type { BoardFacet, BoardPage, BoardRow } from "./types";
import { Button, Dialog, Empty, Input, Listbox, Monogram, Table, type Column } from "./ui/index";
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
function StateCell({ r, asked }: { r: BoardRow; asked?: boolean }) {
  if (asked && r.state !== "queue") return <span className="bd-state">In queue</span>;
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
  const [level, setLevel] = useDraft("board-level", "all");
  // Grouped by company is the default: a company with hundreds of postings shows its best few and "N more at X".
  const [grouped, setGrouped] = useDraft("board-group", true);
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
  useEffect(() => setPage(1), [q, fit, role, level, company, place, posted, platform, sort, grouped, size]);
  const key = new URLSearchParams({ q, fit, role, level, company, place, posted, platform, sort, group: grouped ? "1" : "0", page: String(page), size: String(size) }).toString();
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
  const shown = useMemo(() => rows.map((r) => r.id), [rows]);
  // A whole Select all: the server takes this many in one request (nothing starts until each job's turn).
  const selection = useSelection(5000);
  // What the filters show changed, so what was selected under them no longer means the same rows.
  useEffect(() => selection.clear(), [q, fit, role, level, company, place, posted, platform, grouped]); // eslint-disable-line react-hooks/exhaustive-deps
  const rowActs = useRowActions();
  // A row reads as in line the moment Queue is pressed; the next read of the board confirms it, or a refusal puts it back.
  const [asked, setAsked] = useState<ReadonlyMap<string, number>>(() => new Map());
  const inLine = (r: BoardRow) => r.state === "queue" || (asked.get(r.id) ?? 0) > Date.now() - 30000;
  const [asking, setAsking] = useState<"add" | "front" | null>(null);
  const [refused, setRefused] = useState<QueueMany["refused"]>([]);
  const paging: Paging = { items: rows, total: b?.total ?? 0, page: b?.page ?? page, pages: b?.pages ?? 1, size, initialSize: 25, setPage, setSize };
  const filtered = !!q || fit !== "match" || role !== "all" || level !== "all" || company !== "all" || place !== "all" || posted !== "all" || platform !== "all";
  const clear = () => {
    setQuery("");
    setFit("match");
    setRole("all");
    setLevel("all");
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
    // The server is reading the board again behind that answer: the numbers settle on the read after it, not fifteen seconds later.
    window.setTimeout(() => void board.refresh(), 2500);
  };
  const add = async (r: BoardRow) => {
    setAsked((m) => new Map(m).set(r.id, Date.now()));
    const out = await rowActs.act("add:" + r.id, () => addToQueue(r.id), (x) => x.message);
    if (!out) setAsked((m) => { const next = new Map(m); next.delete(r.id); return next; });
    void again();
  };
  const front = (r: BoardRow) => rowActs.act("add:" + r.id, async () => { const out = await moveToFront(r.id); void again(); return out; }, (out) => out.message);
  const addSelected = (to: "add" | "front") =>
    run(
      "many",
      async () => {
        const ids = [...selection.ids];
        const out = await (to === "front" ? frontManyInQueue([...ids].reverse()) : addManyToQueue(ids));
        selection.clear();
        setRefused(out.refused);
        const failed = new Set(out.refused.map((x) => x.job_id));
        setAsked((m) => { const next = new Map(m); for (const id of ids) if (!failed.has(id)) next.set(id, Date.now()); return next; });
        await again();
        return out;
      },
      (out) => out.message,
    );
  const selectAll = () =>
    run("select", async () => {
      const params = new URLSearchParams(key);
      params.set("want", "ids");
      const out = await api<{ ids: string[] }>(`/board?${params}`);
      selection.replace(new Set(out.ids));
    });
  const reserve = (r: BoardRow) =>
    run(
      "reserve:" + r.id,
      async () => {
        const out = r.reserved
          ? await api("/reserved/employers?name=" + encodeURIComponent(r.reserved), "DELETE")
          : await api("/reserved/employers", "POST", { text: r.company });
        await again();
        return out;
      },
      () =>
        r.reserved
          ? `${r.reserved} is no longer reserved. Its jobs apply like any other.`
          : `${r.company} is reserved. AXIOM still finds and ranks its jobs, and applies only after you approve each one.`,
    );
  const [adding, setAdding] = useState(false);
  const [boardLink, setBoardLink] = useDraft("board-add-link", "");
  const addBoard = () =>
    run(
      "board",
      async () => {
        const out = await api<{ name: string }>("/job-board/sources/link", "POST", { url: boardLink.trim() });
        setBoardLink("");
        setAdding(false);
        void refresh();
        return out;
      },
      (out) => out && `${out.name} added. It is searched on the schedule; press Search now to read it at once.`,
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
  // What the search has done so far, in the words the person would use: boards read of all.
  const progress = b && searching && b.scan_total ? `${(b.scan_read ?? 0).toLocaleString()} of ${b.scan_total.toLocaleString()} employer boards read` : "";
  const span = !b ? "" : b.window >= 720 ? "30 days" : b.window >= 168 ? "7 days" : "48 hours";

  const columns: Column<BoardRow>[] = [
    {
      key: "job",
      header: "Job",
      width: 420,
      truncate: true,
      min: 220,
      cell: (r) => (
        <div className="bd-cell">
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
            <StateCell r={r} asked={inLine(r)} />
          </span>
        </button>
        {r.more > 0 && (
          <button
            type="button"
            className="ax-link bd-more"
            onClick={(e) => {
              e.stopPropagation();
              setCompany(r.company);
            }}
          >
            {r.more.toLocaleString()} more at {r.company}
          </button>
        )}
        </div>
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
    { key: "state", header: "Status", hideBelow: 600, cell: (r) => <StateCell r={r} asked={inLine(r)} /> },
    {
      key: "act",
      header: <span className="sr-only">Posting and queue</span>,
      width: "1%",
      pin: true,
      cell: (r) => {
        const link = webLink(r.url);
        return (
          <div className="ax-act" onClick={(e) => e.stopPropagation()}>
            <ApplyChoice jobId={r.id} />
            {r.state === "queue" && r.position !== 1 && (
              <Button size="sm" className="ax-lead" loading={rowActs.busy.has("add:" + r.id)} onClick={() => void front(r)}>
                To front
              </Button>
            )}
            {r.state === "approval" && (
              <Button size="sm" className="ax-lead" onClick={() => go({ view: "needs", filter: r.approval || "reserved" })}>
                Review
              </Button>
            )}
            {r.can_add && r.state !== "queue" && (inLine(r) ? (
              <span className="bd-state bd-queued">In queue</span>
            ) : (
              <Button size="sm" className="ax-lead" icon={<Plus size={13} aria-hidden="true" />} onClick={() => void add(r)}>
                Queue
              </Button>
            ))}
            <Button
              size="sm"
              variant="ghost"
              className="ax-lead"
              disabled={pending !== null}
              loading={pending === "reserve:" + r.id}
              icon={<ShieldCheck size={13} aria-hidden="true" />}
              title={
                r.reserved
                  ? `${r.reserved} is reserved: AXIOM applies there only after you approve each job. Press to stop holding it.`
                  : `Reserve ${r.company}: AXIOM finds and ranks its jobs, and applies only after you approve each one.`
              }
              onClick={() => reserve(r)}
            >
              {r.reserved ? "Unreserve" : "Reserve"}
            </Button>
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
      <Dialog
        open={adding}
        onClose={() => setAdding(false)}
        title="Add a company's job board"
        sub="Paste the link to its list of open roles, or to any one posting on it. AXIOM reads the board on its schedule."
        footer={
          <>
            <Button variant="ghost" onClick={() => setAdding(false)}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" form="add-board-link" loading={pending === "board"} disabled={!boardLink.trim()}>
              Add board
            </Button>
          </>
        }
      >
        <form
          id="add-board-link"
          onSubmit={(e) => {
            e.preventDefault();
            if (boardLink.trim()) void addBoard();
          }}
        >
          <Input label="Link" value={boardLink} onChange={(e) => setBoardLink(e.target.value)} placeholder="https://boards.greenhouse.io/example" autoFocus />
        </form>
      </Dialog>
      <TopActions>
        {snapshot.config.apply_mode !== "auto" && (
          <Button variant="secondary" icon={<ListChecks size={15} aria-hidden="true" />} onClick={() => go({ view: "review" })}>
            Review a batch
          </Button>
        )}
        <Button variant="secondary" icon={<Plus size={15} aria-hidden="true" />} onClick={() => window.dispatchEvent(new Event(OPEN_JOB_LINK))}>
          Add a link
        </Button>
        <Button variant="secondary" onClick={() => setAdding(true)}>
          Add a board
        </Button>
        <Button loading={searching || pending === "scan"} disabled={pending !== null || !b?.enabled || !snapshot.config.workers_enabled} onClick={scan} icon={<RefreshCw size={14} aria-hidden="true" />}>
          {searching ? "Searching…" : "Search now"}
        </Button>
      </TopActions>
      {b && !planning && (
        <TopLine>
          {b.all.toLocaleString()} open US postings from the last {span}, with closed ones and duplicates left out
          {progress ? `. Searching: ${progress}.` : b.searched_at ? `. Last searched ${ago(b.searched_at, now)}.` : "."}{" "}
          {grouped && company === "all" ? (
            <>
              Each company shows its best {b.per_company ?? 3} (
              <button type="button" className="ax-link" onClick={() => setGrouped(false)}>
                show every job
              </button>
              ).{" "}
            </>
          ) : !grouped ? (
            <>
              All jobs are shown (
              <button type="button" className="ax-link" onClick={() => setGrouped(true)}>
                group by company
              </button>
              ).{" "}
            </>
          ) : null}
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
          <Listbox prefix="Level" label="Level" value={level} options={keep(level, all("Any", b?.facets.level))} onChange={setLevel} />
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
      <Refused refused={refused} onClose={() => setRefused([])} />
      <ApplyChoices ids={shown}>
      <Table
        fill
        label="Job board"
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        onRowClick={(r) => openJob(r.id)}
        selected={selection.chosen}
        onSelectedChange={selection.replace}
        canSelect={(r) => r.can_add || r.state === "queue"}
        selectAll={b && !planning ? { count: b.selectable, run: selectAll, busy: pending === "select" } : undefined}
        rowLabel={(r) => `${r.title} at ${r.company}`}
        loading={(!b || planning) && !board.error}
        error={board.error && !b ? <LoadError error={board.error} retry={board.refresh} /> : undefined}
        busy={!!b && b.key !== key}
        footer={paging.total > 0 ? <Pagination paging={paging} label="Job board pages" /> : undefined}
        bulk={
          <>
            <Button variant="primary" size="sm" loading={pending === "many"} disabled={pending !== null} onClick={() => setAsking("add")}>
              {`Add to queue (${selection.ids.length.toLocaleString()})`}
            </Button>
            <Button size="sm" disabled={pending !== null} onClick={() => setAsking("front")}>
              To front
            </Button>
          </>
        }
        empty={
          <Empty
            icon={filtered ? SearchX : Radar}
            title={filtered ? "No jobs match these filters" : searching ? "Searching for new jobs" : "No open postings right now"}
            hint={filtered ? "Clear a filter to see more." : searching ? `${progress ? progress + ". " : ""}Jobs appear here as they arrive.` : `Only US roles posted in the last ${span} are listed. The next scheduled search checks again.`}
            action={filtered ? <Button size="sm" onClick={clear}>Clear the filters</Button> : undefined}
          />
        }
      />
      </ApplyChoices>
      <ConfirmQueue
        open={asking !== null}
        count={selection.ids.length}
        title={asking === "front" ? `Move ${plural(selection.ids.length, "job")} to the front?` : undefined}
        onClose={() => setAsking(null)}
        onConfirm={() => {
          const to = asking ?? "add";
          setAsking(null);
          void addSelected(to);
        }}
      />
    </div>
  );
}
