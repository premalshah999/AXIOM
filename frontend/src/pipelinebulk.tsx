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
export function useSelectMatching(selection: Selection, rows: JobRow[], filter: Group | Word | "all", query: string, total: number) {
  const { pending, run } = useAction();
  const [known, setKnown] = useState<Record<string, string | undefined>>({});
  useEffect(() => {
    setKnown((k) => ({ ...k, ...Object.fromEntries(rows.map((r) => [r.job.id, r.run?.id])) }));
  }, [rows]);
  const selectAll = () =>
    run(
      "select",
      async () => {
        const found: { job: { id: string }; run?: { id: string } | null }[] = [];
        for (let page = 1; found.length < Math.min(total, selection.limit); page += 1) {
          const params = new URLSearchParams({ group: filter, q: query, page: String(page), size: "200" });
          const part = await api<{ rows: { job: { id: string }; run?: { id: string } | null }[] }>(`/jobs?${params}`);
          found.push(...part.rows);
          if (part.rows.length < 200) break;
        }
        setKnown((k) => ({ ...k, ...Object.fromEntries(found.map((r) => [r.job.id, r.run?.id])) }));
        selection.replace(new Set(found.map((r) => r.job.id)));
      },
    );
  return { known, selectAll, selecting: pending === "select" };
}

// Act on ticked jobs: retry, pause, confirm or remove them together. Each job goes through the same endpoint as its own menu item, so
// every guard still decides each one.
export function PipelineBulk({
  selection,
  rows,
  filter,
  known,
}: {
  selection: Selection;
  rows: JobRow[];
  filter: Group | Word | "all";
  known: Record<string, string | undefined>;
}) {
  const { markJobs, refresh } = useWorkspace();
  const { pending, run } = useAction();
  const n = selection.ids.length;
  const busy = pending !== null;
  const each = async (ids: string[], one: (id: string) => Promise<unknown>) => {
    let ok = 0;
    for (let i = 0; i < ids.length; i += 4) {
      const done = await Promise.allSettled(ids.slice(i, i + 4).map(one));
      ok += done.filter((d) => d.status === "fulfilled").length;
    }
    return ok;
  };
  const finish = (ids: string[], state?: string) => {
    if (state) markJobs(ids, state);
    selection.clear();
    void refresh();
  };
  const retry = () =>
    run(
      "retry",
      async () => {
        const ids = [...selection.ids];
        let started = 0;
        for (let i = 0; i < ids.length; i += 50) {
          const r = await api<{ results: { outcome: string }[] }>("/jobs/apply-many", "POST", {
            job_ids: ids.slice(i, i + 50),
          });
          started += r.results.filter((x) => x.outcome !== "refused").length;
        }
        finish(ids, appliedState("queued"));
        return started;
      },
      (started) => `Started ${plural(started ?? 0, "job")} again.`,
    );
  const pause = () =>
    run(
      "pause",
      async () => {
        const ids = [...selection.ids];
        const ok = await each(ids, (id) => api(`/jobs/${id}/pause`, "POST"));
        finish(ids, "job:paused");
        return ok;
      },
      (ok) => `Paused ${plural(ok ?? 0, "job")}.`,
    );
  const remove = () =>
    run(
      "remove",
      async () => {
        const ids = [...selection.ids];
        const ok = await each(ids, (id) => api(`/jobs/${id}`, "DELETE"));
        finish(ids);
        return ok;
      },
      (ok) => `Removed ${plural(ok ?? 0, "job")}. Their history is kept.`,
    );
  const verdict = (submitted: boolean) =>
    run(
      submitted ? "confirm" : "unsent",
      async () => {
        const ids = selection.ids.filter((id) => known[id]);
        const ok = await each(ids, (id) => reconcileRun(known[id]!, submitted));
        finish(ids, submitted ? "confirmed" : "needs_review");
        return ok;
      },
      (ok) => `${submitted ? "Confirmed" : "Marked not sent:"} ${plural(ok ?? 0, "application")}.`,
    );
  return (
    <>
      {(filter === "unconfirmed" || rows.some((r) => r.run?.state === "submission_unknown")) && (
        <>
          <Button size="sm" variant="primary" disabled={busy} loading={pending === "confirm"} onClick={() => verdict(true)}>
            Confirm submitted
          </Button>
          <Button size="sm" disabled={busy} loading={pending === "unsent"} onClick={() => verdict(false)}>
            Not sent
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
