import { useMemo } from "react";
import { Download, Search } from "lucide-react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useDraft, useNow, usePoll } from "./hooks";
import { evidenceLabel } from "./model";
import { Pagination, usePagination } from "./pagination";
import type { Success } from "./types";
import { ago, Empty, ErrorLine, External, Loading, plural, stamp, StatePill } from "./ui";

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
  a.download = "ApplyPilot-successful-applications.csv";
  document.body.append(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 1000);
}

export function LedgerView() {
  const { snapshot, rows, openJob } = useWorkspace();
  const now = useNow(60000);
  const ledger = usePoll<{ applications: Success[] }>(
    (signal) => api("/successful-applications", "GET", undefined, signal),
    60000,
  );
  const [query, setQuery] = useDraft("ledger-query", "");
  const [filter, setFilter] = useDraft("ledger-evidence", "all");
  const all = ledger.data?.applications || [];
  const q = query.trim().toLowerCase();
  const shown = useMemo(
    () =>
      all.filter(
        (r) =>
          matches(r, filter) &&
          (!q || `${r.company} ${r.title} ${r.ats}`.toLowerCase().includes(q)),
      ),
    [all, filter, q],
  );
  const paging = usePagination(shown, filter + "|" + q, 25);
  const rt = snapshot.autopilot_runtime || {};
  const unconfirmed = rows.filter((r) => r.run?.state === "submission_unknown");
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Ledger</h1>
          <p className="page-sub">
            Every application with trustworthy evidence: a job-specific page
            acknowledgement or an authenticated employer receipt.
          </p>
        </div>
      </header>
      <p className="ledger-summary">
        <strong>{ledger.data ? plural(all.length, "confirmed application") : "…"}</strong>{" "}
        across all history · {snapshot.stats.confirmed_today} today
        {rt.state === "active" && (
          <>
            {" "}
            · this batch counts {rt.confirmed ?? 0} of {rt.target ?? 100} separately
          </>
        )}
      </p>
      <div className="toolbar">
        <label className="search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="Search confirmed applications"
            placeholder="Company, role or platform"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <select aria-label="Evidence" value={filter} onChange={(e) => setFilter(e.target.value)}>
          {EVIDENCE.map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
        <button
          className="btn sm toolbar-end"
          disabled={!shown.length}
          onClick={() => csv(shown)}
        >
          <Download size={14} aria-hidden="true" />
          Export CSV{shown.length !== all.length ? ` (${shown.length})` : ""}
        </button>
      </div>
      <ErrorLine error={ledger.error} retry={ledger.refresh} />
      {!ledger.data && !ledger.error ? (
        <Loading label="Loading confirmed applications…" />
      ) : shown.length ? (
        <>
          <div className="table-wrap">
            <table className="table ledger-table">
              <thead>
                <tr>
                  <th scope="col">Company</th>
                  <th scope="col">Role</th>
                  <th scope="col">Applied</th>
                  <th scope="col">Evidence</th>
                  <th scope="col">Posting</th>
                </tr>
              </thead>
              <tbody>
                {paging.items.map((r) => (
                  <tr key={r.job_id}>
                    <td className="cell-company">
                      <button className="link" onClick={() => openJob(r.job_id, r.run_id)}>
                        <strong>{r.company}</strong>
                      </button>
                      <small className="muted">{r.ats}</small>
                    </td>
                    <td className="cell-role">{r.title}</td>
                    <td data-label="Applied">
                      <time dateTime={r.applied_at} title={stamp(r.applied_at)}>
                        {ago(r.applied_at, now)}
                      </time>
                    </td>
                    <td data-label="Evidence">
                      <span className="evidence-tag">{evidenceLabel(r)}</span>
                    </td>
                    <td data-label="Posting">
                      <External href={r.url} className="link">
                        Open
                      </External>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination paging={paging} label="Ledger pages" />
        </>
      ) : (
        <Empty title={all.length ? "No confirmed applications match" : "No confirmed applications yet"}>
          {all.length
            ? "Clear the search or evidence filter."
            : "An application lands here once the employer's page acknowledges it or an authenticated receipt email arrives."}
        </Empty>
      )}
      {unconfirmed.length > 0 && (
        <section className="block unconfirmed" aria-labelledby="unconfirmed-title">
          <header className="block-head">
            <h2 id="unconfirmed-title">Unconfirmed submissions</h2>
            <span className="muted">{plural(unconfirmed.length, "job")} · protected</span>
          </header>
          <p className="block-note">
            Submit was attempted but no acknowledgement or receipt was captured.
            They may have been sent, so they're excluded from the ledger and never
            retried. Open one to check email or record what you verified.
          </p>
          <ul className="feed">
            {unconfirmed.map((r) => (
              <li key={r.job.id}>
                <time dateTime={r.updated}>{ago(r.updated, now)}</time>
                <StatePill state="submission_unknown" />
                <button className="feed-job link" onClick={() => openJob(r.job.id, r.run?.id)}>
                  <strong>{r.job.company}</strong>
                  <span>{r.job.title}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
