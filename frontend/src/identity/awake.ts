// Whether AXIOM is actually running, for the sign. A sign that says AWAKE over a stopped machine is worse than no sign.

export type Awake = { state: "awake" | "asleep" | "unknown"; reason: string };

export function awakeFrom({
  known = true,
  workersEnabled,
  paused,
  degraded,
  batchActive,
  intakeEnabled,
  setupUnfinished,
  busy,
}: {
  /** False before the first snapshot, or once the connection is lost: nothing on screen is current. */
  known?: boolean;
  /** Snapshot config.workers_enabled. */
  workersEnabled: boolean;
  /** Snapshot control.paused. */
  paused: boolean;
  /** Snapshot scheduler_health.degraded or autopilot_runtime.scheduler.degraded. */
  degraded?: boolean;
  /** autopilot_runtime.state === "active". */
  batchActive: boolean;
  /** autopilot_runtime.intake_enabled. */
  intakeEnabled?: boolean;
  setupUnfinished?: boolean;
  /** Workers with a run right now (runtime-health workers with a run_id). */
  busy: number;
}): Awake {
  if (!known) return { state: "unknown", reason: "Can't reach AXIOM, so this may be out of date" };
  if (!workersEnabled) return { state: "asleep", reason: "Workers are turned off in this installation" };
  // Pausing stops new work; a run already in a browser finishes its page first, and the machine is not asleep until it has.
  if (busy > 0) return { state: "awake", reason: paused ? `Paused, ${busy} still finishing` : `${busy} at work` };
  if (paused) return { state: "asleep", reason: "Paused" };
  if (setupUnfinished) return { state: "asleep", reason: "Setup isn't finished" };
  if (degraded) return { state: "asleep", reason: "Automation stopped responding" };
  if (batchActive && intakeEnabled) return { state: "awake", reason: "Waiting for the next job" };
  return { state: "asleep", reason: "Nothing is running" };
}
