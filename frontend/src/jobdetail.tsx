import { lazy, Suspense, useMemo, useState } from "react";
import { Copy, X } from "lucide-react";
import { api, applyJob, PREPARING, queueJob } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, usePoll } from "./hooks";
import { JobControls } from "./jobs";
import {
  ACTIVE,
  appliedState,
  displayState,
  rowFromServer,
  stateInfo,
  withMarks,
  type JobRow,
} from "./model";
import { groupQuestions, QuestionCard } from "./questions";
import type { Detail, Job, JobRequest, Run } from "./types";
import {
  Dialog,
  ErrorLine,
  External,
  Field,
  KeyValues,
  linkHost,
  Loading,
  plural,
  seconds,
  shortId,
  stamp,
  StatePill,
  words,
} from "./ui";

// The tailored-resume section lives with Setup's resume features; it loads with the panel's first open.
const JobResume = lazy(() => import("./features").then((m) => ({ default: m.JobResume })));

export function JobPanel({
  jobId,
  runId,
  close,
}: {
  jobId: string;
  runId?: string;
  close: () => void;
}) {
  const { snapshot, rows, version, marks } = useWorkspace();
  const workspaceRow = rows.find((r) => r.job.id === jobId);
  // A job that is not live (the snapshot carries only live rows) is read on its own, again after every action.
  const historical = usePoll<{
    job: Job;
    latest_run: Omit<Run, "receipt"> | null;
    job_control: { state?: string };
    request?: JobRequest | null;
  }>(
    (signal) => api(`/jobs/${jobId}`, "GET", undefined, signal),
    60000,
    [jobId, version],
    !workspaceRow,
  );
  // Every attempt of this job from the server (the snapshot no longer carries run history).
  const attempts = usePoll<{ runs: Run[] }>(
    (signal) => api(`/runs?job_id=${encodeURIComponent(jobId)}`, "GET", undefined, signal),
    60000,
    [jobId, version],
  );
  const historicalRow = useMemo(
    () =>
      historical.data
        ? withMarks(
            [
              rowFromServer({
                job: historical.data.job,
                run: historical.data.latest_run ? { ...historical.data.latest_run, receipt: {} } : null,
                attempts: attempts.data?.runs.length ?? (historical.data.latest_run ? 1 : 0),
                control: historical.data.job_control,
                // A waiting Apply reads as "Waiting to start", not "Eligible" with another Apply button.
                request: historical.data.request,
                reviews: [],
              }),
            ],
            marks,
            historical.requestedAt,
          )[0]
        : undefined,
    [historical.data, historical.requestedAt, attempts.data, marks],
  );
  const row = workspaceRow || historicalRow;
  const runs = useMemo(
    () =>
      attempts.data?.runs ||
      snapshot.runs
        .filter((r) => r.job_id === jobId)
        .sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [attempts.data, snapshot.runs, jobId],
  );
  const selectedRun = runId || row?.run?.id || runs[0]?.id;
  const job = row?.job;
  return (
    <Dialog open onClose={close} variant="drawer" labelledBy="job-title">
      <div className="panel-top">
        <div className="panel-title">
          <p className="panel-company">
            {job?.company || linkHost(job?.url) || "Job"}
          </p>
          <h2 id="job-title">
            {job?.title || (job ? "Saved job link" : "Application record")}
          </h2>
        </div>
        <button
          className="icon-btn"
          aria-label="Close job details"
          onClick={close}
        >
          <X size={18} />
        </button>
      </div>
      {job ? (
        <JobSummary row={row!} />
      ) : (
        <p className="panel-note">
          This job isn't in the current workspace list (it may be archived or
          older history). Its recorded attempt is shown below.
        </p>
      )}
      {!workspaceRow && historical.error && (
        <ErrorLine error={historical.error} />
      )}
      {selectedRun ? (
        <RunSection
          key={selectedRun}
          runId={selectedRun}
          row={row}
          runs={runs}
          latest={!runId || runId === row?.run?.id}
        />
      ) : (
        row && <NotStarted row={row} />
      )}
      {job && (
        <Suspense fallback={null}>
          <JobResume jobId={job.id} eligible={job.status === "ready"} />
        </Suspense>
      )}
    </Dialog>
  );
}

function JobSummary({ row }: { row: JobRow }) {
  const { job } = row;
  const notify = useAction();
  return (
    <div className="panel-meta">
      <External href={job.url}>Open posting</External>
      <span>{job.location || "Location not listed"}</span>
      <span>{job.ats}</span>
      {job.score != null && <span>Fit {job.score}/100</span>}
      <button
        className="link mono"
        title="Copy job ID"
        onClick={() =>
          notify.run(
            "copy",
            () => navigator.clipboard.writeText(job.id),
            "Job ID copied.",
          )
        }
      >
        {shortId(job.id)} <Copy size={12} aria-hidden="true" />
        <span className="sr-only">Copy job ID</span>
      </button>
    </div>
  );
}

function RunSection({
  runId,
  row,
  runs,
  latest,
}: {
  runId: string;
  row?: JobRow;
  runs: Run[];
  latest: boolean;
}) {
  const { snapshot, openJob } = useWorkspace();
  const snapRun = snapshot.runs.find((r) => r.id === runId);
  const live =
    !!snapRun &&
    (ACTIVE.has(snapRun.state) ||
      snapRun.state === "waiting_browser" ||
      snapRun.state === "waiting_email");
  const detail = usePoll<Detail>(
    (signal) => api(`/runs/${runId}`, "GET", undefined, signal),
    live ? 10000 : 3_600_000,
    [runId, live],
  );
  const run = detail.data?.run || snapRun;
  const state =
    latest && row
      ? row.state
      : run
        ? displayState(row?.job || ({} as never), run, undefined)
        : "";
  const info = stateInfo(state);
  const questions = row && latest ? row.reviews : [];
  const jobs = new Map(snapshot.jobs.map((j) => [j.id, j]));
  return (
    <>
      <section className="panel-status" aria-label="Status">
        <div className="panel-status-head">
          <StatePill state={state} />
          {run && !latest && (
            <span className="muted sm">
              Earlier attempt · {stamp(run.created_at)}
            </span>
          )}
        </div>
        {info.explain && <p>{info.explain}</p>}
        {run?.reason && state !== "confirmed" && (
          <p className="panel-reason">{run.reason}</p>
        )}
        {row && latest && <JobControls row={row} detailed />}
      </section>

      {questions.length > 0 && (
        <section className="panel-section" aria-label="Question">
          {groupQuestions(questions, (id) =>
            jobs.get(snapshot.runs.find((r) => r.id === id)?.job_id || ""),
          ).map((g) => (
            <QuestionCard key={g.review.id} group={g} compact />
          ))}
        </section>
      )}

      {run &&
        (run.state === "submission_unknown" || run.state === "confirmed") && (
          <EvidenceActions run={run} />
        )}

      <ErrorLine error={detail.error} retry={detail.refresh} />
      {!detail.data && !detail.error && (
        <Loading label="Loading attempt details…" />
      )}
      {detail.data && <DetailBody detail={detail.data} />}

      {runs.length > 1 && (
        <section className="panel-section" aria-labelledby="attempts-title">
          <h3 id="attempts-title">Attempts ({runs.length})</h3>
          <p className="muted sm">
            Retries of one job count as one application; only a confirmed
            attempt counts toward the batch.
          </p>
          <ol className="attempts">
            {runs.map((r, i) => (
              <li key={r.id} aria-current={r.id === runId || undefined}>
                <button
                  className="link"
                  onClick={() => openJob(r.job_id, i === 0 ? undefined : r.id)}
                >
                  {stamp(r.created_at)}
                </button>
                <StatePill state={r.state} />
                <span className="muted sm">
                  {words(r.mode)} · {r.model_calls} AI steps
                </span>
              </li>
            ))}
          </ol>
        </section>
      )}
    </>
  );
}

function EvidenceActions({ run }: { run: Run }) {
  const { refresh } = useWorkspace();
  const { pending, run: act } = useAction();
  const [submitted, setSubmitted] = useState("true");
  const [evidence, setEvidence] = useDraft("reconcile:" + run.id, "");
  const [open, setOpen] = useState(false);
  const email = run.receipt?.email as { sender_trusted?: boolean } | undefined;
  const hasEmail = !!email && email.sender_trusted !== false;
  return (
    <section className="panel-section" aria-label="Submission evidence">
      {!hasEmail && (
        <div className="row-actions">
          <button
            className="btn sm"
            disabled={pending !== null}
            onClick={() =>
              act(
                "email",
                async () => {
                  const r = await api<{ emails_confirmed?: number }>(
                    `/runs/${run.id}/check-email`,
                    "POST",
                  );
                  await refresh();
                  return r;
                },
                (r) =>
                  r?.emails_confirmed
                    ? `Found ${plural(r.emails_confirmed, "employer receipt")}.`
                    : "No trusted receipt email found yet. Mail is checked again automatically.",
              )
            }
          >
            {pending === "email"
              ? "Checking email…"
              : "Check email for a receipt"}
          </button>
        </div>
      )}
      {run.state === "submission_unknown" && (
        <>
          <button
            className="link sm"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            {open ? "Hide" : "Record a verified outcome"}
          </button>
          {open && (
            <form
              className="reconcile"
              onSubmit={(e) => {
                e.preventDefault();
                act(
                  "reconcile",
                  async () => {
                    await api(`/runs/${run.id}/reconcile`, "POST", {
                      submitted: submitted === "true",
                      evidence: evidence.trim(),
                    });
                    setEvidence("");
                    await refresh();
                  },
                  "Verified outcome recorded.",
                );
              }}
            >
              <p className="muted sm">
                Record only what you verified in the employer's portal or a
                confirmation email. This records a fact; it never retries the
                application.
              </p>
              <Field label="What you verified">
                <select
                  value={submitted}
                  onChange={(e) => setSubmitted(e.target.value)}
                >
                  <option value="true">The application was submitted</option>
                  <option value="false">
                    The application was not submitted
                  </option>
                </select>
              </Field>
              <Field
                label="Evidence"
                hint="For example: “Portal shows Application received on Oct 5.”"
              >
                <textarea
                  required
                  minLength={5}
                  maxLength={4000}
                  rows={3}
                  value={evidence}
                  onChange={(e) => setEvidence(e.target.value)}
                />
              </Field>
              <button
                className="btn primary sm"
                disabled={pending !== null || evidence.trim().length < 5}
              >
                {pending === "reconcile" ? "Saving…" : "Save verified outcome"}
              </button>
            </form>
          )}
        </>
      )}
    </section>
  );
}

function receiptLines(receipt: Record<string, unknown>) {
  const lines: string[] = [];
  const email = receipt.email as { sender_trusted?: boolean } | undefined;
  if (receipt.website_confirmed)
    lines.push("Job-specific page acknowledgement captured");
  if (
    receipt.email_confirmed ||
    (email && Object.keys(email).length && email.sender_trusted !== false)
  )
    lines.push("Authenticated employer receipt email");
  if (receipt.existing_application_notice)
    lines.push("Employer reported an existing application");
  if (receipt.evidence_reconciliation === "pending")
    lines.push(
      "Evidence reconciliation pending: excluded from success counts until resolved",
    );
  return lines;
}

function DetailBody({ detail }: { detail: Detail }) {
  const { run, failure_report: report } = detail;
  const [shot, setShot] = useState(false);
  const answers = Object.values(detail.answers || {});
  const verified = answers.filter((a) => a.verified).length;
  const receipt = run.receipt || {};
  const lines = receiptLines(receipt);
  return (
    <>
      {report && (
        <section
          className="panel-section report"
          aria-labelledby="report-title"
        >
          <h3 id="report-title">What stopped this attempt</h3>
          <p>{report.summary}</p>
          {report.likely_cause && (
            <p className="muted">{report.likely_cause}</p>
          )}
          {report.next_steps?.length > 0 && (
            <ul>
              {report.next_steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
          )}
          <details>
            <summary>Diagnostic evidence</summary>
            <pre>{JSON.stringify(report.evidence, null, 2)}</pre>
          </details>
          <small className="muted">
            {report.source === "ai"
              ? "Summarized from recorded evidence"
              : "Recorded evidence"}
          </small>
        </section>
      )}
      <section className="panel-section" aria-labelledby="attempt-title">
        <h3 id="attempt-title">This attempt</h3>
        <KeyValues
          items={[
            [
              "Mode",
              run.mode === "dry_run" ? "Dry run (never submits)" : "Submit",
            ],
            ["Started", stamp(run.started_at || run.created_at)],
            [
              "Finished",
              run.finished_at ? stamp(run.finished_at) : "In progress",
            ],
            ["Time", run.elapsed ? seconds(run.elapsed) : "—"],
            ["AI usage", `${run.model_calls} steps · $${run.cost.toFixed(3)}`],
            ["Attempt ID", <span className="mono">{shortId(run.id)}</span>],
          ]}
        />
      </section>
      {(lines.length > 0 || Object.keys(receipt).length > 0) && (
        <section className="panel-section" aria-labelledby="evidence-title">
          <h3 id="evidence-title">Evidence</h3>
          {lines.length ? (
            <ul className="evidence">
              {lines.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          ) : (
            <p className="muted sm">No acknowledgement or receipt recorded.</p>
          )}
          <details>
            <summary>Raw receipt</summary>
            <pre>{JSON.stringify(receipt, null, 2)}</pre>
          </details>
        </section>
      )}
      {detail.screenshot && (
        <section className="panel-section" aria-labelledby="shot-title">
          <h3 id="shot-title">Final page</h3>
          {shot ? (
            <a
              className="shot"
              href={`/api/runs/${run.id}/screenshot`}
              target="_blank"
              rel="noreferrer"
            >
              <img
                src={`/api/runs/${run.id}/screenshot`}
                alt="The last application page ApplyPilot saw"
              />
            </a>
          ) : (
            <button className="btn sm" onClick={() => setShot(true)}>
              Show screenshot
            </button>
          )}
        </section>
      )}
      <details className="panel-section fold">
        <summary>
          Answers{" "}
          <span className="muted">
            {answers.length
              ? `${answers.length} · ${verified} verified`
              : "none yet"}
          </span>
        </summary>
        {answers.length ? (
          <table className="table compact">
            <thead>
              <tr>
                <th scope="col">Field</th>
                <th scope="col">Answer</th>
                <th scope="col">Check</th>
              </tr>
            </thead>
            <tbody>
              {answers.map((a, i) => (
                <tr key={i}>
                  <td>{a.label}</td>
                  <td className="wrap">{a.answer}</td>
                  <td>
                    <span className={a.verified ? "ok-text" : "muted"}>
                      {a.verified ? "Verified" : "Unverified"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted sm">
            Answers are recorded when ApplyPilot finishes filling the form.
          </p>
        )}
      </details>
      <details className="panel-section fold">
        <summary>
          Activity{" "}
          <span className="muted">{plural(detail.events.length, "event")}</span>
        </summary>
        {detail.events.length ? (
          <ol className="timeline">
            {detail.events.map((e) => (
              <li key={e.id}>
                <time dateTime={e.created_at}>{stamp(e.created_at)}</time>
                <span className="timeline-kind">{words(e.kind)}</span>
                <p>{e.message}</p>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted sm">No events recorded yet.</p>
        )}
      </details>
    </>
  );
}

function NotStarted({ row }: { row: JobRow }) {
  const { snapshot, refresh, markJobs } = useWorkspace();
  const { pending, run } = useAction();
  const job = row.job;
  const info = stateInfo(row.state);
  const ready = job.status === "ready";
  // One click applies (owner, 2026-10-18): no reason to type and no separate ranking step. The server approves the
  // job and starts it, or keeps the request until a worker, pacing or a shared email sender allows it.
  const applyNow = () =>
    run(
      "submit",
      async () => {
        const r = await applyJob(job.id);
        // Reads as queued (or waiting) at once; the lists confirm with their next read.
        markJobs([job.id], appliedState(r.state));
        void refresh();
        return r;
      },
      (r) => r?.message,
    );
  const dryRun = () =>
    run(
      "dry_run",
      async () => {
        const r = await queueJob(`/jobs/${job.id}/queue`, {
          mode: "dry_run",
          resume_id: job.resume_id || undefined,
        });
        await refresh();
        return r;
      },
      (r) => (r?.preparing ? PREPARING : r?.message || "Dry run queued. It stops before submitting."),
    );
  const rank = () =>
    run(
      "rank",
      async () => {
        const r = await api<{ score: number; eligible: boolean }>(`/jobs/${job.id}/rank`, "POST");
        await refresh();
        return r;
      },
      (r) => r && `Ranked ${r.score}/100 · ${r.eligible ? "eligible" : "below your minimum; Apply still works"}.`,
    );
  return (
    <>
      <section className="panel-status" aria-label="Status">
        <div className="panel-status-head">
          <StatePill state={row.state} />
        </div>
        {info.explain && <p>{info.explain}</p>}
        {row.state === "pending:waiting" && row.request?.reason ? (
          <p className="panel-reason">{row.request.reason}</p>
        ) : (
          job.reason && <p className="panel-reason">{job.reason}</p>
        )}
        <div className="row-actions">
          {row.state !== "job:paused" && row.state !== "pending:waiting" && snapshot.resumes.length > 0 && (
            <>
              <button className="btn primary sm" disabled={pending !== null} onClick={applyNow}>
                {pending === "submit" ? "Applying…" : ready ? "Apply now" : "Apply"}
              </button>
              {ready && (
                <button className="btn sm" disabled={pending !== null} onClick={dryRun}>
                  Dry run
                </button>
              )}
              <button className="btn sm" disabled={pending !== null} onClick={rank}>
                {pending === "rank" ? "Ranking…" : job.score == null ? "Rank" : "Rank again"}
              </button>
            </>
          )}
          <JobControls row={row} detailed />
        </div>
      </section>
    </>
  );
}
