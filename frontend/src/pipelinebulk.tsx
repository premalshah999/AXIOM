import { useEffect, useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction } from "./hooks";
import type { Selection } from "./bulk";
import { reconcileRun } from "./jobactions";
import { appliedState, type Group, type JobRow } from "./model";
import { plural } from "./ui";
import { Button } from "./ui/index";
import { ConfirmButton } from "./worktools";

// Act on ticked jobs: a page or every match (up to 200), then retry, pause, confirm or remove them together. Each job
// goes through the same endpoint as its own menu item, so every guard still decides each one.
export function PipelineBulk({
  selection,
  rows,
  filter,
  query,
  total,
}: {
  selection: Selection;
  rows: JobRow[];
  filter: Group | "all";
  query: string;
  total: number;
}) {
  const { markJobs, refresh } = useWorkspace();
  const { pending, run } = useAction();
  // Job id -> its latest run, for every row seen here (Confirm needs the run).
  const [known, setKnown] = useState<Record<string, string | undefined>>({});
  useEffect(() => {
    setKnown((k) => ({ ...k, ...Object.fromEntries(rows.map((r) => [r.job.id, r.run?.id])) }));
  }, [rows]);
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
  const selectMatching = () =>
    run(
      "select",
      async () => {
        const params = new URLSearchParams({ group: filter, q: query, page: "1", size: "200" });
        const page = await api<{ rows: { job: { id: string }; run?: { id: string } | null }[] }>(`/jobs?${params}`);
        setKnown((k) => ({ ...k, ...Object.fromEntries(page.rows.map((r) => [r.job.id, r.run?.id])) }));
        selection.setMany(page.rows.map((r) => r.job.id), true);
        return page.rows.length;
      },
      (count) => (count ? `Selected ${plural(count, "job")}.` : undefined),
    );
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
      {total > rows.length && (
        <Button size="sm" variant="ghost" disabled={busy} loading={pending === "select"} onClick={selectMatching}>
          {total > 200 ? "Select the first 200" : `Select all ${total.toLocaleString()}`}
        </Button>
      )}
      {filter === "unconfirmed" && (
        <>
          <Button size="sm" variant="primary" disabled={busy} loading={pending === "confirm"} onClick={() => verdict(true)}>
            Confirm submitted
          </Button>
          <Button size="sm" disabled={busy} loading={pending === "unsent"} onClick={() => verdict(false)}>
            Not sent
          </Button>
        </>
      )}
      <Button size="sm" disabled={busy} loading={pending === "retry"} onClick={retry}>
        Retry
      </Button>
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
