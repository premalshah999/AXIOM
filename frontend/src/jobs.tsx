import { Briefcase, Plus, SearchX } from "lucide-react";
import { useEffect, useMemo } from "react";
import { useWorkspace } from "./context";
import { useDraft, useNow } from "./hooks";
import { useSelection } from "./bulk";
import { DiscoveredJobs } from "./jobboard";
import { OPEN_JOB_LINK } from "./jobintake";
import { RowActions } from "./jobactions";
import { GROUPS, type Group, type JobRow } from "./model";
import { PipelineBulk } from "./pipelinebulk";
import { Pagination } from "./pagination";
import { usePipelinePage } from "./pipeline";
import { ReservedJobs } from "./reserved";
import { PAGE_TABS } from "./shell/nav";
import { TopActions, TopLine } from "./shell/Shell";
import { ago, employerOf, urlRole } from "./format";
import { Button, Empty, RouteTabs, Table, Tooltip, type Column } from "./ui/index";
import { Chips, JobCell, LoadError, Note, SearchBox, StatusPill } from "./worktools";

// Three named views of one question, "what is happening with this job?": Started (everything AXIOM has started or queued),
// Discovered (found, not applied to yet) and Reserved (employers that wait for your approval). The tab ids stay as they were, so
// every old address still works; "pipeline" is engineer's language and no one sees it.
export function JobsView() {
  const { route, counts: all } = useWorkspace();
  const tabs = PAGE_TABS.jobs!;
  const tab = tabs.some((t) => t.id === route.tab) ? route.tab! : "pipeline";
  const counted = tabs.map((t) => ({
    ...t,
    // Server counts over every job. Discovered is the jobs not applied to yet, the same number its page shows.
    count: t.id === "pipeline" ? all.pipeline : t.id === "discovered" ? all.discovered : all.groups.reserved,
  }));
  return (
    <div className="ax-page ax-page-fill">
      <TopActions>
        <Button variant={tab === "pipeline" ? "primary" : "secondary"} icon={<Plus size={15} aria-hidden="true" />} onClick={() => window.dispatchEvent(new Event(OPEN_JOB_LINK))}>
          Add job
        </Button>
      </TopActions>
      <RouteTabs view="jobs" tabs={counted} label="Job lists" current={tab} />
      {tab === "pipeline" && <Started />}
      {tab === "discovered" && <DiscoveredJobs />}
      {tab === "reserved" && <ReservedJobs />}
    </div>
  );
}

// Jobs › Started: applications that have started or will start (the queue, applying, needs you, repairing, done, stopped). Jobs
// found but never applied to are in Discovered, which is where they are selected and applied to (owner, 2026-10-23: "so confusing
// for the job pipeline part, like the pipeline and the discovery").
function Started() {
  const { route, go, counts: all, openJob } = useWorkspace();
  const [query, setQuery] = useDraft("pipeline-query", "");
  // A number elsewhere can open this list already searched (Overview's platforms).
  useEffect(() => {
    if (route.q) setQuery(route.q);
  }, [route.q, setQuery]);
  const filter = (route.filter || "all") as Group | "all";
  const list = usePipelinePage(filter, query);
  // Exact group counts read together with the rows on screen, so a filter and its list always agree.
  const counts = list.counts?.groups || all.groups;
  const q = query.trim();
  const label = GROUPS.find((g) => g.id === filter)?.label || "All";
  // Tick rows, a page or every match, then act on them together (owner 2026-10-28: "no checkboxes for select all").
  const selection = useSelection(200);
  useEffect(() => selection.clear(), [filter, query]); // eslint-disable-line react-hooks/exhaustive-deps
  const now = useNow(30000);
  const chips = GROUPS.filter((c) => c.id === "all" || counts[c.id] || filter === c.id).map((c) => ({
    id: c.id,
    label: c.label,
    count: counts[c.id] || 0,
  }));

  const columns: Column<JobRow>[] = useMemo(() => {
    const why = (r: JobRow) => (r.state === "pending:waiting" && r.request?.reason ? r.request.reason : r.state !== "confirmed" ? r.run?.reason : "") || "";
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
            company={employerOf(r.job.company, r.job.url)}
            role={r.job.title || urlRole(r.job.url)}
            onOpen={() => openJob(r.job.id, r.run?.id)}
            meta={
              <>
                <StatusPill state={r.state} />
                {when(r)}
              </>
            }
          />
        ),
      },
      { key: "where", header: "Location", hideBelow: 860, width: 170, truncate: true, cell: (r) => <Note>{r.job.location}</Note> },
      {
        key: "status",
        header: "Status",
        hideBelow: 600,
        cell: (r) => (
          <Tooltip label={why(r)}>
            <StatusPill state={r.state} />
          </Tooltip>
        ),
      },
      { key: "updated", header: "Updated", hideBelow: 600, cell: when },
      { key: "act", header: <span className="sr-only">Actions</span>, width: "1%", pin: true, cell: (r) => <RowActions row={r} /> },
    ];
  }, [now, openJob]);

  return (
    <>
      <TopLine>Every job AXIOM has started or queued.</TopLine>
      <div className="ax-toolbar">
        <SearchBox value={query} onChange={setQuery} label="Search jobs" placeholder="Company, role or location" />
        <Chips
          items={chips}
          value={filter}
          label="Filter by status"
          onChange={(id) => go({ view: "jobs", tab: "pipeline", filter: id === "all" ? undefined : id })}
        />
      </div>
      {list.error && list.loaded && <LoadError error={list.error} retry={list.refresh} />}
      <Table
        fill
        label="Started jobs"
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
        bulk={<PipelineBulk selection={selection} rows={list.rows} filter={filter} query={q} total={list.paging.total} />}
        empty={
          <Empty
            icon={q ? SearchX : Briefcase}
            title={q ? "No jobs match this search" : filter === "all" ? "No jobs started yet" : `Nothing in “${label}” right now`}
            hint={
              q
                ? "Try a company name, or clear the search."
                : filter === "all"
                  ? "Applications appear here as soon as they start: add a job link, pick from Discovered, or start a batch on Overview."
                  : filter === "needs"
                    ? "When a form asks something only you know, the job appears here."
                    : "Jobs appear here as they reach this status."
            }
            action={
              q ? (
                <Button size="sm" onClick={() => setQuery("")}>
                  Clear the search
                </Button>
              ) : filter === "all" ? (
                <Button size="sm" onClick={() => go({ view: "jobs", tab: "discovered" })}>
                  Open Discovered
                </Button>
              ) : undefined
            }
          />
        }
      />
    </>
  );
}

