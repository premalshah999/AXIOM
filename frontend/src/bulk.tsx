// Apply to several jobs at once (owner, 2026-10-22: "for unfit applications there should be an option of ticking it
// or select all, to apply to a few at once rather than opening every single one"). One request applies each job
// through the one-click path, so duplicate protection and every admission guard decide each job (POST
// /api/jobs/apply-many); the outcome of each is listed here.
import { useCallback, useMemo, useState } from "react";
import { api, type QueueMany } from "./api";
import { useWorkspace } from "./context";
import { useAction } from "./hooks";
import { appliedState } from "./model";
import type { ApplyManyResult, ApplyOutcome } from "./types";
import { plural, shortId } from "./format";
import { Button, Dialog, Panel } from "./ui/index";
import { StatusPill } from "./worktools";

// The server applies to at most this many jobs per request (owner_apply.MANY).
const MAX_SELECTED = 50;

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

function summarize(summary: ApplyManyResult["summary"]) {
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
  worker_capacity: "a free moment",
  ats_pacing: "the site to allow another",
  sender_busy: "a shared email sender",
  submission_disabled: "sending to be turned on",
  employer_cooldown: "the employer's cooldown",
  site_circuit: "the employer's site to recover",
  provider_unavailable: "the model provider",
  deployment_hold: "the deployment hold to end",
  company_limit: "the employer limit",
  daily_limit: "today's limit to reset",
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

// Said before anything joins the line, because each of these becomes a real application to a real employer. With sending off the
// jobs would only wait, so that is said too, with the way to turn it on in the same press.
export function ConfirmQueue({ open, count, onClose, onConfirm, title, warning }: { open: boolean; count: number; onClose: () => void; onConfirm: () => void; title?: string; warning?: string }) {
  const { snapshot, refresh } = useWorkspace();
  const { pending, run } = useAction();
  const sending = snapshot.control.auto_submit;
  const noun = plural(count, "job");
  const sendOn = () =>
    run("send-on", async () => {
      // Only what changes: writing `paused` back from a read is how a stop gets lifted by accident.
      await api("/control", "PUT", { auto_submit: true });
      await refresh();
      onConfirm();
    });
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title ?? `Queue ${noun}?`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Not yet
          </Button>
          {sending ? (
            <Button variant="primary" onClick={onConfirm}>
              {`Queue ${noun}`}
            </Button>
          ) : (
            <>
              <Button onClick={onConfirm} disabled={pending !== null}>
                Queue only
              </Button>
              <Button variant="primary" loading={pending === "send-on"} disabled={pending !== null} onClick={sendOn}>
                Queue and turn sending on
              </Button>
            </>
          )}
        </>
      }
    >
      {warning && <strong>{warning} </strong>}
      AXIOM sends a real application to each of these employers, in its turn, using your profile and resume. You can take any of them out of the Queue before it starts.
      {!sending && " Sending is off in Settings, so with Queue only they wait there until you turn it on."}
    </Dialog>
  );
}

// What couldn't join the line, and why, until dismissed. The reasons are the server's sentences, each with what to do.
export function Refused({ refused, onClose }: { refused: QueueMany["refused"]; onClose: () => void }) {
  const { openJob, go } = useWorkspace();
  if (!refused.length) return null;
  const row = (r: QueueMany["refused"][number]) => (
    <li key={r.job_id} className="ax-row">
      <span className="ax-row-main">
        <button type="button" className="ax-link" onClick={() => openJob(r.job_id)}>
          {r.company || r.title ? `${r.company || "Employer"} · ${r.title || ""}` : shortId(r.job_id)}
        </button>
        <small>{r.reason}</small>
      </span>
    </li>
  );
  const first = refused.slice(0, 5);
  const rest = refused.slice(5);
  return (
    <Panel
      title={`${plural(refused.length, "job")} couldn't be queued`}
      actions={
        <>
          {refused.some((r) => r.reason.includes("Needs you")) && (
            <Button size="sm" onClick={() => go({ view: "needs" })}>
              Open Needs you
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={onClose}>
            Dismiss
          </Button>
        </>
      }
    >
      <ul className="ax-rows" aria-label="Jobs that couldn't be queued">
        {first.map(row)}
      </ul>
      {rest.length > 0 && (
        <details className="ax-fold">
          <summary>{`Show the other ${rest.length.toLocaleString()}`}</summary>
          <ul className="ax-rows">{rest.map(row)}</ul>
        </details>
      )}
    </Panel>
  );
}
