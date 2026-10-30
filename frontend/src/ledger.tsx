import { Download, ExternalLink } from "lucide-react";
import { useMemo } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useDraft, useNow, usePoll } from "./hooks";
import { evidenceLabel } from "./model";
import { usePipelinePage } from "./pipeline";
import { Pagination, usePagination } from "./pagination";
import type { Success } from "./types";
import { ArchiveList } from "./archive";
import { useSelection } from "./bulk";
import { RowActions } from "./jobactions";
import type { JobRow } from "./model";
import { PipelineBulk } from "./pipelinebulk";
import { TopActions } from "./shell/Shell";
import { ago, linkHost, plural, stamp, urlRole } from "./ui";
import { Button, Empty, RouteTabs, Table, type Column } from "./ui/index";
import { JobCell, Notice, Note, SearchBox, StatusPill } from "./worktools";

const EVIDENCE = [
  ["all", "Any evidence"],
  ["page", "Page acknowledgement"],
  ["email", "Employer email"],
  ["existing", "Existing application"],
] as const;

function matches(row: Success, filter: string) {
  if (filter === "page") return row.website_confirmed;
  if (filter === "email") return row.email_confirmed;
  if (filter === "existing") return row.existing_application;
  return true;
}

function csv(rows: Success[]) {
  // Formula-like cells are neutralized so a spreadsheet never executes them.
  const cell = (v: unknown) => {
    let text = String(v ?? "");
    if (/^[\s]*[=+@-]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  };
  const lines = [
    ["Company", "Job title", "Date applied", "Application link", "Platform", "Evidence"],
    ...rows.map((r) => [
      r.company,
      r.title,
      new Date(r.applied_at).toLocaleString(),
      r.url,
      r.ats,
      evidenceLabel(r),
    ]),
  ];
  const href = URL.createObjectURL(
    new Blob(["﻿" + lines.map((l) => l.map(cell).join(",")).join("\r\n")], {
      type: "text/csv;charset=utf-8",
    }),
  );
  const a = document.createElement("a");
  a.href = href;
  a.download = "AXIOM-confirmed-applications.csv";
  document.body.append(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 1000);
}

const TABS = ["confirmed", "unconfirmed", "archive"];

// Applications: what was sent. Confirmed applications are the ledger (trustworthy evidence only); Unconfirmed are
// submissions with no receipt, protected from any retry; Archive keeps earlier failures. Discovered and the pipeline
// are under Jobs.
export function LedgerView({ forceTab }: { forceTab?: string }) {
  const { route, counts } = useWorkspace();
  const tab = forceTab || (TABS.includes(route.tab || "") ? route.tab! : "confirmed");
  const tabs = [
    { id: "confirmed", label: "Confirmed" },
    { id: "unconfirmed", label: "Unconfirmed", count: counts.groups.unconfirmed },
    { id: "archive", label: "Archive" },
  ];
  return (
    <div className="ax-page ax-page-fill">
      <RouteTabs view="ledger" tabs={tabs} label="Applications" current={tab} />
      {tab === "confirmed" && <Confirmed />}
      {tab === "unconfirmed" && <Unconfirmed />}
      {tab === "archive" && <ArchiveList />}
    </div>
  );
}

function Confirmed() {
  const { snapshot, openJob } = useWorkspace();
  const now = useNow(60000);
  const ledger = usePoll<{ applications: Success[] }>(
    (signal) => api("/successful-applications", "GET", undefined, signal),
    60000,
  );
  const [query, setQuery] = useDraft("ledger-query", "");
  const [filter, setFilter] = useDraft("ledger-evidence", "all");
  const all = useMemo(() => ledger.data?.applications || [], [ledger.data]);
  const q = query.trim().toLowerCase();
  const shown = useMemo(
    () =>
      all.filter(
        (r) => matches(r, filter) && (!q || `${r.company} ${r.title} ${r.ats}`.toLowerCase().includes(q)),
      ),
    [all, filter, q],
  );
  const paging = usePagination(shown, filter + "|" + q, 25);
  const rt = snapshot.autopilot_runtime || {};
  const columns: Column<Success>[] = [
    {
      key: "job",
      header: "Job",
      width: 440,
      truncate: true,
      cell: (r) => <JobCell company={r.company} role={r.title} onOpen={() => openJob(r.job_id, r.run_id)} />,
    },
    {
      key: "applied",
      header: "Applied",
      cell: (r) => (
        <time dateTime={r.applied_at} title={stamp(r.applied_at)}>
          {ago(r.applied_at, now)}
        </time>
      ),
    },
    { key: "evidence", header: "Evidence", hideBelow: 600, cell: (r) => <StatusPill state="confirmed" label={evidenceLabel(r)} /> },
    {
      key: "posting",
      header: <span className="sr-only">Posting</span>,
      width: "1%",
      cell: (r) => (
        <a
          className="ax-link"
          href={r.url}
          target="_blank"
          rel="noreferrer noopener"
          onClick={(e) => e.stopPropagation()}
          title={`Open the posting (${linkHost(r.url)})`}
        >
          Posting <ExternalLink size={12} aria-hidden="true" />
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
      ),
    },
  ];
  return (
    <>
      <TopActions>
        <Button size="sm" disabled={!shown.length} icon={<Download size={14} aria-hidden="true" />} onClick={() => csv(shown)}>
          Export CSV{shown.length !== all.length ? ` (${shown.length})` : ""}
        </Button>
      </TopActions>
      <div className="ax-toolbar">
        <SearchBox value={query} onChange={setQuery} label="Search confirmed applications" placeholder="Company, role or platform" />
        <select className="ax-control ax-inline-select" aria-label="Evidence" value={filter} onChange={(e) => setFilter(e.target.value)}>
          {EVIDENCE.map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
        <span className="ax-note ax-toolbar-note">
          {ledger.data ? plural(all.length, "confirmed application") : "…"} across all history · {snapshot.stats.confirmed_today} today
          {rt.state === "active" && ` · ${rt.confirmed ?? 0} of ${rt.target ?? 100} this batch`}.
        </span>
      </div>
      {ledger.error && (
        <Notice
          tone="bad"
          action={
            <Button size="sm" onClick={() => ledger.refresh()}>
              Try again
            </Button>
          }
        >
          {ledger.error.message}
        </Notice>
      )}
      <Table
        fill
        label="Confirmed applications"
        columns={columns}
        rows={paging.items}
        rowKey={(r) => r.job_id}
        onRowClick={(r) => openJob(r.job_id, r.run_id)}
        loading={!ledger.data && !ledger.error}
        empty={
          <Empty
            title={all.length ? "No confirmed applications match" : "No confirmed applications yet"}
            hint={
              all.length
                ? "Clear the search or the evidence filter."
                : "An application lands here once the employer's page acknowledges it or an authenticated receipt email arrives."
            }
          />
        }
      />
      <Pagination paging={paging} label="Confirmed pages" />
    </>
  );
}

// Every unconfirmed submission, from the server (the browser only had the newest 1,000 jobs to look through).
function Unconfirmed() {
  const { openJob, go } = useWorkspace();
  const now = useNow(30000);
  const list = usePipelinePage("unconfirmed", "", 25);
  const selection = useSelection(200);
  const columns: Column<JobRow>[] = [
    {
      key: "job",
      header: "Job",
      width: 440,
      truncate: true,
      cell: (r) => (
        <JobCell
          company={r.job.company || linkHost(r.job.url) || "Employer"}
          role={r.job.title || urlRole(r.job.url)}
          onOpen={() => openJob(r.job.id, r.run?.id)}
        />
      ),
    },
    { key: "status", header: "Status", cell: (r) => <StatusPill state={r.state} /> },
    { key: "detail", header: "Detail", hideBelow: 600, width: 300, truncate: true, cell: (r) => (r.run?.reason ? <Note>{r.run.reason}</Note> : null) },
    {
      key: "updated",
      header: "Updated",
      hideBelow: 600,
      cell: (r) => (
        <time dateTime={r.updated} title={stamp(r.updated)}>
          {ago(r.updated, now)}
        </time>
      ),
    },
    { key: "act", header: <span className="sr-only">Actions</span>, width: "1%", pin: true, cell: (r) => <RowActions row={r} promote="confirm" /> },
  ];
  return (
    <>
      <Notice>No receipt was captured for these. They may have been sent, so they are not in Confirmed and are never retried.</Notice>
      {list.error && (
        <Notice
          tone="bad"
          action={
            <Button size="sm" onClick={() => list.refresh()}>
              Try again
            </Button>
          }
        >
          {list.error.message}
        </Notice>
      )}
      <Table
        fill
        label="Unconfirmed submissions"
        columns={columns}
        rows={list.rows}
        rowKey={(r) => r.job.id}
        onRowClick={(r) => openJob(r.job.id, r.run?.id)}
        selected={selection.chosen}
        onSelectedChange={selection.replace}
        rowLabel={(r) => r.job.company || "job"}
        loading={!list.loaded}
        busy={list.loaded && !list.current}
        bulk={<PipelineBulk selection={selection} rows={list.rows} filter="unconfirmed" query="" total={list.paging.total} />}
        empty={
          <Empty
            title="No unconfirmed submissions"
            hint="Every submission so far has a receipt or acknowledgement."
            action={
              <Button size="sm" onClick={() => go({ view: "ledger" })}>
                See confirmed applications
              </Button>
            }
          />
        }
      />
      <Pagination paging={list.paging} label="Unconfirmed pages" />
    </>
  );
}
