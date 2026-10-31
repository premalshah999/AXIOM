// Which rung of a channel a worker is on, and what it is doing, from the run's state and its newest telling event
// (DESIGN.md, Channels). Most events are timing and model bookkeeping; these are the ones that say where a run is.
//
// The state alone only proves a floor: `running` says a browser has the job, not which page it is on, so it reads as "open"
// until an event says more, and it never claims further than the engine recorded.

export const RUNGS = ["open", "sign in", "fill", "questions", "review", "submit"] as const;

/** events.kind -> [rung, 1 to 6, and the sentence]. An `account` event's own message is used: it is already plain. */
const TELLING: Record<string, [number, string]> = {
  browser: [1, "Opening the application"],
  new_tab: [1, "Opening the application"],
  auth: [2, "Signing in to the employer's site"],
  account: [2, "Signing in to the employer's site"],
  captcha: [2, "Solving a security check"],
  email_wait: [2, "Waiting on an emailed code"],
  email_verified: [2, "Code received, continuing"],
  step: [3, "Working through the form"],
  form_page: [3, "Filling the form"],
  form_post: [3, "Moving to the next page"],
  field_error: [3, "Fixing a field the form rejected"],
  agent_step: [3, "Working through the form"],
  agent_action_result: [3, "Working through the form"],
  recovery: [3, "Recovering from a page error"],
  browser_recovery: [3, "Recovering from a page error"],
  answer_quality: [4, "Answering screening questions"],
  answer_validation: [4, "Answering screening questions"],
  knowledge_inference: [4, "Working out an answer from your profile"],
  knowledge_inference_declined: [4, "Answering screening questions"],
  verification: [5, "Rereading the answers"],
  submitting: [6, "Sending the application"],
  submission_requests: [6, "Sending the application"],
};

export type Progress = { rung: number; doing: string };

/** The newest event that says where the run is. */
export function progressFrom(events: readonly { kind: string; message?: string }[]): Progress | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    const told = TELLING[e.kind];
    if (told) return { rung: told[0], doing: e.kind === "account" && e.message ? e.message : told[1] };
  }
  return null;
}

/** The floor from the state, raised by what the events told. */
export function progressOf(state: string, told: Progress | null | undefined): Progress {
  if (state === "submitting") return { rung: 6, doing: "Sending the application" };
  if (state === "pausing") return { rung: told?.rung ?? 1, doing: "Pausing at the next safe point" };
  return told ?? { rung: 1, doing: "Applying" };
}
