import { useEffect, useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction } from "./hooks";
import type { Selection } from "./bulk";
import { reconcileRun } from "./jobactions";
import { appliedState, type Group, type JobRow, type Word } from "./model";
import { plural } from "./format";
import { Button } from "./ui/index";
import { ConfirmButton } from "./worktools";

// Every match on any page, a page at a time from the server. Job id -> its latest run, for every row seen here (Confirm needs the run).
export type KnownRuns = Record<string, { id?: string; state?: string } | undefined>;

export function useSelectMatching(selection: Selection, rows: JobRow[], filter: Group | Word | "all", query: string, total: number) {
  const { pending, run } = useAction();
  const [known, setKnown] = useState<KnownRuns>({});
  useEffect(() => {
    setKnown((k) => ({ ...k, ...Object.fromEntries(rows.map((r) => [r.job.id, r.run ? { id: r.run.id, state: r.run.state } : undefined])) }));
  }, [rows]);
  const selectAll = () =>
    run(
      "select",
      async () => {
        const found: { job: { id: string }; run?: { id: string; state: string } | null }[] = [];
        for (let page = 1; found.length < Math.min(total, selection.limit); page += 1) {
          const params = new URLSearchParams({ group: filter, q: query, page: String(page), size: "200" });
          const part = await api<{ rows: { job: { id: string }; run?: { id: string; state: string } | null }[] }>(`/jobs?${params}`);
          found.push(...part.rows);
          if (part.rows.length < 200) break;
        }
        setKnown((k) => ({ ...k, ...Object.fromEntries(found.map((r) => [r.job.id, r.run ? { id: r.run.id, state: r.run.state } : undefined])) }));
        selection.replace(new Set(found.map((r) => r.job.id)));
      },
    );
  return { known, selectAll, selecting: pending === "select" };
}

// Act on ticked jobs: retry, pause, confirm or remove them together. Each job goes through the same endpoint as its own menu item, so
// every guard still decides each one.
export function PipelineBulk({
  selection,
  known,
}: {
  selection: Pick<Selection, "ids" | "clear">;
  known: KnownRuns;
}) {
  const { markJobs, refresh } = useWorkspace();
  const { pending, run } = useAction();
  const n = selection.ids.length;
  const busy = pending !== null;
  // Each job's own answer decides: only the ones the server took read as changed.
  const each = async (ids: string[], one: (id: string) => Promise<unknown>) => {
    const ok: string[] = [];
    for (let i = 0; i < ids.length; i += 4) {
      const part = ids.slice(i, i + 4);
      const done = await Promise.allSettled(part.map(one));
      done.forEach((d, at) => d.status === "fulfilled" && ok.push(part[at]!));
    }
    return ok;
  };
  const finish = (ids: string[], state?: string) => {
    if (state && ids.length) markJobs(ids, state);
    selection.clear();
    void refresh();
  };
  const retry = () =>
    run(
      "retry",
      async () => {
        const ids = [...selection.ids];
        const results: { job_id: string; outcome: string }[] = [];
        for (let i = 0; i < ids.length; i += 50)
          results.push(...(await api<{ results: { job_id: string; outcome: string }[] }>("/jobs/apply-many", "POST", { job_ids: ids.slice(i, i + 50) })).results);
        // "already" is a job already sent or running: nothing started, and only what started reads as started.
        for (const outcome of ["queued", "waiting", "preparing"] as const) {
          const started = results.filter((x) => x.outcome === outcome).map((x) => x.job_id);
          if (started.length) markJobs(started, appliedState(outcome));
        }
        finish([]);
        return {
          started: results.filter((x) => x.outcome === "queued" || x.outcome === "waiting" || x.outcome === "preparing").length,
          already: results.filter((x) => x.outcome === "already").length,
          refused: results.filter((x) => x.outcome === "refused").length,
        };
      },
      (r) =>
        r &&
        [
          `Started ${plural(r.started, "job")} again.`,
          r.already ? `${plural(r.already, "was", "were")} already sent or running, so nothing happened to ${r.already === 1 ? "it" : "them"}.` : "",
          r.refused ? `${plural(r.refused, "job")} couldn't start: open ${r.refused === 1 ? "it" : "them"} to see why.` : "",
        ]
          .filter(Boolean)
          .join(" "),
    );
  const pause = () =>
    run(
      "pause",
      async () => {
        const ok = await each([...selection.ids], (id) => api(`/jobs/${id}/pause`, "POST"));
        finish(ok, "job:paused");
        return ok.length;
      },
      (ok) => `Paused ${plural(ok ?? 0, "job")}.`,
    );
  const remove = () =>
    run(
      "remove",
      async () => {
        const ok = await each([...selection.ids], (id) => api(`/jobs/${id}`, "DELETE"));
        finish([]);
        return ok.length;
      },
      (ok) => `Removed ${plural(ok ?? 0, "job")}. Their history is kept.`,
    );
  const unknown = selection.ids.filter((id) => known[id]?.state === "submission_unknown");
  const verdict = (submitted: boolean) =>
    run(
      submitted ? "confirm" : "unsent",
      async () => {
        // Only an application that may have been sent has a verdict to record; the rest of a selection stays as it is.
        const ok = await each(unknown, (id) => reconcileRun(known[id]!.id!, submitted));
        finish(ok, submitted ? "confirmed" : "needs_review");
        return ok.length;
      },
      (ok) => `${submitted ? "Confirmed" : "Marked not sent:"} ${plural(ok ?? 0, "application")}.`,
    );
  return (
    <>
      {unknown.length > 0 && (
        <>
          <Button size="sm" variant="primary" disabled={busy} loading={pending === "confirm"} onClick={() => verdict(true)}>
            {unknown.length === n ? "Confirm submitted" : `Confirm ${unknown.length} as submitted`}
          </Button>
          <Button size="sm" disabled={busy} loading={pending === "unsent"} onClick={() => verdict(false)}>
            {unknown.length === n ? "Not sent" : `${unknown.length} not sent`}
          </Button>
        </>
      )}
      <ConfirmButton
        label="Retry"
        question={`Apply to ${plural(n, "job")} again? AXIOM sends a real application to each, in its turn. One already sent is never sent twice.`}
        confirmLabel="Retry"
        loading={pending === "retry"}
        disabled={busy && pending !== "retry"}
        onConfirm={retry}
      />
      <Button size="sm" disabled={busy} loading={pending === "pause"} onClick={pause}>
        Pause
      </Button>
      <ConfirmButton
        label="Remove"
        question={`Remove ${plural(n, "job")}? Sent applications are not withdrawn.`}
        confirmLabel="Remove"
        loading={pending === "remove"}
        disabled={busy && pending !== "remove"}
        onConfirm={remove}
      />
    </>
  );
}
