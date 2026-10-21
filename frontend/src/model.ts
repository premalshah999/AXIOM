// Product semantics for run/job states. One place decides how a state reads,
// which group it belongs to and which controls are honest for it.
import type { Job, Review, Run, Snapshot } from "./types";

export type Tone = "ok" | "run" | "ask" | "hold" | "fix" | "bad" | "idle";
export type Group =
  | "active"
  | "needs"
  | "repair"
  | "unconfirmed"
  | "paused"
  | "done"
  | "stopped"
  | "new";

type StateInfo = { label: string; tone: Tone; group: Group; explain: string };

export const STATES: Record<string, StateInfo> = {
  queued: {
    label: "Queued",
    tone: "run",
    group: "active",
    explain:
      "Waiting for a free application owner. It starts automatically; nothing is needed from you.",
  },
  running: {
    label: "Applying",
    tone: "run",
    group: "active",
    explain:
      "An application owner is filling this form in its own browser tab.",
  },
  submitting: {
    label: "Submitting",
    tone: "run",
    group: "active",
    explain:
      "The final submission is in progress. It can't be paused now; the outcome is verified next.",
  },
  pausing: {
    label: "Pausing…",
    tone: "idle",
    group: "paused",
    explain:
      "Pause requested. The owner saves the draft and then releases its browser slot. Other applications keep running.",
  },
  paused: {
    label: "Paused",
    tone: "idle",
    group: "paused",
    explain:
      "Paused by you. Other applications and the batch keep running. Resume continues from the saved draft.",
  },
  waiting_answer: {
    label: "Needs your answer",
    tone: "ask",
    group: "needs",
    explain:
      "A required personal fact is missing. The browser slot was released. Your answer is saved for future applications and this job resumes automatically.",
  },
  needs_review: {
    label: "Needs your answer",
    tone: "ask",
    group: "needs",
    explain:
      "A required personal fact is missing. Answering saves it with provenance and resumes this job.",
  },
  waiting_browser: {
    label: "Repairing",
    tone: "fix",
    group: "repair",
    explain:
      "The website rejected a control or the page couldn't be read. This isn't a missing fact: the draft is kept and automatic recovery retries it.",
  },
  waiting_email: {
    label: "Waiting for email",
    tone: "hold",
    group: "active",
    explain:
      "The employer is sending a verification code or link. The browser slot was released; when the matching email arrives this application continues by itself. Nothing is needed from you.",
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
      "Submit was attempted but no trustworthy acknowledgement or receipt was captured. It may have been sent, so it is protected from any retry. Check email or record a verified outcome.",
  },
  confirmed: {
    label: "Confirmed",
    tone: "ok",
    group: "done",
    explain:
      "Confirmed by a job-specific page acknowledgement or an authenticated employer receipt. Protected from duplicate applications.",
  },
  already_applied: {
    label: "Already applied",
    tone: "ok",
    group: "done",
    explain:
      "The employer reports an existing application. Protected from applying again.",
  },
  dry_run_passed: {
    label: "Dry run passed",
    tone: "ok",
    group: "done",
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
      "Discovered but not yet ranked against your profile. Ranking happens in batches automatically.",
  },
  "job:ready": {
    label: "Eligible",
    tone: "run",
    group: "new",
    explain:
      "Ranked as a match with a selected resume. The batch applies to eligible jobs automatically.",
  },
  "job:review": {
    label: "Ranked · uncertain",
    tone: "ask",
    group: "new",
    explain:
      "Ranked as eligible with open uncertainties. It isn't applied to automatically unless autonomous mode is on.",
  },
  "job:skipped": {
    label: "Below fit",
    tone: "idle",
    group: "new",
    explain:
      "Ranked below your minimum fit or excluded by a keyword. It won't be applied to automatically.",
  },
  // A paused attempt whose job was resumed but needs ranking again: the batch
  // only picks up never-attempted jobs, so this one waits for an explicit action.
  resumed: {
    label: "Resumed · rank to apply",
    tone: "idle",
    group: "paused",
    explain:
      "Resumed from pause. This job needs ranking before a new application starts, and the batch doesn't pick it up automatically. Rank it, then apply.",
  },
  "job:paused": {
    label: "Paused",
    tone: "idle",
    group: "paused",
    explain: "Paused by you. It is held out of automatic intake.",
  },
};

export const GROUPS: { id: Group | "all"; label: string }[] = [
  { id: "all", label: "All" },
  { id: "active", label: "In progress" },
  { id: "needs", label: "Needs you" },
  { id: "repair", label: "Repairing" },
  { id: "unconfirmed", label: "Unconfirmed" },
  { id: "paused", label: "Paused" },
  { id: "done", label: "Done" },
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
  reviews: Review[];
  updated: string;
};

const PRIORITY: Record<Group, number> = {
  active: 0,
  needs: 1,
  repair: 2,
  unconfirmed: 3,
  paused: 4,
  new: 5,
  done: 6,
  stopped: 7,
};

export function displayState(
  job: Job,
  run: Run | undefined,
  control: string | undefined,
) {
  if (control === "paused" && (!run || !COMMITTED.has(run.state)))
    return run?.state === "running" ? "pausing" : "paused";
  if (run?.state === "paused") return "resumed";
  return run ? run.state : "job:" + job.status;
}

// Snapshot collections are bounded; derive per-job rows from what is present.
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

export function sortRows(rows: JobRow[]) {
  return [...rows].sort(
    (a, b) =>
      PRIORITY[stateInfo(a.state).group] - PRIORITY[stateInfo(b.state).group] ||
      b.updated.localeCompare(a.updated),
  );
}

export function evidenceLabel(r: {
  website_confirmed?: boolean;
  email_confirmed?: boolean;
  existing_application?: boolean;
}) {
  if (r.existing_application) return "Existing application";
  if (r.website_confirmed && r.email_confirmed) return "Page + email";
  if (r.email_confirmed) return "Employer email";
  return "Page acknowledgement";
}
