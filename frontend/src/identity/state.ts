// What the machine is doing, once, for everything that says so: Overview's sentence and key, the overview station's readout,
// and whether the plate is live. One priority order, the worst true thing first.
import type { Snapshot } from "../types";

type PanelState = "lost" | "restart" | "paused" | "complete" | "running" | "held" | "none";

export const STATE_WORD: Record<PanelState, string> = {
  lost: "no connection",
  restart: "needs a restart",
  paused: "paused",
  complete: "target reached",
  running: "running",
  held: "held",
  none: "no batch",
};

/**
 * Confirmations since local midnight, as epoch ms. A notice that the employer already had an application is not one AXIOM just
 * sent, so it is not counted. Overview's count and chart and Applications' "today" all read this.
 */
export function confirmedToday(applications: readonly { applied_at: string; existing_application?: boolean }[], now: number): number[] {
  const midnight = new Date(now).setHours(0, 0, 0, 0);
  return applications
    .filter((a) => !a.existing_application)
    .map((a) => Date.parse(a.applied_at))
    .filter((t) => t >= midnight && t <= now);
}

export function panelState(snapshot: Snapshot, lost: boolean): PanelState {
  const rt = snapshot.autopilot_runtime || {};
  const state = rt.state || snapshot.autopilot?.state || "idle";
  if (lost) return "lost";
  if (rt.scheduler?.degraded || snapshot.scheduler_health?.degraded) return "restart";
  if (snapshot.control.paused) return "paused";
  if (state === "complete") return "complete";
  if (rt.intake_enabled) return "running";
  if (state === "active") return "held";
  return "none";
}

/** Whether vermilion may say "alive": applying now, or a run you started is in a browser. Paused and broken are not live. */
export function isLive(snapshot: Snapshot, state: PanelState): boolean {
  if (state === "lost" || state === "restart" || state === "paused") return false;
  return state === "running" || snapshot.runs.some((r) => r.state === "running" || r.state === "submitting");
}
