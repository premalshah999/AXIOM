// From the real API to the identity's props, so Overview only has to fetch.
//
//   GET /api/runtime-health       application_workers: normal_workers + specialist_workers + custom_workers = slots;
//                                 workers[]: { slot, lane, state, phase (today the same as state), run_id, job_id, ats }
//   GET /api/snapshot             jobs[], runs[] (started_at, state), control.paused, config.workers_enabled,
//                                 autopilot_runtime.{state, intake_enabled, scheduler}, scheduler_health
//   GET /api/runs/{id}            events[]: { kind, message } for what a worker is doing and how far it has got
//   GET /api/successful-applications  applications[]: { applied_at, company, title, existing_application }
import type { FacadeWorker } from "./Facade";
import type { Confirmation } from "./SkyChart";
import { doingFor, fanFor } from "./stages";

type ApiWorker = { slot: number; state?: string; run_id?: string | null; job_id?: string | null };
type ApiWorkers = { normal_workers: number; specialist_workers: number; custom_workers?: number; workers?: ApiWorker[]; assignments?: ApiWorker[] };
type ApiJob = { id: string; company?: string; title?: string };
type ApiRun = { id: string; state: string; started_at?: string | null; created_at?: string };
type ApiEvent = { kind: string; message?: string };

export function slotCount(workers: ApiWorkers | undefined, fallback = 0): number {
  return workers ? workers.normal_workers + workers.specialist_workers + (workers.custom_workers ?? 0) : fallback;
}

/** The slots that have a run. A job the snapshot has not caught up with says so instead of inventing a name. */
export function facadeWorkers(
  workers: ApiWorkers | undefined,
  jobs: readonly ApiJob[],
  runs: readonly ApiRun[],
  eventsByRun?: Readonly<Record<string, readonly ApiEvent[]>>,
): FacadeWorker[] {
  const jobById = new Map(jobs.map((j) => [j.id, j]));
  const runById = new Map(runs.map((r) => [r.id, r]));
  return (workers?.workers ?? workers?.assignments ?? [])
    .filter((w) => w.run_id)
    .map((w) => {
      const run = runById.get(w.run_id!);
      const job = w.job_id ? jobById.get(w.job_id) : undefined;
      const state = run?.state ?? w.state ?? "running";
      const events = eventsByRun?.[w.run_id!];
      return {
        id: w.run_id!,
        slot: w.slot,
        jobId: w.job_id,
        company: job?.company || "An employer",
        role: job?.title || "Details loading",
        doing: doingFor(state, events),
        startedAt: run?.started_at ?? run?.created_at ?? null,
        fan: fanFor(state, events?.map((e) => e.kind)),
      };
    });
}

/**
 * Confirmations for the sky. A notice that the employer already had an application is not one AXIOM just sent, so it is not
 * a dot; the page's "confirmed today" must count the same list.
 */
export function confirmations(
  applications: readonly { applied_at: string; company?: string; title?: string; existing_application?: boolean }[],
): Confirmation[] {
  return applications.filter((a) => !a.existing_application).map((a) => ({ at: a.applied_at, company: a.company, title: a.title }));
}
