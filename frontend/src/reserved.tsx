// Jobs › Reserved: FAANG, MANGO and the Big Four apply only with the owner's approval (owner, 2026-10-27: "make a
// different section for FAANG, MANGO, Big four consulting, and these scrapers should not apply before my permission").
// Their jobs are found and ranked as usual and wait here, one section at a time; Approve starts one exactly as Apply
// does, and Approve all lifts the hold on a whole section so the autopilot applies to the ones that fit.
import { useEffect, useMemo, useState } from "react";
import { api, approveReserved } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { appliedState, rowFromServer, stateInfo, STATES, withMarks, type JobRow } from "./model";
import { Pagination, type Paging } from "./pagination";
import type { ReservedEmployer, ReservedPage, ReservedRules } from "./types";
import { ago, linkHost, plural } from "./ui";
import { Button, Drawer, Empty, Table, Textarea, type Column } from "./ui/index";
import { TopActions } from "./shell/Shell";
import { Chips, ConfirmButton, JobCell, Notice, Note, SearchBox, StatusPill } from "./worktools";

export function ReservedJobs() {
  const { route, go, version, marks, markJobs, refresh, openJob } = useWorkspace();
  const { pending, run } = useAction();
  const now = useNow(30000);
  const [query, setQuery] = useDraft("reserved-query", "");
  const [q, setQ] = useState(query.trim());
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [editing, setEditing] = useState(false);
  const section = route.filter || "";
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
      width: 400,
      truncate: true,
      cell: (r) => (
        <JobCell
          company={r.job.company || linkHost(r.job.url) || r.reserved?.employer || "Employer"}
          role={r.job.title || r.job.url}
          onOpen={() => openJob(r.job.id, r.run?.id)}
        />
      ),
    },
    { key: "where", header: "Location", hideBelow: 600, width: 200, truncate: true, cell: (r) => <Note>{r.job.location}</Note> },
    {
      key: "status",
      header: "Status",
      width: 300,
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
          <StatusPill state={"job:" + r.job.status} />
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
      <TopActions>
        <Button size="sm" onClick={() => setEditing(true)} disabled={!data}>
          Edit employers
        </Button>
      </TopActions>
      {data && (
        <p className="ax-lede">
          {data.enabled
            ? `${plural(waiting, "job")} waiting for your approval.${current && current.employers.length > 0 ? ` ${current.label}: ${current.employers.join(", ")}.` : ""}`
            : "Reserved employers are off: every employer is applied to as usual."}
        </p>
      )}
      {data && data.enabled && data.sections.length > 0 && (
        <div className="ax-toolbar">
          <Chips
            label="Section"
            value={data.section}
            items={data.sections.map((s) => ({ id: s.id, label: s.label, count: s.held }))}
            onChange={(id) => go({ view: "jobs", tab: "reserved", filter: id })}
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
      {(!data || data.enabled) && (
        <Table
          fill
          label="Reserved employers"
          columns={columns}
          rows={rows}
          rowKey={(r) => r.job.id}
          onRowClick={(r) => openJob(r.job.id, r.run?.id)}
          loading={!data && !list.error}
          busy={!!data && data.key !== key}
          empty={
            <Empty
              title={q ? "No reserved jobs match this search" : `No ${current?.label || ""} jobs are waiting`}
              hint={q ? "Try a company name or clear the search." : "New postings at these employers appear here as they are found."}
            />
          }
        />
      )}
      {data && data.enabled && <Pagination paging={paging} label="Reserved pages" />}
      {data && (
        <Drawer open={editing} onClose={() => setEditing(false)} title="Reserved employers" width={560}>
          <EmployerEditor rules={data.rules} />
        </Drawer>
      )}
    </>
  );
}

// One employer per line: name | other names | career sites. The server validates and normalizes every field.
const toText = (employers: ReservedEmployer[]) =>
  employers
    .map((e) => [e.name, e.aliases.join(", "), e.sites.join(", ")].join(" | ").replace(/( \| )+$/, ""))
    .join("\n");

const commaList = (text = "") =>
  text
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

const fromText = (text: string): ReservedEmployer[] =>
  text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [name, aliases, sites] = line.split("|").map((x) => x.trim());
      return { name, aliases: commaList(aliases), sites: commaList(sites) };
    });

function EmployerEditor({ rules }: { rules: ReservedRules }) {
  const { refresh } = useWorkspace();
  const { pending, run } = useAction();
  const saved = useMemo(() => rules.sections.map((s) => toText(s.employers)), [rules]);
  // Null while untouched: the editor follows the saved list as polls bring it.
  const [draft, setDraft] = useState<{ texts: string[]; enabled: boolean } | null>(null);
  const texts = draft?.texts ?? saved;
  const enabled = draft?.enabled ?? rules.enabled;
  const dirty =
    draft !== null && (draft.enabled !== rules.enabled || draft.texts.some((t, i) => t !== saved[i]));
  const setTexts = (next: (all: string[]) => string[]) => setDraft({ texts: next(texts), enabled });
  const setEnabled = (value: boolean) => setDraft({ texts, enabled: value });
  const save = () =>
    run(
      "save",
      async () => {
        const body: ReservedRules = {
          enabled,
          sections: rules.sections.map((s, i) => ({ id: s.id, label: s.label, employers: fromText(texts[i] ?? "") })),
        };
        const r = await api<ReservedPage>("/reserved", "PUT", body);
        setDraft(null);
        void refresh();
        return r;
      },
      "Reserved employers saved.",
    );
  return (
    <div className="ax-stack">
      <label className="ax-check">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        <span>Hold these employers until I approve each job</span>
      </label>
      {rules.sections.map((s, i) => (
        <Textarea
          key={s.id}
          label={s.label}
          hint={
            i === 0
              ? "One employer per line: name | other names | career sites. Sites include their subdomains; * stands for one part of a shared platform's address, as in nvidia.*.myworkdayjobs.com."
              : undefined
          }
          className="ax-code"
          rows={Math.min(10, Math.max(3, (texts[i] ?? "").split("\n").length + 1))}
          value={texts[i] ?? ""}
          disabled={!enabled}
          onChange={(e) => setTexts((all) => all.map((t, j) => (j === i ? e.target.value : t)))}
        />
      ))}
      <div className="ax-toolbar">
        <Button size="sm" variant="primary" disabled={!dirty} loading={pending === "save"} onClick={save}>
          Save employers
        </Button>
        {dirty && (
          <Button size="sm" variant="ghost" disabled={pending !== null} onClick={() => setDraft(null)}>
            Discard changes
          </Button>
        )}
      </div>
    </div>
  );
}
