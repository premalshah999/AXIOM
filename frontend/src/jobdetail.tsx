import { lazy, Suspense, useMemo, useState } from "react";
import { Copy } from "lucide-react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, usePoll } from "./hooks";
import { DrawerActions } from "./jobactions";
import {
  ACTIVE,
  displayState,
  rowFromServer,
  stateInfo,
  withMarks,
  type JobRow,
} from "./model";
import { groupQuestions, QuestionCard } from "./questions";
import type { Detail, Job, JobRequest, Reserved, Run } from "./types";
import { linkHost, plural, seconds, shortId, stamp, words } from "./format";
import { Button, Drawer, Panel, Select, Textarea } from "./ui/index";
import { Facts, Notice, StatusPill } from "./worktools";

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
  const actions = row && (!selectedRun || latest) ? <DrawerActions row={row} started={!!selectedRun} asking={!!row.reviews.length} /> : null;
  return (
    <Drawer open onClose={close} title={title} width={640}>
      <div className="ax-stack">
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
          <RunSection key={selectedRun} runId={selectedRun} row={row} runs={runs} latest={latest} actions={actions} />
        ) : (
          row && <NotStarted row={row} actions={actions} />
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

function JobSummary({ row }: { row: JobRow }) {
  const { job } = row;
  const notify = useAction();
  return (
    <p className="ax-note">
      <a className="ax-link" href={job.url} target="_blank" rel="noreferrer noopener">
        Open posting<span className="sr-only"> (opens in a new tab)</span>
      </a>
      {" · "}
      {job.location || "Location not listed"}
      {" · "}
      {job.ats}
      {job.score != null && ` · Fit ${job.score}/100`}
      {" · "}
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

/** The status as a sentence, then what the attempt said about itself. The state's label starts the sentence. */
function StatusSentence({ state, reason, earlier }: { state: string; reason?: string; earlier?: string }) {
  const info = stateInfo(state);
  return (
    <section className="dr-stack" aria-label="Status">
      {earlier && <p className="ax-note">Earlier attempt, {earlier}</p>}
      <p>
        <strong>{info.label}.</strong> {info.explain}
      </p>
      {reason && <p>{reason}</p>}
    </section>
  );
}

function RunSection({
  runId,
  row,
  runs,
  latest,
  actions,
}: {
  runId: string;
  row?: JobRow;
  runs: Run[];
  latest: boolean;
  actions: React.ReactNode;
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
  const questions = row && latest ? row.reviews : [];
  const jobs = new Map(snapshot.jobs.map((j) => [j.id, j]));
  return (
    <>
      <div className="dr-stack">
        <StatusSentence
          state={state}
          reason={run?.reason && state !== "confirmed" ? run.reason : undefined}
          earlier={run && !latest ? stamp(run.created_at) : undefined}
        />
        {questions.length > 0 &&
          groupQuestions(questions, (id) => jobs.get(snapshot.runs.find((r) => r.id === id)?.job_id || "")).map((g) => (
            <QuestionCard key={g.review.id} group={g} compact />
          ))}
        {actions}
        {run && (run.state === "submission_unknown" || run.state === "confirmed") && <EvidenceActions run={run} />}
      </div>

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
      {!detail.data && !detail.error && (
        <p className="ax-note" role="status">
          Loading attempt details…
        </p>
      )}
      {detail.data && <DetailBody detail={detail.data} state={state} />}

      {runs.length > 1 && (
        <Panel title={`Attempts (${runs.length})`}>
          <ol className="ax-rows">
            {runs.map((r, i) => (
              <li key={r.id} className="ax-row" aria-current={r.id === runId || undefined}>
                <button type="button" className="ax-link" onClick={() => openJob(r.job_id, i === 0 ? undefined : r.id)}>
                  {stamp(r.created_at)}
                </button>
                <span className="ax-row-end">
                  <StatusPill state={r.state} />
                  <span className="ax-note">
                    {words(r.mode)} · {r.model_calls} AI steps
                  </span>
                </span>
              </li>
            ))}
          </ol>
        </Panel>
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
    <section className="dr-stack" aria-label="Submission evidence">
      <div className="ax-toolbar">
        {!hasEmail && (
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
        )}
        {run.state === "submission_unknown" && (
          <button type="button" className="ax-link" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? "Hide" : "Record a verified outcome"}
          </button>
        )}
      </div>
      {run.state === "submission_unknown" && open && (
        <form
          className="dr-stack"
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
          <Select label="What you verified" value={submitted} onChange={(e) => setSubmitted(e.target.value)}>
            <option value="true">The application was submitted</option>
            <option value="false">The application was not submitted</option>
          </Select>
          <Textarea
            label="Evidence"
            hint="Only what you verified in the employer's portal or a confirmation email. This never retries the application."
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

// The history: what this attempt did, from the newest facts to the raw record. The long ones fold.
function DetailBody({ detail, state }: { detail: Detail; state: string }) {
  const { run, failure_report: report } = detail;
  const [shot, setShot] = useState(false);
  const answers = Object.values(detail.answers || {});
  const verified = answers.filter((a) => a.verified).length;
  const receipt = run.receipt || {};
  const lines = receiptLines(receipt);
  return (
    <>
      {/* The status above already says what a question or the attempt's own reason says; the report adds the cause and next steps. */}
      {report && stateInfo(state).group !== "needs" && (
        <Panel
          title="What stopped this attempt"
          actions={<span className="ax-note">{report.source === "ai" ? "Summarized from recorded evidence" : "Recorded evidence"}</span>}
        >
          <div className="dr-stack">
            {report.summary !== run.reason && <p>{report.summary}</p>}
            {report.likely_cause && <p className="ax-note">{report.likely_cause}</p>}
            {report.next_steps?.length > 0 && (
              <ul className="ax-rows">
                {report.next_steps.map((s, i) => (
                  <li key={i} className="ax-row">
                    {s}
                  </li>
                ))}
              </ul>
            )}
            <details className="ax-fold">
              <summary>Diagnostic evidence</summary>
              <pre>{JSON.stringify(report.evidence, null, 2)}</pre>
            </details>
          </div>
        </Panel>
      )}
      <Panel title="This attempt">
        <Facts
          items={[
            ["Mode", run.mode === "dry_run" ? "Dry run (never submits)" : "Submit"],
            ["Started", stamp(run.started_at || run.created_at)],
            ["Finished", run.finished_at ? stamp(run.finished_at) : "In progress"],
            ["Time", run.elapsed ? seconds(run.elapsed) : "—"],
            ["AI usage", `${run.model_calls} steps · $${run.cost.toFixed(3)}`],
            ["Attempt ID", <span className="mono">{shortId(run.id)}</span>],
          ]}
        />
      </Panel>
      {(lines.length > 0 || Object.keys(receipt).length > 0) && (
        <Panel title="Evidence">
          <div className="dr-stack">
            {lines.length ? (
              <ul className="ax-rows">
                {lines.map((l) => (
                  <li key={l} className="ax-row">
                    {l}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="ax-note">No acknowledgement or receipt recorded.</p>
            )}
            <details className="ax-fold">
              <summary>Raw receipt</summary>
              <pre>{JSON.stringify(receipt, null, 2)}</pre>
            </details>
          </div>
        </Panel>
      )}
      {detail.screenshot && (
        <Panel title="Final page">
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
        </Panel>
      )}
      <details className="ax-fold">
        <summary>
          Answers <span className="ax-note">{answers.length ? `${answers.length} · ${verified} verified` : "none yet"}</span>
        </summary>
        {answers.length ? (
          <Facts
            items={answers.map((a, i): [React.ReactNode, React.ReactNode] => [
              a.label,
              <span key={i}>
                {a.answer}
                {!a.verified && <span className="ax-note"> · unverified</span>}
              </span>,
            ])}
          />
        ) : (
          <p className="ax-note">Answers are recorded when AXIOM finishes filling the form.</p>
        )}
      </details>
      <details className="ax-fold">
        <summary>
          Activity <span className="ax-note">{plural(detail.events.length, "event")}</span>
        </summary>
        {detail.events.length ? (
          <Facts
            items={detail.events.map((e): [React.ReactNode, React.ReactNode] => [
              <time dateTime={e.created_at}>{stamp(e.created_at)}</time>,
              <span>
                <span className="ax-note">{words(e.kind)}</span> {e.message}
              </span>,
            ])}
          />
        ) : (
          <p className="ax-note">No events recorded yet.</p>
        )}
      </details>
    </>
  );
}

// A job with no attempt yet: why it is where it is. Its actions (Apply now, Dry run, Rank) come in under More.
function NotStarted({ row, actions }: { row: JobRow; actions: React.ReactNode }) {
  const job = row.job;
  return (
    <div className="dr-stack">
      <StatusSentence state={row.state} reason={row.state === "pending:waiting" && row.request?.reason ? row.request.reason : job.reason} />
      {actions}
    </div>
  );
}
