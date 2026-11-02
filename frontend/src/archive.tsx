// Applications › Archive: earlier failed and removed jobs, with their compressed diagnostic logs. Nothing here starts by itself; a job
// nothing was sent for can be put back, and the server refuses one that may have reached the employer.
import { Archive, Download, SearchX } from "lucide-react";
import { useEffect, useMemo } from "react";
import { api } from "./api";
import { useSelection } from "./bulk";
import { useWorkspace } from "./context";
import { useAction, useDraft, type usePoll } from "./hooks";
import { Pagination, usePagination } from "./pagination";
import { TopLine } from "./shell/Shell";
import type { Archived } from "./types";
import { day, jobWords, plural, plainReason } from "./format";
import { stateInfo } from "./model";
import { Button, Empty, Table, type Column } from "./ui/index";
import { ConfirmButton, JobCell, LoadError, Note, SearchBox, StatusPill } from "./worktools";

// What a row ended as. Nothing archived needs you, whatever state it ended in, so no lamp is lit here.
function Outcome({ a }: { a: Archived }) {
  return a.uncertain_submission ? (
    <StatusPill state="submission_unknown" label="May have been sent" />
  ) : (
    <StatusPill state="cancelled" label={a.owner_removed ? "Removed by you" : stateInfo(a.state).label} />
  );
}

export function ArchiveList({ archive }: { archive: ReturnType<typeof usePoll<{ applications: Archived[]; log_bytes: number }>> }) {
  const { openJob, refresh } = useWorkspace();
  const { pending, run } = useAction();
  const [query, setQuery] = useDraft("archive-query", "");
  const q = query.trim().toLowerCase();
  const shown = useMemo(
    () => (archive.data?.applications || []).filter((a) => !q || `${a.company} ${a.title}`.toLowerCase().includes(q)),
    [archive.data, q],
  );
  const paging = usePagination(shown, q, 25);
  const selection = useSelection(5000);
  useEffect(() => selection.clear(), [q]); // eslint-disable-line react-hooks/exhaustive-deps
  // Restoring a job the server won't let back says why, one job or many; a job that may have been sent stays where it is.
  const restore = (ids: string[]) =>
    run(
      "restore",
      async () => {
        const outcomes = await Promise.allSettled(ids.map((id) => api(`/jobs/${id}/restore`, "POST")));
        const back = outcomes.filter((o) => o.status === "fulfilled").length;
        selection.clear();
        await Promise.all([archive.refresh(), refresh()]);
        return { back, kept: ids.length - back, why: outcomes.find((o): o is PromiseRejectedResult => o.status === "rejected")?.reason?.message as string | undefined };
      },
      (r) =>
        r &&
        [
          r.back ? `Restored ${plural(r.back, "job")}. ${r.back === 1 ? "It" : "They"} can be applied to again.` : "",
          r.kept ? (ids.length === 1 ? r.why : `${plural(r.kept, "job")} stayed removed: an application may already have reached the employer.`) : "",
        ]
          .filter(Boolean)
          .join(" "),
    );
  const columns: Column<Archived>[] = [
    {
      key: "job",
      header: "Job",
      width: 420,
      truncate: true,
      cell: (a) => (
        <JobCell
          {...jobWords(a)}
          onOpen={() => openJob(a.job_id, a.run_id)}
          meta={
            <>
              <Outcome a={a} />
              <time dateTime={a.archived_at}>{day(a.archived_at)}</time>
            </>
          }
        />
      ),
    },
    { key: "outcome", header: "Outcome", hideBelow: 600, cell: (a) => <Outcome a={a} /> },
    { key: "reason", header: "Detail", hideBelow: 600, width: 300, truncate: true, cell: (a) => (a.reason ? <Note title={plainReason(a.reason)}>{plainReason(a.reason)}</Note> : null) },
    { key: "archived", header: "Archived", hideBelow: 600, cell: (a) => <time dateTime={a.archived_at}>{day(a.archived_at)}</time> },
    {
      key: "log",
      header: <span className="sr-only">Restore and log</span>,
      hideBelow: 600,
      width: "1%",
      pin: true,
      cell: (a) => (
        <span className="ax-act" onClick={(e) => e.stopPropagation()}>
          {!a.uncertain_submission && (
            <Button size="sm" className="ax-lead" disabled={pending !== null} onClick={() => void restore([a.job_id])}>
              Restore
            </Button>
          )}
          {/* The app serves the log (as it does a resume), so it saves the same way in a browser tab and in the native window. */}
          <a className="ax-btn is-sm ax-lead" href={`/api/archive/${a.job_id}/log`} download>
            <Download size={13} aria-hidden="true" /> Log
            {a.log_bytes ? ` · ${Math.max(1, Math.ceil(a.log_bytes / 1024))} KB` : ""}
          </a>
        </span>
      ),
    },
  ];
  return (
    <>
      <TopLine>Earlier failures and jobs you removed, kept with their logs. Nothing here starts by itself.</TopLine>
      <div className="ax-toolbar">
        <SearchBox value={query} onChange={setQuery} label="Search archive" placeholder="Company or role" />
        <div className="ax-toolbar-end">
          <ConfirmButton
            label="Archive earlier failures"
            question="Archive every earlier failure? Confirmed and unconfirmed submissions are never archived."
            confirmLabel="Archive failures"
            danger={false}
            loading={pending === "archive"}
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
                  return Number.isFinite(n) ? `${plural(n, "failed job")} archived.` : "Earlier failures archived.";
                },
              )
            }
          />
        </div>
      </div>
      {archive.error && archive.data && <LoadError error={archive.error} retry={archive.refresh} />}
      <Table
        fill
        label="Archived applications"
        columns={columns}
        rows={paging.items}
        rowKey={(a) => a.job_id}
        onRowClick={(a) => openJob(a.job_id, a.run_id)}
        selected={selection.chosen}
        onSelectedChange={selection.replace}
        rowLabel={(a) => a.company || "job"}
        selectAll={shown.length ? { count: shown.length, run: () => selection.replace(new Set(shown.map((a) => a.job_id))) } : undefined}
        bulk={
          <ConfirmButton
            label="Restore"
            question={`Restore ${plural(selection.ids.length, "job")}? Any that may have reached an employer stay removed.`}
            confirmLabel="Restore"
            danger={false}
            loading={pending === "restore"}
            disabled={pending !== null && pending !== "restore"}
            onConfirm={() => restore([...selection.ids])}
          />
        }
        loading={!archive.data && !archive.error}
        error={archive.error && !archive.data ? <LoadError error={archive.error} retry={archive.refresh} /> : undefined}
        footer={paging.total > 0 ? <Pagination paging={paging} label="Archive pages" /> : undefined}
        empty={
          q ? (
            <Empty
              icon={SearchX}
              title="No archived jobs match"
              hint="Try a company name, or clear the search."
              action={
                <Button size="sm" onClick={() => setQuery("")}>
                  Clear the search
                </Button>
              }
            />
          ) : (
            <Empty
              icon={Archive}
              title="The archive is empty"
              hint="Jobs you remove, and earlier failures you archive, are kept here with their logs."
            />
          )
        }
      />
    </>
  );
}
