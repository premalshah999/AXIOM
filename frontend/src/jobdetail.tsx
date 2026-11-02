import { lazy, Suspense, useMemo, useState } from "react";
import { Copy, ExternalLink } from "lucide-react";
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
import { SteerPanel, TakeOverButton } from "./steer";
import type { Detail, Hold, Job, JobPosting, JobRequest, QueuePlace, Reserved, Run, Snapshot } from "./types";
import { ago, day, jobWords, plainReason, platformName, plural, seconds, shortId, shownFit, stamp, words } from "./format";
import { Button, Drawer, Monogram, Panel, Select, Skeleton, Textarea } from "./ui/index";
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
    hold?: Hold | null;
    queue?: QueuePlace | null;
  }>(
    (signal) => api(`/jobs/${jobId}`, "GET", undefined, signal),
    30000,
    [jobId, version],
    !workspaceRow,
  );
  // The posting's own words, its date and where it was found: the same on every row's panel.
  const posting = usePoll<JobPosting>((signal) => api(`/jobs/${jobId}/posting`, "GET", undefined, signal), 600000, [jobId]);
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
  // Whether the app is still trying, and when: the server's one answer (problems.next_for_job), the same one Applications reads.
  const outlook = usePoll<Outlook>((signal) => api(`/jobs/${jobId}/outlook`, "GET", undefined, signal), 30000, [jobId, version]);
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
  const words = job ? jobWords(job) : null;
  const company = words?.company ?? "Application record";
  const place = historical.data?.queue;
  const held = historical.data?.hold;
  const actions = row && (!selectedRun || latest) ? <DrawerActions row={row} started={!!selectedRun} asking={!!row.reviews.length} place={place} hold={held} /> : null;
  return (
    <Drawer open onClose={close} title={company} sub={words ? (words.unread ? [words.role, words.hint].filter(Boolean).join(" · ") : words.role) : undefined} lead={job ? <Monogram name={company} url={job.url} size="lg" employer /> : undefined} width={640}>
      <div className="ax-stack">
        {job ? (
          <JobSummary row={row!} posting={posting.data} />
        ) : (
          <p className="ax-note">This job isn't in the current list (it may be archived or older history). Its recorded attempt is shown below.</p>
        )}
        {!workspaceRow && historical.error && <Notice tone="bad">{historical.error.message}</Notice>}
        {selectedRun ? (
          <RunSection key={selectedRun} runId={selectedRun} row={row} runs={runs} latest={latest} actions={actions} outlook={outlook.data} />
        ) : (
          row && <NotStarted row={row} actions={actions} hold={held} place={place} />
        )}
        {job && posting.data?.text && (
          <details className="ax-fold" open={!selectedRun || undefined}>
            <summary>The posting</summary>
            <div className="dr-posting">{posting.data.text}</div>
          </details>
        )}
        {job && (
          <Suspense fallback={null}>
            <JobResume jobId={job.id} eligible={job.status === "ready"} />
          </Suspense>
        )}
        {job && <Technical jobId={job.id} />}
      </div>
    </Drawer>
  );
}

/** Where, what platform, when it was posted and where from, how good a fit, and the way to the posting. */
function JobSummary({ row, posting }: { row: JobRow; posting?: JobPosting | null }) {
  const { job } = row;
  const at = posting?.posted_at || job.created_at;
  return (
    <p className="dr-meta">
      <span>{job.location || "Location not listed"}</span>
      <span>{platformName(job.ats)}</span>
      {at && <span title={stamp(at)}>{posting?.dated ? "Posted" : "Found"} {ago(at)}</span>}
      {posting?.source && <span>{posting.source}</span>}
      {shownFit(job) != null && (
        <span>
          Fit <span className="mono">{shownFit(job)}</span>
        </span>
      )}
      <a className="ax-more-link" href={posting?.link || job.url} target="_blank" rel="noreferrer noopener">
        Open posting
        <ExternalLink size={13} aria-hidden="true" />
        <span className="sr-only"> (opens in a new tab)</span>
      </a>
    </p>
  );
}

/** IDs, tucked away: they matter only when reporting a problem. */
function Technical({ jobId }: { jobId: string }) {
  const notify = useAction();
  return (
    <details className="ax-fold">
      <summary>Technical details</summary>
      <Facts
        items={[
          [
            "Job ID",
            <button type="button" className="ax-link mono" title="Copy job ID" onClick={() => notify.run("copy", () => navigator.clipboard.writeText(jobId), "Job ID copied.")}>
              {shortId(jobId)} <Copy size={12} aria-hidden="true" />
              <span className="sr-only">Copy job ID</span>
            </button>,
          ],
        ]}
      />
    </details>
  );
}

// What happens to this job next, in one sentence, from where it stands. Every row's panel says it the same way.
function nextStep(state: string, paused: boolean, place?: QueuePlace | null, hold?: Hold | null, mode?: string) {
  if (hold) return hold.next;
  const when = paused ? "Applying is paused, so it starts after you resume." : "It starts by itself when a worker is free.";
  if (place) return `Number ${place.position} of ${place.total} in the queue. ${when}${place.wait ? " " + place.wait : ""}`;
  if (state === "running") return "It finishes by itself, and appears under Applications with the outcome.";
  if (state === "submitting") return "It is being sent. It can't be stopped now.";
  if (state === "queued" || state === "pending:waiting" || state === "pending:preparing") return when;
  if (state === "waiting_email") return "It continues when the employer's email arrives.";
  if (state === "gave_up") return "The app won't try again by itself. Add it to the queue for one fresh try.";
  if (state === "reserved") return "It starts after you approve it.";
  if (state === "job:new") return "When the check is done, it joins the queue if it fits.";
  if (state === "job:ready") return mode === "auto" ? "It joins the queue on the next check of what can start." : "It won't start until you queue it: here, on the Job board, or in a review batch.";
  if (state === "job:review" || state === "job:skipped") return "It won't start by itself. Adding it to the queue applies anyway, unless the posting rules you out.";
  if (state === "job:paused" || state === "paused" || state === "resumed") return "It stays out of the queue until you put it back.";
  const group = stateInfo(state).group;
  if (group === "repair") return "The app tries again by itself; there is no fixed time.";
  if (group === "needs") return "It continues as soon as you answer.";
  if (group === "unconfirmed") return "It is never sent twice. Check your email, or confirm it yourself.";
  if (group === "done") return "Nothing more to do. Replies arrive by email.";
  return "Nothing more happens to this job.";
}

/** The status first, in words, with what to do next inside it. */
function StatusBlock({ state, reason, earlier, hold, place, next, children }: { state: string; reason?: string; earlier?: string; hold?: Hold | null; place?: QueuePlace | null; next?: string; children?: React.ReactNode }) {
  const { snapshot, go } = useWorkspace();
  const info = stateInfo(state);
  // A job with a place in line reads In queue; a Ready one without one is not in it, whatever the lists call Ready.
  const word = place || state === "pending:waiting" ? "In queue" : state === "job:ready" ? "Not queued" : undefined;
  // What it waits for, said once: "as soon as a worker is free" twice around "this one starts when that one ends" read as three answers.
  const waitingFor = state === "pending:waiting" && !!reason;
  return (
    <section className="dr-status" aria-label="Status">
      {earlier && <p className="ax-note">Earlier attempt, {earlier}</p>}
      <p className="dr-status-word">
        {hold ? <StatusPill state={state} label={hold.label ?? "Held"} tone="hold" /> : <StatusPill state={state} label={word} />}
      </p>
      {(hold ? hold.reason : !waitingFor && info.explain) && <p className="dr-status-why">{hold ? hold.reason : info.explain}</p>}
      {!hold && reason && <p className="dr-status-reason">{reason}</p>}
      {!earlier && (!waitingFor || !!next || !!place) && (
        <p className="dr-next">
          {next || nextStep(state, snapshot.control.paused, place, hold, snapshot.config.apply_mode)}
          {place && (
            <>
              {" "}
              <button type="button" className="ax-link" onClick={() => go({ view: "queue" })}>
                Open the queue
              </button>
            </>
          )}
        </p>
      )}
      {children}
    </section>
  );
}

type Outlook = { word: string; kind: string; sentence: string; at: string };

function RunSection({
  runId,
  row,
  runs,
  latest,
  actions,
  outlook,
}: {
  outlook?: Outlook | null;
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
  const shown = latest && row ? row.state : run ? displayState(row?.job || ({} as never), run, undefined) : "";
  // A stop the app has given up on is "Couldn't apply" here as in the list, and one it will retry says when (outlook).
  const state = latest && outlook?.word === "failed" && stateInfo(shown).group === "repair" ? "gave_up" : shown;
  const next = latest && (outlook?.kind === "retry" || outlook?.kind === "stopped") ? outlook.sentence : "";
  const questions = row && latest ? row.reviews : [];
  const jobs = new Map(snapshot.jobs.map((j) => [j.id, j]));
  return (
    <>
      <StatusBlock state={state} next={next} reason={run?.reason && state !== "confirmed" ? plainReason(run.reason) : undefined} earlier={run && !latest ? stamp(run.created_at) : undefined}>
        {questions.length > 0 &&
          groupQuestions(questions, (id) => jobs.get(snapshot.runs.find((r) => r.id === id)?.job_id || "")).map((g) => (
            <div key={g.review.id} className="dr-question">
              <QuestionCard group={g} compact />
            </div>
          ))}
        {actions}
        {row && latest && <SteerPanel jobId={row.job.id} />}
        {row && latest && ["needs", "repair"].includes(stateInfo(state).group) && (
          <div className="ax-toolbar">
            <TakeOverButton jobId={row.job.id} />
          </div>
        )}
        {run && (run.state === "submission_unknown" || run.state === "confirmed") && <EvidenceActions run={run} />}
      </StatusBlock>

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
      {!detail.data && !detail.error && <Skeleton rows={3} mono={false} />}
      {detail.data && <DetailBody detail={detail.data} state={state} runs={runs} runId={runId} open={openJob} />}
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
  if (receipt.website_confirmed) lines.push("Employer's confirmation page captured");
  if (receipt.email_confirmed || (email && Object.keys(email).length && email.sender_trusted !== false))
    lines.push("Confirmation email from the employer");
  if (receipt.existing_application_notice) lines.push("The employer says you already applied");
  if (receipt.evidence_reconciliation === "pending")
    lines.push("Still being checked: not counted as confirmed yet");
  return lines;
}

// The history: what this attempt did, from the newest facts to the raw record. The long ones fold.
function DetailBody({ detail, state, runs, runId, open }: { detail: Detail; state: string; runs: Run[]; runId: string; open: (job: string, run?: string) => void }) {
  const { run, failure_report: report } = detail;
  const [shot, setShot] = useState(false);
  const answers = Object.values(detail.answers || {});
  const verified = answers.filter((a) => a.verified).length;
  const receipt = run.receipt || {};
  const lines = receiptLines(receipt);
  // A report from before 2026-11-01 may hold the engine's empty finding ("does not establish a more specific cause", "browser
  // trace"), which told someone new nothing and sounded like a job for them.
  const cause = report?.likely_cause && !/does not establish a more specific cause/.test(report.likely_cause) ? report.likely_cause : "";
  const steps = (report?.next_steps || []).filter((s) => !/browser trace/.test(s));
  const reportSays = !!report && (report.summary !== run.reason || !!cause || steps.length > 0);
  return (
    <>
      {/* The status above already says what a question or the attempt's own reason says; the report adds the cause and next steps. */}
      {report && stateInfo(state).group !== "needs" && reportSays && (
        <Panel title="What stopped this attempt">
          <div className="dr-stack">
            {report.summary !== run.reason && <p>{report.summary}</p>}
            {cause && <p className="ax-note">{cause}</p>}
            {steps.length > 0 && (
              <ul className="ax-rows">
                {steps.map((s, i) => (
                  <li key={i} className="ax-row">
                    {s}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Panel>
      )}
      {lines.length > 0 && (
        <Panel title="Proof">
          <ul className="ax-rows">
            {lines.map((l) => (
              <li key={l} className="ax-row">
                {l}
              </li>
            ))}
          </ul>
        </Panel>
      )}
      <Panel title={runs.length > 1 ? `History, ${runs.length} attempts` : "History"}>
        <ol className="dr-history">
          {(runs.length ? runs : [run]).map((r, i) => (
            <li key={r.id} className={r.id === runId ? "is-here" : ""}>
              <i aria-hidden="true" />
              {runs.length > 1 && r.id !== runId ? (
                <button type="button" className="ax-link" onClick={() => open(r.job_id, i === 0 ? undefined : r.id)}>
                  {stamp(r.created_at)}
                </button>
              ) : (
                <span>{stamp(r.created_at)}</span>
              )}
              <StatusPill state={r.state} />
              <span className="dr-history-note">
                {r.mode === "dry_run" ? "Dry run" : ""}
                {r.mode === "dry_run" && r.elapsed ? " · " : ""}
                {r.elapsed ? seconds(r.elapsed) : ""}
              </span>
            </li>
          ))}
        </ol>
      </Panel>
      {detail.screenshot && (
        <Panel title="Final page">
          {shot ? (
            // Shown in place: a link to the same picture would open the system browser from the native window, with no session.
            <div className="ax-shot">
              <img src={`/api/runs/${run.id}/screenshot`} alt="The last application page AXIOM saw" />
            </div>
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
              // The message says it; the event's own kind ("settle timing", "phase timing") is engine vocabulary and only a hint.
              <span title={words(e.kind)}>{e.message}</span>,
            ])}
          />
        ) : (
          <p className="ax-note">No events recorded yet.</p>
        )}
      </details>
      <details className="ax-fold">
        <summary>This attempt in detail</summary>
        <div className="dr-stack">
          <Facts
            items={[
              ["Mode", run.mode === "dry_run" ? "Dry run (never sends)" : "Sends"],
              ["Started", stamp(run.started_at || run.created_at)],
              ["Finished", run.finished_at ? stamp(run.finished_at) : "In progress"],
              ["AI usage", `${run.model_calls} steps · $${run.cost.toFixed(3)}`],
              ["Attempt ID", <span className="mono">{shortId(run.id)}</span>],
            ]}
          />
          {Object.keys(receipt).length > 0 && (
            <details className="ax-fold">
              <summary>Raw receipt</summary>
              <pre>{JSON.stringify(receipt, null, 2)}</pre>
            </details>
          )}
          {report && (
            <details className="ax-fold">
              <summary>Diagnostic evidence</summary>
              <pre>{JSON.stringify(report.evidence, null, 2)}</pre>
            </details>
          )}
        </div>
      </details>
    </>
  );
}

// A job with no attempt yet: why it is where it is. Its actions (Apply now, Dry run, Rank) come in under More.
function NotStarted({ row, actions, hold, place }: { row: JobRow; actions: React.ReactNode; hold?: Hold | null; place?: QueuePlace | null }) {
  const job = row.job;
  const { refresh } = useWorkspace();
  const { pending, run } = useAction();
  // The job was added with sending off: it can never start until that changes, so say so and offer the switch here.
  const sendingOff = row.state === "pending:waiting" && row.request?.code === "submission_disabled";
  if (sendingOff)
    return (
      <StatusBlock
        state={row.state}
        hold={{ code: "submission_disabled", label: "Sending is off", reason: "Nothing has gone to this employer.", next: "It starts once you turn sending on." }}
      >
        <Button
          variant="primary"
          loading={pending === "send"}
          onClick={() =>
            void run(
              "send",
              async () => {
                // Read the current controls right before writing them back.
                const fresh = await api<Snapshot["control"]>("/control");
                await api("/control", "PUT", { ...fresh, auto_submit: true });
                await refresh();
              },
              "Sending is on.",
            )
          }
        >
          Turn on sending
        </Button>
        {actions}
      </StatusBlock>
    );
  return (
    <StatusBlock
      state={row.state}
      hold={row.state === "job:ready" ? hold : undefined}
      place={place}
      reason={plainReason(row.state === "pending:waiting" && row.request?.reason ? row.request.reason : job.reason)}
    >
      {actions}
    </StatusBlock>
  );
}
