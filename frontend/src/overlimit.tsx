// Needs you › Over employer limit: jobs the autopilot holds only because their employer already has as many applications as the user's
// limit (Settings). The limit stays; the jobs wait here for the user, as a reserved employer's do (owner, 2026-11-01: "per employer
// limit stays as 25, or add them to a new section, like reserved, so I can approve"). Approve is the user's own Apply: it skips the
// cap and no other guard. The entry shows only while something is in it, with the same number as the rows it opens.
import { useEffect, useMemo, useState } from "react";
import { SearchX, ShieldCheck } from "lucide-react";
import { api, approveOverLimit } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, usePoll } from "./hooks";
import { Pagination, type Paging } from "./pagination";
import type { OverLimitPage, OverLimitRow } from "./types";
import { jobWords, plural } from "./format";
import { Button, Empty, Table, type Column } from "./ui/index";
import { ConfirmButton, JobCell, LoadError, Note, SearchBox } from "./worktools";

type AllResult = { approved: number; remaining: number; summary: Record<string, number>; results: { message?: string; outcome: string }[] };

export function OverLimitJobs() {
  const { version, refresh, openJob } = useWorkspace();
  const { pending, run } = useAction();
  const [query, setQuery] = useDraft("over-limit-query", "");
  const [q, setQ] = useState(query.trim());
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => setPage(1), [q, size]);
  const key = new URLSearchParams({ q, page: String(page), size: String(size) }).toString();
  const list = usePoll<OverLimitPage & { key: string }>(
    async (signal) => ({ ...(await api<OverLimitPage>(`/over-limit?${key}`, "GET", undefined, signal)), key }),
    20000,
    [key, version],
  );
  const data = list.data;
  const rows = data?.rows ?? [];
  const limit = rows[0]?.hold.limit;
  const paging: Paging = {
    items: rows,
    total: data?.total ?? 0,
    page: data?.page ?? page,
    pages: data?.pages ?? 1,
    size,
    initialSize: 25,
    setPage,
    setSize,
  };
  const again = async () => {
    await refresh(); // the tab's number and the rows come from one reading, read again
    list.refresh();
  };
  const approve = (job: OverLimitRow) =>
    run("approve:" + job.id, async () => {
      const r = await approveOverLimit(job.id);
      await again();
      return r;
    }, (r) => r?.message);
  const approveAll = () =>
    run("all", async () => {
      const r = await api<AllResult>("/over-limit/approve-all", "POST");
      await again();
      return r;
    }, (r) => {
      if (!r) return undefined;
      const refused = r.summary.refused ?? 0;
      return `${plural(r.approved, "application")} started or queued.${refused ? ` ${plural(refused, "job")} refused: each stays here with the reason.` : ""}${r.remaining ? ` ${r.remaining} more are still here.` : ""}`;
    });

  const columns: Column<OverLimitRow>[] = useMemo(
    () => [
      {
        key: "job",
        header: "Job",
        width: 460,
        truncate: true,
        cell: (r) => (
          <JobCell
            {...jobWords(r)}
            onOpen={() => openJob(r.id)}
            meta={
              <>
                {r.score != null && <span className="mono">Match {r.score}</span>}
                <span>{`Has ${plural(r.hold.count, "application")}; limit ${r.hold.limit}`}</span>
              </>
            }
          />
        ),
      },
      { key: "fit", header: "Match", hideBelow: 600, num: true, cell: (r) => r.score ?? "—" },
      { key: "why", header: "Employer's applications", hideBelow: 600, truncate: true, width: 340, cell: (r) => <Note title={r.hold.reason}>{`Already has ${plural(r.hold.count, "application")}; your limit is ${r.hold.limit}.`}</Note> },
      {
        key: "act",
        header: <span className="sr-only">Actions</span>,
        width: "1%",
        pin: true,
        cell: (r) => (
          <div className="ax-act" onClick={(e) => e.stopPropagation()}>
            <Button size="sm" className="ax-lead" disabled={pending !== null} loading={pending === "approve:" + r.id} onClick={() => approve(r)}>
              Approve
            </Button>
          </div>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pending, openJob],
  );

  const total = data?.count ?? 0;
  return (
    <>
      <header className="nd-card-head">
        <h2 className="nd-q">Over your employer limit</h2>
        <p className="nd-meta">
          {total > 0
            ? `${plural(total, "job")} at employers that have reached your limit${limit != null ? ` of ${limit} applications` : ""}. AXIOM will not apply to these by itself. Approve puts one in line now; the limit stays as it is.`
            : "Jobs whose employer has reached your limit wait here for your approval."}
        </p>
      </header>
      <div className="ax-toolbar">
        <SearchBox value={query} onChange={setQuery} label="Search jobs over the employer limit" placeholder="Company or role" />
        {total > 0 && (
          <div className="ax-toolbar-end">
            <ConfirmButton
              label={`Approve all ${total}`}
              question={`Apply to ${plural(Math.min(total, 50), "job")} now, past the employer limit? These are real applications.${total > 50 ? " The rest stay here." : ""}`}
              confirmLabel="Apply to all"
              danger={false}
              loading={pending === "all"}
              disabled={pending !== null && pending !== "all"}
              onConfirm={approveAll}
            />
          </div>
        )}
      </div>
      {list.error && data && <LoadError error={list.error} retry={list.refresh} />}
      <Table
        label="Jobs over the employer limit"
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        onRowClick={(r) => openJob(r.id)}
        rowLabel={(r) => r.company || "job"}
        loading={!data && !list.error}
        error={list.error && !data ? <LoadError error={list.error} retry={list.refresh} /> : undefined}
        busy={!!data && data.key !== key}
        footer={data && paging.total > 0 ? <Pagination paging={paging} label="Over employer limit pages" /> : undefined}
        empty={
          <Empty
            icon={q ? SearchX : ShieldCheck}
            title={q ? "No jobs match this search" : "Nothing is over the employer limit"}
            hint={q ? "Try a company name, or clear the search." : "A job appears here when its employer already has as many applications as your limit."}
          />
        }
      />
    </>
  );
}
