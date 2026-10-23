import { useMemo } from "react";
import { Download, Search } from "lucide-react";
import { api, applyJob } from "./api";
import { routeUrl, useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { DiscoveredJobs } from "./jobboard";
import {
  COMMITTED,
  GROUPS,
  RETRYABLE,
  sortRows,
  stateInfo,
  type Group,
  type JobRow,
} from "./model";
import { Pagination, usePagination } from "./pagination";
import { QueuePanel } from "./queue";
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
  words,
} from "./ui";

const TABS = [
  { id: "pipeline", label: "Pipeline" },
  { id: "discovered", label: "Discovered" },
  { id: "archive", label: "Archive" },
];

export function JobsView() {
  const { route, go, snapshot, rows } = useWorkspace();
  const tab = TABS.some((t) => t.id === route.tab) ? route.tab! : "pipeline";
  const pipelineCount = rows.filter((r) => r.run || r.control === "paused").length;
  const counts: Record<string, number | undefined> = {
    pipeline: pipelineCount,
    discovered: snapshot.autopilot_runtime?.inventory?.fresh,
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
      {tab === "archive" && <ArchiveList />}
    </div>
  );
}

function Pipeline() {
  const { rows, route, go } = useWorkspace();
  const [query, setQuery] = useDraft("pipeline-query", "");
  const filter = (route.filter || "all") as Group | "all";
  const scoped = useMemo(
    () => rows.filter((r) => r.run || r.control === "paused" || stateInfo(r.state).group === "new"),
    [rows],
  );
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of scoped) {
      const g = stateInfo(r.state).group;
      c[g] = (c[g] || 0) + 1;
    }
    c.all = scoped.filter((r) => stateInfo(r.state).group !== "new").length;
    return c;
  }, [scoped]);
  const q = query.trim().toLowerCase();
  const shown = useMemo(
    () =>
      sortRows(
        scoped.filter((r) => {
          const g = stateInfo(r.state).group;
          if (filter === "all" ? g === "new" : g !== filter) return false;
          return (
            !q ||
            `${r.job.company} ${r.job.title} ${r.job.location} ${r.job.ats}`
              .toLowerCase()
              .includes(q)
          );
        }),
      ),
    [scoped, filter, q],
  );
  const paging = usePagination(shown, filter + "|" + q, 25);
  const chips = [...GROUPS, { id: "new" as const, label: "Not started" }];
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
          {chips.map((c) => {
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
      {shown.length ? (
        <>
          <JobTable rows={paging.items} />
          <Pagination paging={paging} label="Pipeline pages" />
        </>
      ) : !q && filter === "all" && counts.new ? (
        <Empty title="No applications have started yet">
          {plural(counts.new, "saved job")} {counts.new === 1 ? "is" : "are"} waiting to be ranked and applied to.{" "}
          <button
            className="link"
            onClick={() => go({ view: "jobs", tab: "pipeline", filter: "new" })}
          >
            Show saved jobs
          </button>
        </Empty>
      ) : (
        <Empty
          title={
            q
              ? "No jobs match this search"
              : filter === "all"
                ? "No applications yet"
                : `Nothing is ${stateInfo(
                    filter === "needs" ? "waiting_answer" : filter,
                  ).label.toLowerCase()} right now`
          }
        >
          {q
            ? "Try a company name or clear the search."
            : filter === "all"
              ? "Applications appear here once the batch or a pasted link queues them."
              : filter === "needs"
                ? "No application is waiting for a personal fact."
                : null}
        </Empty>
      )}
    </section>
  );
}

function JobTable({ rows }: { rows: JobRow[] }) {
  const { openJob, route } = useWorkspace();
  const now = useNow(30000);
  return (
    <div className="table-wrap">
      <table className="table jobs-table">
        <thead>
          <tr>
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
              <td className="cell-job">
                <button className="link job-open" onClick={() => openJob(r.job.id, r.run?.id)}>
                  <strong>{r.job.company || linkHost(r.job.url) || "Employer"}</strong>
                  <span>{r.job.title || r.job.url}</span>
                </button>
                <small className="muted">
                  {[r.job.location, r.job.ats].filter(Boolean).join(" · ")}
                </small>
              </td>
              <td className="cell-state">
                <StatePill state={r.state} />
                {r.run?.reason && r.state !== "confirmed" && (
                  <small className="reason" title={r.run.reason}>
                    {r.run.reason}
                  </small>
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
  const { refresh, openJob } = useWorkspace();
  const { pending, run } = useAction();
  const run_ = row.run;
  const state = row.state;
  const committed = !!run_ && COMMITTED.has(run_.state);
  const control = (action: "pause" | "resume" | "delete") =>
    run(
      action,
      async () => {
        const result = await api<ControlResult>(
          action === "delete" ? `/jobs/${row.job.id}` : `/jobs/${row.job.id}/${action}`,
          action === "delete" ? "DELETE" : "POST",
        );
        await refresh();
        return result;
      },
      (r) => r?.message,
    );
  const applyNow = () =>
    run(
      "apply",
      async () => {
        const r = await applyJob(row.job.id);
        await refresh();
        return r;
      },
      (r) => r?.message,
    );
  const busy = pending !== null;
  const buttons: React.ReactNode[] = [];
  if (state === "pausing")
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
