// Queue: what is running, what starts next and in what order, and what is held back (owner, 2026-11-01: "don't know where my queue
// is"). The order is the dispatcher's own (jobpilot/queue_order.py). Moving a job or removing one changes what the dispatcher does:
// nothing here starts an application by itself.
import { ArrowUpToLine, ListChecks, ListOrdered, Plus } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { addManyToQueue, api, frontManyInQueue, moveToFront, removeFromQueue, removeManyFromQueue, type QueueMany } from "./api";
import { ConfirmQueue, Refused, useSelection } from "./bulk";
import { useWorkspace } from "./context";
import { ago, employerOf, jobWords, platformName, plural, seconds, shownFit } from "./format";
import { useAction, useNow, usePoll } from "./hooks";
import { progressFrom, progressOf } from "./identity/stages";
import { OPEN_JOB_LINK } from "./jobintake";
import { Pagination, type Paging } from "./pagination";
import { TopActions, TopLine } from "./shell/Shell";
import type { HeldPage, HeldRow, QueueItem, QueuePage, QueueRetry, QueueRunning } from "./types";
import { Button, Empty, Listbox, Monogram, Skeleton, Table, type Column } from "./ui/index";
import { ConfirmButton, JobCell, LoadError, Note, RowMenu, type MenuAction } from "./worktools";
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

  // A whole Select all, across every page of the line.
  const selection = useSelection(5000);
  const [refused, setRefused] = useState<QueueMany["refused"]>([]);
  const selectAll = () =>
    run("select", async () => {
      const out = await api<{ ids: string[] }>("/queue/ids");
      selection.replace(new Set(out.ids));
    });
  const frontSelected = () =>
    run(
      "many-front",
      async () => {
        const out = await frontManyInQueue([...selection.ids].reverse());
        selection.clear();
        setRefused(out.refused);
        setPage(1);
        await again();
        return out;
      },
      (out) => out.message,
    );
  const removeSelected = () =>
    run(
      "many-remove",
      async () => {
        const out = await removeManyFromQueue(selection.ids);
        selection.clear();
        await again();
        return out;
      },
      (out) => `${plural(out.removed, "job")} out of the queue. They're under Applications, Not applying, if you want them back.${out.not_in_line ? ` ${out.not_in_line} had already started.` : ""}`,
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
      {q && <TopLine><StatusLine q={q} onSettings={() => go({ view: "settings", tab: "applying" })} /></TopLine>}
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
            <Refused refused={refused} onClose={() => setRefused([])} />
            <NextTable
              q={q}
              paging={paging}
              pending={pending}
              onFront={toFront}
              onRemove={remove}
              planning={!!q.planning}
              go={go}
              selection={selection}
              selectAll={selectAll}
              bulk={
                <>
                  <Button size="sm" variant="primary" loading={pending === "many-front"} disabled={pending !== null} onClick={() => void frontSelected()}>
                    To front
                  </Button>
                  <ConfirmButton
                    label="Remove"
                    question={`Take ${plural(selection.ids.length, "job")} out of the queue? They won't start until you put them back.`}
                    confirmLabel="Remove"
                    loading={pending === "many-remove"}
                    disabled={pending !== null && pending !== "many-remove"}
                    onConfirm={() => void removeSelected()}
                  />
                </>
              }
            />
          </section>
          {!!q.retrying?.length && <Retrying rows={q.retrying} />}
          {q.held_total > 0 && <Held q={q} open={heldOpen} code={route.filter?.startsWith("held:") ? route.filter.slice(5) : ""} />}
        </>
      )}
    </div>
  );
}

// The one sentence under the title: running or not, and why not.
function StatusLine({ q, onSettings }: { q: QueuePage; onSettings: () => void }) {
  const running = q.running.filter((r) => r.state !== "queued").length;
  if (q.paused)
    return (
      <>
        Paused. Nothing starts until you resume{q.running.length ? `, and ${plural(q.running.length, "application")} already under way finish${q.running.length === 1 ? "es" : ""}` : ""}.
      </>
    );
  if (q.stopped) return <>{q.stopped}</>;
  if (!q.auto_submit)
    return (
      <>
        Sending is off in Settings, so nothing starts{q.total ? ` and these ${plural(q.total, "job")} wait` : ""}.{" "}
        <button type="button" className="ax-link" onClick={onSettings}>
          Open Settings
        </button>
      </>
    );
  if (running) return <>Applying to {plural(running, "job")} now. The next one starts as soon as a worker is free.</>;
  if (!q.total) return q.dispatching === false ? <>Nothing is in line. Only jobs you queue start, one click on the Job board or a batch you accept.</> : <>Nothing is in line right now.</>;
  return <>Nothing is running this moment. The next job starts within a few seconds.</>;
}

// ---------- applying now ----------
const LOOKS: Record<string, string> = { queued: "Waiting for a free worker", waiting_email: "Waiting on an emailed code" };

function Running({ q, paused }: { q: QueuePage; paused: boolean }) {
  const { openJob, refresh } = useWorkspace();
  const { pending, run } = useAction();
  const now = useNow(1000);
  const rows = q.running;
  // Only what can be paused now can be ticked: one already sending can't be stopped.
  const can = rows.filter((r) => r.state === "running" || r.state === "queued");
  const [ticked, setTicked] = useState<ReadonlySet<string>>(() => new Set());
  const chosen = can.filter((r) => ticked.has(r.job_id));
  const pauseChosen = () =>
    run(
      "many",
      async () => {
        const done = await Promise.allSettled(chosen.map((r) => api(`/jobs/${r.job_id}/pause`, "POST")));
        setTicked(new Set());
        void refresh();
        return done.filter((d) => d.status === "fulfilled").length;
      },
      (ok) => `Paused ${plural(ok ?? 0, "application")}. Each stops at its next safe step.`,
    );
  return (
    <section className="qu-section" aria-labelledby="qu-now">
      <header className="qu-head">
        <h2 id="qu-now">
          Applying now<span className="qu-count">{rows.length.toLocaleString()}</span>
        </h2>
      </header>
      {rows.length ? (
        <div className="qu-live-wrap">
          {can.length > 0 && (
            <div className="ax-bulk" role="status">
              {chosen.length > 0 && <span>{chosen.length.toLocaleString()} selected</span>}
              {chosen.length < can.length && (
                <Button size="sm" variant="secondary" onClick={() => setTicked(new Set(can.map((r) => r.job_id)))}>
                  {`Select all ${can.length.toLocaleString()}`}
                </Button>
              )}
              {chosen.length > 0 && (
                <div className="ax-bulk-actions">
                  <Button size="sm" variant="primary" loading={pending === "many"} disabled={pending !== null} onClick={() => void pauseChosen()}>
                    {`Pause ${chosen.length.toLocaleString()}`}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setTicked(new Set())}>
                    Clear
                  </Button>
                </div>
              )}
            </div>
          )}
        <ol className="qu-live">
          {rows.map((r: QueueRunning) => {
            const words = jobWords(r);
            const doing = ((r.state === "queued" && r.gate) || LOOKS[r.state]) ?? progressOf(r.state, r.phase ? progressFrom([{ kind: r.phase }]) : null).doing;
            const elapsed = r.since ? (now - new Date(r.since).getTime()) / 1000 : 0;
            const pausable = r.state === "running" || r.state === "queued";
            return (
              <li key={r.run_id}>
                <span className="qu-live-tick">
                  <input
                    type="checkbox"
                    aria-label={`Select ${words.company}`}
                    disabled={!pausable}
                    checked={pausable && ticked.has(r.job_id)}
                    onChange={() =>
                      setTicked((old) => {
                        const next = new Set(old);
                        if (next.has(r.job_id)) next.delete(r.job_id);
                        else next.add(r.job_id);
                        return next;
                      })
                    }
                  />
                </span>
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
                  {pausable && (
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
        </div>
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
  selection,
  selectAll,
  bulk,
}: {
  q: QueuePage;
  paging: Paging;
  pending: string | null;
  onFront: (item: QueueItem) => void;
  onRemove: (item: QueueItem) => void;
  planning: boolean;
  go: ReturnType<typeof useWorkspace>["go"];
  selection: ReturnType<typeof useSelection>;
  selectAll: () => void;
  bulk: React.ReactNode;
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
                {shownFit(i) != null && <span className="mono">Match {shownFit(i)}</span>}
                {i.location && <span>{i.location}</span>}
              </>
            }
          />
        ),
      },
      { key: "where", header: "Location", hideBelow: 860, width: 150, truncate: true, cell: (i) => <Note>{i.location || "Not listed"}</Note> },
      { key: "fit", header: "Match", num: true, hideBelow: 600, cell: (i) => (shownFit(i) == null ? "—" : shownFit(i)) },
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
      selected={selection.chosen}
      onSelectedChange={selection.replace}
      selectAll={q.total ? { count: q.total, run: selectAll, busy: pending === "select" } : undefined}
      rowLabel={(i) => `${i.title} at ${i.company}`}
      bulk={bulk}
      footer={paging.total > paging.size ? <Pagination paging={paging} label="Queue pages" /> : undefined}
      empty={
        <Empty
          compact
          icon={ListOrdered}
          title="Nothing is in line"
          hint={
            q.dispatching === false
              ? "Only jobs you queue start. Review a batch of your best matches, or press Queue on any job."
              : q.held_total
                ? "Everything found is held back, each for the reason below. The Job board shows all of it."
                : "New jobs join the line once they are found and checked against your profile."
          }
          action={
            <>
              {q.dispatching === false && (
                <Button size="sm" variant="primary" icon={<ListChecks size={14} aria-hidden="true" />} onClick={() => go({ view: "review" })}>
                  Review a batch
                </Button>
              )}
              <Button size="sm" onClick={() => go({ view: "board" })}>
                Open the Job board
              </Button>
            </>
          }
        />
      }
    />
  );
}

// ---------- trying again by itself ----------
function Retrying({ rows }: { rows: QueueRetry[] }) {
  const { openJob, go, refresh } = useWorkspace();
  const { pending, run } = useAction();
  const selection = useSelection(5000);
  const [refused, setRefused] = useState<QueueMany["refused"]>([]);
  const tryNow = () =>
    run(
      "try",
      async () => {
        const out = await frontManyInQueue(selection.ids);
        selection.clear();
        setRefused(out.refused);
        void refresh();
        return out;
      },
      (out) => out.message,
    );
  const stop = () =>
    run(
      "stop",
      async () => {
        const done = await Promise.allSettled(selection.ids.map((id) => api(`/jobs/${id}/pause`, "POST")));
        selection.clear();
        void refresh();
        return done.filter((d) => d.status === "fulfilled").length;
      },
      (ok) => `${plural(ok ?? 0, "job")} won't be tried again until you put them back.`,
    );
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
      <Refused refused={refused} onClose={() => setRefused([])} />
      <Table
        label="Trying again by itself"
        columns={columns}
        rows={rows}
        rowKey={(r) => r.job_id}
        onRowClick={(r) => openJob(r.job_id, r.run_id)}
        selected={selection.chosen}
        onSelectedChange={selection.replace}
        selectAll={{ count: rows.length, run: () => selection.replace(new Set(rows.map((r) => r.job_id))) }}
        rowLabel={(r) => `${r.title} at ${r.company}`}
        bulk={
          <>
            <ConfirmButton
              label="Try now"
              question={`Try ${plural(selection.ids.length, "job")} next? AXIOM sends a real application to each, in its turn.`}
              confirmLabel="Try now"
              loading={pending === "try"}
              disabled={pending !== null && pending !== "try"}
              onConfirm={() => void tryNow()}
            />
            <ConfirmButton
              label="Stop trying"
              question={`Stop trying ${plural(selection.ids.length, "job")}? They wait until you put them back.`}
              confirmLabel="Stop trying"
              loading={pending === "stop"}
              disabled={pending !== null && pending !== "stop"}
              onConfirm={() => void stop()}
            />
          </>
        }
      />
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
  // Only what asking for it can lift: the rest would only be refused (owner_apply.REFUSED_HOLDS).
  const canQueue = (r: HeldRow) => !needsYou(r) && !["employer_cap", "protected", "excluded", "gate", "twin", "aged"].includes(r.hold.code);
  const { refresh } = useWorkspace();
  const { pending, run } = useAction();
  const selection = useSelection(5000);
  useEffect(() => selection.clear(), [reason]); // eslint-disable-line react-hooks/exhaustive-deps
  const [refused, setRefused] = useState<QueueMany["refused"]>([]);
  const [asking, setAsking] = useState(false);
  const selectAll = () =>
    run("select", async () => {
      const out = await api<{ ids: string[] }>(`/queue/held/ids?code=${encodeURIComponent(reason)}`);
      selection.replace(new Set(out.ids));
    });
  const queueSelected = () =>
    run(
      "many",
      async () => {
        const out = await addManyToQueue(selection.ids);
        selection.clear();
        setRefused(out.refused);
        await held.refresh();
        void refresh();
        return out;
      },
      (out) => out.message,
    );
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
          <Refused refused={refused} onClose={() => setRefused([])} />
          <Table
            label="Held back"
            columns={columns}
            rows={held.data?.rows ?? []}
            rowKey={(r) => r.id}
            onRowClick={(r) => openJob(r.id)}
            selected={selection.chosen}
            onSelectedChange={selection.replace}
            canSelect={(r) => canQueue(r)}
            selectAll={held.data?.selectable ? { count: held.data.selectable, run: selectAll, busy: pending === "select" } : undefined}
            rowLabel={(r) => `${r.title} at ${r.company}`}
            bulk={
              <Button size="sm" variant="primary" loading={pending === "many"} disabled={pending !== null} onClick={() => setAsking(true)}>
                {`Queue anyway (${selection.ids.length.toLocaleString()})`}
              </Button>
            }
            loading={!held.data && !held.error}
            error={held.error && !held.data ? <LoadError error={held.error} retry={held.refresh} /> : undefined}
            footer={paging.total > 25 ? <Pagination paging={paging} label="Held back pages" sizes={false} /> : undefined}
            empty={<Empty compact title="Nothing is held for this reason" />}
          />
          <ConfirmQueue
            open={asking}
            count={selection.ids.length}
            title={`Queue ${plural(selection.ids.length, "held job")} anyway?`}
            onClose={() => setAsking(false)}
            onConfirm={() => {
              setAsking(false);
              void queueSelected();
            }}
          />
        </div>
      )}
    </details>
  );
}
