import { useEffect, useState } from "react";
import { Download, Search } from "lucide-react";
import { api, applyJob, approveReserved } from "./api";
import { routeUrl, useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { useSelection, type Selection } from "./bulk";
import { DiscoveredJobs } from "./jobboard";
import {
  appliedState,
  COMMITTED,
  GROUPS,
  RETRYABLE,
  STATES,
  stateInfo,
  type Group,
  type JobRow,
} from "./model";
import { Pagination, usePagination } from "./pagination";
import { usePipelinePage } from "./pipeline";
import { QueuePanel } from "./queue";
import { ReservedJobs } from "./reserved";
import type { Archived, ControlResult } from "./types";
import {
  ago,
  Confirm,
  day,
  Empty,
  ErrorLine,
  Loading,
  linkHost,
  plural,
  StatePill,
  urlRole,
  words,
} from "./ui";

const TABS = [
  { id: "pipeline", label: "Pipeline" },
  { id: "discovered", label: "Discovered" },
  // FAANG, MANGO and the Big Four wait here for the owner's approval (owner, 2026-10-27).
  { id: "reserved", label: "Reserved" },
  { id: "archive", label: "Archive" },
];

export function JobsView() {
  const { route, go, counts: all } = useWorkspace();
  const tab = TABS.some((t) => t.id === route.tab) ? route.tab! : "pipeline";
  // Server counts over every job (the browser used to count the newest 1,000 it was sent). Discovered is the jobs not
  // applied to yet, the same number its page shows; Pipeline is everything started or waiting to start.
  const counts: Record<string, number | undefined> = {
    pipeline: all.pipeline,
    discovered: all.discovered,
    reserved: all.groups.reserved,
  };
  return (
    <div className="page">
      <header className="page-head">
        <h1>Jobs</h1>
        <nav className="tabs" aria-label="Job lists">
          {TABS.map((t) => (
            <a
              key={t.id}
              href={routeUrl({ view: "jobs", tab: t.id })}
              aria-current={tab === t.id ? "page" : undefined}
              onClick={(e) => {
                e.preventDefault();
                go({ view: "jobs", tab: t.id });
              }}
            >
              {t.label}
              {counts[t.id] !== undefined && (
                <span className="tab-count">{counts[t.id]}</span>
              )}
            </a>
          ))}
        </nav>
      </header>
      {tab === "pipeline" && <Pipeline />}
      {tab === "discovered" && <DiscoveredJobs />}
      {tab === "reserved" && <ReservedJobs />}
      {tab === "archive" && <ArchiveList />}
    </div>
  );
}

// Jobs › Pipeline: applications that have started or will start (the queue, applying, needs you, repairing, done,
// stopped). Jobs found but never applied to are in Discovered, which is where they are selected and applied to
// (owner, 2026-10-23: "so confusing for the job pipeline part, like the pipeline and the discovery").
function Pipeline() {
  const { route, go, counts: all } = useWorkspace();
  const [query, setQuery] = useDraft("pipeline-query", "");
  const filter = (route.filter || "all") as Group | "all";
  // Old links to the Pipeline's "Not started" list now open Discovered.
  useEffect(() => {
    if (filter === "new") go({ view: "jobs", tab: "discovered" });
  }, [filter, go]);
  const list = usePipelinePage(filter === "new" ? "all" : filter, query);
  // Exact group counts read together with the rows on screen, so a chip and its list always agree.
  const counts = list.counts?.groups || all.groups;
  const q = query.trim();
  const shown = list.rows;
  const label = GROUPS.find((g) => g.id === filter)?.label || "All";
  // Tick rows, a page or every match, then act on them together (owner 2026-10-28: "no checkboxes for select all").
  const selection = useSelection(200);
  useEffect(() => selection.clear(), [filter, query]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <section aria-label="Application pipeline">
      <QueuePanel />
      <div className="toolbar">
        <label className="search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="Search jobs"
            placeholder="Company, role or location"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <div className="chips" role="group" aria-label="Filter by status">
          {GROUPS.map((c) => {
            const n = counts[c.id] || 0;
            if (c.id !== "all" && !n && filter !== c.id) return null;
            return (
              <button
                key={c.id}
                className="chip"
                aria-pressed={filter === c.id}
                onClick={() =>
                  go({
                    view: "jobs",
                    tab: "pipeline",
                    filter: c.id === "all" ? undefined : c.id,
                  })
                }
              >
                {c.label} <span className="chip-n">{n}</span>
              </button>
            );
          })}
        </div>
      </div>
      <ErrorLine error={list.error} retry={list.refresh} />
      {shown.length > 0 && (
        <PipelineBulkBar
          selection={selection}
          rows={shown}
          filter={filter === "new" ? "all" : filter}
          query={q}
          total={list.paging.total}
        />
      )}
      {shown.length ? (
        <>
          <JobTable rows={shown} selection={selection} />
          <Pagination paging={list.paging} label="Pipeline pages" />
        </>
      ) : !list.loaded || !list.current ? (
        <Loading label="Loading applications…" />
      ) : (
        <Empty
          title={
            q
              ? "No applications match this search"
              : filter === "all"
                ? "No applications yet"
                : `Nothing in “${label}” right now`
          }
        >
          {q ? (
            "Try a company name or clear the search."
          ) : filter === "all" ? (
            <>
              Paste a job link above, start a batch, or pick jobs in{" "}
              <button className="link" onClick={() => go({ view: "jobs", tab: "discovered" })}>
                Discovered
              </button>
              .
            </>
          ) : filter === "needs" ? (
            "No application is waiting for a personal fact."
          ) : null}
        </Empty>
      )}
    </section>
  );
}

// Act on ticked jobs: a page or every match (up to 200), then retry, pause, confirm or remove them together. Each job
// goes through the same endpoint as its own row button, so every guard still decides each one.
function PipelineBulkBar({
  selection,
  rows,
  filter,
  query,
  total,
}: {
  selection: Selection;
  rows: JobRow[];
  filter: Group | "all";
  query: string;
  total: number;
}) {
  const { markJobs, refresh } = useWorkspace();
  const { pending, run } = useAction();
  // Job id -> its latest run, for every row seen here (Confirm needs the run).
  const [known, setKnown] = useState<Record<string, string | undefined>>({});
  useEffect(() => {
    setKnown((k) => ({ ...k, ...Object.fromEntries(rows.map((r) => [r.job.id, r.run?.id])) }));
  }, [rows]);
  const n = selection.ids.length;
  const pageIds = rows.map((r) => r.job.id);
  const busy = pending !== null;
  const each = async (ids: string[], one: (id: string) => Promise<unknown>) => {
    let ok = 0;
    for (let i = 0; i < ids.length; i += 4) {
      const done = await Promise.allSettled(ids.slice(i, i + 4).map(one));
      ok += done.filter((d) => d.status === "fulfilled").length;
    }
    return ok;
  };
  const finish = (ids: string[], state?: string) => {
    if (state) markJobs(ids, state);
    selection.clear();
    void refresh();
  };
  const selectMatching = () =>
    run(
      "select",
      async () => {
        const params = new URLSearchParams({ group: filter, q: query, page: "1", size: "200" });
        const page = await api<{ rows: { job: { id: string }; run?: { id: string } | null }[] }>(`/jobs?${params}`);
        setKnown((k) => ({ ...k, ...Object.fromEntries(page.rows.map((r) => [r.job.id, r.run?.id])) }));
        selection.setMany(page.rows.map((r) => r.job.id), true);
        return page.rows.length;
      },
      (count) => (count ? `Selected ${plural(count, "job")}.` : undefined),
    );
  const retry = () =>
    run(
      "retry",
      async () => {
        const ids = [...selection.ids];
        let started = 0;
        for (let i = 0; i < ids.length; i += 50) {
          const r = await api<{ results: { outcome: string }[] }>("/jobs/apply-many", "POST", {
            job_ids: ids.slice(i, i + 50),
          });
          started += r.results.filter((x) => x.outcome !== "refused").length;
        }
        finish(ids, appliedState("queued"));
        return started;
      },
      (started) => `Started ${plural(started ?? 0, "job")} again.`,
    );
  const pause = () =>
    run(
      "pause",
      async () => {
        const ids = [...selection.ids];
        const ok = await each(ids, (id) => api(`/jobs/${id}/pause`, "POST"));
        finish(ids, "job:paused");
        return ok;
      },
      (ok) => `Paused ${plural(ok ?? 0, "job")}.`,
    );
  const remove = () =>
    run(
      "remove",
      async () => {
        const ids = [...selection.ids];
        const ok = await each(ids, (id) => api(`/jobs/${id}`, "DELETE"));
        finish(ids);
        return ok;
      },
      (ok) => `Removed ${plural(ok ?? 0, "job")}. Their history is kept.`,
    );
  const verdict = (submitted: boolean) =>
    run(
      submitted ? "confirm" : "unsent",
      async () => {
        const ids = selection.ids.filter((id) => known[id]);
        const ok = await each(ids, (id) => reconcileRun(known[id]!, submitted));
        finish(ids, submitted ? "confirmed" : "needs_review");
        return ok;
      },
      (ok) => `${submitted ? "Confirmed" : "Marked not sent:"} ${plural(ok ?? 0, "application")}.`,
    );
  return (
    <div className="bulk-bar" role="group" aria-label="Selected jobs">
      <span className="bulk-count" aria-live="polite">
        {n ? `${plural(n, "job")} selected` : "None selected"}
      </span>
      {pageIds.some((id) => !selection.has(id)) && (
        <button className="link sm" onClick={() => selection.setMany(pageIds, true)}>
          Select this page ({pageIds.length})
        </button>
      )}
      {total > pageIds.length && (
        <button className="link sm" disabled={busy} onClick={selectMatching}>
          {pending === "select"
            ? "Selecting…"
            : `Select all ${total.toLocaleString()} matching${total > 200 ? " (first 200)" : ""}`}
        </button>
      )}
      {n > 0 && (
        <button className="link sm" onClick={selection.clear}>
          Clear
        </button>
      )}
      {n > 0 && (
        <span className="bulk-actions toolbar-end">
          {filter === "unconfirmed" && (
            <>
              <button className="btn sm primary" disabled={busy} onClick={() => verdict(true)}>
                {pending === "confirm" ? "Confirming…" : "Confirm submitted"}
              </button>
              <button className="btn sm" disabled={busy} onClick={() => verdict(false)}>
                {pending === "unsent" ? "Saving…" : "Not sent"}
              </button>
            </>
          )}
          <button className="btn sm" disabled={busy} onClick={retry}>
            {pending === "retry" ? "Starting…" : "Retry"}
          </button>
          <button className="btn sm" disabled={busy} onClick={pause}>
            {pending === "pause" ? "Pausing…" : "Pause"}
          </button>
          <Confirm
            label="Remove"
            confirm={`Remove ${plural(n, "job")}`}
            pending={pending === "remove"}
            disabled={busy && pending !== "remove"}
            note="Removes them from the pipeline. Sent applications are not withdrawn; history is kept."
            onConfirm={remove}
          />
        </span>
      )}
    </div>
  );
}

// The owner's own verdict on an application the engine could not confirm (owner 2026-10-28: "a confirm button in
// unconfirmed applications to manually confirm applications the app was not able to detect").
function reconcileRun(runId: string, submitted: boolean) {
  return api(`/runs/${runId}/reconcile`, "POST", {
    submitted,
    evidence: submitted
      ? "Confirmed by the applicant in the dashboard: the employer's receipt, email or account shows this application."
      : "Marked not submitted by the applicant in the dashboard.",
  });
}

function JobTable({ rows, selection }: { rows: JobRow[]; selection?: Selection }) {
  const { openJob, route } = useWorkspace();
  const now = useNow(30000);
  const ids = rows.map((r) => r.job.id);
  return (
    <div className="table-wrap">
      <table className="table jobs-table">
        <thead>
          <tr>
            {selection && (
              <th scope="col" className="cell-check">
                <input
                  type="checkbox"
                  aria-label="Select every job on this page"
                  checked={!!ids.length && ids.every((id) => selection.has(id))}
                  onChange={(e) => selection.setMany(ids, e.target.checked)}
                />
              </th>
            )}
            <th scope="col">Job</th>
            <th scope="col">Status</th>
            <th scope="col" className="num">
              Tries
            </th>
            <th scope="col">Updated</th>
            <th scope="col">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.job.id} aria-selected={route.job === r.job.id || undefined}>
              {selection && (
                <td className="cell-check">
                  <input
                    type="checkbox"
                    aria-label={`Select ${r.job.company || "this job"}`}
                    checked={selection.has(r.job.id)}
                    onChange={(e) => selection.toggle(r.job.id, e.target.checked)}
                  />
                </td>
              )}
              <td className="cell-job">
                <button
                  className="link job-open"
                  title={r.job.url}
                  onClick={() => openJob(r.job.id, r.run?.id)}
                >
                  <strong>{r.job.company || linkHost(r.job.url) || "Employer"}</strong>
                  <span>{r.job.title || urlRole(r.job.url)}</span>
                </button>
                <small className="muted">
                  {[r.job.location, r.job.ats].filter(Boolean).join(" · ")}
                </small>
              </td>
              <td className="cell-state">
                <StatePill state={r.state} />
                {r.state === "pending:waiting" && r.request?.reason ? (
                  <small className="reason" title={r.request.reason}>
                    {r.request.reason}
                  </small>
                ) : (
                  r.run?.reason &&
                  r.state !== "confirmed" && (
                    <small className="reason" title={r.run.reason}>
                      {r.run.reason}
                    </small>
                  )
                )}
              </td>
              <td className="num cell-tries" data-label="Tries">
                {r.attempts || "—"}
              </td>
              <td className="cell-time" data-label="Updated">
                <time dateTime={r.updated} title={new Date(r.updated).toLocaleString()}>
                  {ago(r.updated, now)}
                </time>
              </td>
              <td className="cell-actions">
                <JobControls row={r} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Row controls. Button names stay "Pause", "Resume", "Retry" and "Delete"
// inside each row so assistive tech and tests find them by role and name.
export function JobControls({
  row,
  detailed,
}: {
  row: JobRow;
  detailed?: boolean;
}) {
  const { refresh, openJob, markJobs } = useWorkspace();
  const { pending, run } = useAction();
  const run_ = row.run;
  const state = row.state;
  const committed = !!run_ && COMMITTED.has(run_.state);
  // The row reads as what the server just answered (queued, paused, waiting…) at once; the lists re-read in the
  // background and confirm it. The button no longer waits for that re-read.
  const settle = (result?: { state?: string }) => {
    const shown = result?.state ? appliedState(result.state) : "";
    if (shown in STATES) markJobs([row.job.id], shown);
    void refresh();
  };
  const control = (action: "pause" | "resume" | "delete") =>
    run(
      action,
      async () => {
        const result = await api<ControlResult>(
          action === "delete" ? `/jobs/${row.job.id}` : `/jobs/${row.job.id}/${action}`,
          action === "delete" ? "DELETE" : "POST",
        );
        settle(result);
        return result;
      },
      (r) => r?.message,
    );
  const applyNow = () =>
    run(
      "apply",
      async () => {
        const r = await applyJob(row.job.id);
        settle(r);
        return r;
      },
      (r) => r?.message,
    );
  // A reserved employer's job: the owner's approval, then the same start as Apply.
  const approve = () =>
    run(
      "approve",
      async () => {
        const r = await approveReserved(row.job.id);
        settle(r);
        return r;
      },
      (r) => r?.message,
    );
  const withdraw = () =>
    run(
      "withdraw",
      async () => {
        const result = await api<{ removed: boolean }>(`/queue/${row.job.id}`, "DELETE");
        void refresh();
        return result;
      },
      "Removed from the queue.",
    );
  const verdict = (submitted: boolean) =>
    run(
      submitted ? "confirm" : "unsent",
      async () => {
        const r = await reconcileRun(run_!.id, submitted);
        markJobs([row.job.id], submitted ? "confirmed" : "needs_review");
        void refresh();
        return r;
      },
      submitted ? "Confirmed as submitted." : "Marked not sent.",
    );
  const busy = pending !== null;
  const buttons: React.ReactNode[] = [];
  if (run_?.state === "submission_unknown")
    buttons.push(
      <button key="c" className="btn sm primary" disabled={busy} onClick={() => verdict(true)}>
        {pending === "confirm" ? "Confirming…" : "Confirm"}
      </button>,
      <button key="u" className="btn sm" disabled={busy} onClick={() => verdict(false)}>
        {pending === "unsent" ? "Saving…" : "Not sent"}
      </button>,
    );
  else if (state === "reserved")
    buttons.push(
      <button key="v" className="btn sm primary" disabled={busy} onClick={approve}>
        {pending === "approve" ? "Approving…" : "Approve & apply"}
      </button>,
    );
  else if (state === "pending:waiting")
    buttons.push(
      <button key="w" className="btn sm" disabled={busy} onClick={withdraw}>
        {pending === "withdraw" ? "Removing…" : "Remove from queue"}
      </button>,
    );
  else if (state === "pausing")
    buttons.push(
      <button key="p" className="btn sm" disabled>
        Pausing…
      </button>,
    );
  else if (state === "paused" || state === "job:paused")
    buttons.push(
      <button key="r" className="btn sm" disabled={busy} onClick={() => control("resume")}>
        {pending === "resume" ? "Resuming…" : "Resume"}
      </button>,
    );
  else if (state === "resumed")
    buttons.push(
      <button key="q" className="btn sm" disabled={busy} onClick={applyNow}>
        {pending === "apply" ? "Applying…" : "Apply"}
      </button>,
    );
  else if (run_ && (run_.state === "queued" || run_.state === "running"))
    buttons.push(
      <button key="p" className="btn sm" disabled={busy} onClick={() => control("pause")}>
        {pending === "pause" ? "Pausing…" : "Pause"}
      </button>,
    );
  else if (stateInfo(state).group === "needs" && !detailed)
    buttons.push(
      <button key="a" className="btn sm ask" onClick={() => openJob(row.job.id, run_?.id)}>
        Answer
      </button>,
    );
  else if (run_ && RETRYABLE.has(run_.state) && stateInfo(state).group !== "needs" && !committed)
    buttons.push(
      <button key="t" className="btn sm" disabled={busy} onClick={() => control("resume")}>
        {pending === "resume" ? "Retrying…" : "Retry"}
      </button>,
    );
  if (detailed && stateInfo(state).group === "needs")
    buttons.push(
      <button key="p" className="btn sm" disabled={busy} onClick={() => control("pause")}>
        {pending === "pause" ? "Pausing…" : "Pause"}
      </button>,
    );
  const deletable =
    !run_ || !["confirmed", "already_applied", "submitting"].includes(run_.state);
  if (deletable && (detailed || !committed))
    buttons.push(
      <Confirm
        key="d"
        label="Delete"
        confirm="Delete job"
        pending={pending === "delete"}
        disabled={busy && pending !== "delete"}
        note={
          committed
            ? "Hides it; a sent application is not withdrawn."
            : detailed
              ? "Removes it from the pipeline and board. History is kept."
              : undefined
        }
        onConfirm={() => control("delete")}
      />,
    );
  if (!buttons.length)
    return committed && run_?.state !== "confirmed" ? (
      <span className="muted sm">Protected</span>
    ) : null;
  return <div className="row-actions">{buttons}</div>;
}

function ArchiveList() {
  const { openJob } = useWorkspace();
  const { pending, run } = useAction();
  const [query, setQuery] = useDraft("archive-query", "");
  const archive = usePoll<{ applications: Archived[]; log_bytes: number }>(
    (signal) => api("/archive", "GET", undefined, signal),
    60000,
  );
  const items = archive.data?.applications || [];
  const q = query.trim().toLowerCase();
  const shown = items.filter(
    (a) => !q || `${a.company} ${a.title}`.toLowerCase().includes(q),
  );
  const paging = usePagination(shown, q, 25);
  return (
    <section aria-label="Archived applications">
      <div className="toolbar">
        <label className="search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="Search archive"
            placeholder="Company or role"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <span className="toolbar-note muted">
          Earlier failed and removed jobs, with compressed diagnostic logs. Nothing
          here is retried.
        </span>
        <Confirm
          label="Archive earlier failures"
          confirm="Archive failures"
          danger={false}
          pending={pending === "archive"}
          note="Confirmed and unconfirmed submissions are never archived."
          onConfirm={() =>
            run(
              "archive",
              async () => {
                const r = await api<Record<string, unknown>>("/archive/failed", "POST");
                await archive.refresh();
                return r;
              },
              (r) => {
                const n = Number((r as { archived?: unknown })?.archived ?? NaN);
                return Number.isFinite(n)
                  ? `${plural(n, "failed job")} archived.`
                  : "Earlier failures archived.";
              },
            )
          }
        />
      </div>
      <ErrorLine error={archive.error} retry={archive.refresh} />
      {!archive.data && !archive.error ? (
        <Loading label="Loading archive…" />
      ) : shown.length ? (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Job</th>
                  <th scope="col">Outcome</th>
                  <th scope="col" className="num">
                    Tries
                  </th>
                  <th scope="col">Archived</th>
                  <th scope="col">Log</th>
                </tr>
              </thead>
              <tbody>
                {paging.items.map((a) => (
                  <tr key={a.job_id}>
                    <td className="cell-job">
                      <button className="link job-open" onClick={() => openJob(a.job_id, a.run_id)}>
                        <strong>{a.company}</strong>
                        <span>{a.title}</span>
                      </button>
                    </td>
                    <td className="cell-state">
                      {a.uncertain_submission ? (
                        <StatePill state="submission_unknown" label="Unconfirmed · protected" />
                      ) : (
                        <StatePill state={a.state} label={a.owner_removed ? "Removed by you" : undefined} />
                      )}
                      {a.reason && (
                        <small className="reason" title={a.reason}>
                          {a.reason}
                        </small>
                      )}
                    </td>
                    <td className="num" data-label="Tries">
                      {a.attempts}
                    </td>
                    <td data-label="Archived">{day(a.archived_at)}</td>
                    <td data-label="Log">
                      <a className="link" href={`/api/archive/${a.job_id}/log`} download>
                        <Download size={14} aria-hidden="true" /> {words("log")}
                        {a.log_bytes ? ` · ${Math.max(1, Math.ceil(a.log_bytes / 1024))} KB` : ""}
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination paging={paging} label="Archive pages" />
        </>
      ) : (
        <Empty title={q ? "No archived jobs match" : "The archive is empty"}>
          {q ? null : "Jobs you delete and earlier failures you archive are kept here with their logs."}
        </Empty>
      )}
    </section>
  );
}
