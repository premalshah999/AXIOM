// Applications' Couldn't apply and Not applying, and Needs you's applications with nothing to answer: one row per job, in a plain
// sentence saying what happened (jobpilot/vocabulary.py). Pages come from GET /api/jobs, the numbers on the tabs from the same count.
import { Briefcase, SearchX } from "lucide-react";
import { useEffect, useMemo } from "react";
import { useSelection } from "./bulk";
import { useWorkspace } from "./context";
import { ago, jobWords } from "./format";
import { useDraft, useNow } from "./hooks";
import { RowActions } from "./jobactions";
import type { Group, JobRow, Word } from "./model";
import { Pagination } from "./pagination";
import { usePipelinePage } from "./pipeline";
import { PipelineBulk, useSelectMatching } from "./pipelinebulk";
import { TopLine } from "./shell/Shell";
import { Button, Empty, Table, type Column } from "./ui/index";
import { JobCell, LoadError, Note, SearchBox, StatusPill } from "./worktools";

const SAYS: Record<string, { line: string; empty: string; hint: string }> = {
  failed: { line: "The app has given up on these. Each says why.", empty: "Nothing has failed", hint: "A job the app will retry itself is on the Queue, not here." },
  not: { line: "Closed, removed by you, not eligible, or the employer already has you. Each says which.", empty: "Nothing here", hint: "Jobs that will not be applied to appear here, each with the reason." },
  needs: { line: "", empty: "Nothing needs you", hint: "When a form asks something only you know, the job appears here." },
  unconfirmed: { line: "", empty: "Nothing is unconfirmed", hint: "An application sent without a receipt appears here for you to check." },
};

export function HistoryList({ word, inline }: { word: Word | "unconfirmed"; inline?: boolean }) {
  const { route, openJob } = useWorkspace();
  const [query, setQuery] = useDraft("history-query:" + word, "");
  useEffect(() => {
    if (route.q) setQuery(route.q);
  }, [route.q, setQuery]);
  const list = usePipelinePage(word as Word, query);
  const q = query.trim();
  const say = SAYS[word] ?? SAYS.failed!;
  const selection = useSelection(5000);
  const matching = useSelectMatching(selection, list.rows, word as Group, q, list.paging.total);
  useEffect(() => selection.clear(), [word, query]); // eslint-disable-line react-hooks/exhaustive-deps
  const now = useNow(30000);

  const columns: Column<JobRow>[] = useMemo(
    () => [
      {
        key: "job",
        header: "Job",
        width: 460,
        truncate: true,
        cell: (r) => (
          <JobCell
            {...jobWords(r.job)}
            onOpen={() => openJob(r.job.id, r.run?.id)}
            meta={
              <>
                <StatusPill state={r.state} />
                {r.sentence && <span>{r.sentence}</span>}
              </>
            }
          />
        ),
      },
      { key: "why", header: "What happened", hideBelow: 600, truncate: true, width: 440, cell: (r) => <Note title={r.sentence}>{r.sentence}</Note> },
      {
        key: "updated",
        header: "When",
        hideBelow: 860,
        cell: (r) => (
          <time dateTime={r.updated} title={new Date(r.updated).toLocaleString()}>
            {ago(r.updated, now)}
          </time>
        ),
      },
      { key: "act", header: <span className="sr-only">Actions</span>, width: "1%", pin: true, cell: (r) => <RowActions row={r} /> },
    ],
    [now, openJob],
  );

  return (
    <>
      {!inline && say.line && <TopLine>{say.line}</TopLine>}
      <div className="ax-toolbar">
        <SearchBox value={query} onChange={setQuery} label="Search jobs" placeholder="Company or role" />
      </div>
      {list.error && list.loaded && <LoadError error={list.error} retry={list.refresh} />}
      <Table
        fill={!inline}
        label="Jobs"
        columns={columns}
        rows={list.rows}
        rowKey={(r) => r.job.id}
        onRowClick={(r) => openJob(r.job.id, r.run?.id)}
        selected={selection.chosen}
        onSelectedChange={selection.replace}
        rowLabel={(r) => r.job.company || "job"}
        loading={!list.loaded && !list.error}
        error={list.error && !list.loaded ? <LoadError error={list.error} retry={list.refresh} /> : undefined}
        busy={list.loaded && !list.current}
        footer={list.paging.total > 0 ? <Pagination paging={list.paging} label="Job list pages" /> : undefined}
        selectAll={list.paging.total ? { count: list.paging.total, run: matching.selectAll, busy: matching.selecting } : undefined}
        bulk={<PipelineBulk selection={selection} known={matching.known} />}
        empty={
          <Empty
            icon={q ? SearchX : Briefcase}
            title={q ? "No jobs match this search" : say.empty}
            hint={q ? "Try a company name, or clear the search." : say.hint}
            action={q ? <Button size="sm" onClick={() => setQuery("")}>Clear the search</Button> : undefined}
          />
        }
      />
    </>
  );
}
