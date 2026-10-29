import { lazy, Suspense, useMemo, useState } from "react";
import { Copy } from "lucide-react";
import { api, applyJob, PREPARING, queueJob } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, usePoll } from "./hooks";
import { DrawerActions } from "./jobactions";
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
import type { Detail, Job, JobRequest, Reserved, Run } from "./types";
import { linkHost, Loading, plural, seconds, shortId, stamp, words } from "./ui";
import { Button, Drawer, Textarea, Select } from "./ui/index";
import { Notice, StatusPill } from "./worktools";

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
    reserved?: Reserved | null;
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
                // A reserved employer's job reads as "Needs your approval" and offers the approval.
                reserved: historical.data.reserved,
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
  const latest = !runId || runId === row?.run?.id;
  const title = job
    ? [job.company || linkHost(job.url), job.title].filter(Boolean).join(" · ") || "Saved job link"
    : "Application record";
  return (
    <Drawer
      open
      onClose={close}
      title={title}
      width={640}
      footer={row && (!selectedRun || latest) ? <JobFooter row={row} started={!!selectedRun} /> : undefined}
    >
      <div className="ax-dr">
        {job ? (
          <JobSummary row={row!} />
        ) : (
          <p className="ax-note">
            This job isn't in the current workspace list (it may be archived or older history). Its recorded attempt is
            shown below.
          </p>
        )}
        {!workspaceRow && historical.error && <Notice tone="bad">{historical.error.message}</Notice>}
        {selectedRun ? (
          <RunSection key={selectedRun} runId={selectedRun} row={row} runs={runs} latest={latest} />
        ) : (
          row && <NotStarted row={row} />
        )}
        {job && (
          <Suspense fallback={null}>
            <JobResume jobId={job.id} eligible={job.status === "ready"} />
          </Suspense>
        )}
      </div>
    </Drawer>
  );
}

// The drawer's footer stays in view while the details scroll: Apply, Dry run and Rank for a job not started yet, then
// whatever else the job's state allows (Resume, Retry, Pause, Confirm…, Delete).
function JobFooter({ row, started }: { row: JobRow; started: boolean }) {
  return (
    <div className="ax-dr-foot">
      {!started && <StartActions row={row} />}
      <DrawerActions row={row} />
    </div>
  );
}

function JobSummary({ row }: { row: JobRow }) {
  const { job } = row;
  const notify = useAction();
  return (
    <p className="ax-dr-meta">
      <a className="ax-link" href={job.url} target="_blank" rel="noreferrer noopener">
        Open posting<span className="sr-only"> (opens in a new tab)</span>
      </a>
      <span>{job.location || "Location not listed"}</span>
      <span>{job.ats}</span>
      {job.score != null && <span>Fit {job.score}/100</span>}
      <button
        type="button"
        className="ax-link mono"
        title="Copy job ID"
        onClick={() => notify.run("copy", () => navigator.clipboard.writeText(job.id), "Job ID copied.")}
      >
        {shortId(job.id)} <Copy size={12} aria-hidden="true" />
        <span className="sr-only">Copy job ID</span>
      </button>
    </p>
  );
}

function Section({ title, id, children }: { title: string; id: string; children: React.ReactNode }) {
  return (
    <section className="ax-dr-sec" aria-labelledby={id}>
      <h3 id={id}>{title}</h3>
      {children}
    </section>
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
    (ACTIVE.has(snapRun.state) || snapRun.state === "waiting_browser" || snapRun.state === "waiting_email");
  const detail = usePoll<Detail>(
    (signal) => api(`/runs/${runId}`, "GET", undefined, signal),
    live ? 10000 : 3_600_000,
    [runId, live],
  );
  const run = detail.data?.run || snapRun;
  const state =
    latest && row ? row.state : run ? displayState(row?.job || ({} as never), run, undefined) : "";
  const info = stateInfo(state);
  const questions = row && latest ? row.reviews : [];
  const jobs = new Map(snapshot.jobs.map((j) => [j.id, j]));
  return (
    <>
      <section className="ax-dr-status" aria-label="Status">
        <div className="ax-dr-statushead">
          <StatusPill state={state} />
          {run && !latest && <span className="ax-note">Earlier attempt · {stamp(run.created_at)}</span>}
        </div>
        {info.explain && <p>{info.explain}</p>}
        {run?.reason && state !== "confirmed" && <p className="ax-dr-reason">{run.reason}</p>}
      </section>

      {questions.length > 0 && (
        <section className="ax-dr-sec" aria-label="Question">
          {groupQuestions(questions, (id) => jobs.get(snapshot.runs.find((r) => r.id === id)?.job_id || "")).map((g) => (
            <QuestionCard key={g.review.id} group={g} compact />
          ))}
        </section>
      )}

      {run && (run.state === "submission_unknown" || run.state === "confirmed") && <EvidenceActions run={run} />}

      {detail.error && (
        <Notice
          tone="bad"
          action={
            <Button size="sm" onClick={() => detail.refresh()}>
              Try again
            </Button>
          }
        >
          {detail.error.message}
        </Notice>
      )}
      {!detail.data && !detail.error && <Loading label="Loading attempt details…" />}
      {detail.data && <DetailBody detail={detail.data} />}

      {runs.length > 1 && (
        <Section title={`Attempts (${runs.length})`} id="attempts-title">
          <p className="ax-note">Retries of one job count as one application; only a confirmed attempt counts toward the batch.</p>
          <ol className="ax-attempts">
            {runs.map((r, i) => (
              <li key={r.id} aria-current={r.id === runId || undefined}>
                <button type="button" className="ax-link" onClick={() => openJob(r.job_id, i === 0 ? undefined : r.id)}>
                  {stamp(r.created_at)}
                </button>
                <StatusPill state={r.state} />
                <span className="ax-note">
                  {words(r.mode)} · {r.model_calls} AI steps
                </span>
              </li>
            ))}
          </ol>
        </Section>
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
    <section className="ax-dr-sec" aria-label="Submission evidence">
      {!hasEmail && (
        <div>
          <Button
            size="sm"
            loading={pending === "email"}
            disabled={pending !== null}
            onClick={() =>
              act(
                "email",
                async () => {
                  const r = await api<{ emails_confirmed?: number }>(`/runs/${run.id}/check-email`, "POST");
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
            Check email for a receipt
          </Button>
        </div>
      )}
      {run.state === "submission_unknown" && (
        <>
          <button type="button" className="ax-link" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? "Hide" : "Record a verified outcome"}
          </button>
          {open && (
            <form
              className="ax-reconcile"
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
              <p className="ax-note">
                Record only what you verified in the employer's portal or a confirmation email. This records a fact; it
                never retries the application.
              </p>
              <Select label="What you verified" value={submitted} onChange={(e) => setSubmitted(e.target.value)}>
                <option value="true">The application was submitted</option>
                <option value="false">The application was not submitted</option>
              </Select>
              <Textarea
                label="Evidence"
                hint="For example: “Portal shows Application received on Oct 5.”"
                required
                minLength={5}
                maxLength={4000}
                rows={3}
                value={evidence}
                onChange={(e) => setEvidence(e.target.value)}
              />
              <div>
                <Button type="submit" variant="primary" size="sm" loading={pending === "reconcile"} disabled={pending !== null || evidence.trim().length < 5}>
                  Save verified outcome
                </Button>
              </div>
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
  if (receipt.website_confirmed) lines.push("Job-specific page acknowledgement captured");
  if (receipt.email_confirmed || (email && Object.keys(email).length && email.sender_trusted !== false))
    lines.push("Authenticated employer receipt email");
  if (receipt.existing_application_notice) lines.push("Employer reported an existing application");
  if (receipt.evidence_reconciliation === "pending")
    lines.push("Evidence reconciliation pending: excluded from success counts until resolved");
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
        <Section title="What stopped this attempt" id="report-title">
          <p>{report.summary}</p>
          {report.likely_cause && <p className="ax-note">{report.likely_cause}</p>}
          {report.next_steps?.length > 0 && (
            <ul className="ax-list">
              {report.next_steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
          )}
          <details className="ax-fold">
            <summary>Diagnostic evidence</summary>
            <pre>{JSON.stringify(report.evidence, null, 2)}</pre>
          </details>
          <small className="ax-note">
            {report.source === "ai" ? "Summarized from recorded evidence" : "Recorded evidence"}
          </small>
        </Section>
      )}
      <Section title="This attempt" id="attempt-title">
        <dl className="ax-kv">
          {(
            [
              ["Mode", run.mode === "dry_run" ? "Dry run (never submits)" : "Submit"],
              ["Started", stamp(run.started_at || run.created_at)],
              ["Finished", run.finished_at ? stamp(run.finished_at) : "In progress"],
              ["Time", run.elapsed ? seconds(run.elapsed) : "—"],
              ["AI usage", `${run.model_calls} steps · $${run.cost.toFixed(3)}`],
              ["Attempt ID", <span className="mono">{shortId(run.id)}</span>],
            ] as [string, React.ReactNode][]
          ).map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      </Section>
      {(lines.length > 0 || Object.keys(receipt).length > 0) && (
        <Section title="Evidence" id="evidence-title">
          {lines.length ? (
            <ul className="ax-list">
              {lines.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          ) : (
            <p className="ax-note">No acknowledgement or receipt recorded.</p>
          )}
          <details className="ax-fold">
            <summary>Raw receipt</summary>
            <pre>{JSON.stringify(receipt, null, 2)}</pre>
          </details>
        </Section>
      )}
      {detail.screenshot && (
        <Section title="Final page" id="shot-title">
          {shot ? (
            <a className="ax-shot" href={`/api/runs/${run.id}/screenshot`} target="_blank" rel="noreferrer">
              <img src={`/api/runs/${run.id}/screenshot`} alt="The last application page AXIOM saw" />
            </a>
          ) : (
            <div>
              <Button size="sm" onClick={() => setShot(true)}>
                Show screenshot
              </Button>
            </div>
          )}
        </Section>
      )}
      <details className="ax-dr-sec ax-fold">
        <summary>
          Answers <span className="ax-note">{answers.length ? `${answers.length} · ${verified} verified` : "none yet"}</span>
        </summary>
        {answers.length ? (
          <table className="ax-mini">
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
                  <td>{a.answer}</td>
                  <td>
                    <span className={a.verified ? "ax-ok" : "ax-note"}>{a.verified ? "Verified" : "Unverified"}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="ax-note">Answers are recorded when AXIOM finishes filling the form.</p>
        )}
      </details>
      <details className="ax-dr-sec ax-fold">
        <summary>
          Activity <span className="ax-note">{plural(detail.events.length, "event")}</span>
        </summary>
        {detail.events.length ? (
          <ol className="ax-timeline">
            {detail.events.map((e) => (
              <li key={e.id}>
                <time dateTime={e.created_at}>{stamp(e.created_at)}</time>
                <span className="ax-note">{words(e.kind)}</span>
                <p>{e.message}</p>
              </li>
            ))}
          </ol>
        ) : (
          <p className="ax-note">No events recorded yet.</p>
        )}
      </details>
    </>
  );
}

// The explanation for a job that has no attempt yet; its actions (Apply now, Dry run, Rank) are in the footer.
function NotStarted({ row }: { row: JobRow }) {
  const job = row.job;
  const info = stateInfo(row.state);
  return (
    <section className="ax-dr-status" aria-label="Status">
      <div className="ax-dr-statushead">
        <StatusPill state={row.state} />
      </div>
      {info.explain && <p>{info.explain}</p>}
      {row.state === "pending:waiting" && row.request?.reason ? (
        <p className="ax-dr-reason">{row.request.reason}</p>
      ) : (
        job.reason && <p className="ax-dr-reason">{job.reason}</p>
      )}
    </section>
  );
}

// One click applies (owner, 2026-10-18): no reason to type and no separate ranking step. The server approves the job
// and starts it, or keeps the request until a worker, pacing or a shared email sender allows it.
function StartActions({ row }: { row: JobRow }) {
  const { snapshot, refresh, markJobs } = useWorkspace();
  const { pending, run } = useAction();
  const job = row.job;
  const ready = job.status === "ready";
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
        const r = await queueJob(`/jobs/${job.id}/queue`, { mode: "dry_run", resume_id: job.resume_id || undefined });
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
  if (row.state === "job:paused" || row.state === "pending:waiting" || row.state === "reserved" || snapshot.resumes.length === 0)
    return null;
  return (
    <>
      <Button variant="primary" size="sm" loading={pending === "submit"} disabled={pending !== null} onClick={applyNow}>
        {ready ? "Apply now" : "Apply"}
      </Button>
      {ready && (
        <Button size="sm" loading={pending === "dry_run"} disabled={pending !== null} onClick={dryRun}>
          Dry run
        </Button>
      )}
      <Button size="sm" loading={pending === "rank"} disabled={pending !== null} onClick={rank}>
        {job.score == null ? "Rank" : "Rank again"}
      </Button>
    </>
  );
}
