// Applications › Archive: earlier failed and removed jobs, with their compressed diagnostic logs. Nothing here is retried.
import { Download } from "lucide-react";
import { useMemo } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, usePoll } from "./hooks";
import { Pagination, usePagination } from "./pagination";
import type { Archived } from "./types";
import { day, plural } from "./format";
import { Button, Empty, Table, type Column } from "./ui/index";
import { ConfirmButton, JobCell, LoadError, Note, SearchBox, StatusPill } from "./worktools";

export function ArchiveList() {
  const { openJob } = useWorkspace();
  const { pending, run } = useAction();
  const [query, setQuery] = useDraft("archive-query", "");
  const archive = usePoll<{ applications: Archived[]; log_bytes: number }>(
    (signal) => api("/archive", "GET", undefined, signal),
    60000,
  );
  const items = archive.data?.applications || [];
  const q = query.trim().toLowerCase();
  const shown = useMemo(
    () => items.filter((a) => !q || `${a.company} ${a.title}`.toLowerCase().includes(q)),
    [items, q],
  );
  const paging = usePagination(shown, q, 25);
  const columns: Column<Archived>[] = [
    {
      key: "job",
      header: "Job",
      width: 420,
      truncate: true,
      cell: (a) => <JobCell company={a.company || "Employer"} role={a.title || "Untitled role"} onOpen={() => openJob(a.job_id, a.run_id)} />,
    },
    {
      key: "outcome",
      header: "Outcome",
      cell: (a) =>
        a.uncertain_submission ? (
          <StatusPill state="submission_unknown" label="Unconfirmed · protected" />
        ) : (
          <StatusPill state={a.state} label={a.owner_removed ? "Removed by you" : undefined} />
        ),
    },
    { key: "reason", header: "Detail", hideBelow: 600, width: 300, truncate: true, cell: (a) => (a.reason ? <Note>{a.reason}</Note> : null) },
    { key: "archived", header: "Archived", hideBelow: 600, cell: (a) => day(a.archived_at) },
    {
      key: "log",
      header: "Log",
      cell: (a) => (
        <a className="ax-link" href={`/api/archive/${a.job_id}/log`} download>
          <Download size={13} aria-hidden="true" /> Log
          {a.log_bytes ? ` · ${Math.max(1, Math.ceil(a.log_bytes / 1024))} KB` : ""}
        </a>
      ),
    },
  ];
  return (
    <>
      <div className="ax-toolbar">
        <SearchBox value={query} onChange={setQuery} label="Search archive" placeholder="Company or role" />
        <span className="ax-note ax-toolbar-note">Nothing here is retried.</span>
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
        loading={!archive.data && !archive.error}
        error={archive.error && !archive.data ? <LoadError error={archive.error} retry={archive.refresh} /> : undefined}
        empty={
          <Empty
            title={q ? "No archived jobs match" : "The archive is empty"}
            hint={q ? undefined : "Jobs you delete and earlier failures you archive are kept here with their logs."}
          />
        }
      />
      <Pagination paging={paging} label="Archive pages" />
    </>
  );
}
