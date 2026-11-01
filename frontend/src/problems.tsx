// Applications, What went wrong: every recent attempt that ended without a sent application, newest first, including the ones the app is
// about to retry (they read "Applying" everywhere else). Employer and role, what happened in a sentence, what the app does next and when,
// and the attempt's screenshot and record. GET /api/problems (jobpilot/problems.py), read-only.
import { CircleCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { ago, jobWords } from "./format";
import { useNow, usePoll } from "./hooks";
import { Pagination, type Paging } from "./pagination";
import { TopLine } from "./shell/Shell";
import type { Problem, ProblemPage } from "./types";
import { Empty, Listbox, Table, type Column } from "./ui/index";
import { JobCell, LoadError, Note } from "./worktools";

const SHOW = [
  { value: "", label: "Everything" },
  { value: "retry", label: "Will retry" },
  { value: "you", label: "Needs you" },
  { value: "stopped", label: "Stopped trying" },
  { value: "done", label: "Nothing more to do" },
];

export function ProblemList() {
  const { openJob, version } = useWorkspace();
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(50);
  const [kind, setKind] = useState("");
  useEffect(() => setPage(1), [kind]);
  const key = new URLSearchParams({ days: "7", page: String(page), size: String(size), kind }).toString();
  const list = usePoll<ProblemPage>((signal) => api(`/problems?${key}`, "GET", undefined, signal), 30000, [key, version]);
  const now = useNow(30000);
  const rows = list.data?.rows ?? [];
  const paging: Paging = {
    items: rows,
    total: list.data?.total ?? 0,
    page: list.data?.page ?? page,
    pages: list.data?.pages ?? 1,
    size,
    initialSize: 50,
    setPage,
    setSize,
  };
  const columns: Column<Problem>[] = useMemo(
    () => [
      {
        key: "job",
        header: "Job",
        width: 380,
        truncate: true,
        cell: (r) => (
          <JobCell
            {...jobWords(r)}
            onOpen={() => openJob(r.job_id, r.run_id)}
            meta={
              <>
                <span>{r.what}</span>
                <span>{r.next.sentence}</span>
              </>
            }
          />
        ),
      },
      { key: "what", header: "What happened", hideBelow: 600, width: 360, wrap: true, cell: (r) => <Note>{r.what}</Note> },
      {
        key: "next",
        header: "What happens next",
        hideBelow: 600,
        width: 300,
        wrap: true,
        cell: (r) => <Note>{r.next.sentence}</Note>,
      },
      {
        key: "when",
        header: "When",
        hideBelow: 860,
        cell: (r) => (
          <time dateTime={r.at} title={new Date(r.at).toLocaleString()}>
            {ago(r.at, now)}
          </time>
        ),
      },
      {
        key: "attempt",
        header: "Attempt",
        width: "1%",
        cell: (r) => (
          <span className="ax-row-links" onClick={(e) => e.stopPropagation()}>
            {r.screenshot && (
              <a className="ax-quiet-link" href={`/api/runs/${r.run_id}/screenshot`} target="_blank" rel="noreferrer">
                Screenshot
              </a>
            )}
            <button type="button" className="ax-quiet-link" onClick={() => openJob(r.job_id, r.run_id)}>
              Record
            </button>
          </span>
        ),
      },
    ],
    [now, openJob],
  );
  return (
    <>
      <TopLine>Every attempt from the last 7 days that ended without a sent application, newest first, and what the app does about it.</TopLine>
      <div className="ax-toolbar">
        <Listbox prefix="Show" label="Which attempts to show" value={kind} options={SHOW} onChange={setKind} />
      </div>
      {list.error && !!list.data && <LoadError error={list.error} retry={list.refresh} />}
      <div className="pb-list ax-page-fill">
      <Table
        fill
        label="What went wrong"
        columns={columns}
        rows={rows}
        rowKey={(r) => r.run_id}
        onRowClick={(r) => openJob(r.job_id, r.run_id)}
        loading={!list.data && !list.error}
        error={list.error && !list.data ? <LoadError error={list.error} retry={list.refresh} /> : undefined}
        footer={paging.total > 0 ? <Pagination paging={paging} label="What went wrong pages" /> : undefined}
        empty={<Empty icon={CircleCheck} title={kind ? "Nothing like this in the last 7 days" : "Nothing went wrong in the last 7 days"} hint="An attempt that ends without a sent application appears here." />}
      />
      </div>
    </>
  );
}
