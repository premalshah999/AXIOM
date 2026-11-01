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

/** The five words every screen uses (jobpilot/vocabulary.py WORDS). */
export type Word = "applied" | "applying" | "needs" | "failed" | "not";
type StateInfo = { label: string; tone: Tone; group: Group; explain: string };

export const STATES: Record<string, StateInfo> = {
  queued: {
    label: "Applying",
    tone: "hold",
    group: "active",
    explain:
      "Waiting for a free worker. It starts by itself.",
  },
  running: {
    label: "Applying",
    tone: "run",
    group: "active",
    explain:
      "Filling in the application now.",
  },
  submitting: {
    label: "Applying",
    tone: "run",
    group: "active",
    explain:
      "Sending it now. It can't be paused.",
  },
  pausing: {
    label: "Not applying",
    tone: "idle",
    group: "paused",
    explain:
      "Stopping at the next safe point.",
  },
  paused: {
    label: "Not applying",
    tone: "idle",
    group: "paused",
    explain:
      "Paused by you. Resume continues from where it stopped.",
  },
  waiting_answer: {
    label: "Needs you",
    tone: "ask",
    group: "needs",
    explain:
      "The form asks something only you know. Your answer is saved for next time.",
  },
  needs_review: {
    label: "Needs you",
    tone: "ask",
    group: "needs",
    explain:
      "The form asks something only you know. Your answer is saved for next time.",
  },
  waiting_browser: {
    label: "Applying",
    tone: "fix",
    group: "repair",
    explain:
      "It stopped on a problem and will try again on its own.",
  },
  waiting_email: {
    label: "Applying",
    tone: "hold",
    group: "active",
    explain:
      "Waiting for the employer's emailed code or link.",
  },
  timed_out: {
    label: "Applying",
    tone: "fix",
    group: "repair",
    explain:
      "It stopped on a problem and will try again on its own.",
  },
  failed: {
    label: "Applying",
    tone: "bad",
    group: "repair",
    explain:
      "It stopped on a problem and will try again on its own.",
  },
  submission_unknown: {
    label: "Needs you",
    tone: "hold",
    group: "unconfirmed",
    explain:
      "Sent, but no receipt was captured. Check your email, or confirm it yourself.",
  },
  confirmed: {
    label: "Applied",
    tone: "ok",
    group: "done",
    explain:
      "The employer has your application.",
  },
  already_applied: {
    label: "Not applying",
    tone: "ok",
    group: "existing",
    explain:
      "The employer already has an application from you.",
  },
  dry_run_passed: {
    label: "Not applying",
    tone: "ok",
    group: "stopped",
    explain:
      "A practice run. Nothing was sent.",
  },
  cancelled: {
    label: "Not applying",
    tone: "idle",
    group: "stopped",
    explain: "Stopped before anything was sent.",
  },
  skipped: {
    label: "Not applying",
    tone: "idle",
    group: "stopped",
    explain: "Skipped. Nothing was sent.",
  },
  closed: {
    label: "Not applying",
    tone: "idle",
    group: "stopped",
    explain: "The posting is closed or no longer available.",
  },
  // Jobs with no application attempt yet (Job.status).
  "job:new": {
    label: "To apply",
    tone: "idle",
    group: "new",
    explain:
      "Found. It is checked against your profile by itself.",
  },
  "job:ready": {
    label: "To apply",
    tone: "idle",
    group: "new",
    explain:
      "A match for your profile. A running batch applies to it by itself.",
  },
  "job:review": {
    label: "To apply",
    tone: "ask",
    group: "new",
    explain:
      "A likely match with some doubts. It is applied to only if you say so.",
  },
  "job:skipped": {
    label: "Not applying",
    tone: "idle",
    group: "new",
    explain:
      "Below your minimum fit, or ruled out by the posting.",
  },
  // A paused attempt whose job was resumed but needs ranking again: the batch
  // only picks up never-attempted jobs, so this one waits for an explicit action.
  resumed: {
    label: "Not applying",
    tone: "idle",
    group: "paused",
    explain:
      "Resumed from a pause. Rank it, then apply.",
  },
  "job:paused": {
    label: "Not applying",
    tone: "idle",
    group: "paused",
    explain: "Paused by you.",
  },
  // A stopped job the app has given up on (server word "failed": jobpilot/outlook.py). Never a backend state.
  gave_up: {
    label: "Couldn't apply",
    tone: "bad",
    group: "repair",
    explain: "The app has stopped trying.",
  },
  // A reserved employer's job (FAANG, MANGO, the Big Four) the owner has not approved yet (pipeline.py "reserved",
  // owner 2026-10-27). Whatever its latest attempt said, nothing starts it until the owner approves it.
  reserved: {
    label: "Not applying",
    tone: "ask",
    group: "reserved",
    explain:
      "A reserved employer. It applies only after you approve it.",
  },
  // Shown right after an Apply click until the next poll confirms what the server did (optimistic marks).
  "pending:waiting": {
    label: "Applying",
    tone: "hold",
    group: "active",
    explain:
      "Your request is kept. It starts by itself as soon as a worker is free.",
  },
  "pending:preparing": {
    label: "Applying",
    tone: "hold",
    group: "active",
    explain:
      "A tailored resume is being prepared. The application starts when it is ready.",
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
  word?: PipelineRow["word"];
  sentence?: string;
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
    // The app has given up on it (server word "failed"): its own entry, never the run's internal state.
    state: r.confirmed && !(run && ACTIVE.has(run.state)) ? "confirmed" : r.word === "failed" ? "gave_up" : displayState(r.job, run, control, r.request, r.reserved),
    word: r.word,
    sentence: r.sentence,
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
