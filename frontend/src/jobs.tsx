import { Briefcase, Plus, SearchX } from "lucide-react";
import { useEffect, useMemo } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useDraft, useNow, usePoll } from "./hooks";
import { useSelection } from "./bulk";
import { DiscoveredJobs } from "./jobboard";
import { OPEN_JOB_LINK } from "./jobintake";
import { RowActions } from "./jobactions";
import { type Group, type JobRow, type Word } from "./model";
import { PipelineBulk } from "./pipelinebulk";
import { Pagination } from "./pagination";
import { usePipelinePage } from "./pipeline";
import { PAGE_TABS } from "./shell/nav";
import { TopActions, TopLine } from "./shell/Shell";
import type { Startable } from "./types";
import { ago, jobWords } from "./format";
import { Button, Empty, RouteTabs, Table, type Column } from "./ui/index";
import { JobCell, LoadError, Note, SearchBox, StatusPill } from "./worktools";

// One page, five words. Each tab is one word of the vocabulary (jobpilot/vocabulary.py) and its number opens exactly that many rows:
// To apply (found, and the batch can start it), Applying (running, queued, or stopped and the app retries it itself), Needs you,
// Couldn't apply (the app gave up) and Not applying (closed, removed, not eligible, applied elsewhere, paused, or waiting for your
// approval). Applied is Applications. Old tab ids still open: "pipeline" is Applying, "reserved" is Not applying.
const WORD_OF_TAB: Record<string, string> = { pipeline: "applying", applying: "applying", needs: "needs", failed: "failed", not: "not", reserved: "not" };

export function JobsView() {
  const { route, counts: all, version } = useWorkspace();
  const tabs = PAGE_TABS.jobs!;
  const tab = route.tab && (tabs.some((t) => t.id === route.tab) || WORD_OF_TAB[route.tab]) ? route.tab : "discovered";
  const shown = tab === "pipeline" ? "applying" : tab === "reserved" ? "not" : tab;
  // How many Ready jobs can start: the same number Overview's sentence and the To apply list name (jobpilot/startable.py).
  const ready = usePoll<Startable>((signal) => api("/startable", "GET", undefined, signal), 30000, [version]);
  const words = all.words;
  const counted = tabs.map((t) => ({
    ...t,
    count: t.id === "discovered" ? ready.data?.startable : t.id === "needs" ? undefined : words?.[t.id as keyof NonNullable<typeof words>],
  }));
  return (
    <div className="ax-page ax-page-fill">
      <TopActions>
        <Button variant="secondary" icon={<Plus size={15} aria-hidden="true" />} onClick={() => window.dispatchEvent(new Event(OPEN_JOB_LINK))}>
          Add job
        </Button>
      </TopActions>
      <RouteTabs view="jobs" tabs={counted} label="Jobs" current={shown} />
      {shown === "discovered" ? <DiscoveredJobs /> : <WordList word={WORD_OF_TAB[shown] ?? "applying"} />}
    </div>
  );
}

const SAYS: Record<string, { line: string; empty: string; hint: string }> = {
  applying: { line: "Running now, waiting for a worker, or stopped on a problem the app will retry by itself.", empty: "Nothing is being applied to", hint: "Applications appear here as they start." },
  needs: { line: "Only you can answer these. Answer them one by one on the Needs you page.", empty: "Nothing needs you", hint: "When a form asks something only you know, the job appears here." },
  failed: { line: "The app has given up on these. Each says why.", empty: "Nothing has failed", hint: "A job the app will retry itself is under Applying, not here." },
  not: { line: "Closed, removed, not eligible, already applied elsewhere, paused, or waiting for your approval.", empty: "Nothing here", hint: "Jobs that will not be applied to appear here, each with the reason." },
};

function WordList({ word }: { word: string }) {
  const { route, go, openJob } = useWorkspace();
  const [query, setQuery] = useDraft("pipeline-query", "");
  useEffect(() => {
    if (route.q) setQuery(route.q);
  }, [route.q, setQuery]);
  const list = usePipelinePage(word as Word, query);
  const q = query.trim();
  const say = SAYS[word] ?? SAYS.applying!;
  const selection = useSelection(200);
  useEffect(() => selection.clear(), [word, query]); // eslint-disable-line react-hooks/exhaustive-deps
  const now = useNow(30000);

  const columns: Column<JobRow>[] = useMemo(() => {
    const when = (r: JobRow) => (
      <time dateTime={r.updated} title={new Date(r.updated).toLocaleString()}>
        {ago(r.updated, now)}
      </time>
    );
    return [
      {
        key: "job",
        header: "Job",
        width: 520,
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
      { key: "why", header: "What is happening", hideBelow: 600, truncate: true, width: 380, cell: (r) => <Note title={r.sentence}>{r.sentence}</Note> },
      { key: "updated", header: "Updated", hideBelow: 860, cell: when },
      { key: "act", header: <span className="sr-only">Actions</span>, width: "1%", pin: true, cell: (r) => <RowActions row={r} /> },
    ];
  }, [now, openJob]);

  return (
    <>
      <TopLine>
        {say.line}
        {word === "needs" && (
          <>
            {" "}
            <button type="button" className="ax-link" onClick={() => go({ view: "needs" })}>
              Open Needs you
            </button>
          </>
        )}
      </TopLine>
      <div className="ax-toolbar">
        <SearchBox value={query} onChange={setQuery} label="Search jobs" placeholder="Company or role" />
      </div>
      {list.error && list.loaded && <LoadError error={list.error} retry={list.refresh} />}
      <Table
        fill
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
        bulk={<PipelineBulk selection={selection} rows={list.rows} filter={word as Group} query={q} total={list.paging.total} />}
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
