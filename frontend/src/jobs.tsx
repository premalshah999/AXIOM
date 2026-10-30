import { useEffect, useMemo } from "react";
import { useWorkspace } from "./context";
import { useDraft, useNow } from "./hooks";
import { useSelection } from "./bulk";
import { DiscoveredJobs } from "./jobboard";
import { RowActions } from "./jobactions";
import { GROUPS, type Group, type JobRow } from "./model";
import { PipelineBulk } from "./pipelinebulk";
import { Pagination } from "./pagination";
import { usePipelinePage } from "./pipeline";
import { QueuePanel } from "./queue";
import { ReservedJobs } from "./reserved";
import { ago, linkHost, plural, urlRole } from "./ui";
import { Button, Empty, RouteTabs, Table, type Column } from "./ui/index";
import { Chips, JobCell, LoadError, Notice, Note, SearchBox, StatusPill } from "./worktools";

const TABS = [
  { id: "pipeline", label: "Pipeline" },
  { id: "discovered", label: "Discovered" },
  // FAANG, MANGO and the Big Four wait here for the owner's approval (owner, 2026-10-27).
  { id: "reserved", label: "Reserved" },
];

export function JobsView() {
  const { route, counts: all } = useWorkspace();
  const tab = TABS.some((t) => t.id === route.tab) ? route.tab! : "pipeline";
  // Server counts over every job (the browser used to count the newest 1,000 it was sent). Discovered is the jobs not
  // applied to yet, the same number its page shows; Pipeline is everything started or waiting to start.
  const tabs = TABS.map((t) => ({
    ...t,
    count: t.id === "pipeline" ? all.pipeline : t.id === "discovered" ? all.discovered : all.groups.reserved,
  }));
  return (
    <div className="ax-page ax-page-fill">
      <RouteTabs view="jobs" tabs={tabs} label="Job lists" current={tab} />
      {tab === "pipeline" && <Pipeline />}
      {tab === "discovered" && <DiscoveredJobs />}
      {tab === "reserved" && <ReservedJobs />}
    </div>
  );
}

// Jobs › Pipeline: applications that have started or will start (the queue, applying, needs you, repairing, done,
// stopped). Jobs found but never applied to are in Discovered, which is where they are selected and applied to
// (owner, 2026-10-23: "so confusing for the job pipeline part, like the pipeline and the discovery"). Adding a job
// link is the paste box above the list, and ⌘K.
function Pipeline() {
  const { route, go, counts: all } = useWorkspace();
  const [query, setQuery] = useDraft("pipeline-query", "");
  const filter = (route.filter || "all") as Group | "all";
  const list = usePipelinePage(filter, query);
  // Exact group counts read together with the rows on screen, so a chip and its list always agree.
  const counts = list.counts?.groups || all.groups;
  const q = query.trim();
  const label = GROUPS.find((g) => g.id === filter)?.label || "All";
  // Tick rows, a page or every match, then act on them together (owner 2026-10-28: "no checkboxes for select all").
  const selection = useSelection(200);
  useEffect(() => selection.clear(), [filter, query]); // eslint-disable-line react-hooks/exhaustive-deps
  const now = useNow(30000);
  const { openJob } = useWorkspace();
  const chips = GROUPS.filter((c) => c.id === "all" || counts[c.id] || filter === c.id).map((c) => ({
    id: c.id,
    label: c.label,
    count: counts[c.id] || 0,
  }));

  const columns: Column<JobRow>[] = useMemo(
    () => [
      {
        key: "job",
        header: "Job",
        width: 400,
        truncate: true,
        cell: (r) => (
          <JobCell
            company={r.job.company || linkHost(r.job.url) || "Employer"}
            role={r.job.title || urlRole(r.job.url)}
            title={r.job.url}
            onOpen={() => openJob(r.job.id, r.run?.id)}
          />
        ),
      },
      { key: "status", header: "Status", cell: (r) => <StatusPill state={r.state} /> },
      {
        key: "detail",
        header: "Detail",
        hideBelow: 600,
        width: 300,
        truncate: true,
        cell: (r) => {
          const text =
            r.state === "pending:waiting" && r.request?.reason ? r.request.reason : r.state !== "confirmed" ? r.run?.reason : "";
          return text ? <Note>{text}</Note> : null;
        },
      },
      {
        key: "updated",
        header: "Updated",
        hideBelow: 600,
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
      <QueuePanel />
      <div className="ax-toolbar">
        <SearchBox value={query} onChange={setQuery} label="Search jobs" placeholder="Company, role or location" />
        <Chips
          items={chips}
          value={filter}
          label="Filter by status"
          onChange={(id) => go({ view: "jobs", tab: "pipeline", filter: id === "all" ? undefined : id })}
        />
      </div>
      {filter === "needs" && !!(counts.needs ?? 0) && (
        <Notice
          action={
            <Button size="sm" onClick={() => go({ view: "needs" })}>
              Open Needs you
            </Button>
          }
        >
          {plural(counts.needs ?? 0, "application")} {(counts.needs ?? 0) === 1 ? "is" : "are"} waiting for an answer. Answer the questions on Needs you.
        </Notice>
      )}
      {list.error && list.loaded && <LoadError error={list.error} retry={list.refresh} />}
      <Table
        fill
        label="Application pipeline"
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
        bulk={
          <PipelineBulk
            selection={selection}
            rows={list.rows}
            filter={filter}
            query={q}
            total={list.paging.total}
          />
        }
        empty={
          <Empty
            title={
              q
                ? "No applications match this search"
                : filter === "all"
                  ? "No applications yet"
                  : `Nothing in “${label}” right now`
            }
            hint={
              q ? (
                "Try a company name or clear the search."
              ) : filter === "all" ? (
                "Paste a job link above, start a batch from Overview, or pick jobs in Discovered."
              ) : filter === "needs" ? (
                "No application is waiting for a personal fact."
              ) : undefined
            }
            action={
              filter === "all" && !q ? (
                <Button size="sm" onClick={() => go({ view: "jobs", tab: "discovered" })}>
                  Open Discovered
                </Button>
              ) : undefined
            }
          />
        }
      />
      <Pagination paging={list.paging} label="Pipeline pages" />
    </>
  );
}
