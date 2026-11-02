// What can be done to a job, decided once. A table row shows the one call to action (Answer) and puts the rest in its
// overflow menu; the job drawer shows the one action as a button and the rest under More. Every action goes through the
// same endpoint as before, so every server-side guard still decides (owner 2026-10-29: no red Delete button on every row).
import { ChevronDown } from "lucide-react";
import { addToQueue, api, approveOverLimit, approveReserved, backInQueue, moveToFront, PREPARING, queueJob, removeFromQueue } from "./api";
import { useWorkspace } from "./context";
import { useAction } from "./hooks";
import { appliedState, COMMITTED, RETRYABLE, STATES, stateInfo, type JobRow } from "./model";
import type { ControlResult } from "./types";
import { Button } from "./ui/index";
import { RowMenu, type MenuAction } from "./worktools";

type Act = {
  id: string;
  label: string;
  run: () => unknown;
  /** The one call to action for this state: a row shows it as a button, the rest go in the menu. */
  lead?: boolean;
  tone?: "primary" | "danger";
  confirm?: string;
  confirmLabel?: string;
  disabled?: boolean;
};

// The owner's own verdict on an application the engine could not confirm (owner 2026-10-28: "a confirm button in
// unconfirmed applications to manually confirm applications the app was not able to detect").
export function reconcileRun(runId: string, submitted: boolean) {
  return api(`/runs/${runId}/reconcile`, "POST", {
    submitted,
    evidence: submitted
      ? "Confirmed by the applicant in the dashboard: the employer's receipt, email or account shows this application."
      : "Marked not submitted by the applicant in the dashboard.",
  });
}

function useJobActions(row: JobRow, detailed = false) {
  const { refresh, openJob, markJobs } = useWorkspace();
  const { pending, run } = useAction();
  const run_ = row.run;
  const state = row.state;
  const committed = !!run_ && COMMITTED.has(run_.state);
  // The row reads as what the server just answered (queued, paused, waiting…) at once; the lists re-read in the
  // background and confirm it. The action does not wait for that re-read.
  const settle = (result?: { state?: string }) => {
    const shown = result?.state ? appliedState(result.state) : "";
    if (shown in STATES) markJobs([row.job.id], shown);
    void refresh();
  };
  const control = (action: "pause" | "resume" | "delete") =>
    run(
      action,
      async () => {
        const result = await api<ControlResult>(
          action === "delete" ? `/jobs/${row.job.id}` : `/jobs/${row.job.id}/${action}`,
          action === "delete" ? "DELETE" : "POST",
        );
        settle(result);
        return result;
      },
      (r) => r?.message,
    );
  // A reserved employer's job: the owner's approval, then the same start as Apply.
  const approve = () =>
    run(
      "approve",
      async () => {
        const r = await approveReserved(row.job.id);
        settle(r);
        return r;
      },
      (r) => r?.message,
    );
  // The user's own reserved list: hold this company (or stop holding it); AXIOM then applies only after they approve each job.
  const reserve = () =>
    run(
      "reserve",
      async () => {
        const out = row.reserved
          ? await api("/reserved/employers?name=" + encodeURIComponent(row.reserved.employer), "DELETE")
          : await api("/reserved/employers", "POST", { text: row.job.company });
        void refresh();
        return out;
      },
      row.reserved
        ? `${row.reserved.employer} is no longer reserved. Its jobs apply like any other.`
        : `${row.job.company} is reserved. AXIOM still finds and ranks its jobs, and applies only after you approve each one.`,
    );
  const withdraw = () =>
    run(
      "withdraw",
      async () => {
        const result = await removeFromQueue(row.job.id);
        void refresh();
        return result;
      },
      "Removed from the queue. It stays out until you put it back.",
    );
  const front = () =>
    run(
      "front",
      async () => {
        const result = await moveToFront(row.job.id);
        void refresh();
        return result;
      },
      (r) => r?.message,
    );
  const putBack = () =>
    run(
      "back",
      async () => {
        const result = await backInQueue(row.job.id);
        void refresh();
        return result;
      },
      (r) => r?.message,
    );
  const verdict = (submitted: boolean) =>
    run(
      submitted ? "confirm" : "unsent",
      async () => {
        const r = await reconcileRun(run_!.id, submitted);
        markJobs([row.job.id], submitted ? "confirmed" : "needs_review");
        void refresh();
        return r;
      },
      submitted ? "Confirmed as submitted." : "Marked not sent.",
    );

  const restore = () =>
    run(
      "restore",
      async () => {
        const r = await api<ControlResult>(`/jobs/${row.job.id}/restore`, "POST");
        settle();
        return r;
      },
      (r) => r?.message,
    );

  const acts: Act[] = [];
  const needs = stateInfo(state).group === "needs";
  // A removed job does nothing but come back, and the server keeps one that may have reached the employer where it is.
  if (row.job.status === "archived") {
    if (run_?.state && COMMITTED.has(run_.state)) return { acts, busy: pending !== null, pending, protectedNote: false };
    acts.push({ id: "restore", label: "Restore", tone: "primary", run: restore });
    return { acts, busy: pending !== null, pending, protectedNote: false };
  }
  if (run_?.state === "submission_unknown")
    acts.push(
      { id: "confirm", label: "Confirm as submitted", tone: "primary", run: () => verdict(true) },
      { id: "unsent", label: "Mark not sent", run: () => verdict(false) },
    );
  else if (state === "reserved") acts.push({ id: "approve", label: "Approve and apply", tone: "primary", lead: true, run: approve });
  else if (state === "pending:waiting") acts.push({ id: "front", label: "Move to the front", tone: "primary", run: front }, { id: "withdraw", label: "Remove from the queue", run: withdraw });
  else if (state === "job:paused" || (state === "paused" && !run_)) acts.push({ id: "back", label: "Put back in the queue", tone: "primary", lead: true, run: putBack });
  else if (state === "paused") acts.push({ id: "resume", label: "Resume", tone: "primary", run: () => control("resume") });
  else if (state === "resumed") acts.push({ id: "add", label: "Put back in the queue", tone: "primary", run: () => run("add", async () => { const r = await addToQueue(row.job.id); void refresh(); return r; }, (r) => r?.message) });
  else if (state !== "pausing" && run_ && (run_.state === "queued" || run_.state === "running"))
    acts.push({ id: "pause", label: "Pause", run: () => control("pause") });
  else if (needs && !detailed) acts.push({ id: "answer", label: "Answer", lead: true, run: () => openJob(row.job.id, run_?.id) });
  else if (run_ && RETRYABLE.has(run_.state) && !needs && !committed)
    acts.push({ id: "retry", label: "Retry", tone: "primary", run: () => control("resume") });
  if (detailed && needs) acts.push({ id: "pause", label: "Pause", run: () => control("pause") });
  if (row.job.company && !row.job.demo) acts.push({ id: "reserve", label: row.reserved ? `Stop reserving ${row.reserved.employer}` : `Reserve ${row.job.company}`, run: reserve });
  const deletable = !run_ || !["confirmed", "already_applied", "submitting"].includes(run_.state);
  if (deletable && (detailed || !committed))
    acts.push({
      id: "delete",
      label: "Delete job",
      tone: "danger",
      confirm: committed
        ? "Hide this job? A sent application is not withdrawn."
        : "Delete this job? It leaves your lists, and its history is kept.",
      confirmLabel: committed ? "Hide it" : "Delete",
      run: () => control("delete"),
    });
  return {
    acts,
    busy: pending !== null,
    pending,
    // A submitted application that is not confirmed cannot be removed or retried: say so instead of showing nothing.
    protectedNote: !acts.length && committed && run_?.state !== "confirmed",
  };
}

const asMenu = (acts: Act[], busy: boolean): MenuAction[] =>
  acts.map((a) => ({
    id: a.id,
    label: a.label,
    onSelect: () => void a.run(),
    danger: a.tone === "danger",
    confirm: a.confirm,
    confirmLabel: a.confirmLabel,
    disabled: busy || a.disabled,
  }));

/**
 * The actions cell of a table row: the one lead action as a button, everything else in the overflow menu. The lead is
 * a hairline button (a screen has one primary), and a phone leaves it to the job's drawer so the status stays readable.
 */
export function RowActions({ row, promote }: { row: JobRow; promote?: string }) {
  const { acts, busy, protectedNote } = useJobActions(row);
  const lead = acts.find((a) => a.lead || a.id === promote);
  const rest = acts.filter((a) => a !== lead);
  const name = `${row.job.company || "job"}`;
  return (
    <div className="ax-act" onClick={(e) => e.stopPropagation()}>
      {lead && (
        <Button size="sm" className="ax-lead" disabled={busy} onClick={() => void lead.run()}>
          {lead.label}
        </Button>
      )}
      
      <RowMenu label={`More actions for ${name}`} actions={asMenu(rest, busy)} busy={busy} />
    </div>
  );
}

// A job with no attempt yet. One click applies (owner, 2026-10-18): no reason to type and no separate ranking step. The
// server approves the job and starts it, or keeps the request until a worker, pacing or a shared email sender allows it.
function useStartActions(row: JobRow, started: boolean, place?: { position: number } | null, hold?: { code: string } | null) {
  const { snapshot, refresh } = useWorkspace();
  const { pending, run } = useAction();
  const job = row.job;
  const ready = job.status === "ready";
  if (started || job.status === "archived" || row.state === "job:paused" || row.state === "pending:waiting" || row.state === "reserved" || snapshot.resumes.length === 0)
    return { acts: [] as Act[], pending };
  // A job the posting itself rules out: the queue would only refuse it when its turn came.
  const ruledOut = /^(?:an )?explicit hiring restriction/i.test(job.reason || "");
  const line = (id: string, go: () => Promise<{ message?: string }>) =>
    run(
      id,
      async () => {
        const r = await go();
        void refresh();
        return r;
      },
      (r) => r?.message,
    );
  const dryRun = () =>
    run(
      "dry_run",
      async () => {
        const r = await queueJob(`/jobs/${job.id}/queue`, { mode: "dry_run", resume_id: job.resume_id || undefined });
        await refresh();
        return r;
      },
      (r) => (r?.preparing ? PREPARING : r?.message || "Dry run queued. It stops before submitting."),
    );
  const rank = () =>
    run(
      "rank",
      async () => {
        const r = await api<{ score: number; eligible: boolean }>(`/jobs/${job.id}/rank`, "POST");
        await refresh();
        return r;
      },
      (r) => r && `Ranked ${r.score}/100 · ${r.eligible ? "eligible" : "below your minimum; Apply still works"}.`,
    );
  const acts: Act[] = [];
  if (place)
    acts.push(
      ...(place.position > 1 ? [{ id: "front", label: "Move to the front", tone: "primary" as const, run: () => line("front", () => moveToFront(job.id)) }] : []),
      { id: "remove", label: "Remove from the queue", run: () => line("remove", async () => ({ ...(await removeFromQueue(job.id)), message: "Removed from the queue. It stays out until you put it back." })) },
    );
  else if (hold?.code === "employer_limit") acts.push({ id: "approve", label: "Approve and apply", tone: "primary", run: () => line("approve", () => approveOverLimit(job.id)) });
  else if (!ruledOut && hold?.code !== "reserved") acts.push({ id: "add", label: "Add to the queue", tone: "primary", run: () => line("add", () => addToQueue(job.id)) });
  if (ready) acts.push({ id: "dry_run", label: "Dry run", run: dryRun });
  acts.push({ id: "rank", label: job.score == null ? "Rank" : "Rank again", run: rank });
  return { acts, pending };
}

/**
 * The job drawer's action: the one thing to do as a button, everything else under More. A question on screen is the
 * action for a job that needs an answer, so it gets no second button.
 */
export function DrawerActions({ row, started, asking, place, hold }: { row: JobRow; started: boolean; asking?: boolean; place?: { position: number } | null; hold?: { code: string } | null }) {
  const base = useJobActions(row, true);
  const start = useStartActions(row, started, place, hold);
  const busy = base.busy || start.pending !== null;
  const pending = base.pending ?? start.pending;
  // First in line already: "Move to the front" would do nothing.
  const all = [...start.acts, ...base.acts].filter((a) => !(a.id === "front" && place?.position === 1));
  const danger = all.filter((a) => a.tone === "danger");
  const normal = all.filter((a) => a.tone !== "danger");
  const lead = normal.find((a) => a.tone === "primary") ?? (asking ? undefined : normal[0]);
  const rest = [...normal.filter((a) => a !== lead), ...danger];
  if (!lead && rest.length === 0) return null;
  return (
    <div className="ax-toolbar">
      {lead && (
        <Button
          variant={lead.tone === "primary" ? "primary" : "secondary"}
          loading={pending === lead.id}
          disabled={busy}
          onClick={() => void lead.run()}
        >
          {lead.label}
        </Button>
      )}
      <RowMenu
        label="More actions"
        actions={asMenu(rest, busy)}
        busy={busy}
        trigger={() => (
          <>
            More
            <ChevronDown size={14} aria-hidden="true" />
          </>
        )}
      />
    </div>
  );
}
