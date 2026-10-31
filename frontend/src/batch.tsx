import { useMemo, type ReactNode } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { ChannelBank, type Channel } from "./identity/ChannelBank";
import { DayChart, DotPatterns } from "./identity/DayChart";
import { MasterKey } from "./identity/MasterKey";
import { confirmedToday, panelState, STATE_WORD } from "./identity/state";
import { progressFrom, progressOf } from "./identity/stages";
import "./identity/panel.css";
import { appliedState, STATES, type JobRow } from "./model";
import { Pagination, usePagination } from "./pagination";
import { OPEN_JOB_LINK } from "./jobintake";
import { countQuestions } from "./questions";
import type { ApplicationWorkers, MailStatus, RuntimeHealth, Snapshot } from "./types";
import { ago, clock, day, plural } from "./format";
import { Button, Empty, Panel, Table, type Column } from "./ui/index";
import { Facts, JobCell, Notice, Note, StatusPill } from "./worktools";
import "./styles/work.css";

const LIVE_MAIL = new Set(["pending", "matched", "consumed"]);

type Working = {
  key: string;
  slot: number;
  runId?: string;
  company: string;
  role: string;
  platform: string;
  state: string;
  /** The newest event kind that says where the run is, or its state when none has: from the worker poll. */
  phase?: string;
  open?: () => void;
  /** The job to pause, when it can be paused now. */
  pause?: string;
  /** When its attempt started, for the time on the job. */
  since?: string;
};

// The home page: the panel. One sentence on whether the batch is running, what each worker is on, what waits, what just
// finished. The questions themselves are on Needs you; worker and host detail is in Settings › System.
export function BatchView() {
  const health = usePoll<RuntimeHealth>(
    (signal) => api("/runtime-health", "GET", undefined, signal),
    30000,
  );
  const mail = usePoll<MailStatus>(
    (signal) => api("/mail", "GET", undefined, signal),
    30000,
  );
  const work = useWorking(mail.data, health.data?.application_workers);
  return (
    <div className="ax-page ax-panel-page">
      <DotPatterns />
      <Master work={work} />
      <Bank work={work} checking={!health.data && !work.known} />
      <div className="ax-panel-below">
        <FirstRun mail={mail.data} />
        <Backlog work={work} />
        <Activity />
      </div>
    </div>
  );
}

// ---------- the workers that are doing something ----------
function useWorking(mail: MailStatus | null, workers?: ApplicationWorkers) {
  const { snapshot, rows, counts, openJob } = useWorkspace();
  const runtimeWorkers = snapshot.autopilot_runtime?.application_workers || workers;
  const lanes = runtimeWorkers;
  const limit = lanes
    ? lanes.normal_workers + lanes.specialist_workers + (lanes.custom_workers ?? 0)
    : Math.max(0, snapshot.config.workers || 0);
  const owners = rows
    .filter((r) => r.run && ["running", "submitting"].includes(r.run.state))
    .sort((a, b) => (a.run!.started_at || a.run!.created_at).localeCompare(b.run!.started_at || b.run!.created_at));
  const assignments = runtimeWorkers?.assignments || [];
  const jobs = new Map(snapshot.jobs.map((j) => [j.id, j]));
  const runJob = new Map(snapshot.runs.map((r) => [r.id, r.job_id]));
  const emailWaits = (mail?.challenges || [])
    .filter((c) => LIVE_MAIL.has(c.state))
    .map((c) => ({ c, job: jobs.get(runJob.get(c.run_id) || "") }));

  const busy: Working[] = [];
  Array.from({ length: limit }, (_, i) => {
    const slot = i + 1;
    const assignment = assignments.find((worker) => worker.slot === slot);
    const row: JobRow | undefined = assignment?.run_id
      ? rows.find((owner) => owner.run?.id === assignment.run_id)
      : runtimeWorkers
        ? undefined
        : owners.find((owner) => owner.run?.worker_slot === slot);
    if (!row && !assignment?.run_id) return;
    if (!row && assignment) {
      const job = assignment.job_id ? jobs.get(assignment.job_id) : undefined;
      busy.push({
        key: "assigned" + slot,
        slot,
        runId: assignment.run_id || undefined,
        company: job?.company || "Assigned application",
        role: job?.title || "Details loading",
        platform: assignment.ats,
        state: assignment.state || "running",
        phase: assignment.phase,
      });
      return;
    }
    if (!row) return;
    const r = row.run!;
    busy.push({
      key: r.id,
      slot,
      runId: r.id,
      company: row.job.company || "Employer",
      role: row.job.title,
      platform: assignment?.ats || row.job.ats,
      state: row.state,
      phase: assignment?.phase,
      open: () => openJob(row.job.id, r.id),
      pause: r.state === "running" && row.state !== "pausing" ? row.job.id : undefined,
      since: r.started_at || r.created_at,
    });
  });
  return {
    busy,
    limit,
    // Whether the slots themselves have reported, so an idle channel can say idle.
    known: !!runtimeWorkers,
    working: Math.max(runtimeWorkers ? new Set(assignments.map((a) => a.run_id).filter(Boolean)).size : 0, owners.length),
    emailWaits,
    waiting: {
      queued: counts.states.queued ?? 0,
      starting: counts.states["pending:waiting"] ?? 0,
      repairing: counts.states.waiting_browser ?? 0,
      answers: counts.groups.needs ?? 0,
      email: counts.states.waiting_email ?? emailWaits.length,
    },
  };
}
type WorkingState = ReturnType<typeof useWorking>;

// ---------- the master row: today's count, the one sentence, what waits on you, and the key ----------
type Success = { applied_at: string; existing_application?: boolean };

function Master({ work }: { work: WorkingState }) {
  const { snapshot, counts, link, refresh, go } = useWorkspace();
  const { pending, run } = useAction();
  const now = useNow();
  const ledger = usePoll<{ applications: Success[] }>(
    (signal) => api("/successful-applications", "GET", undefined, signal),
    60000,
  );
  const today = useMemo(() => confirmedToday(ledger.data?.applications || [], now), [ledger.data, now]);

  const rt = snapshot.autopilot_runtime || {};
  const batch = snapshot.autopilot || {};
  const state = rt.state || batch.state || "idle";
  const active = state === "active";
  const target = rt.target || batch.target || 100;
  const confirmed = rt.confirmed ?? 0;
  const [goal, setGoal] = useDraft("batch-target", counts.jobs || snapshot.jobs.length ? 100 : 25);
  const workers = snapshot.config.workers_enabled;
  const noResume = snapshot.resumes.length === 0;
  const paused = snapshot.control.paused;
  const panel = panelState(snapshot, link.lost);
  // Applications you started yourself count too, so a batch can finish above its target ("560/477" read as a mistake).
  const progress = confirmed > target ? `${confirmed} confirmed, target ${target}` : `${confirmed} of ${target} confirmed`;
  const stopped = paused || (!rt.intake_enabled && rt.intake_reason === "Paused");

  // The worst thing that is true right now, in plain words.
  const said: Record<typeof panel, ["info" | "warn" | "bad", string]> = {
    lost: ["bad", `Connection lost. Showing data from ${ago(link.updatedAt, now)}; retrying.`],
    restart: ["bad", "Needs a restart. Automation is degraded; restart it to keep applying."],
    paused: ["warn", "Paused. No new applications start; progress is kept."],
    complete: ["info", `Target reached. ${progress}.`],
    running: ["info", `Applying. ${work.working} of ${plural(work.limit, "worker")} busy, ${progress}.`],
    held: ["warn", `Held. ${(rt.intake_reason || "New applications are not starting").replace(/\.$/, "")}. ${progress}.`],
    none: ["info", work.limit ? `No batch running. ${plural(work.limit, "worker")} ready.` : "No batch running."],
  };
  const [tone, sentence] = said[panel];

  const begin = (value: number) =>
    run(
      "start",
      async () => {
        const result = await api("/job-board/autopilot/start", "POST", { target: value });
        await refresh();
        return result;
      },
      active
        ? "Batch resumed. Existing progress is kept."
        : "Batch started. Discovery, ranking and applications continue without this page open.",
    );
  const pause = () =>
    run(
      "pause",
      async () => {
        // Read the current control values right before writing them back.
        const fresh = await api<Snapshot["control"]>("/control");
        await api("/control", "PUT", { ...fresh, paused: true });
        await refresh();
      },
      "New applications paused. Progress is kept, and you can still manage each job.",
    );

  const key = !workers ? null : !active ? (
    <form
      className="ax-power"
      onSubmit={(e) => {
        e.preventDefault();
        begin(Math.max(1, Math.min(500, Math.round(goal || 25))));
      }}
    >
      <div className="ax-legend">
        <span className="ax-legend-state">{STATE_WORD[panel]}</span>
        <label className="ax-target">
          <span>Apply until</span>
          <input
            type="number"
            min={1}
            max={500}
            step={1}
            required
            value={goal}
            onChange={(e) => setGoal(Number(e.target.value))}
            aria-describedby="target-hint"
          />
          <span>confirmed</span>
        </label>
        <span id="target-hint" className="sr-only">
          Up to 500 confirmed applications.
        </span>
      </div>
      <MasterKey type="submit" word="start" icon="start" aria-label="Start applying" busy={pending === "start"} disabled={pending !== null || noResume} />
    </form>
  ) : stopped ? (
    <div className="ax-power">
      <div className="ax-legend">
        <span className="ax-legend-state">{STATE_WORD[panel]}</span>
        <span className="ax-legend-note">Nothing new starts until you press resume.</span>
      </div>
      <MasterKey word="resume" icon="start" aria-label="Resume applying" busy={pending === "start"} disabled={pending !== null} onClick={() => begin(target)} />
    </div>
  ) : (
    <div className="ax-power">
      <div className="ax-legend">
        <span className="ax-legend-state">{STATE_WORD[panel]}</span>
        {panel === "restart" ? (
          <Button variant="primary" loading={pending === "start"} disabled={pending !== null} onClick={() => begin(target)}>
            Restart automation
          </Button>
        ) : (
          <span className="ax-legend-note" id="pause-hint">
            Pausing holds each channel before it sends.
          </span>
        )}
      </div>
      <MasterKey word="pause" icon="pause" aria-label="Pause applying" busy={pending === "pause"} disabled={pending !== null} onClick={pause} />
    </div>
  );

  const questions = countQuestions(snapshot.reviews);
  const count = today.length.toLocaleString();
  return (
    <>
      <div className="ax-master">
        <div className="ax-big">
          <span className="ax-read" style={{ "--chars": count.length } as React.CSSProperties}>
            {ledger.data ? count : "–"}
          </span>
          <span className="ax-engraved">confirmed today</span>
        </div>
        <div className="ax-say">
          <p className={"ax-sentence is-" + tone} aria-live="polite">
            {sentence}
          </p>
          {active && (
            <p
              className="ax-meta"
              title={[
                batch.started_at && `Started ${day(batch.started_at)}`,
                rt.confirmed_by_owner ? `${rt.confirmed_by_batch ?? 0} by the batch, ${rt.confirmed_by_owner} you started` : "",
                rt.last_tick && `checked ${ago(rt.last_tick, now)}`,
              ]
                .filter(Boolean)
                .join(" · ")}
            >
              {rt.remaining ?? Math.max(0, target - confirmed)} to go · {plural(rt.attempted_jobs ?? 0, "job")} attempted
            </p>
          )}
          <DayChart times={today} now={now} />
        </div>
        <a
          className="ax-wait"
          href="?view=needs"
          onClick={(e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
            e.preventDefault();
            go({ view: "needs" });
          }}
        >
          <span className={"ax-window ax-litwin" + (questions ? "" : " is-dark")}>
            <span className="ax-read" style={{ "--chars": String(questions).length } as React.CSSProperties}>
              {questions}
            </span>
          </span>
          <span className="ax-engraved">{questions === 1 ? "question waiting" : "questions waiting"}</span>
          {questions > 0 && <span className="ax-wait-go">{questions === 1 ? "answer it" : "answer them"}</span>}
        </a>
        {key}
      </div>
      {(!workers || (!active && noResume && workers)) && (
        <div className="ax-panel-notes">
          {!workers && (
            <Notice tone="warn">
              Application workers are disabled in this installation's configuration, so a batch can't run. Enable workers on
              the server and restart.
            </Notice>
          )}
          {!active && noResume && workers && <ResumeNotice />}
        </div>
      )}
    </>
  );
}

function ResumeNotice() {
  const { go } = useWorkspace();
  return (
    <Notice
      tone="warn"
      action={
        <button type="button" className="ax-link" onClick={() => go({ view: "profile", tab: "resumes" })}>
          Add a resume
        </button>
      }
    >
      Upload a resume first: ranking selects one of your resumes for each job.
    </Notice>
  );
}

// ---------- the bank: one channel per worker slot ----------
function Bank({ work, checking }: { work: WorkingState; checking: boolean }) {
  const { refresh, markJobs } = useWorkspace();
  const { pending, run } = useAction();
  const now = useNow(1000);
  const channels: Channel[] = work.busy.map((w) => ({
    slot: w.slot,
    id: w.key,
    company: w.company,
    role: w.role,
    platform: w.platform,
    state: w.state,
    since: w.since,
    // The worker's phase is the newest telling event kind (pipeline_comparison.TELLING_EVENTS), already in this poll.
    progress: progressOf(w.state, w.phase ? progressFrom([{ kind: w.phase }]) : null),
    open: w.open,
    pause: w.pause ? (
      <button
        type="button"
        className="ax-ch-pause"
        disabled={pending !== null}
        onClick={() =>
          run(
            w.key,
            async () => {
              const res = await api<{ message: string; state?: string }>(`/jobs/${w.pause}/pause`, "POST");
              // Reads as pausing at once; the next poll confirms.
              const shown = appliedState(res.state || "");
              if (shown in STATES) markJobs([w.pause!], shown);
              void refresh();
              return res;
            },
            (res) => res?.message,
          )
        }
      >
        {pending === w.key ? "Pausing…" : "Pause"}
      </button>
    ) : undefined,
  }));
  return <ChannelBank slots={work.limit} channels={channels} now={now} checking={checking} />;
}

/** Until the first job exists: the few things that decide whether the first application works. */
function FirstRun({ mail }: { mail: MailStatus | null }) {
  const { snapshot, counts, go, refresh } = useWorkspace();
  const { pending, run } = useAction();
  if (counts.jobs > 0 || snapshot.jobs.length > 0) return null;
  const submitting = snapshot.control.auto_submit;
  const p = snapshot.profile;
  const boxes = mail?.mailboxes.filter((m) => m.state === "connected").length ?? 0;
  const email = snapshot.config.imap || boxes > 0;
  const steps: { done: boolean; title: string; detail: string; action: string; onClick: () => void }[] = [
    {
      done: !!(p.email && (p.work.length || p.education.length)),
      title: "Profile and screening answers",
      detail: p.email ? `${p.first_name} ${p.last_name} · ${p.email}` : "Contact details, work authorization and the questions forms ask.",
      action: "Review",
      onClick: () => go({ view: "profile", tab: "identity" }),
    },
    {
      done: snapshot.resumes.length > 0,
      title: "Resume",
      detail: snapshot.resumes.length
        ? `${plural(snapshot.resumes.length, "resume")} ready to attach`
        : "Upload the PDF that applications attach.",
      action: snapshot.resumes.length ? "Manage" : "Upload",
      onClick: () => go({ view: "profile", tab: "resumes" }),
    },
    {
      done: snapshot.config.mimo,
      title: "Model key",
      detail: snapshot.config.mimo
        ? `${snapshot.config.model_provider ?? "The model provider"} key found`
        : "Reads job pages and writes answers. Paste your AI key in Settings › Connections.",
      action: "Model settings",
      onClick: () => go({ view: "settings", tab: "system" }),
    },
    {
      done: email,
      title: "Email for verification codes",
      detail: email
        ? "Codes and confirmation emails are read automatically."
        : "Workday, Oracle and iCIMS email a code before you can apply. Connect the inbox you apply with.",
      action: email ? "Settings" : "Connect email",
      onClick: () => go({ view: "settings", tab: "email" }),
    },
    {
      done: p.target_roles.length > 0,
      title: "What to look for",
      detail: p.target_roles.length
        ? `${p.target_roles.join(", ")} · ${p.target_locations.join(", ") || "Anywhere in the USA"}`
        : "Choose the roles discovery searches for.",
      action: "Adjust",
      onClick: () => go({ view: "profile", tab: "experience", filter: "preferences" }),
    },
    {
      done: submitting,
      title: "Automatic submission",
      detail: submitting
        ? "Applications submit once every required answer is verified."
        : "Off: applications fill every page and stop before the final submit.",
      action: submitting ? "Settings" : pending === "submit-on" ? "Turning on…" : "Turn on",
      onClick: submitting
        ? () => go({ view: "settings", tab: "system" })
        : () =>
            run(
              "submit-on",
              async () => {
                // Read the current controls right before writing them back.
                const fresh = await api<Snapshot["control"]>("/control");
                await api("/control", "PUT", { ...fresh, auto_submit: true });
                await refresh();
              },
              "Automatic submission turned on.",
            ),
    },
    {
      done: false,
      title: "First application",
      detail: "Paste a job link in Jobs › Pipeline (it goes straight into the queue), pick jobs in Discovered, or start a batch above.",
      action: "Add job link",
      onClick: () => window.dispatchEvent(new Event(OPEN_JOB_LINK)),
    },
  ];
  return (
    <Panel title="Get started" actions={<span className="ax-note">{steps.filter((s) => s.done).length} of {steps.length} done</span>}>
      <ul className="ax-rows">
        {steps.map((s) => (
          <li key={s.title} className="ax-row">
            <span className="ax-row-main">
              <strong>{s.title}</strong>
              <small>{s.detail}</small>
            </span>
            <span className="ax-row-end">
              {s.done && <span className="ax-note">Done</span>}
              <Button size="sm" variant={s.done ? "ghost" : "secondary"} onClick={s.onClick} disabled={pending !== null}>
                {s.action}
              </Button>
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

// ---------- what waits, and what is left to apply to ----------
function Backlog({ work }: { work: WorkingState }) {
  const { snapshot, counts, go, openJob } = useWorkspace();
  const now = useNow();
  const rt = snapshot.autopilot_runtime || {};
  const active = (rt.state || snapshot.autopilot?.state) === "active";
  const inv = rt.inventory || {};
  const fits = inv.fits || {};
  const { waiting, emailWaits } = work;
  // Each figure opens the list of that size (the Pipeline and Discovered tabs' own numbers; the page once showed three
  // different "fresh" counts from three computations, 2026-10-23).
  const tally = (n: number, label: string, to?: { tab: string; filter?: string }): ReactNode =>
    n && to ? (
      <button type="button" className="ax-link" onClick={() => go({ view: "jobs", ...to })}>
        {n.toLocaleString()} {label}
      </button>
    ) : (
      <span className="ax-note">
        {n.toLocaleString()} {label}
      </span>
    );
  const line = (items: ReactNode[]): ReactNode => (
    <>
      {items.map((item, i) => (
        <span key={i}>
          {i > 0 && " · "}
          {item}
        </span>
      ))}
    </>
  );
  // Only what is there: a row of zeros is noise on the one page that is already full.
  const waitingNow = [
    waiting.queued + waiting.starting > 0 && tally(waiting.queued + waiting.starting, "queued", { tab: "pipeline", filter: "active" }),
    waiting.repairing > 0 && tally(waiting.repairing, "being fixed", { tab: "pipeline", filter: "repair" }),
    waiting.answers > 0 && tally(waiting.answers, waiting.answers === 1 ? "needs your answer" : "need your answer", { tab: "pipeline", filter: "needs" }),
    waiting.email > 0 && tally(waiting.email, "waiting for email"),
  ].filter(Boolean);
  const rows: [ReactNode, ReactNode][] = [["Waiting", waitingNow.length ? line(waitingNow) : <span className="ax-note">Nothing waiting</span>]];
  if (active && ((rt.blocked ?? 0) > 0 || (rt.failed ?? 0) > 0))
    rows.push([
      "This batch",
      line([
        (rt.blocked ?? 0) > 0 && tally(rt.blocked ?? 0, "blocked or unconfirmed", { tab: "pipeline", filter: "repair" }),
        (rt.failed ?? 0) > 0 && tally(rt.failed ?? 0, "not submitted", { tab: "pipeline", filter: "repair" }),
      ].filter(Boolean)),
    ]);
  rows.push([
    "Discovered",
    line([
      (inv.unattempted ?? 0) === 0
        ? tally(0, "eligible")
        : tally(fits.eligible ?? 0, "eligible", { tab: "discovered", filter: "eligible" }),
      tally(fits.unranked ?? 0, "awaiting ranking", (inv.unattempted ?? 0) === 0 ? undefined : { tab: "discovered", filter: "unranked" }),
      tally(counts.discovered ?? inv.discovered ?? 0, "not applied to", { tab: "discovered" }),
      ...(inv.sources_due ? [<span className="ax-note">{plural(inv.sources_due, "source")} due for a check</span>] : []),
    ]),
  ]);
  return (
    <section aria-label="Pipeline">
      <Facts items={rows} />
      {emailWaits.length > 0 && (
        <ul className="ax-rows" aria-label="Waiting for email">
          {emailWaits.slice(0, 5).map(({ c, job }) => {
            const minutes = (now - new Date(c.created_at).getTime()) / 60000;
            return (
              <li key={c.id} className="ax-row">
                {job ? (
                  <button type="button" className="ax-link" onClick={() => openJob(job.id, c.run_id)}>
                    {job.company}, {job.title}
                  </button>
                ) : (
                  <span>Run {c.run_id.slice(0, 8)}</span>
                )}
                <span className="ax-note">
                  {c.kind === "auto" ? "email verification" : c.kind.replaceAll("_", " ")} · {minutes > 10 ? "delayed, " : ""}
                  waiting {Math.round(minutes)}m
                </span>
              </li>
            );
          })}
          {emailWaits.length > 5 && <li className="ax-row ax-note">and {emailWaits.length - 5} more</li>}
        </ul>
      )}
    </section>
  );
}

// Finished applications, newest first, ten to a page. The snapshot carries the live rows only, so this is the recent
// activity; every application is under Applications.
function Activity() {
  const { rows, openJob, go } = useWorkspace();
  const now = useNow();
  const recent = useMemo(
    () =>
      rows
        .filter((r) => r.run?.finished_at && r.state !== "paused")
        .sort((a, b) => b.run!.finished_at!.localeCompare(a.run!.finished_at!)),
    [rows],
  );
  const paging = usePagination(recent, "", 10);
  const columns: Column<(typeof recent)[number]>[] = [
    {
      key: "time",
      header: "Time",
      cell: (r) => {
        const finished = r.run!.finished_at!;
        const today = new Date(finished).toDateString() === new Date(now).toDateString();
        return (
          <time dateTime={finished} title={new Date(finished).toLocaleString()}>
            {today ? clock(finished) : day(finished)}
          </time>
        );
      },
    },
    { key: "status", header: "What happened", cell: (r) => <StatusPill state={r.state} /> },
    {
      key: "job",
      header: "Job",
      width: 520,
      truncate: true,
      cell: (r) => <JobCell company={r.job.company} role={r.job.title} onOpen={() => openJob(r.job.id, r.run!.id)} />,
    },
    {
      key: "reason",
      header: "Detail",
      hideBelow: 860,
      width: 300,
      truncate: true,
      cell: (r) => (r.state !== "confirmed" && r.run!.reason ? <Note>{r.run!.reason}</Note> : null),
    },
    { key: "platform", header: "Platform", hideBelow: 600, cell: (r) => <span className="mono">{r.job.ats}</span> },
  ];
  return (
    <Panel
      title="Latest activity"
      flush
      actions={
        <>
          <button type="button" className="ax-link" onClick={() => go({ view: "jobs" })}>
            All jobs
          </button>
          <button type="button" className="ax-link" onClick={() => go({ view: "ledger" })}>
            Applications
          </button>
        </>
      }
    >
      <Table
        label="Latest activity"
        columns={columns}
        rows={paging.items}
        rowKey={(r) => r.run!.id}
        onRowClick={(r) => openJob(r.job.id, r.run!.id)}
        empty={<Empty title="No finished applications yet" hint="Confirmed, unconfirmed and stopped applications appear here as they finish." />}
      />
      <Pagination paging={paging} label="Activity pages" sizes={false} />
    </Panel>
  );
}
