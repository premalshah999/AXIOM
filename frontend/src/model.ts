// Product semantics for run/job states. One place decides how a state reads,
// which group it belongs to and which controls are honest for it.
import type { Job, JobRequest, PipelineRow, Proof, Reserved, Review, Run, Snapshot } from "./types";

export type Tone = "ok" | "run" | "ask" | "hold" | "fix" | "bad" | "idle";
export type Group =
  | "active"
  | "needs"
  | "repair"
  | "unconfirmed"
  | "paused"
  | "done"
  | "existing"
  | "stopped"
  | "new"
  | "reserved";

type StateInfo = { label: string; tone: Tone; group: Group; explain: string };

export const STATES: Record<string, StateInfo> = {
  queued: {
    label: "Queued",
    tone: "hold",
    group: "active",
    explain:
      "Waiting for a free slot. It starts by itself; nothing is needed from you.",
  },
  running: {
    label: "Applying",
    tone: "run",
    group: "active",
    explain:
      "AXIOM is filling in this application now.",
  },
  submitting: {
    label: "Submitting",
    tone: "run",
    group: "active",
    explain:
      "The application is being sent. It can't be paused now; the outcome is checked next.",
  },
  pausing: {
    label: "Pausing…",
    tone: "idle",
    group: "paused",
    explain:
      "Stopping at the next safe point. The draft is saved, and other applications keep running.",
  },
  paused: {
    label: "Paused",
    tone: "idle",
    group: "paused",
    explain:
      "Paused by you. Other applications keep running. Resume continues from the saved draft.",
  },
  waiting_answer: {
    label: "Needs your answer",
    tone: "ask",
    group: "needs",
    explain:
      "The form asks something only you know. Your answer is saved for future applications, and this one continues by itself.",
  },
  needs_review: {
    label: "Needs your answer",
    tone: "ask",
    group: "needs",
    explain:
      "The form asks something only you know. Your answer is saved, and this one continues by itself.",
  },
  waiting_browser: {
    label: "Repairing",
    tone: "fix",
    group: "repair",
    explain:
      "The site rejected a step or a page couldn't be read. Nothing is needed from you: the draft is kept and AXIOM tries again by itself.",
  },
  waiting_email: {
    label: "Waiting for email",
    tone: "hold",
    group: "active",
    explain:
      "The employer is sending a code or link by email. When it arrives this application continues by itself. Nothing is needed from you.",
  },
  timed_out: {
    label: "Timed out",
    tone: "fix",
    group: "repair",
    explain:
      "The application ran past its time limit before submitting. The draft is kept and can be retried.",
  },
  failed: {
    label: "Not submitted",
    tone: "bad",
    group: "repair",
    explain:
      "This attempt stopped before submission. Nothing was sent to the employer.",
  },
  submission_unknown: {
    label: "Unconfirmed",
    tone: "hold",
    group: "unconfirmed",
    explain:
      "It was sent, but no confirmation page or employer email was captured. It may have gone through, so it is never retried. Check your email or record what you verified.",
  },
  confirmed: {
    label: "Confirmed",
    tone: "ok",
    group: "done",
    explain:
      "Proven by the employer's confirmation page or an email from the employer. AXIOM won't apply here again.",
  },
  already_applied: {
    label: "Already applied",
    tone: "ok",
    group: "existing",
    explain:
      "The employer says you have already applied. AXIOM won't apply here again.",
  },
  dry_run_passed: {
    label: "Dry run passed",
    tone: "ok",
    group: "stopped",
    explain:
      "A dry run reached the final step and stopped. Dry runs never submit.",
  },
  cancelled: {
    label: "Stopped",
    tone: "idle",
    group: "stopped",
    explain: "This attempt was stopped before submission.",
  },
  skipped: {
    label: "Skipped",
    tone: "idle",
    group: "stopped",
    explain: "Skipped. No application was sent.",
  },
  closed: {
    label: "Posting closed",
    tone: "idle",
    group: "stopped",
    explain: "The posting closed before an application was sent.",
  },
  // Jobs with no application attempt yet (Job.status).
  "job:new": {
    label: "Not ranked",
    tone: "idle",
    group: "new",
    explain:
      "Found, but not yet checked against your profile. That happens by itself.",
  },
  "job:ready": {
    label: "Eligible",
    tone: "idle",
    group: "new",
    explain:
      "A match for your profile, with a resume chosen. A running batch applies to these by itself.",
  },
  "job:review": {
    label: "Ranked · uncertain",
    tone: "ask",
    group: "new",
    explain:
      "A likely match, with some doubts. It isn't applied to automatically unless autonomous mode is on.",
  },
  "job:skipped": {
    label: "Below fit",
    tone: "idle",
    group: "new",
    explain:
      "Below your minimum fit, or excluded by a keyword. It won't be applied to automatically.",
  },
  // A paused attempt whose job was resumed but needs ranking again: the batch
  // only picks up never-attempted jobs, so this one waits for an explicit action.
  resumed: {
    label: "Resumed · rank to apply",
    tone: "idle",
    group: "paused",
    explain:
      "Resumed from pause. It needs checking against your profile before a new application starts: rank it, then apply.",
  },
  "job:paused": {
    label: "Paused",
    tone: "idle",
    group: "paused",
    explain: "Paused by you. It is held out of automatic intake.",
  },
  // A reserved employer's job (FAANG, MANGO, the Big Four) the owner has not approved yet (pipeline.py "reserved",
  // owner 2026-10-27). Whatever its latest attempt said, nothing starts it until the owner approves it.
  reserved: {
    label: "Needs your approval",
    tone: "ask",
    group: "reserved",
    explain:
      "A reserved employer. AXIOM finds and ranks these jobs but applies only after you approve the job in Jobs › Reserved.",
  },
  // Shown right after an Apply click until the next poll confirms what the server did (optimistic marks).
  "pending:waiting": {
    label: "Waiting to start",
    tone: "hold",
    group: "active",
    explain:
      "Your request is kept. It starts by itself as soon as a slot is free and the site's pacing allows it.",
  },
  "pending:preparing": {
    label: "Preparing resume",
    tone: "hold",
    group: "active",
    explain:
      "A tailored resume is being prepared. The application starts on its own when it's ready.",
  },
};

export const GROUPS: { id: Group | "all"; label: string }[] = [
  { id: "all", label: "All" },
  { id: "active", label: "In progress" },
  { id: "needs", label: "Needs an answer" },
  { id: "repair", label: "Repairing" },
  { id: "unconfirmed", label: "Unconfirmed" },
  { id: "paused", label: "Paused" },
  // "Confirmed" is the Applications ledger's own list and count (jobpilot/api/pipeline.py confirmed_jobs).
  { id: "done", label: "Confirmed" },
  { id: "existing", label: "Already applied" },
  { id: "stopped", label: "Stopped" },
];

export function stateInfo(key: string): StateInfo {
  return (
    STATES[key] || {
      label: key.replace(/^job:/, "").replaceAll("_", " ") || "Unknown",
      tone: "idle",
      group: key.startsWith("job:") ? "new" : "stopped",
      explain: "",
    }
  );
}

// A submission that began or has evidence must never be offered as a retry.
export const COMMITTED = new Set([
  "confirmed",
  "submission_unknown",
  "already_applied",
  "submitting",
]);
export const ACTIVE = new Set(["queued", "running", "submitting"]);
export const RETRYABLE = new Set([
  "waiting_browser",
  "waiting_email",
  "timed_out",
  "failed",
  "cancelled",
  "needs_review",
  "waiting_answer",
]);

export type JobRow = {
  job: Job;
  run?: Run;
  attempts: number;
  state: string;
  control?: string;
  // The owner's Apply waiting for a worker, pacing or a shared sender (why, and when it is tried again).
  request?: JobRequest | null;
  // A reserved employer (FAANG, MANGO, the Big Four): its section and whether the owner approved the job.
  reserved?: Reserved | null;
  reviews: Review[];
  updated: string;
};

// A run of these states means a waiting Apply has started or must not start (pipeline.py STARTED).
const STARTED = new Set(["queued", "running", "confirmed", "submission_unknown", "already_applied", "submitting"]);

// The server orders Jobs › Pipeline by this (jobpilot/api/pipeline.py PRIORITY; a test keeps them equal).
const PRIORITY: Record<Group, number> = {
  active: 0,
  needs: 1,
  repair: 2,
  unconfirmed: 3,
  paused: 4,
  new: 5,
  done: 6,
  existing: 7,
  stopped: 8,
  reserved: 9,
};

export function displayState(
  job: Job,
  run: Run | undefined,
  control: string | undefined,
  request?: JobRequest | null,
  reserved?: Reserved | null,
) {
  if (control === "paused" && (!run || !COMMITTED.has(run.state)))
    return run?.state === "running" ? "pausing" : "paused";
  // Held for the owner's approval unless an application is already under way or submitted (pipeline.py classified).
  if (reserved && !reserved.approved && (!run || !STARTED.has(run.state))) return "reserved";
  if (request && (!run || !STARTED.has(run.state))) return "pending:waiting";
  if (run?.state === "paused") return "resumed";
  return run ? run.state : "job:" + job.status;
}

// A Jobs › Pipeline row from GET /api/jobs (server-paged, exact).
export function rowFromServer(r: PipelineRow): JobRow {
  const run = r.run ?? undefined;
  const control = r.control?.state;
  return {
    job: r.job,
    run,
    attempts: r.attempts,
    // A confirmed job reads Confirmed unless another attempt is running now (the Jobs "Confirmed" chip lists it).
    state: r.confirmed && !(run && ACTIVE.has(run.state)) ? "confirmed" : displayState(r.job, run, control, r.request, r.reserved),
    control,
    request: r.request,
    reserved: r.reserved,
    reviews: r.reviews,
    updated:
      run?.finished_at || run?.started_at || run?.created_at || r.job.created_at,
  };
}

// Optimistic marks: what an action just did to a job, shown until a list fetched after the action arrives.
export type Marks = Record<string, { state: string; at: number }>;

// The state an Apply result should read as until the next poll.
export function appliedState(serverState: string) {
  if (serverState === "waiting" || serverState === "preparing")
    return "pending:" + serverState;
  return serverState;
}

export function withMarks<T extends { job: Job; state: string }>(
  rows: T[],
  marks: Marks,
  requestedAt: number,
): T[] {
  if (!Object.keys(marks).length) return rows;
  return rows.map((row) => {
    const mark = marks[row.job.id];
    return mark && mark.at > requestedAt ? { ...row, state: mark.state } : row;
  });
}

// The snapshot carries only live rows; derive per-job rows from what is present.
export function deriveRows(s: Snapshot): JobRow[] {
  const runsByJob = new Map<string, Run[]>();
  for (const run of s.runs) {
    const list = runsByJob.get(run.job_id);
    if (list) list.push(run);
    else runsByJob.set(run.job_id, [run]);
  }
  const reviewsByRun = new Map<string, Review[]>();
  for (const q of s.reviews) {
    const list = reviewsByRun.get(q.run_id);
    if (list) list.push(q);
    else reviewsByRun.set(q.run_id, [q]);
  }
  const rows: JobRow[] = [];
  for (const job of s.jobs) {
    if (job.demo) continue;
    const runs = runsByJob.get(job.id) || [];
    // The API orders runs newest first; sort defensively anyway.
    const run = runs.reduce<Run | undefined>(
      (latest, r) => (!latest || r.created_at > latest.created_at ? r : latest),
      undefined,
    );
    const control = s.job_controls?.[job.id]?.state;
    rows.push({
      job,
      run,
      attempts: runs.length,
      state: displayState(job, run, control),
      control,
      reviews: run ? reviewsByRun.get(run.id) || [] : [],
      updated:
        run?.finished_at || run?.started_at || run?.created_at || job.created_at,
    });
  }
  return rows;
}

// What proved a confirmed application, in plain words. The server picks the id from the receipt (jobpilot/api/routes/state.py, whose
// spreadsheet says the same words, and a test keeps the two equal).
export const PROOF: Record<Proof, string> = {
  page: "Confirmation page",
  both: "Confirmation page and email",
  email: "Employer email",
  existing: "Existing application",
  platform: "Platform response",
  final_page: "Final page",
  dashboard: "Employer dashboard",
  applicant: "Recorded by you",
  none: "No receipt on file",
};

export function evidenceLabel(r: { proof: Proof }) {
  return PROOF[r.proof];
}
