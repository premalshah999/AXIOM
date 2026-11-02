// Overview's Best matches (owner, 2026-11-01: "on the dashboard, maybe make a list of most matched applications"): the board's top fits
// not yet applied to, with the one thing to do about each. The same rows as the Job board's first page, so a job reads the same on both.
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { addToQueue, api } from "./api";
import { useWorkspace } from "./context";
import { useRowActions, usePoll } from "./hooks";
import { MoreLink } from "./identity/Results";
import type { BoardPage, BoardRow } from "./types";
import { Button, Empty, Monogram, Panel, Skeleton } from "./ui/index";

const SHOWN = 8;

export function BestMatches() {
  const { snapshot, go, openJob, version, refresh } = useWorkspace();
  const { busy, act } = useRowActions();
  const list = usePoll<BoardPage>((signal) => api(`/board?fit=match&sort=fit&group=1&size=${SHOWN}&want=rows`, "GET", undefined, signal), 60000, [version]);
  const [asked, setAsked] = useState<ReadonlySet<string>>(() => new Set());
  const rows = list.data?.rows ?? [];
  // The first read after a launch is still being prepared: a 60 second poll left the panel on a skeleton for a minute.
  const planning = !!list.data?.planning;
  const reread = list.refresh;
  useEffect(() => {
    if (!planning) return;
    const timer = window.setTimeout(reread, 1500);
    return () => window.clearTimeout(timer);
  }, [planning, reread, list.data]);
  const reviewing = snapshot.config.apply_mode !== "auto";
  const queue = async (r: BoardRow) => {
    setAsked((s) => new Set(s).add(r.id));
    const out = await act(r.id, () => addToQueue(r.id), (x) => x.message);
    if (!out)
      setAsked((s) => {
        const next = new Set(s);
        next.delete(r.id);
        return next;
      });
    void list.refresh();
    void refresh();
  };
  const action = (r: BoardRow) => {
    if (asked.has(r.id) && r.state !== "queue") return <span className="ov-match-state">In queue</span>;
    if (r.state === "queue") return <span className="ov-match-state">In queue <b className="mono">{r.position}</b></span>;
    if (r.state === "approval")
      return (
        <Button size="sm" onClick={() => go({ view: "needs", filter: r.approval || "reserved" })}>
          Review
        </Button>
      );
    if (r.can_add)
      return (
        <Button size="sm" loading={busy.has(r.id)} icon={<Plus size={13} aria-hidden="true" />} onClick={() => void queue(r)}>
          Queue
        </Button>
      );
    return null;
  };
  return (
    <Panel
      title="Best matches"
      flush
      actions={
        reviewing ? <MoreLink onClick={() => go({ view: "review" })}>Review a batch</MoreLink> : list.data?.total ? <MoreLink onClick={() => go({ view: "board" })}>All {list.data.total.toLocaleString()}</MoreLink> : undefined
      }
    >
      {!list.data ? (
        <div className="ov-pad">
          <Skeleton rows={4} />
        </div>
      ) : list.data.planning ? (
        <div className="ov-pad">
          <Skeleton rows={4} />
        </div>
      ) : rows.length ? (
        <ul className="ov-list ov-matches">
          {rows.map((r) => (
            <li key={r.id}>
              <button type="button" className="ov-match" onClick={() => openJob(r.id)}>
                <Monogram name={r.company} url={r.url} employer />
                <span className="ov-match-who">
                  <b title={r.title}>{r.title}</b>
                  <span title={`${r.company}${r.location ? " · " + r.location : ""}`}>
                    {r.company}
                    {r.location ? ` · ${r.location}` : ""}
                  </span>
                </span>
                <span className="ov-match-why" title={r.line}>
                  {r.line}
                </span>
                <span className="ov-match-fit mono" title="How well it fits your profile, out of 100">
                  {r.score ?? "–"}
                </span>
              </button>
              <span className="ov-match-act">{action(r)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <Empty compact title="No matches yet" hint="The best fits appear here once jobs are found and checked against your profile." />
      )}
    </Panel>
  );
}
