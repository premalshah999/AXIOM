// Applications › Archive: earlier failed and removed jobs, with their compressed diagnostic logs. Nothing here is retried.
import { Archive, Download, SearchX } from "lucide-react";
import { useMemo } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, type usePoll } from "./hooks";
import { Pagination, usePagination } from "./pagination";
import { TopLine } from "./shell/Shell";
import type { Archived } from "./types";
import { day, jobWords, plural } from "./format";
import { stateInfo } from "./model";
import { Button, Empty, Table, type Column } from "./ui/index";
import { ConfirmButton, JobCell, LoadError, Note, SearchBox, StatusPill } from "./worktools";

// What a row ended as. Nothing archived needs you, whatever state it ended in, so no lamp is lit here.
function Outcome({ a }: { a: Archived }) {
  return a.uncertain_submission ? (
    <StatusPill state="submission_unknown" label="Unconfirmed · protected" />
  ) : (
    <StatusPill state="cancelled" label={a.owner_removed ? "Removed by you" : stateInfo(a.state).label} />
  );
}

export function ArchiveList({ archive }: { archive: ReturnType<typeof usePoll<{ applications: Archived[]; log_bytes: number }>> }) {
  const { openJob } = useWorkspace();
  const { pending, run } = useAction();
  const [query, setQuery] = useDraft("archive-query", "");
  const q = query.trim().toLowerCase();
  const shown = useMemo(
    () => (archive.data?.applications || []).filter((a) => !q || `${a.company} ${a.title}`.toLowerCase().includes(q)),
    [archive.data, q],
  );
  const paging = usePagination(shown, q, 25);
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
    { key: "reason", header: "Detail", hideBelow: 600, width: 300, truncate: true, cell: (a) => (a.reason ? <Note>{a.reason}</Note> : null) },
    { key: "archived", header: "Archived", hideBelow: 600, cell: (a) => <time dateTime={a.archived_at}>{day(a.archived_at)}</time> },
    {
      key: "log",
      header: <span className="sr-only">Log</span>,
      hideBelow: 600,
      width: "1%",
      pin: true,
      cell: (a) => (
        // The app serves the log (as it does a resume), so it saves the same way in a browser tab and in the native window.
        <a className="ax-btn is-sm ax-lead" href={`/api/archive/${a.job_id}/log`} download onClick={(e) => e.stopPropagation()}>
          <Download size={13} aria-hidden="true" /> Log
          {a.log_bytes ? ` · ${Math.max(1, Math.ceil(a.log_bytes / 1024))} KB` : ""}
        </a>
      ),
    },
  ];
  return (
    <>
      <TopLine>Earlier failures and jobs you removed, kept with their logs. Nothing here is retried.</TopLine>
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
