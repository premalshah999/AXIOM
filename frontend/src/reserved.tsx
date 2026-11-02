// Needs you › Reserved: the companies and job boards the user reserves (and, if they switch it on, FAANG, MANGO and the Big Four)
// apply only with their approval (owner, 2026-10-27: "these scrapers should not apply before my permission"; 2026-11-01: "users
// could actually reserve job boards, companies they like").
// Their jobs are found and ranked as usual and wait here, one section at a time; Approve starts one exactly as Apply
// does, and Approve all lifts the hold on a whole section so the autopilot applies to the ones that fit.
import { useEffect, useMemo, useState } from "react";
import { api, approveReserved } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { appliedState, rowFromServer, stateInfo, STATES, withMarks, type JobRow } from "./model";
import { Pagination, type Paging } from "./pagination";
import type { ReservedEmployer, ReservedPage, ReservedRules } from "./types";
import { ago, jobWords, plural } from "./format";
import { ShieldCheck, SearchX } from "lucide-react";
import { Button, Check, Drawer, Empty, Input, Table, type Column } from "./ui/index";
import { Chips, ConfirmButton, JobCell, LoadError, Note, SearchBox, StatusPill } from "./worktools";

export function ReservedJobs() {
  const { route, version, marks, markJobs, refresh, openJob } = useWorkspace();
  const { pending, run } = useAction();
  const now = useNow(30000);
  const [query, setQuery] = useDraft("reserved-query", "");
  const [q, setQ] = useState(query.trim());
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [editing, setEditing] = useState(false);
  const [picked, setPicked] = useState(route.filter && route.filter !== "reserved" && route.filter !== "over" ? route.filter : "");
  const section = picked;
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => setPage(1), [q, section, size]);
  const key = new URLSearchParams({ section, q, page: String(page), size: String(size) }).toString();
  const list = usePoll<ReservedPage & { key: string }>(
    async (signal) => ({ ...(await api<ReservedPage>(`/reserved?${key}`, "GET", undefined, signal)), key }),
    20000,
    [key, version],
  );
  const data = list.data;
  const rows = useMemo(
    () => withMarks((data?.rows || []).map(rowFromServer), marks, list.requestedAt),
    [data, marks, list.requestedAt],
  );
  const current = data?.sections.find((s) => s.id === data.section);
  const waiting = data?.sections.reduce((n, s) => n + s.held, 0) ?? 0;
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
  const approve = (jobId: string) =>
    run(
      "approve:" + jobId,
      async () => {
        const r = await approveReserved(jobId);
        const shown = appliedState(r.state);
        if (shown in STATES) markJobs([jobId], shown);
        void refresh();
        return r;
      },
      (r) => r?.message,
    );
  const approveAll = () =>
    current &&
    run(
      "all",
      async () => {
        const r = await api<{ approved: number }>("/reserved/approve-all", "POST", { section: current.id });
        void refresh();
        return r;
      },
      (r) => r && `${plural(r.approved, "job")} approved at ${current.label}. The autopilot applies to the ones that fit.`,
    );

  const columns: Column<JobRow>[] = [
    {
      key: "job",
      header: "Job",
      width: 520,
      truncate: true,
      cell: (r) => (
        <JobCell
          {...jobWords({ ...r.job, company: r.job.company || r.reserved?.employer })}
          onOpen={() => openJob(r.job.id, r.run?.id)}
          meta={r.state !== "reserved" ? <StatusPill state={r.state} /> : r.job.score != null ? <span className="mono">Fit {r.job.score}</span> : undefined}
        />
      ),
    },
    { key: "where", header: "Location", hideBelow: 860, width: 170, truncate: true, cell: (r) => <Note>{r.job.location}</Note> },
    {
      key: "status",
      header: "Status",
      hideBelow: 600,
      width: 240,
      truncate: true,
      cell: (r) =>
        // Every row here waits for approval; what it shows is the job's fit or its last try.
        r.state !== "reserved" ? (
          <StatusPill state={r.state} />
        ) : r.run ? (
          <Note title={r.run.reason}>
            {plural(r.attempts, "try", "tries")} · last: {stateInfo(r.run.state).label}
            {r.run.reason ? ` · ${r.run.reason}` : ""}
          </Note>
        ) : (
          // Never its job status: Ready reads "In queue" elsewhere, and this row is the one thing that is not in line.
          <StatusPill state="reserved" label="Needs your approval" />
        ),
    },
    { key: "fit", header: "Fit", hideBelow: 600, num: true, cell: (r) => r.job.score ?? "—" },
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
    {
      key: "act",
      header: <span className="sr-only">Actions</span>,
      width: "1%",
      pin: true,
      cell: (r) =>
        r.state === "reserved" ? (
          <Button
            size="sm"
            className="ax-lead"
            disabled={pending !== null}
            loading={pending === "approve:" + r.job.id}
            onClick={() => approve(r.job.id)}
          >
            Approve and apply
          </Button>
        ) : null,
    },
  ];

  return (
    <>
      <header className="nd-card-head">
        <h2 className="nd-q">Reserved</h2>
        {data && (
          <p className="nd-meta">
            Companies and job boards you reserve. AXIOM finds and ranks them, and applies only after you approve each one.{" "}
            {data.enabled
              ? `${plural(waiting, "job")} waiting.${current && current.employers.length > 0 ? ` ${current.label}: ${current.employers.slice(0, 6).join(", ")}${current.employers.length > 6 ? ` and ${current.employers.length - 6} more` : ""}.` : ""}`
              : "Nothing is reserved yet."}{" "}
            <button type="button" className="ax-link" disabled={!data} onClick={() => setEditing(true)}>
              Manage reserved
            </button>
          </p>
        )}
      </header>
      {data && data.enabled && data.sections.length > 0 && (
        <div className="ax-toolbar">
          <Chips
            label="Section"
            value={data.section}
            items={data.sections.map((s) => ({ id: s.id, label: s.label, count: s.held }))}
            onChange={setPicked}
          />
          <SearchBox value={query} onChange={setQuery} label="Search reserved jobs" placeholder="Company, role or location" />
          {current && current.held > 0 && (
            <div className="ax-toolbar-end">
              <ConfirmButton
                label={`Approve all ${current.held}`}
                question={`Lift the hold on ${plural(current.held, "job")}? The autopilot applies to the ones that fit.`}
                confirmLabel="Approve all"
                danger={false}
                loading={pending === "all"}
                disabled={pending !== null && pending !== "all"}
                onConfirm={approveAll}
              />
            </div>
          )}
        </div>
      )}
      {list.error && data && <LoadError error={list.error} retry={list.refresh} />}
      {(!data || data.enabled) && (
        <Table
          label="Reserved"
          columns={columns}
          rows={rows}
          rowKey={(r) => r.job.id}
          onRowClick={(r) => openJob(r.job.id, r.run?.id)}
          loading={!data && !list.error}
          error={list.error && !data ? <LoadError error={list.error} retry={list.refresh} /> : undefined}
          busy={!!data && data.key !== key}
          footer={data && data.enabled && paging.total > 0 ? <Pagination paging={paging} label="Reserved pages" /> : undefined}
          empty={
            <Empty
              icon={q ? SearchX : ShieldCheck}
              title={q ? "No reserved jobs match this search" : `No ${current?.label || ""} jobs are waiting`}
              hint={q ? "Try a company name or clear the search." : "New postings at these employers appear here as they are found."}
            />
          }
        />
      )}
      {data && (
        <Drawer open={editing} onClose={() => setEditing(false)} title="Reserved" width={560}>
          <Manager rules={data.rules} onSaved={(rules) => list.setData((was) => was && { ...was, rules })} />
        </Drawer>
      )}
    </>
  );
}

// The user's own list: add a company by name or a job board by its link, take any off (the suggested FAANG, MANGO and Big Four
// included). The server keeps and validates the list; each press is one request.
function Manager({ rules, onSaved }: { rules: ReservedRules; onSaved: (rules: ReservedRules) => void }) {
  const { refresh } = useWorkspace();
  const { pending, run } = useAction();
  const [text, setText] = useDraft("reserve-text", "");
  const add = () =>
    run(
      "add",
      async () => {
        const r = await api<{ reserved: string; rules: ReservedRules }>("/reserved/employers", "POST", { text: text.trim() });
        setText("");
        onSaved(r.rules);
        void refresh();
        return r;
      },
      (r) => r && `${r.reserved} is reserved. AXIOM applies there only after you approve each job.`,
    );
  const remove = (name: string) =>
    run(
      "rm:" + name,
      async () => {
        const r = await api<{ rules: ReservedRules }>("/reserved/employers?name=" + encodeURIComponent(name), "DELETE");
        onSaved(r.rules);
        void refresh();
      },
      `${name} is no longer reserved.`,
    );
  const suggested = (on: boolean) =>
    run(
      "suggested",
      async () => {
        const r = await api<{ rules: ReservedRules }>("/reserved", "PUT", { ...rules, enabled: on });
        onSaved(r.rules);
        void refresh();
      },
      on ? "FAANG, MANGO and the Big Four are held until you approve." : "FAANG, MANGO and the Big Four apply like any other employer.",
    );
  const mine = rules.sections.find((x) => x.id === "mine");
  const others = rules.sections.filter((x) => x.id !== "mine");
  const line = (e: ReservedEmployer, off = false) => (
    <li key={e.name} className={"rs-item" + (off ? " is-off" : "")}>
      <span className="rs-name">
        {e.name}
        {e.sites.length > 0 && <small>{e.sites.slice(0, 2).join(", ")}</small>}
      </span>
      <Button size="sm" variant="ghost" disabled={pending !== null} loading={pending === "rm:" + e.name} onClick={() => remove(e.name)}>
        Remove
        <span className="sr-only"> {e.name}</span>
      </Button>
    </li>
  );
  return (
    <div className="ax-stack">
      <p className="nd-meta">
        Reserving a company or a job board keeps AXIOM finding and ranking its jobs, but it applies only after you approve each one.
      </p>
      <form
        className="rs-add"
        onSubmit={(e) => {
          e.preventDefault();
          if (text.trim()) void add();
        }}
      >
        <Input label="Company name or job board link" value={text} onChange={(e) => setText(e.target.value)} placeholder="Example Inc, or boards.greenhouse.io/example" />
        <Button variant="primary" type="submit" disabled={!text.trim() || pending !== null} loading={pending === "add"}>
          Reserve
        </Button>
      </form>
      <section>
        <h3 className="rs-head">Yours</h3>
        {mine && mine.employers.length > 0 ? (
          <ul className="rs-list">{mine.employers.map((e) => line(e))}</ul>
        ) : (
          <p className="nd-meta">Nothing yet. Add one above, or press Reserve on a job in the Job board.</p>
        )}
      </section>
      <section>
        <Check checked={rules.enabled} disabled={pending !== null} onChange={suggested} label="Also hold FAANG, MANGO and the Big Four" />
        {others.map((sec) => (
          <div key={sec.id}>
            <h3 className="rs-head">{sec.label}</h3>
            {sec.employers.length > 0 ? (
              <ul className="rs-list">{sec.employers.map((e) => line(e, !rules.enabled))}</ul>
            ) : (
              <p className="nd-meta">None left.</p>
            )}
          </div>
        ))}
      </section>
    </div>
  );
}
