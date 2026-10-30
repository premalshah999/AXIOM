// Apply to several jobs at once (owner, 2026-10-22: "for unfit applications there should be an option of ticking it
// or select all, to apply to a few at once rather than opening every single one"). One request applies each job
// through the one-click path, so duplicate protection and every admission guard decide each job (POST
// /api/jobs/apply-many); the outcome of each is listed here.
import { useCallback, useMemo, useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction } from "./hooks";
import { appliedState } from "./model";
import type { ApplyManyResult, ApplyOutcome } from "./types";
import { plural, shortId } from "./format";
import { Button, Panel } from "./ui/index";
import { StatusPill } from "./worktools";

// The server applies to at most this many jobs per request (owner_apply.MANY).
export const MAX_SELECTED = 50;

export type Selection = ReturnType<typeof useSelection>;

export function useSelection(limit = MAX_SELECTED) {
  const [ids, setIds] = useState<string[]>([]);
  const chosen = useMemo(() => new Set(ids), [ids]);
  const toggle = useCallback(
    (id: string, on: boolean) =>
      setIds((current) =>
        on
          ? current.includes(id) || current.length >= limit
            ? current
            : [...current, id]
          : current.filter((x) => x !== id),
      ),
    [limit],
  );
  // Adds in order until the limit; removing takes every given id out.
  const setMany = useCallback(
    (many: string[], on: boolean) =>
      setIds((current) =>
        on
          ? [...new Set([...current, ...many])].slice(
              0,
              Math.max(limit, current.length),
            )
          : current.filter((x) => !many.includes(x)),
      ),
    [limit],
  );
  const clear = useCallback(() => setIds([]), []);
  // The table's own checkboxes hand over the whole new selection; it is cut to the limit.
  const replace = useCallback((next: ReadonlySet<string>) => setIds([...next].slice(0, limit)), [limit]);
  return {
    ids,
    chosen,
    replace,
    has: (id: string) => chosen.has(id),
    toggle,
    setMany,
    clear,
    full: ids.length >= limit,
    limit,
  };
}

const OUTCOME_LABEL: Record<ApplyOutcome, string> = {
  queued: "queued",
  waiting: "waiting to start",
  preparing: "preparing a resume",
  already: "already running or applied",
  refused: "not applied",
};

export function summarize(summary: ApplyManyResult["summary"]) {
  return (Object.keys(OUTCOME_LABEL) as ApplyOutcome[])
    .filter((key) => summary[key])
    .map((key) => `${summary[key]} ${OUTCOME_LABEL[key]}`)
    .join(" · ");
}

// Apply to the selected jobs; each job reads as queued/waiting at once and the lists confirm with the next poll.
export function useApplyMany() {
  const { markJobs, refresh } = useWorkspace();
  const { pending, run } = useAction();
  const [last, setLast] = useState<ApplyManyResult | null>(null);
  const apply = useCallback(
    async (ids: string[]) => {
      if (!ids.length) return undefined;
      const answer = await run(
        "apply-many",
        () =>
          api<ApplyManyResult>("/jobs/apply-many", "POST", {
            job_ids: ids.slice(0, MAX_SELECTED),
          }),
        (r) =>
          r
            ? `Applied to ${plural(r.results.length, "job")}: ${summarize(r.summary)}.`
            : undefined,
      );
      if (answer) {
        for (const outcome of ["queued", "waiting", "preparing"] as const) {
          const started = answer.results
            .filter((r) => r.outcome === outcome)
            .map((r) => r.job_id);
          if (started.length) markJobs(started, appliedState(outcome));
        }
        setLast(answer);
        void refresh();
      }
      return answer;
    },
    [run, markJobs, refresh],
  );
  return {
    apply,
    applying: pending === "apply-many",
    last,
    dismiss: () => setLast(null),
  };
}

// What a waiting job waits for, by the admission code that deferred it (owner_apply.start).
const WAITS_FOR: Record<string, string> = {
  worker_capacity: "a free worker",
  ats_pacing: "the platform's pacing",
  sender_busy: "a shared email sender",
  submission_disabled: "automatic submission to be turned on",
  employer_cooldown: "the employer's cooldown",
  site_circuit: "the employer's site to recover",
  provider_unavailable: "the model provider",
  deployment_hold: "the deployment hold to end",
  company_limit: "the employer limit",
};

// What happened to the last "Apply to selected": refusals one by one (they need a look), waits summed up by what
// they wait for, each job behind "Show each job". Fifty "waiting to start" rows pushed the list off the screen.
export function ApplyOutcomes({
  result,
  onClose,
}: {
  result: ApplyManyResult;
  onClose: () => void;
}) {
  const { openJob } = useWorkspace();
  const refused = result.results.filter((r) => r.outcome === "refused");
  const others = result.results.filter((r) => r.outcome !== "refused" && r.outcome !== "queued");
  const waits = new Map<string, number>();
  for (const r of result.results)
    if (r.outcome === "waiting") {
      const what = WAITS_FOR[r.code || ""] || "admission";
      waits.set(what, (waits.get(what) || 0) + 1);
    }
  const row = (r: ApplyManyResult["results"][number]) => (
    <li key={r.job_id} className="ax-row">
      <span className="ax-row-main">
        <button type="button" className="ax-link" onClick={() => openJob(r.job_id)}>
          {r.company || r.title ? `${r.company || "Employer"} · ${r.title || ""}` : shortId(r.job_id)}
        </button>
        {r.message && <small>{r.message}</small>}
      </span>
      <StatusPill
        state={r.outcome === "refused" ? "failed" : r.outcome === "already" ? r.state : appliedState(r.outcome)}
        label={r.outcome === "refused" ? "Not applied" : undefined}
      />
    </li>
  );
  return (
    <Panel
      title={`Applied to ${plural(result.results.length, "job")}`}
      actions={
        <>
          <span className="ax-note">{summarize(result.summary)}</span>
          <Button size="sm" variant="ghost" onClick={onClose}>
            Dismiss
          </Button>
        </>
      }
    >
      <div className="dr-stack">
        {waits.size > 0 && (
          <p className="ax-note">
            {[...waits].map(([what, n]) => `${n} wait for ${what}`).join(" · ")}. They start on their own; follow them in
            Pipeline.
          </p>
        )}
        {refused.length > 0 && (
          <ul className="ax-rows" aria-label="Jobs not applied to">
            {refused.map(row)}
          </ul>
        )}
        {others.length > 0 && (
          <details className="ax-fold">
            <summary>Show each job ({others.length})</summary>
            <ul className="ax-rows">{others.map(row)}</ul>
          </details>
        )}
      </div>
    </Panel>
  );
}
