import { useMemo, useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { appliedState, STATES } from "./model";
import { Pagination, usePagination } from "./pagination";
import { OPEN_JOB_LINK } from "./jobintake";
import { groupQuestions, QuestionCard } from "./questions";
import type { ApplicationWorkers, MailStatus, RuntimeHealth, Snapshot } from "./types";
import {
  ago,
  clock,
  day,
  Dot,
  elapsed,
  Empty,
  KeyValues,
  plural,
  StatePill,
  Tape,
} from "./ui";

const LIVE_MAIL = new Set(["pending", "matched", "consumed"]);

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
    <div className="batch">
      <BatchStatus />
      <div className="batch-grid">
        <div className="batch-main">
          <FirstRun mail={mail.data} />
          <Owners mail={mail.data} workers={health.data?.application_workers} />
          <Outcomes />
        </div>
        <aside className="batch-side" aria-label="Attention and system">
          <NeedsYou />
          <SystemStatus
            snapshot={snapshot}
            health={health.data}
            healthError={health.error}
            mail={mail.data}
          />
        </aside>
      </div>
    </div>
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
    <section className="block firstrun" aria-labelledby="firstrun-title">
      <header className="block-head">
        <h2 id="firstrun-title">Get started</h2>
        <span className="muted">
          {steps.filter((s) => s.done).length} of {steps.length} done
        </span>
      </header>
      <ol className="firstrun-steps">
        {steps.map((s) => (
          <li key={s.title} className={s.done ? "done" : ""}>
            <Dot tone={s.done ? "ok" : "idle"} />
            <span>
              <strong>{s.title}</strong>
              <small className="muted">{s.detail}</small>
            </span>
            <button className={s.done ? "btn sm quiet" : "btn sm"} onClick={s.onClick} disabled={pending !== null}>
              {s.action}
            </button>
          </li>
        ))}
      </ol>
    </section>
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
  const degraded = !!(
    rt.scheduler?.degraded || snapshot.scheduler_health?.degraded
  );
  const reason = paused
    ? "Paused"
    : rt.intake_reason ||
      (active ? "Held" : state === "complete" ? "Complete" : "Not started");
  const tone = paused
    ? "idle"
    : state === "complete"
      ? "ok"
      : rt.intake_enabled
        ? "run"
        : active
          ? "fix"
          : "idle";
  const next =
    paused && active
      ? "Saved progress is kept. Resuming continues this batch; individual jobs stay controllable."
      : rt.next_action ||
        "Start a batch to discover, rank and apply automatically.";

  const start = (value: number) =>
    run(
      "start",
      async () => {
        const result = await api("/job-board/autopilot/start", "POST", {
          target: value,
        });
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

  const controls = !workers ? (
    <p className="status-warn">
      Application workers are disabled in this installation's configuration, so
      a batch can't run. Enable workers on the server and restart.
    </p>
  ) : !active ? (
    <form
      className="start"
      onSubmit={(e) => {
        e.preventDefault();
        start(Math.max(1, Math.min(500, Math.round(goal || 25))));
      }}
    >
      <label className="start-target">
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
        <span>are confirmed</span>
      </label>
      <button className="btn primary" disabled={pending !== null || noResume}>
        {pending === "start" ? "Starting…" : "Start batch"}
      </button>
      <small id="target-hint" className="muted">
        Up to 500 confirmed applications.
      </small>
    </form>
  ) : paused || (!rt.intake_enabled && reason === "Paused") ? (
    <button
      className="btn primary"
      disabled={pending !== null}
      onClick={() => start(target)}
    >
      {pending === "start" ? "Resuming…" : "Resume batch"}
    </button>
  ) : (
    <div className="status-buttons">
      {degraded && (
        <button
          className="btn"
          disabled={pending !== null}
          onClick={() => start(target)}
        >
          {pending === "start" ? "Reconnecting…" : "Restart automation"}
        </button>
      )}
      <button
        className="btn"
        disabled={pending !== null}
        onClick={pause}
        aria-describedby="pause-hint"
      >
        {pending === "pause" ? "Pausing…" : "Pause new applications"}
      </button>
      <span id="pause-hint" className="sr-only">
        Stops new applications and holds running ones before their final submit.
        Progress is kept.
      </span>
    </div>
  );

  return (
    <section className="status" aria-labelledby="batch-title">
      <div className="status-head">
        <h1 id="batch-title" className="count">
          {active || state === "complete" ? (
            <>
              <span className="count-n">{confirmed}</span>
              <span className="count-of">
                of {target} confirmed
                <span className="sr-only"> in this batch</span>
              </span>
            </>
          ) : (
            <span className="count-idle">No batch running</span>
          )}
        </h1>
        <div className="status-controls">{controls}</div>
      </div>
      <Tape
        confirmed={active || state === "complete" ? confirmed : 0}
        target={active || state === "complete" ? target : goal || 25}
      />
      <p className="status-line">
        <Dot tone={tone} />
        <strong>{reason}</strong>
        <span>{next}</span>
      </p>
      {active && (
        <p className="status-meta">
          {batch.started_at && <>Started {day(batch.started_at)} · </>}
          {!!rt.confirmed_by_owner && (
            <>
              {rt.confirmed_by_batch ?? 0} by the batch, {rt.confirmed_by_owner}{" "}
              you started ·{" "}
            </>
          )}
          {plural(rt.attempted_jobs ?? 0, "job")} attempted ·{" "}
          {rt.remaining ?? Math.max(0, target - confirmed)} to go
          {rt.last_tick && <> · checked {ago(rt.last_tick, now)}</>}
        </p>
      )}
      {!active && noResume && workers && (
        <p className="status-warn">
          Upload a resume first: ranking selects one of your resumes for each
          job.{" "}
          <button
            className="link"
            onClick={() => go({ view: "setup", tab: "resumes" })}
          >
            Add a resume
          </button>
        </p>
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
  const link = (filter: string, n: number, label: string, tone: string) => (
    <button
      className={"tally" + (n ? "" : " zero")}
      onClick={() => go({ view: "jobs", tab: "pipeline", filter })}
    >
      <Dot tone={n ? tone : "idle"} />
      <b>{n}</b> {label}
    </button>
  );
  const exhausted = (inv.unattempted ?? 0) === 0;
  return (
    <div className="tallies">
      <div className="tally-row" aria-label="This batch">
        <span className="tally-label">This batch</span>
        {link("needs", rt.awaiting_answer ?? 0, "need your answer", "ask")}
        {link("repair", rt.blocked ?? 0, "blocked or unconfirmed", "fix")}
        {link("repair", rt.failed ?? 0, "not submitted", "bad")}
        {link("active", rt.queued ?? 0, "queued", "run")}
        {!!rt.waiting_email && link("active", rt.waiting_email, "waiting for an email", "hold")}
      </div>
      <div className="tally-row" aria-label="Inventory">
        <span className="tally-label">Inventory</span>
        {exhausted ? (
          <span className="tally-text">
            No eligible jobs waiting. Discovery continues and the batch picks up
            new postings automatically.
          </span>
        ) : (
          <>
            <button
              className="tally"
              onClick={() =>
                go({ view: "jobs", tab: "discovered", filter: "eligible" })
              }
            >
              <b>{fits.eligible ?? 0}</b> eligible
            </button>
            <button
              className="tally"
              onClick={() =>
                go({ view: "jobs", tab: "discovered", filter: "unranked" })
              }
            >
              <b>{fits.unranked ?? 0}</b> awaiting ranking
            </button>
          </>
        )}
        <button
          className="tally"
          onClick={() => go({ view: "jobs", tab: "discovered" })}
        >
          <b>{counts.discovered ?? inv.discovered ?? 0}</b> discovered, not applied to
        </button>
        {!!inv.sources_due && (
          <span className="tally-text">
            {plural(inv.sources_due, "source")} due for a check
          </span>
        )}
      </div>
    </div>
  );
}

function Owners({ mail, workers }: { mail: MailStatus | null; workers?: ApplicationWorkers }) {
  const { snapshot, rows, counts, openJob, go, refresh, markJobs } = useWorkspace();
  const { pending, run } = useAction();
  const now = useNow(1000);
  const runtimeWorkers = snapshot.autopilot_runtime?.application_workers || workers;
  const limit = runtimeWorkers
    ? runtimeWorkers.normal_workers + runtimeWorkers.specialist_workers
    : Math.max(0, snapshot.config.workers || 0);
  const owners = rows
    .filter((r) => r.run && ["running", "submitting"].includes(r.run.state))
    .sort((a, b) =>
      (a.run!.started_at || a.run!.created_at).localeCompare(
        b.run!.started_at || b.run!.created_at,
      ),
    );
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
  const idle = { regular: 0, specialist: 0 };

  return (
    <section className="block" aria-labelledby="owners-title">
      <header className="block-head">
        <h2 id="owners-title">Applying now</h2>
        <span className="muted">
          {activeCount} of {limit} in progress
          {runtimeWorkers && ` · ${runtimeWorkers.normal_workers} regular + ${runtimeWorkers.specialist_workers} difficult-flow`}
        </span>
      </header>
      <ol className="owners">
        {Array.from({ length: limit }, (_, i) => {
          const assignment = assignments.find((worker) => worker.slot === i + 1);
          const row = assignment?.run_id
            ? rows.find((owner) => owner.run?.id === assignment.run_id)
            : runtimeWorkers
              ? undefined
              : owners.find((owner) => owner.run?.worker_slot === i + 1);
          const lane = assignment?.lane === "specialist"
            ? "Difficult-flow"
            : assignment?.lane === "normal"
              ? "Regular"
              : "Worker";
          if (!row && !assignment?.run_id) {
            const status =
              assignment?.state && assignment.state !== "idle"
                ? assignment.state.replaceAll("_", " ")
                : runtimeWorkers && !assignment
                  ? "Status unavailable"
                  : "";
            // Idle slots are summarized in one line below; only slots doing something get a row.
            if (!status) {
              idle[lane === "Difficult-flow" ? "specialist" : "regular"] += 1;
              return null;
            }
            return (
              <li className="owner free" key={"free" + i}>
                <span className="owner-n">{i + 1}</span>
                <span className="owner-free">
                  {lane} · {status}
                </span>
              </li>
            );
          }
          if (!row && assignment)
            return (
              <li className="owner busy" key={"assigned" + assignment.slot}>
                <span className="owner-n">{assignment.slot}</span>
                <span className="owner-job">
                  <strong>{assignment.job_id ? jobs.get(assignment.job_id)?.company || "Assigned application" : "Assigned application"}</strong>
                  <span>{assignment.job_id ? jobs.get(assignment.job_id)?.title || "Application details loading" : "Application details loading"}</span>
                </span>
                <span className="owner-meta">
                  {lane} · {assignment.ats}
                  {assignment.phase && ` · ${assignment.phase.replaceAll("_", " ")}`}
                </span>
                <StatePill state={assignment.state || "assigned"} label={assignment.state ? undefined : "Assigned"} />
              </li>
            );
          if (!row) return null;
          const r = row.run!;
          const pausing = row.state === "pausing";
          return (
            <li className="owner busy" key={r.id}>
              <span className="owner-n">{i + 1}</span>
              <button
                className="owner-job link"
                onClick={() => openJob(row.job.id, r.id)}
              >
                <strong>{row.job.company || "Employer"}</strong>
                <span>{row.job.title}</span>
              </button>
              <span className="owner-meta">
                {lane} · {assignment?.ats || row.job.ats} · {elapsed(r.started_at || r.created_at, now)}
                {assignment?.phase && ` · ${assignment.phase.replaceAll("_", " ")}`}
              </span>
              <StatePill state={row.state} />
              <span className="owner-actions">
                {r.state === "running" && !pausing && (
                  <button
                    className="btn sm"
                    disabled={pending !== null}
                    onClick={() =>
                      run(
                        r.id,
                        async () => {
                          const res = await api<{ message: string; state?: string }>(
                            `/jobs/${row.job.id}/pause`,
                            "POST",
                          );
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
                    {pending === r.id ? "Pausing…" : "Pause"}
                  </button>
                )}
              </span>
            </li>
          );
        })}
        {idle.regular + idle.specialist > 0 && (
          <li className="owner free">
            <span className="owner-n" aria-hidden="true">
              ·
            </span>
            <span className="owner-free">
              {idle.regular + idle.specialist === limit ? `All ${limit} slots idle` : `${idle.regular + idle.specialist} more idle`}
              {runtimeWorkers &&
                activeCount > 0 &&
                ` · ${[idle.regular && `${idle.regular} regular`, idle.specialist && `${idle.specialist} difficult-flow`].filter(Boolean).join(", ")}`}
              {activeCount === 0 && ". Slots pick up work when a batch runs or you add a job link."}
            </span>
          </li>
        )}
      </ol>
      <p className="released">
        <span className="muted">Waiting:</span>{" "}
        <button
          className="link"
          onClick={() =>
            go({ view: "jobs", tab: "pipeline", filter: "active" })
          }
        >
          {queued} queued{starting ? ` · ${starting} waiting to start` : ""}
        </button>{" "}
        ·{" "}
        <button
          className="link"
          onClick={() =>
            go({ view: "jobs", tab: "pipeline", filter: "repair" })
          }
        >
          {repairing} being fixed
        </button>{" "}
        ·{" "}
        <button
          className="link"
          onClick={() => go({ view: "jobs", tab: "pipeline", filter: "needs" })}
        >
          {answers} waiting for your answer
        </button>
        {/* Runs parked on an emailed code or link (server count, as in the Queue line); the list below names them. */}
        {" · "}
        {counts.states.waiting_email ?? emailWaits.length} waiting for email
      </p>
      {emailWaits.length > 0 && (
        <ul className="waits" aria-label="Waiting for email">
          {emailWaits.map(({ c, job }) => {
            const minutes = (now - new Date(c.created_at).getTime()) / 60000;
            return (
              <li key={c.id}>
                <Dot tone={minutes > 10 ? "fix" : "run"} />
                {job ? (
                  <button
                    className="link"
                    onClick={() => openJob(job.id, c.run_id)}
                  >
                    {job.company} — {job.title}
                  </button>
                ) : (
                  <span>Run {c.run_id.slice(0, 8)}</span>
                )}
                <span className="muted">
                  {c.kind === "auto"
                    ? "email verification"
                    : c.kind.replaceAll("_", " ")}{" "}
                  · {minutes > 10 ? "delayed, " : ""}waiting{" "}
                  {Math.round(minutes)}m
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function Outcomes() {
  const { rows, openJob, go } = useWorkspace();
  const now = useNow();
  const recent = useMemo(
    () =>
      rows
        .filter((r) => r.run?.finished_at && r.state !== "paused")
        .sort((a, b) => b.run!.finished_at!.localeCompare(a.run!.finished_at!))
        .slice(0, 10),
    [rows],
  );
  return (
    <section className="block" aria-labelledby="outcomes-title">
      <header className="block-head">
        <h2 id="outcomes-title">Latest outcomes</h2>
        <span className="block-links">
          <button className="link" onClick={() => go({ view: "jobs" })}>
            All jobs
          </button>
          <button className="link" onClick={() => go({ view: "ledger" })}>
            Ledger
          </button>
        </span>
      </header>
      {recent.length ? (
        <ul className="feed">
          {recent.map((r) => {
            const finished = r.run!.finished_at!;
            const today =
              new Date(finished).toDateString() ===
              new Date(now).toDateString();
            return (
              <li key={r.run!.id}>
                <time
                  dateTime={finished}
                  title={new Date(finished).toLocaleString()}
                >
                  {today ? clock(finished) : day(finished)}
                </time>
                <StatePill state={r.state} />
                <button
                  className="feed-job link"
                  onClick={() => openJob(r.job.id, r.run!.id)}
                >
                  <strong>{r.job.company}</strong>
                  <span>{r.job.title}</span>
                </button>
                {r.state !== "confirmed" && r.run!.reason && (
                  <span className="feed-reason">{r.run!.reason}</span>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <Empty title="No finished applications yet">
          Confirmed, unconfirmed and stopped applications appear here as they
          finish them.
        </Empty>
      )}
    </section>
  );
}

function NeedsYou() {
  const { snapshot } = useWorkspace();
  const jobFor = useMemo(() => {
    const runs = new Map(snapshot.runs.map((r) => [r.id, r.job_id]));
    const jobs = new Map(snapshot.jobs.map((j) => [j.id, j]));
    return (runId: string) => jobs.get(runs.get(runId) || "");
  }, [snapshot]);
  const groups = groupQuestions(snapshot.reviews, jobFor);
  const paging = usePagination(groups, "", 3);
  const [expanded] = useState(true);
  return (
    <section
      className={"block needs" + (groups.length ? " has" : "")}
      aria-labelledby="needs-title"
      id="questions"
    >
      <header className="block-head">
        <h2 id="needs-title">Needs you</h2>
        <span className="muted">
          {groups.length
            ? plural(groups.length, "question")
            : "Nothing waiting"}
        </span>
      </header>
      {groups.length ? (
        <>
          <p className="block-note">
            You're asked only for personal details your profile doesn't have. Other applications keep going while these wait.
          </p>
          {expanded &&
            paging.items.map((g) => (
              <QuestionCard key={g.review.id} group={g} />
            ))}
          <Pagination paging={paging} label="Question pages" sizes={false} />
        </>
      ) : (
        <p className="block-note">
          If an application is missing a required personal fact, it appears here
          with the page it came from. Site and control problems are repaired
          automatically and never turned into questions.
        </p>
      )}
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
  const boxes =
    mail?.mailboxes.filter((m) => m.state === "connected").length ?? 0;
  const cfg = snapshot.config;
  const items: [React.ReactNode, React.ReactNode][] = [
    [
      "Automation",
      <span className={scheduler.degraded ? "warn-text" : ""}>
        {scheduler.degraded ? "Needs a restart" : scheduler.state === "active" ? "Running" : scheduler.state || "Unknown"}
        {beat && <span className="muted"> · checked {ago(beat, now)}</span>}
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
        <span className="muted">Unavailable</span>
      ) : (
        <span className="muted">Checking…</span>
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
        <span className="warn-text">Not connected</span>
      ),
    ],
    [
      "Telegram",
      cfg.telegram ? (
        telegram || "Configured"
      ) : (
        <span className="muted">Off (optional)</span>
      ),
    ],
    [
      "Today",
      `${snapshot.stats.submissions_reserved}/${cfg.daily_limit} submissions · $${snapshot.stats.spend_today.toFixed(2)} of $${cfg.budget.toFixed(2)}`,
    ],
  ];
  return (
    <section className="block system" aria-labelledby="system-title">
      <header className="block-head">
        <h2 id="system-title">System</h2>
        <button
          className="link"
          onClick={() => go({ view: "setup", tab: "system" })}
        >
          Details
        </button>
      </header>
      <KeyValues items={items} />
    </section>
  );
}
