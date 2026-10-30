import { useMemo, useState, type ReactNode } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { appliedState, STATES, type JobRow } from "./model";
import { Pagination, usePagination } from "./pagination";
import { OPEN_JOB_LINK } from "./jobintake";
import { countQuestions } from "./questions";
import type { ApplicationWorkers, MailStatus, RuntimeHealth, Snapshot } from "./types";
import { ago, clock, day, elapsed, plural } from "./format";
import { Button, Empty, Panel, Table, type Column } from "./ui/index";
import { Facts, JobCell, Notice, Note, StatusPill } from "./worktools";
import "./styles/work.css";

const LIVE_MAIL = new Set(["pending", "matched", "consumed"]);

type Working = {
  key: string;
  company: string;
  role: string;
  detail: string;
  state: string;
  label?: string;
  open?: () => void;
  /** The job to pause, when it can be paused now. */
  pause?: string;
  /** When its attempt started, for the time on the job. */
  since?: string;
};

// The home page: one sentence on whether the batch is running, what is applying now, what waits, what just finished.
// The questions themselves are on Needs you; worker and host detail is in Settings › System.
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
    <div className="ax-page">
      <div className="ov-head">
        <State work={work} />
        <NeedsLine />
      </div>
      <FirstRun mail={mail.data} />
      <Workers work={work} />
      <Backlog work={work} />
      <Activity />
    </div>
  );
}

// ---------- the workers that are doing something ----------
// Only the workers that are working get a row; the idle ones are one count. Nine rows of "Status unavailable" said
// nothing (owner 2026-10-29).
function useWorking(mail: MailStatus | null, workers?: ApplicationWorkers) {
  const { snapshot, rows, counts, openJob } = useWorkspace();
  const runtimeWorkers = snapshot.autopilot_runtime?.application_workers || workers;
  const lanes = runtimeWorkers as (ApplicationWorkers & { custom_workers?: number }) | undefined;
  const limit = lanes
    ? lanes.normal_workers + lanes.specialist_workers + (lanes.custom_workers ?? 0)
    : Math.max(0, snapshot.config.workers || 0);
  const owners = rows
    .filter((r) => r.run && ["running", "submitting"].includes(r.run.state))
    .sort((a, b) => (a.run!.started_at || a.run!.created_at).localeCompare(b.run!.started_at || b.run!.created_at));
  const assignments = runtimeWorkers?.assignments || [];
  const assignedRuns = new Set(assignments.map((assignment) => assignment.run_id).filter(Boolean));
  // The snapshot carries every running and submitting application, so this is exact; a run without a slot still
  // counts as in progress.
  const working = Math.max(runtimeWorkers ? assignedRuns.size : 0, owners.length);
  const jobs = new Map(snapshot.jobs.map((j) => [j.id, j]));
  const runJob = new Map(snapshot.runs.map((r) => [r.id, r.job_id]));
  const emailWaits = (mail?.challenges || [])
    .filter((c) => LIVE_MAIL.has(c.state))
    .map((c) => ({ c, job: jobs.get(runJob.get(c.run_id) || "") }));

  const busy: Working[] = [];
  let unknown = 0;
  const phaseOf = (phase?: string) => {
    const text = (phase || "").replaceAll("_", " ");
    return text && !["running", "submitting", "idle"].includes(text) ? text : "";
  };
  Array.from({ length: limit }, (_, i) => {
    const assignment = assignments.find((worker) => worker.slot === i + 1);
    const row: JobRow | undefined = assignment?.run_id
      ? rows.find((owner) => owner.run?.id === assignment.run_id)
      : runtimeWorkers
        ? undefined
        : owners.find((owner) => owner.run?.worker_slot === i + 1);
    if (!row && !assignment?.run_id) {
      if (runtimeWorkers && !assignment) unknown += 1;
      return;
    }
    if (!row && assignment) {
      const job = assignment.job_id ? jobs.get(assignment.job_id) : undefined;
      busy.push({
        key: "assigned" + assignment.slot,
        company: job?.company || "Assigned application",
        role: job?.title || "Details loading",
        detail: [assignment.ats, phaseOf(assignment.phase)].filter(Boolean).join(" · "),
        state: assignment.state || "assigned",
        label: assignment.state ? undefined : "Assigned",
      });
      return;
    }
    if (!row) return;
    const r = row.run!;
    busy.push({
      key: r.id,
      company: row.job.company || "Employer",
      role: row.job.title,
      detail: [assignment?.ats || row.job.ats, phaseOf(assignment?.phase)].filter(Boolean).join(" · "),
      state: row.state,
      open: () => openJob(row.job.id, r.id),
      pause: r.state === "running" && row.state !== "pausing" ? row.job.id : undefined,
      since: r.started_at || r.created_at,
    });
  });
  return {
    busy,
    limit,
    working,
    idle: Math.max(0, limit - busy.length - unknown),
    unknown,
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

// ---------- the one sentence ----------
function State({ work }: { work: WorkingState }) {
  const { snapshot, counts, link, refresh } = useWorkspace();
  const { pending, run } = useAction();
  const now = useNow();
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
  const degraded = !!(rt.scheduler?.degraded || snapshot.scheduler_health?.degraded);
  // Applications you started yourself count too, so a batch can finish above its target ("560/477" read as a mistake).
  const progress = confirmed > target ? `${confirmed} confirmed, target ${target}` : `${confirmed} of ${target} confirmed`;
  const stopped = paused || (!rt.intake_enabled && rt.intake_reason === "Paused");

  // The worst thing that is true right now, in plain words (it was the sidebar's status dot).
  const [tone, sentence]: ["info" | "warn" | "bad", string] = link.lost
    ? ["bad", `Connection lost. Showing data from ${ago(link.updatedAt, now)}; retrying.`]
    : degraded
      ? ["bad", "Needs a restart. Automation is degraded; restart it to keep applying."]
      : paused
        ? ["warn", "Paused. No new applications start; progress is kept."]
        : state === "complete"
          ? ["info", `Target reached. ${progress}.`]
          : rt.intake_enabled
            ? ["info", `Applying. ${work.working} of ${work.limit} workers busy, ${progress}.`]
            : active
              ? ["warn", `Held. ${(rt.intake_reason || "New applications are not starting").replace(/\.$/, "")}. ${progress}.`]
              : ["info", work.limit ? `No batch running. ${plural(work.limit, "worker")} ready.` : "No batch running."];

  const start = (value: number) =>
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

  const action = !workers ? null : !active ? (
    <form
      className="ov-start"
      onSubmit={(e) => {
        e.preventDefault();
        start(Math.max(1, Math.min(500, Math.round(goal || 25))));
      }}
    >
      <label className="ov-target">
        <span>Apply until</span>
        <input
          className="ax-control"
          type="number"
          min={1}
          max={500}
          step={1}
          required
          value={goal}
          onChange={(e) => setGoal(Number(e.target.value))}
          aria-describedby="target-hint"
        />
        <span>are confirmed</span>
      </label>
      <Button type="submit" variant="primary" loading={pending === "start"} disabled={pending !== null || noResume}>
        Start batch
      </Button>
      <span id="target-hint" className="sr-only">
        Up to 500 confirmed applications.
      </span>
    </form>
  ) : stopped ? (
    <Button variant="primary" loading={pending === "start"} disabled={pending !== null} onClick={() => start(target)}>
      Resume batch
    </Button>
  ) : (
    <div className="ov-start">
      {degraded && (
        <Button variant="primary" loading={pending === "start"} disabled={pending !== null} onClick={() => start(target)}>
          Restart automation
        </Button>
      )}
      <Button loading={pending === "pause"} disabled={pending !== null} onClick={pause} aria-describedby="pause-hint">
        Pause new applications
      </Button>
      <span id="pause-hint" className="sr-only">
        Stops new applications and holds running ones before their final submit. Progress is kept.
      </span>
    </div>
  );

  return (
    <>
      <Notice lead tone={tone} action={action}>
        {sentence}
      </Notice>
      {!workers && (
        <Notice tone="warn">
          Application workers are disabled in this installation's configuration, so a batch can't run. Enable workers on
          the server and restart.
        </Notice>
      )}
      {active && (
        <p className="ax-note ov-meta">
          {[
            batch.started_at && `Started ${day(batch.started_at)}`,
            rt.confirmed_by_owner ? `${rt.confirmed_by_batch ?? 0} by the batch, ${rt.confirmed_by_owner} you started` : "",
            `${plural(rt.attempted_jobs ?? 0, "job")} attempted`,
            `${rt.remaining ?? Math.max(0, target - confirmed)} to go`,
            rt.last_tick && `checked ${ago(rt.last_tick, now)}`,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      )}
      {!active && noResume && workers && <ResumeNotice />}
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

// One line, not the questions: they are on the Needs you page.
function NeedsLine() {
  const { snapshot, go } = useWorkspace();
  const n = countQuestions(snapshot.reviews);
  if (!n) return null;
  return (
    <Notice
      tone="warn"
      action={
        <button type="button" className="ax-link" onClick={() => go({ view: "needs" })}>
          Answer {n === 1 ? "it" : "them"}
        </button>
      }
    >
      {plural(n, "question")} {n === 1 ? "needs" : "need"} you.
    </Notice>
  );
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

function Workers({ work }: { work: WorkingState }) {
  const { refresh, markJobs } = useWorkspace();
  const { pending, run } = useAction();
  const now = useNow(1000);
  const { busy, limit, working, idle, unknown } = work;
  const columns: Column<Working>[] = [
    {
      key: "job",
      header: "Job",
      width: 420,
      truncate: true,
      cell: (w) => (w.open ? <JobCell company={w.company} role={w.role} onOpen={w.open} /> : <span>{w.company}</span>),
    },
    {
      key: "detail",
      header: "Details",
      hideBelow: 600,
      width: 200,
      truncate: true,
      cell: (w) => <Note>{[w.detail, w.since ? elapsed(w.since, now) : ""].filter(Boolean).join(" · ")}</Note>,
    },
    { key: "status", header: "Status", cell: (w) => <StatusPill state={w.state} label={w.label} /> },
    {
      key: "act",
      header: <span className="sr-only">Actions</span>,
      width: "1%",
      pin: true,
      cell: (w) =>
        w.pause ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={pending !== null}
            loading={pending === w.key}
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
            Pause
          </Button>
        ) : null,
    },
  ];
  const idleLine =
    busy.length === 0 && unknown === 0
      ? limit
        ? `All ${limit} workers are idle. They pick up work when a batch runs or you add a job link.`
        : "No workers are configured."
      : [idle > 0 && `${idle} idle`, unknown > 0 && `status unavailable for ${unknown}`].filter(Boolean).join(" · ");
  return (
    <Panel title="Applying now" actions={<span className="ax-note">{working} of {limit} working</span>} flush={busy.length > 0}>
      {busy.length > 0 && (
        <Table label="Applying now" columns={columns} rows={busy} rowKey={(w) => w.key} />
      )}
      {idleLine && <p className="ax-note ov-idle">{idleLine}</p>}
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
  const rows: [ReactNode, ReactNode][] = [
    [
      "Waiting",
      line([
        tally(waiting.queued + waiting.starting, "queued", { tab: "pipeline", filter: "active" }),
        tally(waiting.repairing, "being fixed", { tab: "pipeline", filter: "repair" }),
        tally(waiting.answers, waiting.answers === 1 ? "needs your answer" : "need your answer", { tab: "pipeline", filter: "needs" }),
        tally(waiting.email, "waiting for email"),
      ]),
    ],
  ];
  if (active)
    rows.push([
      "This batch",
      line([
        tally(rt.blocked ?? 0, "blocked or unconfirmed", { tab: "pipeline", filter: "repair" }),
        tally(rt.failed ?? 0, "not submitted", { tab: "pipeline", filter: "repair" }),
      ]),
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
      key: "job",
      header: "Job",
      width: 420,
      truncate: true,
      cell: (r) => <JobCell company={r.job.company} role={r.job.title} onOpen={() => openJob(r.job.id, r.run!.id)} />,
    },
    { key: "status", header: "Status", cell: (r) => <StatusPill state={r.state} /> },
    {
      key: "reason",
      header: "Detail",
      hideBelow: 600,
      width: 300,
      truncate: true,
      cell: (r) => (r.state !== "confirmed" && r.run!.reason ? <Note>{r.run!.reason}</Note> : null),
    },
    {
      key: "time",
      header: "When",
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
