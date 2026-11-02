// Review a batch (owner, 2026-11-01: "add a batch of jobs for review before applying, and once user accepts the batch, all applications
// are queued"). The app's best new matches on one screen, each with why it fits. Untick any, accept, and what stays ticked goes in line.
// The batch is the server's (jobpilot/review_batch.py), so it stays the same batch until it is decided.
import { ExternalLink, ListChecks } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, type QueueMany } from "./api";
import { ApplyChoice, ApplyChoices } from "./applychoice";
import { ConfirmQueue, Refused } from "./bulk";
import { useWorkspace } from "./context";
import { ago, platformName, plural, stamp } from "./format";
import { useAction, useNow, usePoll } from "./hooks";
import { TopActions, TopLine } from "./shell/Shell";
import type { ReviewBatch, ReviewRow } from "./types";
import { Button, Empty, Listbox, Monogram, Table, type Column } from "./ui/index";
import { LoadError, Notice } from "./worktools";
import "./styles/queue.css";

const SIZES = [10, 25, 50, 100];
const webLink = (url: string) => (/^https?:\/\//i.test(url) ? url : "");

type Accepted = { queued: number; already: number; passed: number; refused: QueueMany["refused"]; next: ReviewBatch };

export function ReviewView() {
  const { snapshot, go, openJob, version, refresh } = useWorkspace();
  const now = useNow(60000);
  const { pending, run } = useAction();
  const poll = usePoll<ReviewBatch>((signal) => api("/review-batch", "GET", undefined, signal), 30000, [version]);
  // What came back from a press wins over the poll it raced, until the next poll reads the same batch.
  const [latest, setLatest] = useState<ReviewBatch | null>(null);
  useEffect(() => setLatest(null), [poll.data]);
  const batch = latest ?? poll.data;
  const rows = useMemo(() => batch?.rows ?? [], [batch]);
  const shown = useMemo(() => rows.map((r) => r.id), [rows]);
  // Everything starts ticked; only what is unticked is remembered, for this batch.
  const [unticked, setUnticked] = useState<{ id: string; ids: Set<string> }>({ id: "", ids: new Set() });
  const off = unticked.id === batch?.id ? unticked.ids : new Set<string>();
  const ticked = useMemo(() => new Set(rows.map((r) => r.id).filter((id) => !off.has(id))), [rows, off]);
  const [asking, setAsking] = useState(false);
  const [done, setDone] = useState<Accepted | null>(null);
  const planning = !!batch?.planning;
  const reread = poll.refresh;
  useEffect(() => {
    if (!planning) return;
    const timer = window.setTimeout(reread, 1500);
    return () => window.clearTimeout(timer);
  }, [planning, reread, batch]);

  const decide = (ids: string[], key: string) =>
    run(
      key,
      async () => {
        const out = await api<Accepted>("/review-batch/accept", "POST", { batch_id: batch!.id, job_ids: ids, unticked: rows.map((r) => r.id).filter((id) => !ids.includes(id)) });
        setDone(out);
        setLatest(out.next);
        void refresh();
        return out;
      },
    ).then(async (out) => {
      if (!out) await poll.refresh(); // Decided elsewhere, or refused: the screen reads what is true now.
    });
  const resize = (size: number) =>
    run("size", async () => {
      setLatest(await api<ReviewBatch>("/review-batch/size", "POST", { size }));
    });

  const n = ticked.size;
  const columns: Column<ReviewRow>[] = [
    {
      key: "job",
      header: "Job",
      width: 420,
      truncate: true,
      min: 220,
      cell: (r) => (
        <div className="bd-cell">
          <button
            type="button"
            className="bd-job"
            onClick={(e) => {
              e.stopPropagation();
              openJob(r.id);
            }}
          >
            <Monogram name={r.company} url={r.url} size="md" employer />
            <span className="bd-title" title={r.title}>
              {r.title}
            </span>
            <span className="bd-sub" title={`${r.company}${r.location ? " · " + r.location : ""}`}>
              {r.company}
              {r.location ? ` · ${r.location}` : " · Location not listed"}
            </span>
            <span className="bd-meta-line">{r.line}</span>
            <span className="bd-meta">
              <span className="mono">Match {r.score}</span>
              <span>{r.dated ? `Posted ${ago(r.posted_at, now)}` : `Found ${ago(r.posted_at, now)}`}</span>
            </span>
          </button>
        </div>
      ),
    },
    { key: "fit", header: "Match", num: true, hideBelow: 600, cell: (r) => r.score },
    {
      key: "why",
      header: "Why it matches",
      hideBelow: 600,
      width: 360,
      truncate: true,
      min: 180,
      cell: (r) => (
        <span className="bd-line" title={r.line}>
          {r.line}
        </span>
      ),
    },
    {
      key: "when",
      header: "Posted",
      hideBelow: 860,
      cell: (r) => (
        <time className="bd-when" dateTime={r.posted_at} title={`${r.dated ? "Posted" : "First found"} ${stamp(r.posted_at)}${r.dated ? "" : ". The posting gives no date."}`}>
          <span>{r.dated ? ago(r.posted_at, now) : `Found ${ago(r.posted_at, now)}`}</span>
          <small>{platformName(r.ats)}</small>
        </time>
      ),
    },
    {
      key: "act",
      header: <span className="sr-only">Posting</span>,
      width: "1%",
      pin: true,
      cell: (r) => {
        const link = webLink(r.url);
        return (
          <div className="ax-act" onClick={(e) => e.stopPropagation()}>
            <ApplyChoice jobId={r.id} />
            {link && (
              <a className="ax-btn is-sm ax-lead bd-link" href={link} target="_blank" rel="noreferrer noopener" title="Open the employer's posting in your browser">
                Posting <ExternalLink size={12} aria-hidden="true" />
                <span className="sr-only"> (opens in your browser)</span>
              </a>
            )}
          </div>
        );
      },
    },
  ];
  const pass = (
    <Button size="sm" variant="ghost" disabled={pending !== null} loading={pending === "pass"} onClick={() => decide([], "pass")}>
      Pass on this batch
    </Button>
  );
  return (
    <div className="ax-page ax-page-fill">
      <TopActions>
        <Listbox
          prefix="Batch size"
          label="How many jobs a batch gathers"
          align="end"
          value={String(batch?.size ?? 25)}
          options={[...new Set([...SIZES, batch?.size ?? 25])].sort((a, b) => a - b).map((v) => ({ value: String(v), label: String(v) }))}
          onChange={(v) => void resize(Number(v))}
        />
      </TopActions>
      <TopLine>
        {batch?.mode === "auto"
          ? "Automatic applying is on, so good matches go in line by themselves."
          : "Your best new matches. Untick any you don't want, then accept: the ticked jobs go to the Queue, and the next batch is prepared."}
        {!!batch?.more && ` ${plural(batch.more, "more match", "more matches")} after this batch.`}
      </TopLine>
      {batch?.mode === "auto" && (
        <Notice tone="info" action={<button type="button" className="ax-link" onClick={() => go({ view: "settings", tab: "applying" })}>Change in Settings</button>}>
          Automatic applying doesn't wait for a review. Choose Review a batch first in Settings, Applying, to decide on each batch yourself.
        </Notice>
      )}
      {done && (done.queued > 0 || done.already > 0 || done.refused.length > 0) && (
        <Notice
          tone="info"
          action={
            <button type="button" className="ax-link" onClick={() => go({ view: "queue" })}>
              Open the Queue
            </button>
          }
        >
          {plural(done.queued, "job")} queued{done.passed ? `, ${done.passed} passed on` : ""}.
          {done.already ? ` ${done.already} already running or applied.` : ""}
        </Notice>
      )}
      {done && <Refused refused={done.refused} onClose={() => setDone({ ...done, refused: [] })} />}
      {snapshot.control.paused && !!rows.length && <Notice tone="warn">AXIOM is stopped. What you accept waits in the Queue until you press Resume.</Notice>}
      {poll.error && !!batch && <LoadError error={poll.error} retry={poll.refresh} />}
      <ApplyChoices ids={shown}>
      <Table
        fill
        label="Review batch"
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        onRowClick={(r) => openJob(r.id)}
        selected={ticked}
        onSelectedChange={(next) => setUnticked({ id: batch?.id ?? "", ids: new Set(rows.map((r) => r.id).filter((id) => !next.has(id))) })}
        rowLabel={(r) => `${r.title} at ${r.company}`}
        selectAll={rows.length ? { count: rows.length, run: () => setUnticked({ id: batch?.id ?? "", ids: new Set() }) } : undefined}
        loading={(!batch || planning) && !poll.error}
        error={poll.error && !batch ? <LoadError error={poll.error} retry={poll.refresh} /> : undefined}
        bulk={
          <>
            <Button variant="primary" size="sm" disabled={pending !== null} loading={pending === "accept"} onClick={() => setAsking(true)}>
              {`Accept and queue ${n}`}
            </Button>
            {pass}
          </>
        }
        idle={rows.length ? pass : undefined}
        empty={
          <Empty
            icon={ListChecks}
            title="Nothing to review right now"
            hint={
              batch?.checking
                ? `${plural(batch.checking, "job")} still being checked against your profile. They appear here as soon as that's done.`
                : batch?.searching
                  ? "Searching for new jobs now. The best matches appear here once they are checked against your profile."
                  : "You've seen every good match. New ones appear here after the next search."
            }
            action={
              <Button size="sm" onClick={() => go({ view: "board" })}>
                Open the Job board
              </Button>
            }
          />
        }
      />
      </ApplyChoices>
      <ConfirmQueue
        open={asking}
        count={n}
        onClose={() => setAsking(false)}
        onConfirm={() => {
          setAsking(false);
          void decide([...ticked], "accept");
        }}
      />
    </div>
  );
}
