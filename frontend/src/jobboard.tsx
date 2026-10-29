import { useEffect, useMemo, useState } from "react";
import { RefreshCw, Search } from "lucide-react";
import { api } from "./api";
import { ApplyOutcomes, useApplyMany, useSelection } from "./bulk";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { Pagination, usePagination, type Paging } from "./pagination";
import { TopActions } from "./shell/Shell";
import type { Board, BoardSettings, Listing } from "./types";
import {
  ago,
  Confirm,
  Empty,
  ErrorLine,
  External,
  Field,
  Loading,
  plural,
  stamp,
} from "./ui";
import { Button, Empty as EmptyState, Table, type Column } from "./ui/index";
import { JobCell, Notice, Note, StatusPill, Chips, SearchBox } from "./worktools";

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
  const minimum = settings?.minimum_auto_fit ?? 70;
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
    !snapshot.control.auto_submit && "automatic submission is off (Setup › System)",
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
    { key: "where", header: "Location", width: 200, truncate: true, cell: (j) => <Note>{j.location || "United States"}</Note> },
    { key: "level", header: "Level", secondary: true, cell: (j) => <Note>{j.career_level}</Note> },
    { key: "ats", header: "Platform", secondary: true, cell: (j) => <Note>{j.ats}</Note> },
    {
      key: "posted",
      header: "Posted",
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
      cell: (j) => (
        <span className={"ax-fit " + (j.score == null ? "is-none" : j.score >= minimum ? "is-good" : "is-low")} title={j.reason || j.match_reason}>
          {j.score == null ? "—" : j.score}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      cell: (j) => (
        <>
          <StatusPill state={RUN_STATES.has(j.status) || j.status.startsWith("pending:") ? j.status : "job:" + j.status} />
          {j.rejection && j.status === "ready" && (
            <span className="ax-whynot" title={`${j.rejection.code || "reason"} · ${stamp(j.rejection.at)} · ${whyNot(j.rejection)}`}>
              held
            </span>
          )}
        </>
      ),
    },
    {
      key: "act",
      header: <span className="sr-only">Actions</span>,
      width: "1%",
      cell: (j) =>
        locked(j.status) ? null : (
          <Button size="sm" disabled={!canApply || bulk.applying} onClick={() => bulk.apply([j.id])}>
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
          disabled={pending !== null || searching || !settings?.enabled}
          onClick={scan}
          icon={<RefreshCw size={14} aria-hidden="true" className={searching ? "spin" : ""} />}
        >
          {searching ? "Searching…" : "Search now"}
        </Button>
      </TopActions>
      {b && (
        <p className="ax-lede">
          <strong>{plural(fresh, "job")} not applied to yet</strong>, US roles posted in the last {span}
          {searching ? " · searching now" : b.last_requested_at ? ` · last search ${ago(b.last_requested_at, now)}` : ""}.{" "}
          {settings?.auto_apply
            ? "The batch applies to eligible ones automatically; pick any others below."
            : "Automatic applications are off; pick jobs below or start a batch."}{" "}
          <button type="button" className="ax-link" onClick={() => go({ view: "setup", tab: "discovery" })}>
            Discovery settings
          </button>
        </p>
      )}
      {b && !settings?.enabled && (
        <Notice
          tone="warn"
          action={
            <Button size="sm" onClick={() => go({ view: "setup", tab: "discovery" })}>
              Turn it on
            </Button>
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
      {board.error && !b && (
        <Notice
          tone="bad"
          action={
            <Button size="sm" onClick={() => board.refresh()}>
              Try again
            </Button>
          }
        >
          {board.error.message}
        </Notice>
      )}
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
                {`Select all ${(b?.selectable_total ?? 0).toLocaleString()} matching${(b?.selectable_total ?? 0) > selection.limit ? ` (first ${selection.limit})` : ""}`}
              </Button>
            )}
            {selection.full && <span className="ax-note">{selection.limit} at a time</span>}
            <Button variant="primary" size="sm" disabled={!canApply} loading={bulk.applying} onClick={applySelected}>
              {`Apply to selected (${selection.ids.length})`}
            </Button>
          </>
        }
        empty={
          <EmptyState
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

// ---------- Setup › Discovery ----------
export function DiscoverySettings() {
  const { refresh } = useWorkspace();
  const board = usePoll<Board>(
    (signal) =>
      api("/job-board?include_sources=true", "GET", undefined, signal),
    60000,
  );
  const { pending, run } = useAction();
  const [form, setForm] = useState<BoardSettings | null>(null);
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (board.data && !dirty) setForm(board.data.settings);
  }, [board.data, dirty]);
  if (!board.data || !form)
    return board.error ? (
      <ErrorLine error={board.error} retry={board.refresh} />
    ) : (
      <Loading label="Loading discovery settings…" />
    );
  const b = board.data;
  const set = <K extends keyof BoardSettings>(
    key: K,
    value: BoardSettings[K],
  ) => {
    setForm({ ...form, [key]: value });
    setDirty(true);
  };
  return (
    <>
      <form
        className="panel"
        onSubmit={(e) => {
          e.preventDefault();
          run(
            "save",
            async () => {
              // The full validated model, merged over the server's current values.
              await api("/job-board/settings", "PUT", {
                ...b.settings,
                ...form,
              });
              setDirty(false);
              await Promise.all([board.refresh(), refresh()]);
            },
            "Discovery settings saved.",
          );
        }}
      >
        <header className="panel-head">
          <h2>What to look for</h2>
          <p className="muted">
            Discovery finds recent US roles; ranking scores each against your
            profile and resumes in batches.
          </p>
        </header>
        <div className="form-grid">
          <label className="check wide">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => set("enabled", e.target.checked)}
            />
            <span>
              <strong>Automatic discovery</strong>
              <small>Check employer boards and search on a schedule.</small>
            </span>
          </label>
          <Field label="Check boards">
            <select
              value={form.interval_hours}
              onChange={(e) =>
                set(
                  "interval_hours",
                  Number(e.target.value) as BoardSettings["interval_hours"],
                )
              }
            >
              {[1, 2, 3, 6].map((n) => (
                <option key={n} value={n}>
                  Every {n === 1 ? "hour" : `${n} hours`}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Posted within">
            <select
              value={form.max_age_hours}
              onChange={(e) =>
                set(
                  "max_age_hours",
                  Number(e.target.value) as BoardSettings["max_age_hours"],
                )
              }
            >
              <option value={48}>48 hours</option>
              <option value={168}>7 days</option>
              <option value={720}>30 days</option>
            </select>
          </Field>
          <label className="check wide">
            <input
              type="checkbox"
              checked={form.early_career_only}
              onChange={(e) => set("early_career_only", e.target.checked)}
            />
            <span>
              <strong>Early career only</strong>
              <small>
                Require new-grad or early-career evidence in the posting.
              </small>
            </span>
          </label>
          <label className="check wide">
            <input
              type="checkbox"
              checked={form.web_search}
              onChange={(e) => set("web_search", e.target.checked)}
            />
            <span>
              <strong>Search the web for new employers</strong>
              <small>
                {b.search.provider}.{" "}
                {!b.search.google_configured &&
                  "Add SERPER_API_KEY on the server for Google results; free search works now."}
              </small>
            </span>
          </label>
        </div>
        <header className="panel-head sub">
          <h2>Automatic applications</h2>
        </header>
        <div className="form-grid">
          <label className="check wide">
            <input
              type="checkbox"
              checked={form.auto_apply}
              onChange={(e) => set("auto_apply", e.target.checked)}
            />
            <span>
              <strong>Apply to eligible matches automatically</strong>
              <small>
                Saving with this on starts a batch if none is active. An active
                batch is never restarted.
              </small>
            </span>
          </label>
          <Field
            label="Minimum fit to apply"
            hint="0–100. Jobs ranked below this are never applied to automatically."
          >
            <input
              type="number"
              min={0}
              max={100}
              value={form.minimum_auto_fit}
              onChange={(e) => set("minimum_auto_fit", Number(e.target.value))}
            />
          </Field>
          <Field
            label="Default batch target"
            hint="Used when a new batch starts. 1–500."
          >
            <input
              type="number"
              min={1}
              max={500}
              value={form.batch_target}
              onChange={(e) => set("batch_target", Number(e.target.value))}
            />
          </Field>
        </div>
        <div className="form-actions">
          {dirty && <span className="muted sm">Unsaved changes</span>}
          {dirty && (
            <button
              type="button"
              className="btn quiet"
              onClick={() => {
                setForm(b.settings);
                setDirty(false);
              }}
            >
              Discard
            </button>
          )}
          <button className="btn primary" disabled={!dirty || pending !== null}>
            {pending === "save" ? "Saving…" : "Save discovery settings"}
          </button>
        </div>
      </form>
      <EmployerBoards board={b} reload={board.refresh} />
    </>
  );
}

// "Client error '404 Not Found' for url ..." is "404 Not Found"; the rest is cut to one line.
function shortError(error: string) {
  const text = error.replace(/^Source failed: /, "");
  const status = text.match(/'(\d{3}[^']*)'/);
  if (status) return status[1];
  if (/could not be resolved/i.test(text)) return "Address not found";
  if (/CERTIFICATE_VERIFY_FAILED/.test(text)) return "Certificate error";
  return text.length > 90 ? text.slice(0, 90) + "…" : text;
}

function until(value: string | null | undefined, now: number) {
  const s = value ? (new Date(value).getTime() - now) / 1000 : NaN;
  if (Number.isNaN(s) || s <= 0) return "soon";
  if (s < 3600) return `in ${Math.max(1, Math.round(s / 60))}m`;
  if (s < 86400) return `in ${Math.round(s / 3600)}h`;
  return `in ${Math.round(s / 86400)}d`;
}

function EmployerBoards({
  board: b,
  reload,
}: {
  board: Board;
  reload: () => Promise<void>;
}) {
  const { pending, run } = useAction();
  const [query, setQuery] = useState("");
  const [name, setName] = useDraft("board-add-name", "");
  const [url, setUrl] = useDraft("board-add-url", "");
  const [kind, setKind] = useDraft("board-add-kind", "greenhouse");
  const now = useNow(30000);
  const q = query.trim().toLowerCase();
  const [failingOnly, setFailingOnly] = useState(false);
  const errors = b.sources.filter((s) => s.error || s.scan?.state === "error");
  // Boards that failed their recent scans, worst first, so the dead ones can be turned off.
  const failing = (s: (typeof b.sources)[number]) =>
    s.enabled && (s.health?.failures ?? 0) > 0;
  const shown = b.sources
    .filter(
      (s) =>
        (!failingOnly || failing(s)) &&
        (!q || `${s.name} ${s.kind} ${s.url}`.toLowerCase().includes(q)),
    )
    .sort((a, c) =>
      failingOnly ? (c.health?.failures ?? 0) - (a.health?.failures ?? 0) : 0,
    );
  const health = b.source_health;
  const off = (s: (typeof b.sources)[number], enabled: boolean) =>
    run(
      "t" + s.id,
      async () => {
        await api("/sources/" + s.id, "PUT", {
          name: s.name,
          kind: s.kind,
          url: s.url,
          cron: s.cron,
          enabled,
          auto_queue: s.auto_queue,
        });
        await reload();
      },
      enabled ? "Board turned on." : "Board turned off.",
    );
  const paging = usePagination(shown, q, 10);
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Employer boards</h2>
        <p className="muted">
          {plural(b.source_count, "board")} checked every{" "}
          {b.settings.interval_hours}{" "}
          {b.settings.interval_hours === 1 ? "hour" : "hours"}
          {health
            ? health.failing
              ? ` · ${health.failing} failing${health.dead ? `, ${health.dead} for 3 or more checks in a row` : ""}`
              : ""
            : errors.length
              ? ` · ${errors.length} couldn't be read last time`
              : ""}
          .
        </p>
      </header>
      <form
        className="inline-form"
        onSubmit={(e) => {
          e.preventDefault();
          run(
            "add",
            async () => {
              await api("/job-board/sources", "POST", {
                name: name.trim(),
                url: url.trim(),
                kind,
                enabled: true,
                auto_queue: false,
                cron: "0 */2 * * *",
              });
              setName("");
              setUrl("");
              await reload();
            },
            "Employer board added.",
          );
        }}
      >
        <Field label="Employer">
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Company name"
          />
        </Field>
        <Field label="Platform">
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {[
              "greenhouse",
              "lever",
              "ashby",
              "smartrecruiters",
              "workday",
              "career_page",
            ].map((k) => (
              <option key={k} value={k}>
                {k.replace("_", " ")}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Board URL">
          <input
            required
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://jobs.ashbyhq.com/company"
          />
        </Field>
        <button className="btn" disabled={pending !== null}>
          {pending === "add" ? "Adding…" : "Add board"}
        </button>
      </form>
      <div className="toolbar">
        <label className="search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="Search employer boards"
            placeholder="Search boards"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        {!!health?.failing && (
          <div className="chips" role="group" aria-label="Filter boards">
            <button
              className="chip"
              aria-pressed={failingOnly}
              onClick={() => setFailingOnly(!failingOnly)}
            >
              Failing
              <span className="chip-n">{health.failing}</span>
            </button>
          </div>
        )}
      </div>
      {shown.length ? (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Board</th>
                  <th scope="col">Last check</th>
                  <th scope="col">Result</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {paging.items.map((s) => {
                  const failed = s.error || s.scan?.state === "error";
                  const checking =
                    s.scan?.state === "queued" || s.scan?.state === "running";
                  return (
                    <tr key={s.id}>
                      <td className="cell-job">
                        <strong>{s.name}</strong>
                        <External href={s.url} className="muted sm">
                          {s.kind.replace("_", " ")}
                        </External>
                      </td>
                      <td data-label="Last check">{ago(s.last_run, now)}</td>
                      <td data-label="Result">
                        {checking ? (
                          <span className="muted">Checking…</span>
                        ) : !s.enabled ? (
                          <span className="muted">Turned off</span>
                        ) : failed ? (
                          <span className="warn-text">
                            {s.scan?.error ||
                              shortError(s.error) ||
                              "Couldn't read this board"}
                            {(s.health?.failures ?? 0) > 1 && (
                              <small className="muted">
                                {" "}
                                failed {s.health!.failures} checks in a row ·
                                next try {until(s.next_run, now)}
                              </small>
                            )}
                          </span>
                        ) : (
                          <span>
                            {s.scan?.matched ?? 0} matches ·{" "}
                            {s.scan?.checked ?? 0} postings checked
                          </span>
                        )}
                      </td>
                      <td className="cell-actions">
                        {(failing(s) || !s.enabled) && s.kind !== "web_search" && (
                          <div className="row-actions">
                            <button
                              className="btn sm"
                              disabled={pending !== null}
                              onClick={() => off(s, !s.enabled)}
                            >
                              {s.enabled ? "Turn off" : "Turn on"}
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <Pagination paging={paging} label="Board pages" />
        </>
      ) : (
        <Empty title={q ? "No boards match" : "No employer boards yet"}>
          {q
            ? null
            : "Turn on discovery and save to load the built-in catalog, or add a board above."}
        </Empty>
      )}
      {errors.length > 0 && (
        <p className="muted sm">
          Boards that fail keep their retry cooldown; they don't block discovery
          from other sources.
        </p>
      )}
      <Confirm
        label="Check every board now"
        confirm="Start search"
        danger={false}
        pending={pending === "scan"}
        note="Queues an immediate check of every enabled board."
        onConfirm={() =>
          run(
            "scan",
            async () => {
              const r = await api<{ queued_sources: number }>(
                "/job-board/scrape",
                "POST",
              );
              await reload();
              return r;
            },
            (r) => r && `Checking ${plural(r.queued_sources, "board")}.`,
          )
        }
      />
    </section>
  );
}
