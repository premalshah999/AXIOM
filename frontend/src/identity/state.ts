// What the machine is doing, once, for everything that says so: Overview's sentence and key, the sidebar's status line, and
// whether vermilion is lit. One priority order, the worst true thing first.
import type { Snapshot } from "../types";

export type PanelState = "lost" | "restart" | "paused" | "complete" | "running" | "held" | "none";

/** How many applications are in a browser now, out of how many can be: from the slots themselves when they have reported. */
function capacity(snapshot: Snapshot) {
  const lanes = snapshot.autopilot_runtime?.application_workers;
  const limit = lanes ? lanes.normal_workers + lanes.specialist_workers + (lanes.custom_workers ?? 0) : Math.max(0, snapshot.config.workers || 0);
  const owners = snapshot.runs.filter((r) => r.state === "running" || r.state === "submitting").length;
  const slots = lanes ? new Set(lanes.assignments.map((a) => a.run_id).filter(Boolean)).size : 0;
  return { limit, busy: Math.max(slots, owners) };
}

/** The sidebar's one line, and how its lamp behaves. */
export function statusLine(snapshot: Snapshot, state: PanelState): { text: string; lamp: "live" | "ask" | "off" | "ok" } {
  const { busy } = capacity(snapshot);
  if (state === "lost") return { text: "Connection lost", lamp: "ask" };
  if (state === "restart") return { text: "Needs a restart", lamp: "ask" };
  if (state === "paused") return { text: "Paused", lamp: "off" };
  if (state === "complete") return { text: "Target reached", lamp: "ok" };
  if (state === "running") return { text: "Applying", lamp: "live" };
  if (state === "held") return { text: "Held", lamp: "off" };
  return busy ? { text: "Applying", lamp: "live" } : { text: "Not applying", lamp: "off" };
}

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
