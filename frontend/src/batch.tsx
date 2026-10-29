import { useMemo, useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { appliedState, STATES } from "./model";
import { Pagination, usePagination } from "./pagination";
import { OPEN_JOB_LINK } from "./jobintake";
import { countQuestions } from "./questions";
import type { ApplicationWorkers, MailStatus, RuntimeHealth, Snapshot } from "./types";
import { ago, clock, day, elapsed, plural } from "./ui";
import { Button, Empty, Panel, Table, type Column } from "./ui/index";
import { Notice, Note, StatusPill } from "./worktools";
import "./styles/work.css";

const LIVE_MAIL = new Set(["pending", "matched", "consumed"]);
const LANE: Record<string, string> = { normal: "Regular", specialist: "Difficult-flow", custom: "Custom-site" };

// The home page: is the batch running, what is happening now, what needs you. The questions themselves are on Needs
// you (they were repeated here as cards, which is why this page was long: owner 2026-10-29).
export function BatchView() {
  const { snapshot } = useWorkspace();
  const health = usePoll<RuntimeHealth>(
    (signal) => api("/runtime-health", "GET", undefined, signal),
    30000,
  );
  const mail = usePoll<MailStatus>(
    (signal) => api("/mail", "GET", undefined, signal),
    30000,
  );
  return (
    <div className="ax-page ov">
      <BatchStatus />
      <NeedsLine />
      <FirstRun mail={mail.data} />
      <div className="ov-cols">
        <div className="ov-main">
          <Workers mail={mail.data} workers={health.data?.application_workers} />
          <Activity />
        </div>
        <aside className="ov-side" aria-label="System">
          <SystemStatus snapshot={snapshot} health={health.data} healthError={health.error} mail={mail.data} />
        </aside>
      </div>
    </div>
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
        <Button size="sm" onClick={() => go({ view: "needs" })}>
          Open
        </Button>
      }
    >
      <strong>{plural(n, "question")}</strong> {n === 1 ? "needs" : "need"} you. Other applications keep going while they wait.
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
      onClick: () => go({ view: "setup", tab: "profile" }),
    },
    {
      done: snapshot.resumes.length > 0,
      title: "Resume",
      detail: snapshot.resumes.length
        ? `${plural(snapshot.resumes.length, "resume")} ready to attach`
        : "Upload the PDF that applications attach.",
      action: snapshot.resumes.length ? "Manage" : "Upload",
      onClick: () => go({ view: "setup", tab: "resumes" }),
    },
    {
      done: snapshot.config.mimo,
      title: "Model key",
      detail: snapshot.config.mimo
        ? `${snapshot.config.model_provider ?? "The model provider"} key found`
        : "Reads job pages and writes answers. Paste your AI key in Setup > Connections.",
      action: "Model settings",
      onClick: () => go({ view: "setup", tab: "system" }),
    },
    {
      done: email,
      title: "Email for verification codes",
      detail: email
        ? "Codes and confirmation emails are read automatically."
        : "Workday, Oracle and iCIMS email a code before you can apply. Connect the inbox you apply with.",
      action: email ? "Settings" : "Connect email",
      onClick: () => go({ view: "setup", tab: "email" }),
    },
    {
      done: p.target_roles.length > 0,
      title: "What to look for",
      detail: p.target_roles.length
        ? `${p.target_roles.join(", ")} · ${p.target_locations.join(", ") || "Anywhere in the USA"}`
        : "Choose the roles discovery searches for.",
      action: "Adjust",
      onClick: () => go({ view: "setup", tab: "profile", filter: "preferences" }),
    },
    {
      done: submitting,
      title: "Automatic submission",
      detail: submitting
        ? "Applications submit once every required answer is verified."
        : "Off: applications fill every page and stop before the final submit.",
      action: submitting ? "Settings" : pending === "submit-on" ? "Turning on…" : "Turn on",
      onClick: submitting
        ? () => go({ view: "setup", tab: "system" })
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
      <ol className="ov-steps">
        {steps.map((s) => (
          <li key={s.title} className={s.done ? "is-done" : ""}>
            <i className={"ov-dot" + (s.done ? " is-ok" : "")} aria-hidden="true" />
            <span>
              <strong>{s.title}</strong>
              <small className="ax-note">{s.detail}</small>
            </span>
            <Button size="sm" variant={s.done ? "ghost" : "secondary"} onClick={s.onClick} disabled={pending !== null}>
              {s.action}
            </Button>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

function BatchStatus() {
  const { snapshot, counts, refresh, go } = useWorkspace();
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
  const reason = paused
    ? "Paused"
    : rt.intake_reason || (active ? "Held" : state === "complete" ? "Complete" : "Not started");
  const tone = paused ? "idle" : state === "complete" ? "ok" : rt.intake_enabled ? "run" : active ? "fix" : "idle";
  const next =
    paused && active
      ? "Saved progress is kept. Resuming continues this batch; individual jobs stay controllable."
      : rt.next_action || "Start a batch to discover, rank and apply automatically.";

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

  const controls = !workers ? null : !active ? (
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
  ) : paused || (!rt.intake_enabled && reason === "Paused") ? (
    <Button variant="primary" loading={pending === "start"} disabled={pending !== null} onClick={() => start(target)}>
      Resume batch
    </Button>
  ) : (
    <div className="ov-buttons">
      {degraded && (
        <Button loading={pending === "start"} disabled={pending !== null} onClick={() => start(target)}>
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
  const shown = active || state === "complete";
  const pct = Math.min(100, Math.round((confirmed / Math.max(1, target)) * 100));

  return (
    <section className="ov-status" aria-label="Batch">
      <div className="ov-head">
        <p className="ov-count" aria-live="polite">
          {shown ? (
            <>
              <span className="ov-n">{confirmed.toLocaleString()}</span>
              <span className="ov-of">
                of {target.toLocaleString()} confirmed<span className="sr-only"> in this batch</span>
              </span>
            </>
          ) : (
            <span className="ov-idle">No batch running</span>
          )}
        </p>
        <div className="ov-controls">{controls}</div>
      </div>
      {shown && (
        <div className="ov-bar" role="progressbar" aria-label="Batch progress" aria-valuemin={0} aria-valuemax={target} aria-valuenow={Math.min(confirmed, target)}>
          <span style={{ width: pct + "%" }} className={state === "complete" ? "is-done" : ""} />
        </div>
      )}
      <p className="ov-line">
        <i className={"ov-dot is-" + tone} aria-hidden="true" />
        <strong>{reason}</strong>
        <span>{next}</span>
      </p>
      {!workers && (
        <Notice tone="warn">
          Application workers are disabled in this installation's configuration, so a batch can't run. Enable workers on
          the server and restart.
        </Notice>
      )}
      {active && (
        <p className="ax-note ov-meta">
          {batch.started_at && <>Started {day(batch.started_at)} · </>}
          {!!rt.confirmed_by_owner && (
            <>
              {rt.confirmed_by_batch ?? 0} by the batch, {rt.confirmed_by_owner} you started ·{" "}
            </>
          )}
          {plural(rt.attempted_jobs ?? 0, "job")} attempted · {rt.remaining ?? Math.max(0, target - confirmed)} to go
          {rt.last_tick && <> · checked {ago(rt.last_tick, now)}</>}
        </p>
      )}
      {!active && noResume && workers && (
        <Notice
          tone="warn"
          action={
            <Button size="sm" onClick={() => go({ view: "setup", tab: "resumes" })}>
              Add a resume
            </Button>
          }
        >
          Upload a resume first: ranking selects one of your resumes for each job.
        </Notice>
      )}
      {active && <Tallies />}
    </section>
  );
}

function Tallies() {
  const { snapshot, counts, go } = useWorkspace();
  const rt = snapshot.autopilot_runtime || {};
  const inv = rt.inventory || {};
  // The Discovered tab's own numbers (the same jobs and fit filters), so each link opens a list of that size.
  // The Batch page showed three different "fresh" figures from three computations (2026-10-23).
  const fits = inv.fits || {};
  const link = (filter: string, n: number, label: string) => (
    <button type="button" className={"ov-tally" + (n ? "" : " is-zero")} onClick={() => go({ view: "jobs", tab: "pipeline", filter })}>
      <b>{n}</b> {label}
    </button>
  );
  const exhausted = (inv.unattempted ?? 0) === 0;
  return (
    <div className="ov-tallies">
      <div className="ov-tally-row" role="group" aria-label="This batch">
        <span className="ov-tally-label">This batch</span>
        {link("needs", rt.awaiting_answer ?? 0, (rt.awaiting_answer ?? 0) === 1 ? "application needs your answer" : "applications need your answer")}
        {link("repair", rt.blocked ?? 0, "blocked or unconfirmed")}
        {link("repair", rt.failed ?? 0, "not submitted")}
        {link("active", rt.queued ?? 0, "queued")}
        {!!rt.waiting_email && link("active", rt.waiting_email, "waiting for an email")}
      </div>
      <div className="ov-tally-row" role="group" aria-label="Inventory">
        <span className="ov-tally-label">Inventory</span>
        {exhausted ? (
          <span className="ax-note">No eligible jobs waiting. Discovery continues and the batch picks up new postings automatically.</span>
        ) : (
          <>
            <button type="button" className="ov-tally" onClick={() => go({ view: "jobs", tab: "discovered", filter: "eligible" })}>
              <b>{fits.eligible ?? 0}</b> eligible
            </button>
            <button type="button" className="ov-tally" onClick={() => go({ view: "jobs", tab: "discovered", filter: "unranked" })}>
              <b>{fits.unranked ?? 0}</b> awaiting ranking
            </button>
          </>
        )}
        <button type="button" className="ov-tally" onClick={() => go({ view: "jobs", tab: "discovered" })}>
          <b>{counts.discovered ?? inv.discovered ?? 0}</b> discovered, not applied to
        </button>
        {!!inv.sources_due && <span className="ax-note">{plural(inv.sources_due, "source")} due for a check</span>}
      </div>
    </div>
  );
}

// Only the workers that are doing something get a row; the idle ones are one count. Nine rows of "Status unavailable"
// said nothing (owner 2026-10-29).
function Workers({ mail, workers }: { mail: MailStatus | null; workers?: ApplicationWorkers }) {
  const { snapshot, rows, counts, openJob, go, refresh, markJobs } = useWorkspace();
  const { pending, run } = useAction();
  const now = useNow(1000);
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
  const activeCount = Math.max(runtimeWorkers ? assignedRuns.size : 0, owners.length);
  // Exact counts over every job from the server (these were counted from the newest 1,000 jobs).
  const queued = counts.states.queued ?? 0;
  const repairing = counts.states.waiting_browser ?? 0;
  const answers = counts.groups.needs ?? 0;
  const starting = counts.states["pending:waiting"] ?? 0;
  const runJob = new Map(snapshot.runs.map((r) => [r.id, r.job_id]));
  const jobs = new Map(snapshot.jobs.map((j) => [j.id, j]));
  const emailWaits = (mail?.challenges || [])
    .filter((c) => LIVE_MAIL.has(c.state))
    .map((c) => ({ c, job: jobs.get(runJob.get(c.run_id) || "") }));
  const idle: Record<string, number> = {};
  let unknown = 0;
  const busy: React.ReactNode[] = [];
  Array.from({ length: limit }, (_, i) => {
    const assignment = assignments.find((worker) => worker.slot === i + 1);
    const row = assignment?.run_id
      ? rows.find((owner) => owner.run?.id === assignment.run_id)
      : runtimeWorkers
        ? undefined
        : owners.find((owner) => owner.run?.worker_slot === i + 1);
    const lane = LANE[assignment?.lane || ""] || "Worker";
    if (!row && !assignment?.run_id) {
      // A slot that reports something other than idle (paused, cooling down…) is worth a row.
      const status = assignment?.state && assignment.state !== "idle" ? assignment.state.replaceAll("_", " ") : "";
      if (status)
        busy.push(
          <li className="ov-worker is-quiet" key={"free" + i}>
            <span className="ov-slot">{i + 1}</span>
            <span className="ov-w-job">
              {lane} · {status}
            </span>
          </li>,
        );
      else if (runtimeWorkers && !assignment) unknown += 1;
      else idle[lane] = (idle[lane] || 0) + 1;
      return;
    }
    if (!row && assignment) {
      const job = assignment.job_id ? jobs.get(assignment.job_id) : undefined;
      busy.push(
        <li className="ov-worker" key={"assigned" + assignment.slot}>
          <span className="ov-slot">{assignment.slot}</span>
          <span className="ov-w-job">
            <strong>{job?.company || "Assigned application"}</strong> <span>{job?.title || "Application details loading"}</span>
          </span>
          <span className="ax-note ov-w-meta">
            {lane} · {assignment.ats}
            {assignment.phase && ` · ${assignment.phase.replaceAll("_", " ")}`}
          </span>
          <StatusPill state={assignment.state || "assigned"} label={assignment.state ? undefined : "Assigned"} />
          <span />
        </li>,
      );
      return;
    }
    if (!row) return;
    const r = row.run!;
    const pausing = row.state === "pausing";
    busy.push(
      <li className="ov-worker" key={r.id}>
        <span className="ov-slot">{i + 1}</span>
        <button type="button" className="ov-w-job ax-job" onClick={() => openJob(row.job.id, r.id)}>
          <strong>{row.job.company || "Employer"}</strong>
          <span>{row.job.title}</span>
        </button>
        <span className="ax-note ov-w-meta">
          {lane} · {assignment?.ats || row.job.ats} · {elapsed(r.started_at || r.created_at, now)}
          {assignment?.phase && ` · ${assignment.phase.replaceAll("_", " ")}`}
        </span>
        <StatusPill state={row.state} />
        <span className="ov-w-act">
          {r.state === "running" && !pausing && (
            <Button
              size="sm"
              variant="ghost"
              disabled={pending !== null}
              loading={pending === r.id}
              onClick={() =>
                run(
                  r.id,
                  async () => {
                    const res = await api<{ message: string; state?: string }>(`/jobs/${row.job.id}/pause`, "POST");
                    // Reads as pausing at once; the next poll confirms.
                    const shown = appliedState(res.state || "");
                    if (shown in STATES) markJobs([row.job.id], shown);
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
      </li>,
    );
  });
  const idleTotal = Object.values(idle).reduce((n, x) => n + x, 0);
  const idleText = Object.entries(idle)
    .map(([lane, n]) => `${n} ${lane.toLowerCase()}`)
    .join(", ");

  return (
    <Panel
      title="Applying now"
      actions={
        <span className="ax-note">
          {activeCount} of {limit} working
        </span>
      }
    >
      {busy.length > 0 && <ol className="ov-workers">{busy}</ol>}
      <p className="ax-note ov-idle-line">
        {idleTotal > 0 &&
          (busy.length === 0 && unknown === 0
            ? `All ${limit} workers are idle. They pick up work when a batch runs or you add a job link.`
            : `${idleTotal} idle (${idleText}).`)}
        {unknown > 0 && ` Status unavailable for ${unknown}.`}
        {idleTotal === 0 && unknown === 0 && busy.length === 0 && "No workers are configured."}
      </p>
      <p className="ov-waiting">
        <span className="ax-note">Waiting:</span>{" "}
        <button type="button" className="ax-link" onClick={() => go({ view: "jobs", tab: "pipeline", filter: "active" })}>
          {queued} queued{starting ? ` · ${starting} waiting to start` : ""}
        </button>
        {" · "}
        <button type="button" className="ax-link" onClick={() => go({ view: "jobs", tab: "pipeline", filter: "repair" })}>
          {repairing} being fixed
        </button>
        {" · "}
        <button type="button" className="ax-link" onClick={() => go({ view: "jobs", tab: "pipeline", filter: "needs" })}>
          {plural(answers, "application")} waiting for your answer
        </button>
        {/* Runs parked on an emailed code or link (server count, as in the Queue line); the list below names them. */}
        {" · "}
        {counts.states.waiting_email ?? emailWaits.length} waiting for email
      </p>
      {emailWaits.length > 0 && (
        <ul className="ov-waits" aria-label="Waiting for email">
          {emailWaits.slice(0, 5).map(({ c, job }) => {
            const minutes = (now - new Date(c.created_at).getTime()) / 60000;
            return (
              <li key={c.id}>
                {job ? (
                  <button type="button" className="ax-link" onClick={() => openJob(job.id, c.run_id)}>
                    {job.company} — {job.title}
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
          {emailWaits.length > 5 && <li className="ax-note">and {emailWaits.length - 5} more</li>}
        </ul>
      )}
    </Panel>
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
    { key: "status", header: "Status", cell: (r) => <StatusPill state={r.state} /> },
    {
      key: "job",
      header: "Job",
      width: 380,
      truncate: true,
      cell: (r) => (
        <button
          type="button"
          className="ax-job"
          onClick={(e) => {
            e.stopPropagation();
            openJob(r.job.id, r.run!.id);
          }}
        >
          <strong>{r.job.company}</strong>
          <span>{r.job.title}</span>
        </button>
      ),
    },
    {
      key: "reason",
      header: "Detail",
      width: 320,
      truncate: true,
      cell: (r) => (r.state !== "confirmed" && r.run!.reason ? <Note>{r.run!.reason}</Note> : null),
    },
  ];
  return (
    <section className="ov-activity" aria-labelledby="activity-title">
      <header className="ov-sec-head">
        <h2 id="activity-title">Latest activity</h2>
        <span className="ov-links">
          <button type="button" className="ax-link" onClick={() => go({ view: "jobs" })}>
            All jobs
          </button>
          <button type="button" className="ax-link" onClick={() => go({ view: "ledger" })}>
            Applications
          </button>
        </span>
      </header>
      <Table
        label="Latest activity"
        columns={columns}
        rows={paging.items}
        rowKey={(r) => r.run!.id}
        onRowClick={(r) => openJob(r.job.id, r.run!.id)}
        empty={<Empty title="No finished applications yet" hint="Confirmed, unconfirmed and stopped applications appear here as they finish." />}
      />
      <Pagination paging={paging} label="Activity pages" sizes={false} />
    </section>
  );
}

function SystemStatus({
  snapshot,
  health,
  healthError,
  mail,
}: {
  snapshot: Snapshot;
  health: RuntimeHealth | null;
  healthError: Error | null;
  mail: MailStatus | null;
}) {
  const { go } = useWorkspace();
  const now = useNow();
  const scheduler = health?.scheduler || snapshot.scheduler_health || {};
  const beat = health?.heartbeat?.at || snapshot.scheduler_heartbeat?.at;
  const telegram = snapshot.telegram_health?.state;
  const boxes = mail?.mailboxes.filter((m) => m.state === "connected").length ?? 0;
  const cfg = snapshot.config;
  const items: [React.ReactNode, React.ReactNode][] = [
    [
      "Automation",
      <span className={scheduler.degraded ? "ax-warn" : ""}>
        {scheduler.degraded ? "Needs a restart" : scheduler.state === "active" ? "Running" : scheduler.state || "Unknown"}
        {beat && <span className="ax-note"> · checked {ago(beat, now)}</span>}
      </span>,
    ],
    [
      "Notifications",
      health ? (
        health.outbox_pending ? (
          `${health.outbox_pending} waiting to send`
        ) : (
          "Clear"
        )
      ) : healthError ? (
        <span className="ax-note">Unavailable</span>
      ) : (
        <span className="ax-note">Checking…</span>
      ),
    ],
    [
      "Email",
      cfg.imap || boxes ? (
        [
          cfg.imap && "Gmail IMAP",
          boxes && plural(boxes, "mailbox", "mailboxes"),
        ]
          .filter(Boolean)
          .join(" · ")
      ) : (
        <span className="ax-warn">Not connected</span>
      ),
    ],
    [
      "Telegram",
      cfg.telegram ? (
        telegram || "Configured"
      ) : (
        <span className="ax-note">Off (optional)</span>
      ),
    ],
    [
      "Today",
      `${snapshot.stats.submissions_reserved}/${cfg.daily_limit} submissions · $${snapshot.stats.spend_today.toFixed(2)} of $${cfg.budget.toFixed(2)}`,
    ],
  ];
  return (
    <Panel
      title="System"
      actions={
        <button type="button" className="ax-link" onClick={() => go({ view: "setup", tab: "system" })}>
          Details
        </button>
      }
    >
      <dl className="ov-sys">
        {items.map(([k, v], i) => (
          <div key={i}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}
