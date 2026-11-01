// Queue: what is running, what starts next and in what order, and what is held back (owner, 2026-11-01: "don't know where my queue
// is"). The order is the dispatcher's own (jobpilot/queue_order.py). Moving a job or removing one changes what the dispatcher does:
// nothing here starts an application by itself.
import { ArrowUpToLine, ListOrdered, Plus } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, moveToFront, removeFromQueue } from "./api";
import { useWorkspace } from "./context";
import { ago, employerOf, jobWords, platformName, plural, seconds, shownFit } from "./format";
import { useAction, useNow, usePoll } from "./hooks";
import { progressFrom, progressOf } from "./identity/stages";
import { OPEN_JOB_LINK } from "./jobintake";
import { Pagination, type Paging } from "./pagination";
import { TopActions, TopLine } from "./shell/Shell";
import type { HeldPage, HeldRow, QueueItem, QueuePage, QueueRetry, QueueRunning } from "./types";
import { Button, Empty, Listbox, Monogram, Skeleton, Table, type Column } from "./ui/index";
import { JobCell, LoadError, Note, RowMenu, type MenuAction } from "./worktools";
import "./styles/queue.css";

const webLink = (url: string) => (/^https?:\/\//i.test(url) ? url : "");

export function QueueView() {
  const { version, snapshot, go, route, refresh } = useWorkspace();
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const key = `page=${page}&size=${size}`;
  const queue = usePoll<QueuePage & { key: string }>(
    async (signal) => ({ ...(await api<QueuePage>(`/queue?${key}`, "GET", undefined, signal)), key }),
    5000,
    [key, version],
  );
  const q = queue.data;
  const { pending, run } = useAction();
  const again = async () => {
    await queue.refresh();
    void refresh();
  };
  const toFront = (item: QueueItem) =>
    run(
      "front:" + item.id,
      async () => {
        const r = await moveToFront(item.id);
        setPage(1);
        await again();
        return r;
      },
      (r) => r?.message,
    );
  const remove = (item: QueueItem) =>
    run(
      "remove:" + item.id,
      async () => {
        const r = await removeFromQueue(item.id);
        await again();
        return r;
      },
      `${employerOf(item.company, item.url)} is out of the queue. It's under Applications, Not applying, if you want it back.`,
    );

  const paging: Paging = {
    items: q?.next ?? [],
    total: q?.total ?? 0,
    page: q?.page ?? page,
    pages: q?.pages ?? 1,
    size,
    initialSize: 25,
    setPage,
    setSize,
  };
  const heldOpen = route.filter === "held" || !!route.filter?.startsWith("held:");
  return (
    <div className="ax-page qu-page">
      <TopActions>
        <Button variant="secondary" icon={<Plus size={15} aria-hidden="true" />} onClick={() => window.dispatchEvent(new Event(OPEN_JOB_LINK))}>
          Add a link
        </Button>
      </TopActions>
      {q && <TopLine><StatusLine q={q} /></TopLine>}
      {queue.error && !q && <LoadError error={queue.error} retry={queue.refresh} />}
      {!q && !queue.error && <Skeleton rows={8} />}
      {q && (
        <>
          <Running q={q} paused={snapshot.control.paused} />
          <section className="qu-section" aria-labelledby="qu-next">
            <header className="qu-head">
              <h2 id="qu-next">
                Up next{q.planning ? "" : <span className="qu-count">{q.total.toLocaleString()}</span>}
              </h2>
              {q.total > 0 && <p>{q.decides}</p>}
            </header>
            <NextTable q={q} paging={paging} pending={pending} onFront={toFront} onRemove={remove} planning={!!q.planning} go={() => go({ view: "board" })} />
          </section>
          {!!q.retrying?.length && <Retrying rows={q.retrying} />}
          {q.held_total > 0 && <Held q={q} open={heldOpen} code={route.filter?.startsWith("held:") ? route.filter.slice(5) : ""} />}
        </>
      )}
    </div>
  );
}

// The one sentence under the title: running or not, and why not.
function StatusLine({ q }: { q: QueuePage }) {
  const running = q.running.filter((r) => r.state !== "queued").length;
  if (q.paused)
    return (
      <>
        Paused. Nothing starts until you resume{q.running.length ? `, and ${plural(q.running.length, "application")} already under way finish${q.running.length === 1 ? "es" : ""}` : ""}.
      </>
    );
  if (q.stopped) return <>{q.stopped}</>;
  if (!q.auto_apply || !q.auto_submit) return <>Automatic applying is off, so only jobs you move to the front start. Turn it on in Settings, Applying.</>;
  if (running) return <>Applying to {plural(running, "job")} now. The next one starts as soon as a worker is free.</>;
  return <>Nothing is running this moment. The next job starts within a few seconds.</>;
}

// ---------- applying now ----------
const LOOKS: Record<string, string> = { queued: "Waiting for a free worker", waiting_email: "Waiting on an emailed code" };

function Running({ q, paused }: { q: QueuePage; paused: boolean }) {
  const { openJob, refresh } = useWorkspace();
  const { pending, run } = useAction();
  const now = useNow(1000);
  const rows = q.running;
  return (
    <section className="qu-section" aria-labelledby="qu-now">
      <header className="qu-head">
        <h2 id="qu-now">
          Applying now<span className="qu-count">{rows.length.toLocaleString()}</span>
        </h2>
      </header>
      {rows.length ? (
        <ol className="qu-live">
          {rows.map((r: QueueRunning) => {
            const words = jobWords(r);
            const doing = LOOKS[r.state] ?? progressOf(r.state, r.phase ? progressFrom([{ kind: r.phase }]) : null).doing;
            const elapsed = r.since ? (now - new Date(r.since).getTime()) / 1000 : 0;
            return (
              <li key={r.run_id}>
                <button type="button" className="qu-live-who" onClick={() => openJob(r.job_id, r.run_id)}>
                  <Monogram name={words.company} url={r.url} size="md" />
                  <span className="qu-live-name">
                    <strong title={words.company}>{words.company}</strong>
                    <span title={words.role}>{words.role}</span>
                  </span>
                </button>
                <span className="qu-live-doing">
                  <i className={"ax-lamp " + (r.state === "running" || r.state === "submitting" ? "is-live" : "is-hold")} aria-hidden="true" />
                  {doing}
                </span>
                <span className="qu-live-data mono">{seconds(elapsed)}</span>
                <span className="qu-live-data qu-hide-phone">{platformName(r.ats)}</span>
                <span className="qu-live-act">
                  {(r.state === "running" || r.state === "queued") && (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={pending !== null}
                      loading={pending === r.run_id}
                      onClick={() =>
                        run(
                          r.run_id,
                          async () => {
                            const res = await api<{ message: string }>(`/jobs/${r.job_id}/pause`, "POST");
                            void refresh();
                            return res;
                          },
                          (res) => res?.message,
                        )
                      }
                    >
                      Pause
                    </Button>
                  )}
                </span>
              </li>
            );
          })}
        </ol>
      ) : (
        <p className="qu-none">{paused ? "Nothing is running. Applying is paused." : "Nothing is running right now."}</p>
      )}
    </section>
  );
}

// ---------- up next ----------
function NextTable({
  q,
  paging,
  pending,
  onFront,
  onRemove,
  planning,
  go,
}: {
  q: QueuePage;
  paging: Paging;
  pending: string | null;
  onFront: (item: QueueItem) => void;
  onRemove: (item: QueueItem) => void;
  planning: boolean;
  go: () => void;
}) {
  const { openJob } = useWorkspace();
  const columns: Column<QueueItem>[] = useMemo(
    () => [
      { key: "at", header: <span title="Its place in the line">#</span>, num: true, cell: (i) => <span className="qu-pos mono">{i.position}</span> },
      {
        key: "job",
        header: "Job",
        width: 520,
        truncate: true,
        cell: (i) => (
          <JobCell
            {...jobWords(i)}
            onOpen={() => openJob(i.id)}
            meta={
              <>
                {shownFit(i) != null && <span className="mono">Fit {shownFit(i)}</span>}
                {i.location && <span>{i.location}</span>}
              </>
            }
          />
        ),
      },
      { key: "where", header: "Location", hideBelow: 860, width: 150, truncate: true, cell: (i) => <Note>{i.location || "Not listed"}</Note> },
      { key: "fit", header: "Fit", num: true, hideBelow: 600, cell: (i) => (shownFit(i) == null ? "—" : shownFit(i)) },
      { key: "why", header: "Waiting on", hideBelow: 860, width: 300, truncate: true, cell: (i) => <Note title={i.wait}>{i.wait || (i.position <= q.exact ? "The next free worker." : "")}</Note> },
      { key: "platform", header: "Platform", hideBelow: 860, cell: (i) => <Note>{platformName(i.ats)}</Note> },
      {
        key: "act",
        header: <span className="sr-only">Actions</span>,
        width: "1%",
        pin: true,
        cell: (i) => {
          const link = webLink(i.url);
          const actions: MenuAction[] = [
            ...(i.position > 1 ? [{ id: "front", label: "Move to the front", onSelect: () => onFront(i), disabled: pending !== null }] : []),
            {
              id: "remove",
              label: "Remove from the queue",
              danger: true,
              confirm: "Take this job out of the queue? It won't start until you put it back.",
              confirmLabel: "Remove",
              onSelect: () => onRemove(i),
              disabled: pending !== null,
            },
            ...(link ? [{ id: "posting", label: "Open the posting", onSelect: () => void window.open(link, "_blank", "noopener,noreferrer") }] : []),
          ];
          return (
            <div className="ax-act" onClick={(e) => e.stopPropagation()}>
              {i.position > 1 && (
                <Button size="sm" className="ax-lead" disabled={pending !== null} loading={pending === "front:" + i.id} icon={<ArrowUpToLine size={13} aria-hidden="true" />} onClick={() => onFront(i)}>
                  To front
                </Button>
              )}
              <RowMenu label={`More actions for ${i.company || "this job"}`} actions={actions} busy={pending !== null} />
            </div>
          );
        },
      },
    ],
    [openJob, onFront, onRemove, pending, q.exact],
  );
  if (planning) return <Skeleton rows={6} />;
  return (
    <Table
      label="Up next, in order"
      columns={columns}
      rows={q.next}
      rowKey={(i) => i.id}
      onRowClick={(i) => openJob(i.id)}
      footer={paging.total > paging.size ? <Pagination paging={paging} label="Queue pages" /> : undefined}
      empty={
        <Empty
          compact
          icon={ListOrdered}
          title="Nothing is in line"
          hint={q.held_total ? "Everything found is held back, each for the reason below. The Job board shows all of it." : "New jobs join the line once they are found and checked against your profile."}
          action={
            <Button size="sm" onClick={go}>
              Open the Job board
            </Button>
          }
        />
      }
    />
  );
}

// ---------- trying again by itself ----------
function Retrying({ rows }: { rows: QueueRetry[] }) {
  const { openJob, go } = useWorkspace();
  const now = useNow(60000);
  const columns: Column<QueueRetry>[] = [
    {
      key: "job",
      header: "Job",
      width: 440,
      truncate: true,
      cell: (r) => <JobCell {...jobWords(r)} onOpen={() => openJob(r.job_id, r.run_id)} meta={<span>{r.sentence}</span>} />,
    },
    { key: "why", header: "What stopped it", hideBelow: 600, width: 420, truncate: true, cell: (r) => <Note title={r.reason}>{r.reason || r.sentence}</Note> },
    { key: "last", header: "Last try", hideBelow: 860, cell: (r) => <time dateTime={r.since}>{ago(r.since, now)}</time> },
    { key: "tries", header: "Tries", num: true, hideBelow: 860, cell: (r) => r.tries },
  ];
  return (
    <section className="qu-section" aria-labelledby="qu-again">
      <header className="qu-head">
        <h2 id="qu-again">
          Trying again by itself<span className="qu-count">{rows.length.toLocaleString()}</span>
        </h2>
        <p>
          These stopped on a problem and the app retries each by itself.{" "}
          <button type="button" className="ax-quiet-link" onClick={() => go({ view: "ledger", tab: "problems" })}>
            See what went wrong and when each tries again
          </button>
        </p>
      </header>
      <Table label="Trying again by itself" columns={columns} rows={rows} rowKey={(r) => r.job_id} onRowClick={(r) => openJob(r.job_id, r.run_id)} />
    </section>
  );
}

// ---------- held back ----------
function Held({ q, open, code }: { q: QueuePage; open: boolean; code: string }) {
  const { openJob, go, version } = useWorkspace();
  const [shown, setShown] = useState(open);
  const [reason, setReason] = useState(code);
  const [page, setPage] = useState(1);
  const fold = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    if (!open) return;
    setShown(true);
    setReason(code);
    fold.current?.scrollIntoView({ block: "start" });
  }, [open, code]);
  useEffect(() => setPage(1), [reason]);
  const key = new URLSearchParams({ code: reason, page: String(page), size: "25" }).toString();
  const held = usePoll<HeldPage>((signal) => api(`/queue/held?${key}`, "GET", undefined, signal), 30000, [key, version], shown);
  const paging: Paging = { items: held.data?.rows ?? [], total: held.data?.total ?? 0, page: held.data?.page ?? page, pages: held.data?.pages ?? 1, size: 25, initialSize: 25, setPage, setSize: () => undefined };
  const needsYou = (r: HeldRow) => r.hold.code === "employer_limit" || r.hold.code === "reserved";
  const columns: Column<HeldRow>[] = [
    { key: "job", header: "Job", width: 440, truncate: true, cell: (r) => <JobCell {...jobWords(r)} onOpen={() => openJob(r.id)} meta={<span>{r.hold.reason}</span>} /> },
    { key: "why", header: "Why it is held", hideBelow: 600, width: 440, truncate: true, cell: (r) => <Note title={`${r.hold.reason} ${r.hold.next}`}>{r.hold.reason}</Note> },
    {
      key: "act",
      header: <span className="sr-only">Actions</span>,
      width: "1%",
      pin: true,
      cell: (r) =>
        needsYou(r) ? (
          <Button size="sm" className="ax-lead" onClick={(e) => { e.stopPropagation(); go({ view: "needs", filter: r.hold.code === "reserved" ? "reserved" : "over" }); }}>
            Review
          </Button>
        ) : null,
    },
  ];
  const options = [{ value: "", label: "Any reason", count: q.held_total }, ...q.held.map((h) => ({ value: h.code, label: h.phrase.charAt(0).toUpperCase() + h.phrase.slice(1), count: h.count }))];
  return (
    <details ref={fold} className="ax-fold qu-held" open={shown || undefined} onToggle={(e) => setShown((e.target as HTMLDetailsElement).open)}>
      <summary>
        <strong>Held back</strong>
        <span className="qu-count">{q.held_total.toLocaleString()}</span>
        <span className="qu-held-note">Found and ready, but the app won't start these by itself.</span>
      </summary>
      {shown && (
        <div className="qu-held-body">
          <div className="ax-toolbar">
            <Listbox prefix="Reason" label="Why it is held" value={reason} options={options} onChange={setReason} />
          </div>
          <Table
            label="Held back"
            columns={columns}
            rows={held.data?.rows ?? []}
            rowKey={(r) => r.id}
            onRowClick={(r) => openJob(r.id)}
            loading={!held.data && !held.error}
            error={held.error && !held.data ? <LoadError error={held.error} retry={held.refresh} /> : undefined}
            footer={paging.total > 25 ? <Pagination paging={paging} label="Held back pages" sizes={false} /> : undefined}
            empty={<Empty compact title="Nothing is held for this reason" />}
          />
        </div>
      )}
    </details>
  );
}
