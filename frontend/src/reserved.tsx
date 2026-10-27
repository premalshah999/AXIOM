// Jobs › Reserved: FAANG, MANGO and the Big Four apply only with the owner's approval (owner, 2026-10-27: "make a
// different section for FAANG, MANGO, Big four consulting, and these scrapers should not apply before my permission").
// Their jobs are found and ranked as usual and wait here, one section at a time; Approve starts one exactly as Apply
// does, and Approve all lifts the hold on a whole section so the autopilot applies to the ones that fit.
import { useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";
import { api, approveReserved } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { appliedState, rowFromServer, stateInfo, STATES, withMarks } from "./model";
import { Pagination, type Paging } from "./pagination";
import type { ReservedEmployer, ReservedPage, ReservedRules } from "./types";
import { ago, Confirm, Empty, ErrorLine, Field, linkHost, Loading, plural, StatePill } from "./ui";

export function ReservedJobs() {
  const { route, go, version, marks, markJobs, refresh, openJob } = useWorkspace();
  const { pending, run } = useAction();
  const now = useNow(30000);
  const [query, setQuery] = useDraft("reserved-query", "");
  const [q, setQ] = useState(query.trim());
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const section = route.filter || "";
  useEffect(() => {
    const timer = window.setTimeout(() => setQ(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => setPage(1), [q, section, size]);
  const key = new URLSearchParams({ section, q, page: String(page), size: String(size) }).toString();
  const list = usePoll<ReservedPage>(
    (signal) => api(`/reserved?${key}`, "GET", undefined, signal),
    20000,
    [key, version],
  );
  const data = list.data;
  const rows = useMemo(
    () => withMarks((data?.rows || []).map(rowFromServer), marks, list.requestedAt),
    [data, marks, list.requestedAt],
  );

  if (!data)
    return list.error ? (
      <ErrorLine error={list.error} retry={list.refresh} />
    ) : (
      <Loading label="Loading reserved employers…" />
    );

  const current = data.sections.find((s) => s.id === data.section);
  const waiting = data.sections.reduce((n, s) => n + s.held, 0);
  const paging: Paging = {
    items: rows,
    total: data.total,
    page: data.page,
    pages: data.pages,
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

  return (
    <section aria-label="Reserved employers">
      <div className="board-status">
        <p>
          {data.enabled ? (
            <>
              <strong>{plural(waiting, "job")} waiting for your approval</strong>. Nothing is applied at these employers
              until you approve the job.
            </>
          ) : (
            <strong>Reserved employers are off: every employer is applied to as usual.</strong>
          )}
        </p>
      </div>
      {data.enabled && data.sections.length > 0 && (
        <>
          <div className="toolbar">
            <div className="chips" role="group" aria-label="Section">
              {data.sections.map((s) => (
                <button
                  key={s.id}
                  className="chip"
                  aria-pressed={s.id === data.section}
                  onClick={() => go({ view: "jobs", tab: "reserved", filter: s.id })}
                >
                  {s.label} <span className="chip-n">{s.held}</span>
                </button>
              ))}
            </div>
            <label className="search">
              <Search size={15} aria-hidden="true" />
              <input
                type="search"
                aria-label="Search reserved jobs"
                placeholder="Company, role or location"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            {current && current.held > 0 && (
              <div className="toolbar-end">
                <Confirm
                  label={`Approve all ${current.held}`}
                  confirm="Approve all"
                  danger={false}
                  pending={pending === "all"}
                  disabled={pending !== null && pending !== "all"}
                  note={`Lifts the hold on ${plural(current.held, "job")}; the autopilot applies to the ones that fit your profile.`}
                  onConfirm={approveAll}
                />
              </div>
            )}
          </div>
          {current && <p className="apply-why">{current.employers.join(" · ")}</p>}
          <ErrorLine error={list.error} retry={list.refresh} />
          {rows.length ? (
            <>
              <div className="table-wrap">
                <table className="table jobs-table">
                  <thead>
                    <tr>
                      <th scope="col">Job</th>
                      <th scope="col">Status</th>
                      <th scope="col" className="num">
                        Fit
                      </th>
                      <th scope="col">Updated</th>
                      <th scope="col">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.job.id}>
                        <td className="cell-job">
                          <button className="link job-open" onClick={() => openJob(r.job.id, r.run?.id)}>
                            <strong>{r.job.company || linkHost(r.job.url) || r.reserved?.employer}</strong>
                            <span>{r.job.title || r.job.url}</span>
                          </button>
                          <small className="muted">
                            {[r.reserved?.employer, r.job.location, r.job.ats].filter(Boolean).join(" · ")}
                          </small>
                        </td>
                        <td className="cell-state">
                          {/* Every row here waits for approval; what it shows is the job's fit or its last try. */}
                          {r.state !== "reserved" ? (
                            <StatePill state={r.state} />
                          ) : r.run ? (
                            <>
                              <span className="muted">
                                {plural(r.attempts, "try", "tries")} · last: {stateInfo(r.run.state).label}
                              </span>
                              {r.run.reason && (
                                <small className="reason" title={r.run.reason}>
                                  {r.run.reason}
                                </small>
                              )}
                            </>
                          ) : (
                            <StatePill state={"job:" + r.job.status} />
                          )}
                        </td>
                        <td className="num" data-label="Fit">
                          {r.job.score ?? "—"}
                        </td>
                        <td className="cell-time" data-label="Updated">
                          <time dateTime={r.updated} title={new Date(r.updated).toLocaleString()}>
                            {ago(r.updated, now)}
                          </time>
                        </td>
                        <td className="cell-actions">
                          {r.state === "reserved" && (
                            <div className="row-actions">
                              <button
                                className="btn sm primary"
                                disabled={pending !== null}
                                onClick={() => approve(r.job.id)}
                              >
                                {pending === "approve:" + r.job.id ? "Approving…" : "Approve & apply"}
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination paging={paging} label="Reserved pages" />
            </>
          ) : (
            <Empty title={q ? "No reserved jobs match this search" : `No ${current?.label || ""} jobs are waiting`}>
              {q ? "Try a company name or clear the search." : "New postings at these employers appear here as they are found."}
            </Empty>
          )}
        </>
      )}
      <EmployerEditor rules={data.rules} />
    </section>
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
    <details className="fold reserved-editor">
      <summary>
        Reserved employers <span className="muted">who waits for your approval</span>
      </summary>
      <div className="stack">
        <label className="check">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          <span>Hold these employers until I approve each job</span>
        </label>
        {rules.sections.map((s, i) => (
          <Field
            key={s.id}
            label={s.label}
            hint={
              i === 0
                ? "One employer per line: name | other names | career sites. Sites include their subdomains; * stands for one part of a shared platform's address, as in nvidia.*.myworkdayjobs.com."
                : undefined
            }
          >
            <textarea
              className="code"
              rows={Math.min(10, Math.max(3, (texts[i] ?? "").split("\n").length + 1))}
              value={texts[i] ?? ""}
              disabled={!enabled}
              onChange={(e) => setTexts((all) => all.map((t, j) => (j === i ? e.target.value : t)))}
            />
          </Field>
        ))}
        <div className="form-actions start">
          <button className="btn sm primary" disabled={!dirty || pending !== null} onClick={save}>
            {pending === "save" ? "Saving…" : "Save employers"}
          </button>
          {dirty && (
            <button className="btn sm quiet" disabled={pending !== null} onClick={() => setDraft(null)}>
              Discard changes
            </button>
          )}
        </div>
      </div>
    </details>
  );
}
