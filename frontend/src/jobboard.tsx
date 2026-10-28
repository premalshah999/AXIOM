import { useEffect, useMemo, useState } from "react";
import { RefreshCw, Search } from "lucide-react";
import { api } from "./api";
import { ApplyOutcomes, BulkBar, useApplyMany, useSelection } from "./bulk";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { Pagination, usePagination, type Paging } from "./pagination";
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
  StatePill,
  stamp,
} from "./ui";

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

function fitClass(score: number | null, minimum: number) {
  if (score == null) return "fit none";
  return score >= minimum ? "fit good" : "fit low";
}

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
  const board = usePoll<Board>(
    (signal) => api(`/job-board?${key}`, "GET", undefined, signal),
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
  const pageIds = jobs.filter((j) => !locked(j.status)).map((j) => j.id);
  const blockers = [
    !snapshot.config.workers_enabled && "application workers are disabled",
    !snapshot.control.auto_submit &&
      "automatic submission is off (Setup › System)",
    snapshot.control.paused &&
      "new applications are paused (resume them on Batch)",
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
        const r = await api<{ queued_sources: number; total_sources: number }>(
          "/job-board/scrape",
          "POST",
        );
        await api("/v2/discovery/feeds/refresh", "POST").catch(() => undefined);
        await Promise.all([board.refresh(), sweep.refresh()]);
        return r;
      },
      (r) =>
        r &&
        `Searching ${plural(r.queued_sources, "employer board")} and the new-grad lists. New jobs appear here as they arrive.`,
    );

  if (!board.data)
    return board.error ? (
      <ErrorLine error={board.error} retry={board.refresh} />
    ) : (
      <Loading label="Loading fresh listings…" />
    );
  const b = board.data;
  const fresh = b.fresh_total ?? b.jobs.length;
  const span = b.max_age_hours === 720 ? "30 days" : b.max_age_hours === 168 ? "7 days" : "48 hours";
  const platforms = b.platforms || [...new Set(b.jobs.map((j) => j.ats))].sort();
  const fits = [
    ["all", "Any fit"],
    ["eligible", "Eligible"],
    ["uncertain", "Uncertain"],
    ["unranked", "Not ranked"],
    ["below", "Below fit"],
  ] as const;
  return (
    <section aria-label="Discovered jobs">
      <div className="board-status">
        <p>
          <strong>{plural(fresh, "job")} not applied to yet</strong>, US roles posted in the last {span}
          {searching ? (
            <> · searching now</>
          ) : (
            b.last_requested_at && (
              <> · last search {ago(b.last_requested_at, now)}</>
            )
          )}
          {settings?.auto_apply
            ? ". The batch applies to eligible ones automatically; pick any others below."
            : ". Automatic applications are off; pick jobs below or start a batch."}
        </p>
        <div className="board-actions">
          <button
            className="btn sm"
            disabled={pending !== null || searching || !settings?.enabled}
            onClick={scan}
          >
            <RefreshCw
              size={14}
              aria-hidden="true"
              className={searching ? "spin" : ""}
            />
            {searching ? "Searching…" : "Search now"}
          </button>
          <button
            className="link sm"
            onClick={() => go({ view: "setup", tab: "discovery" })}
          >
            Discovery settings
          </button>
        </div>
      </div>
      {!settings?.enabled && (
        <p className="inline-warn">
          Discovery is off, so no new listings arrive.{" "}
          <button
            className="link"
            onClick={() => go({ view: "setup", tab: "discovery" })}
          >
            Turn it on in Setup
          </button>
        </p>
      )}
      <div className="toolbar">
        <label className="search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="Search discovered jobs"
            placeholder="Role, company or city"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <select
          aria-label="Platform"
          value={ats}
          onChange={(e) => setAts(e.target.value)}
        >
          <option value="all">All platforms</option>
          {platforms.map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
        <select
          aria-label="Career level"
          value={level}
          onChange={(e) => setLevel(e.target.value)}
        >
          <option value="all">All levels</option>
          <option>New grad</option>
          <option>Early career</option>
          <option>Experience unspecified</option>
        </select>
        <div className="chips" role="group" aria-label="Filter by fit">
          {fits.map(([id, label]) => {
            const n = b.fits?.[id];
            if (id !== "all" && n === 0 && fit !== id) return null;
            return (
              <button
                key={id}
                className="chip"
                aria-pressed={fit === id}
                onClick={() =>
                  go({
                    view: "jobs",
                    tab: "discovered",
                    filter: id === "all" ? undefined : id,
                  })
                }
              >
                {label}
                {n !== undefined && <span className="chip-n">{n}</span>}
              </button>
            );
          })}
        </div>
      </div>
      {bulk.last && <ApplyOutcomes result={bulk.last} onClose={bulk.dismiss} />}
      {jobs.length > 0 && (
        <BulkBar
          selection={selection}
          pageIds={pageIds}
          matchingIds={b.selectable_ids}
          matchingTotal={b.selectable_total}
          disabled={!canApply}
          applying={bulk.applying}
          onApply={applySelected}
        />
      )}
      {!canApply && (
        <p className="muted sm apply-why">
          Applying from this list is unavailable: {blockers.join("; ")}.
        </p>
      )}
      {jobs.length ? (
        <>
          <div className="table-wrap">
            <table className="table board-table">
              <thead>
                <tr>
                  <th scope="col" className="cell-check">
                    <input
                      type="checkbox"
                      aria-label="Select the open jobs on this page"
                      disabled={!pageIds.length}
                      checked={
                        !!pageIds.length &&
                        pageIds.every((id) => selection.has(id))
                      }
                      onChange={(e) => selection.setMany(pageIds, e.target.checked)}
                    />
                  </th>
                  <th scope="col">Job</th>
                  <th scope="col">Posted</th>
                  <th scope="col" className="num">
                    Fit
                  </th>
                  <th scope="col">Status</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <BoardRow
                    key={j.id}
                    job={j}
                    minimum={minimum}
                    now={now}
                    checked={selection.has(j.id)}
                    limitReached={selection.full}
                    toggle={(on) => selection.toggle(j.id, on)}
                    canApply={canApply}
                    applying={bulk.applying}
                    apply={() => bulk.apply([j.id])}
                    open={() => openJob(j.id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <Pagination paging={paging} label="Listing pages" />
        </>
      ) : (
        <Empty
          title={
            fresh
              ? "No jobs match these filters"
              : searching
                ? "Searching for new jobs"
                : "Nothing new to apply to right now"
          }
        >
          {fresh
            ? "Clear a filter to see more."
            : searching
              ? "Employer boards and new-grad lists are being checked. Jobs appear as they arrive."
              : `Jobs you applied to are in Pipeline. Only US roles posted in the last ${span} with a known date are listed; the next scheduled search checks again.`}
        </Empty>
      )}
    </section>
  );
}

// A listing in one of these states is already owned or protected; "pending:" is an Apply just made.
const locked = (status: string) => LOCKED.has(status) || status.startsWith("pending:");

function BoardRow({
  job: j,
  minimum,
  now,
  checked,
  limitReached,
  toggle,
  canApply,
  applying,
  apply,
  open,
}: {
  job: Listing;
  minimum: number;
  now: number;
  checked: boolean;
  limitReached: boolean;
  toggle: (on: boolean) => void;
  canApply: boolean;
  applying: boolean;
  apply: () => void;
  open: () => void;
}) {
  const closed = locked(j.status);
  const runState = [
    "queued",
    "running",
    "submitting",
    "confirmed",
    "submission_unknown",
    "already_applied",
  ].includes(j.status) || j.status.startsWith("pending:");
  return (
    <tr>
      <td className="cell-check">
        <input
          type="checkbox"
          aria-label={`Select ${j.title} at ${j.company}`}
          disabled={closed || (!checked && limitReached)}
          checked={checked && !closed}
          onChange={(e) => toggle(e.target.checked)}
        />
      </td>
      <td className="cell-job">
        <button className="link job-open" onClick={open}>
          <strong>{j.company}</strong>
          <span>{j.title}</span>
        </button>
        <small className="muted">
          {[j.location || "United States", j.career_level, j.ats]
            .filter(Boolean)
            .join(" · ")}
        </small>
      </td>
      <td data-label="Posted">
        <time
          dateTime={j.posted_at}
          title={`${j.date_basis || "posted"} ${stamp(j.posted_at)}`}
        >
          {ago(j.posted_at, now)}
        </time>
      </td>
      <td className="num" data-label="Fit">
        <span
          className={fitClass(j.score, minimum)}
          title={j.reason || j.match_reason}
        >
          {j.score == null ? "—" : j.score}
        </span>
      </td>
      <td data-label="Status">
        <StatePill state={runState ? j.status : "job:" + j.status} />
      </td>
      <td className="cell-actions">
        {!closed && (
          <div className="row-actions">
            <button
              className="btn sm"
              disabled={!canApply || applying}
              onClick={apply}
            >
              Apply
            </button>
          </div>
        )}
      </td>
    </tr>
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
  const errors = b.sources.filter((s) => s.error || s.scan?.state === "error");
  const shown = b.sources.filter(
    (s) => !q || `${s.name} ${s.kind} ${s.url}`.toLowerCase().includes(q),
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
          {errors.length
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
                        ) : failed ? (
                          <span className="warn-text">
                            {s.scan?.error ||
                              s.error ||
                              "Couldn't read this board"}
                          </span>
                        ) : (
                          <span>
                            {s.scan?.matched ?? 0} matches ·{" "}
                            {s.scan?.checked ?? 0} postings checked
                          </span>
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
