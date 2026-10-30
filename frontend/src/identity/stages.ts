// How far an application has got, for the six-pane fanlight (DESIGN.md, Stages). Light means work: panes up to the stage are
// lit, the stage's own pane says how that step is going, and the rest are dark.
//
// A state alone only proves a lower bound. `running` says a browser has the job, not which page it is on, so it reads as
// Opened until the run's own events prove more (fanFor's second argument). A window never shows further than is known.
// tests/test_identity_stages.py keeps BY_STATE and model.ts's STATES in step, so a new state cannot slip past this table.

export const STAGE_NAMES = ["Found", "Ranked", "Opened", "Filling", "Sent", "Confirmed"] as const;
export type Stage = 0 | 1 | 2 | 3 | 4 | 5 | 6;

/** work: under way. ask: waits on the owner. fix: being repaired. unc: sent, no receipt. done: confirmed. stop: ended. idle: not moving. */
export type FanTone = "work" | "ask" | "fix" | "unc" | "done" | "stop" | "idle";
export type FanState = { stage: Stage; tone: FanTone; label: string };

const BY_STATE: Record<string, [Stage, FanTone]> = {
  // A job with no attempt yet.
  "job:new": [1, "idle"],
  "job:skipped": [1, "idle"],
  "job:ready": [2, "idle"],
  "job:review": [2, "ask"],
  "job:paused": [2, "idle"],
  reserved: [2, "ask"],
  // Waiting for a worker: ranked, and next in line.
  queued: [2, "work"],
  "pending:waiting": [2, "work"],
  "pending:preparing": [2, "work"],
  // A browser has it.
  running: [3, "work"],
  waiting_email: [3, "work"],
  pausing: [3, "idle"],
  paused: [3, "idle"],
  resumed: [3, "idle"],
  waiting_browser: [3, "fix"],
  timed_out: [3, "fix"],
  failed: [3, "fix"],
  // A question only comes from reading the form, so these got at least that far.
  waiting_answer: [4, "ask"],
  needs_review: [4, "ask"],
  // A dry run fills every page and stops before sending.
  dry_run_passed: [4, "done"],
  submitting: [5, "work"],
  submission_unknown: [5, "unc"],
  confirmed: [6, "done"],
  already_applied: [6, "done"],
  // Ended before anything was sent: the shutters are down.
  cancelled: [0, "stop"],
  skipped: [0, "stop"],
  closed: [0, "stop"],
};

// Run events that prove a stage, by events.kind. Only what the engine itself recorded raises a window above its state's floor.
const PROVES: Record<string, Stage> = {
  browser: 3,
  new_tab: 3,
  auth: 3,
  account: 3,
  captcha: 3,
  email_wait: 3,
  form_page: 4,
  form_post: 4,
  verification: 4,
  answer_quality: 4,
  agent_step: 4,
  agent_action_result: 4,
  field_error: 4,
  submitting: 5,
  submission_requests: 5,
};
// A run in one of these can be anywhere between opening the page and sending, so its events can move it forward.
const OPEN_ENDED = new Set([
  "running",
  "waiting_email",
  "pausing",
  "paused",
  "resumed",
  "waiting_browser",
  "timed_out",
  "failed",
  "waiting_answer",
  "needs_review",
]);

function describe(stage: Stage, tone: FanTone): string {
  const at = stage ? `stage ${stage} of 6: ${STAGE_NAMES[stage - 1]}` : "";
  switch (tone) {
    case "work":
      return `Stage ${stage} of 6: ${STAGE_NAMES[stage - 1]}`;
    case "ask":
      return "Waiting on you at " + at;
    case "fix":
      return "Being repaired at " + at;
    case "unc":
      return "Sent, no receipt yet";
    case "done":
      return stage === 6 ? "Confirmed" : "Stopped before sending, as a dry run does";
    case "stop":
      return "Stopped, nothing sent";
    case "idle":
      return stage ? "Not moving, at " + at : "Not started";
  }
}

/** The fanlight for a display state (model.ts's displayState), raised by the kinds of the run's events when you have them. */
export function fanFor(state: string, eventKinds?: readonly string[]): FanState {
  const known = BY_STATE[state];
  // Nothing about an unfamiliar state is known, so nothing is lit.
  if (!known) return { stage: 0, tone: "idle", label: "Not started" };
  let [stage] = known;
  const tone = known[1];
  if (eventKinds && OPEN_ENDED.has(state))
    for (const kind of eventKinds) {
      const proved = PROVES[kind];
      if (proved && proved > stage) stage = proved;
    }
  return { stage, tone, label: describe(stage, tone) };
}

// What a worker is doing, in plain words, from the newest event that says. Most events are timing and model bookkeeping.
const DOING: Record<string, string> = {
  browser: "Opening the application",
  new_tab: "Opening the application",
  auth: "Signing in to the employer's site",
  captcha: "Solving a security check",
  email_wait: "Waiting on an emailed code",
  email_verified: "Code received, continuing",
  form_page: "Filling the form",
  form_post: "Moving to the next page",
  verification: "Rereading the answers",
  submitting: "Sending the application",
  recovery: "Recovering from a page error",
  browser_recovery: "Recovering from a page error",
};

type DoingEvent = { kind: string; message?: string };

/** The sentence in a window. Without events it says only what the state proves. */
export function doingFor(state: string, events?: readonly DoingEvent[]): string {
  if (state === "submitting") return DOING.submitting;
  if (state === "waiting_email") return DOING.email_wait;
  for (let i = (events?.length ?? 0) - 1; i >= 0; i--) {
    const e = events![i];
    // The engine's account messages are already plain and carry no personal data ("Creating an employer account").
    if (e.kind === "account" && e.message) return e.message;
    if (DOING[e.kind]) return DOING[e.kind];
  }
  return "Applying";
}
