import { useMemo, useState, type ReactNode } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { LiveWork, type LiveRow } from "./identity/LiveWork";
import { MasterKey } from "./identity/MasterKey";
import { confirmedToday, panelState, type PanelState } from "./identity/state";
import { progressFrom, progressOf } from "./identity/stages";
import "./identity/overview.css";
import { appliedState, evidenceLabel, STATES, type JobRow } from "./model";
import { OPEN_JOB_LINK } from "./jobintake";
import { countQuestions, groupQuestions, QuestionCard, type QuestionGroup } from "./questions";
import type { ApplicationWorkers, MailStatus, RuntimeHealth, Snapshot, Startable, SuccessReport } from "./types";
import { ago, day, employerOf, jobWords, plural, urlRole } from "./format";
import { Button, Dialog, Empty, Monogram, MonogramStack, Panel, Skeleton } from "./ui/index";
import { Notice } from "./worktools";
import "./styles/work.css";

const LIVE_MAIL = new Set(["pending", "matched", "consumed"]);

type Working = {
  key: string;
  slot: number;
  runId?: string;
  company: string;
  url?: string;
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

// The home page, in the order someone reads it: how today is going and the one control; what is running now; then what needs them
// and how the whole thing is doing. Every number opens the list behind it. Worker and host detail is in Settings › Advanced.
export function BatchView() {
  const { version } = useWorkspace();
  const health = usePoll<RuntimeHealth>((signal) => api("/runtime-health", "GET", undefined, signal), 30000);
  const mail = usePoll<MailStatus>((signal) => api("/mail", "GET", undefined, signal), 30000);
  // Which Ready jobs can start, and why the rest cannot (jobpilot/startable.py): the same screening the dispatcher uses.
  const ready = usePoll<Startable>((signal) => api("/startable", "GET", undefined, signal), 30000, [version]);
  const success = usePoll<SuccessReport>((signal) => api("/success?days=7", "GET", undefined, signal), 60000);
  const work = useWorking(mail.data, health.data?.application_workers);
  return (
    <div className="ax-page ov-page">
      <Hero work={work} models={health.data?.models} ready={ready.data} success={success.data} />
      <FirstRun mail={mail.data} />
      <Live work={work} checking={!health.data && !work.known} />
    </div>
  );
}

// ---------- the workers that are doing something ----------
function useWorking(mail: MailStatus | null, workers?: ApplicationWorkers) {
  const { snapshot, rows, counts, openJob } = useWorkspace();
  const runtimeWorkers = snapshot.autopilot_runtime?.application_workers || workers;
  const lanes = runtimeWorkers;
  const limit = lanes ? lanes.normal_workers + lanes.specialist_workers + (lanes.custom_workers ?? 0) : Math.max(0, snapshot.config.workers || 0);
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
        url: job?.url,
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
      company: employerOf(row.job.company, row.job.url),
      url: row.job.url,
      role: row.job.title || urlRole(row.job.url),
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
    // Whether the slots themselves have reported, so idle can be claimed.
    known: !!runtimeWorkers,
    working: Math.max(runtimeWorkers ? new Set(assignments.map((a) => a.run_id).filter(Boolean)).size : 0, owners.length),
    emailWaits,
    waiting: {
      queued: counts.states.queued ?? 0,
      starting: counts.states["pending:waiting"] ?? 0,
      repairing: counts.states.waiting_browser ?? 0,
      email: counts.states.waiting_email ?? emailWaits.length,
    },
  };
}
type WorkingState = ReturnType<typeof useWorking>;

// ---------- the top: today's count, the state and progress, what waits on you, and the key ----------
function Hero({ work, models, ready, success }: { work: WorkingState; models?: RuntimeHealth["models"]; ready?: Startable | null; success?: SuccessReport | null }) {
  const { snapshot, counts, link, refresh, go } = useWorkspace();
  const { pending, run } = useAction();
  const now = useNow();

  const rt = snapshot.autopilot_runtime || {};
  const batch = snapshot.autopilot || {};
  const state = rt.state || batch.state || "idle";
  const active = state === "active";
  const target = rt.target || batch.target || 100;
  const [goal, setGoal] = useDraft("batch-target", counts.jobs || snapshot.jobs.length ? 100 : 25);
  const workers = snapshot.config.workers_enabled;
  const noResume = snapshot.resumes.length === 0;
  const noKey = !snapshot.config.mimo;
  const [confirmStart, setConfirmStart] = useState<number | null>(null);
  const paused = snapshot.control.paused;
  const panel = panelState(snapshot, link.lost);
  const stopped = paused || (!rt.intake_enabled && rt.intake_reason === "Paused");
  const n = work.working;

  // The worst thing that is true right now, in plain words.
  const said: Record<PanelState, ["info" | "bad", string, (string | undefined)?]> = {
    lost: ["bad", "Connection lost.", `Showing what was there ${ago(link.updatedAt, now)}; retrying.`],
    restart: ["bad", "Automation has stopped responding.", "Restart it to keep applying."],
    paused: ["info", "Paused. Nothing new starts until you resume."],
    complete: ["info", "Done. Your goal was reached."],
    running: ["info", n ? "Applying right now." : idleSentence(ready), undefined],
    held: ["info", (rt.intake_reason || "New applications are not starting").replace(/\.$/, "") + "."],
    none: ["info", "Not applying. Press Start to begin.", undefined],
  };
  const [tone, sentence, sub] = said[panel];

  const begin = (value: number) =>
    run(
      "start",
      async () => {
        const result = await api("/job-board/autopilot/start", "POST", { target: value });
        await refresh();
        return result;
      },
      active ? "Batch resumed. Existing progress is kept." : "Batch started. Discovery, ranking and applications continue without this page open.",
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
      className="ov-key"
      onSubmit={(e) => {
        e.preventDefault();
        const value = Math.max(1, Math.min(500, Math.round(goal || 25)));
        // A batch sends on its own, which turns "Let me review first" off. Said before it happens, not found out afterwards.
        if (!snapshot.control.auto_submit) setConfirmStart(value);
        else begin(value);
      }}
    >
      <MasterKey type="submit" word="Start" icon="start" aria-label="Start applying" busy={pending === "start"} disabled={pending !== null || noResume || noKey} />
      <label className="ov-target">
        <span>Apply until</span>
        <input type="number" min={1} max={500} step={1} required value={goal} onChange={(e) => setGoal(Number(e.target.value))} aria-describedby="target-hint" />
        <span>applied</span>
      </label>
      <span id="target-hint" className="sr-only">
        Up to 500 confirmed applications.
      </span>
    </form>
  ) : stopped ? (
    <div className="ov-key">
      <MasterKey word="Resume" icon="start" aria-label="Resume applying" busy={pending === "start"} disabled={pending !== null} onClick={() => begin(target)} />
      <p className="ov-key-note">Picks up where it left off.</p>
    </div>
  ) : panel === "restart" ? (
    <div className="ov-key">
      <MasterKey word="Restart" icon="start" aria-label="Restart automation" busy={pending === "start"} disabled={pending !== null} onClick={() => begin(target)} />
      <Button size="sm" variant="ghost" loading={pending === "pause"} disabled={pending !== null} onClick={pause}>
        Pause instead
      </Button>
    </div>
  ) : (
    <div className="ov-key">
      <MasterKey word="Pause" icon="pause" aria-label="Pause applying" busy={pending === "pause"} disabled={pending !== null} onClick={pause} />
      <p className="ov-key-note">Holds each application before it sends.</p>
    </div>
  );

  const words = counts.words;
  const needs = countQuestions(snapshot.reviews);
  const rate = success?.overall?.final_pct;
  const figures: { label: string; n: string; to: () => void }[] = [
    { label: "Applied", n: (words?.applied ?? counts.confirmed ?? 0).toLocaleString(), to: () => go({ view: "ledger" }) },
    { label: "Applying", n: (words?.applying ?? 0).toLocaleString(), to: () => go({ view: "jobs", tab: "applying" }) },
    { label: "Needs you", n: needs.toLocaleString(), to: () => go({ view: "needs" }) },
    { label: "Success", n: rate == null ? "–" : `${(Math.floor(rate * 10) / 10).toFixed(1)}%`, to: () => go({ view: "settings", tab: "details" }) },
  ];
  return (
    <>
      <section className="ax-panel ov-hero" aria-label="Now">
        <div className="ov-say">
          <div className="ov-state">
            <p className={"ov-sentence is-" + tone} aria-live="polite">
              {panel === "running" && !n && ready ? <Idle said={ready} fallback={idleSentence(null)} go={go} /> : sentence}
            </p>
            {sub && <p className="ov-sub">{sub}</p>}
          </div>
          {key}
        </div>
        <ul className="ov-figures">
          {figures.map((f) => (
            <li key={f.label}>
              <button type="button" onClick={f.to}>
                <span className="ov-fig">{f.n}</span>
                <span className="ov-label">{f.label}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      {confirmStart !== null && (
        <Dialog
          open
          title="Start applying for you?"
          onClose={() => setConfirmStart(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setConfirmStart(null)}>
                Not yet
              </Button>
              <Button
                variant="primary"
                onClick={() => {
                  const value = confirmStart;
                  setConfirmStart(null);
                  void begin(value);
                }}
              >
                Send for me
              </Button>
            </>
          }
        >
          You chose to review each application first. Starting this turns that off: AXIOM will find jobs and send applications once every required
          answer is verified, without asking. To keep reviewing, press Not yet and pick jobs yourself on the Jobs page.
        </Dialog>
      )}
      {workers && noKey && !active && (
        <Notice
          tone="warn"
          action={
            <button type="button" className="ax-link" onClick={() => go({ view: "settings", tab: "connections" })}>
              Add your AI key
            </button>
          }
        >
          AXIOM can't read job pages or write answers until you add an AI key.
        </Notice>
      )}
      <ProviderNotice models={models} go={go} />
      {!workers && (
        <Notice tone="warn">Applying is disabled in this installation's configuration, so a batch can't run. Enable workers on the server and restart.</Notice>
      )}
      {!active && noResume && workers && (
        <Notice
          tone="warn"
          action={
            <button type="button" className="ax-link" onClick={() => go({ view: "resumes" })}>
              Add a resume
            </button>
          }
        >
          Upload a resume first: every application attaches one.
        </Notice>
      )}
    </>
  );
}

// An AI account that ran out of credit looks like "nothing is happening" from the outside. Said once, plainly, with the way out.
function ProviderNotice({ models, go }: { models?: RuntimeHealth["models"]; go: ReturnType<typeof useWorkspace>["go"] }) {
  const down = Object.values(models || {}).find((m) => m && (m.state === "cooldown" || m.state === "recovering"));
  if (!down) return null;
  const credit = down.last_error === "quota_exhausted";
  return (
    <Notice
      tone="warn"
      action={
        credit ? (
          <button type="button" className="ax-link" onClick={() => go({ view: "settings", tab: "connections" })}>
            AI settings
          </button>
        ) : undefined
      }
    >
      {credit
        ? "Your AI account is out of credit, so AXIOM has paused applying. Add credit with your AI provider and it carries on by itself: it checks again every half hour."
        : "The AI service isn't answering, so AXIOM is waiting a few minutes before it tries again. Nothing is lost."}
    </Notice>
  );
}

// ---------- what is running now ----------
function Live({ work, checking }: { work: WorkingState; checking: boolean }) {
  const { snapshot, link, refresh, markJobs, go, openJob, version } = useWorkspace();
  // Which Ready jobs can start, and why the rest cannot (jobpilot/startable.py): the same screening the dispatcher uses.
  const ready = usePoll<Startable>((signal) => api("/startable", "GET", undefined, signal), 30000, [version]);
  const { pending, run } = useAction();
  const now = useNow();
  const panel = panelState(snapshot, link.lost);
  const rt = snapshot.autopilot_runtime || {};
  const active = (rt.state || snapshot.autopilot?.state) === "active";
  const rows: LiveRow[] = work.busy.map((w) => ({
    id: w.key,
    company: w.company,
    url: w.url,
    role: w.role,
    platform: w.platform,
    state: w.state,
    since: w.since,
    // The worker's phase is the newest telling event kind (pipeline_comparison.TELLING_EVENTS), already in this poll.
    progress: progressOf(w.state, w.phase ? progressFrom([{ kind: w.phase }]) : null),
    open: w.open,
    pause: w.pause ? (
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
    ) : undefined,
  }));
  const parked: LiveRow[] = work.emailWaits.map(({ c, job }) => {
    const minutes = (now - new Date(c.created_at).getTime()) / 60000;
    return {
      id: c.id,
      company: job ? employerOf(job.company, job.url) : "An application",
      url: job?.url,
      role: job?.title || "",
      platform: job?.ats || "",
      state: "waiting_email",
      since: c.created_at,
      parked: true,
      progress: { rung: 2, doing: minutes > 10 ? "Waiting on an emailed code, delayed" : "Waiting on an emailed code" },
      open: job ? () => openJob(job.id, c.run_id) : undefined,
    };
  });

  // Only what is running; the sentence above says the rest.
  if (rows.length + parked.length === 0) return null;
  return <LiveWork rows={rows} parked={parked} busy={work.busy.length} limit={work.limit} checking={checking} empty={null} />;
}

function idleSentence(said?: Startable | null) {
  return said?.idle || "Between applications.";
}

/** Why nothing is running while a batch is on: how many Ready jobs can start, or what holds the rest, each number opening its list. */
function Idle({ said, fallback, go }: { said?: Startable | null; fallback: string; go: (route: { view: "jobs"; tab: string; filter: string }) => void }) {
  if (!said) return <>{fallback}</>;
  const open = (filter: string) => go({ view: "jobs", tab: "discovered", filter });
  if (said.startable > 0)
    return (
      <>
        {fallback}{" "}
        <button type="button" className="ov-foot-link" onClick={() => open("ready")}>
          <b>{said.startable.toLocaleString()}</b> {said.startable === 1 ? "job is" : "jobs are"} ready to start
        </button>
        {said.held.length > 0 && (
          <>
            , <button type="button" className="ov-foot-link" onClick={() => open("held")}>{(said.ready - said.startable).toLocaleString()} held</button>
          </>
        )}
        .
      </>
    );
  if (!said.held.length) return <>{said.idle || fallback}</>;
  const shown = said.held.slice(0, 4);
  const rest = said.held.slice(4).reduce((n, h) => n + h.count, 0);
  return (
    <>
      Nothing to run:{" "}
      {shown.map((h, i) => (
        <span key={h.code}>
          {i > 0 && ", "}
          <button type="button" className="ov-foot-link" onClick={() => open(`held:${h.code}`)}>
            <b>{h.count.toLocaleString()}</b> {h.phrase}
          </button>
        </span>
      ))}
      .
      {rest > 0 && (
        <>
          {" "}
          <button type="button" className="ov-foot-link" onClick={() => open("held")}>
            All {(said.ready - said.startable).toLocaleString()} held
          </button>
        </>
      )}
    </>
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
      detail: snapshot.resumes.length ? `${plural(snapshot.resumes.length, "resume")} ready to attach` : "Upload the PDF that applications attach.",
      action: snapshot.resumes.length ? "Manage" : "Upload",
      onClick: () => go({ view: "resumes" }),
    },
    {
      done: snapshot.config.mimo,
      title: "AI key",
      detail: snapshot.config.mimo ? `${snapshot.config.model_provider ?? "The model provider"} key found` : "Reads job pages and writes answers. Paste your key in Settings › Connections.",
      action: "Open settings",
      onClick: () => go({ view: "settings", tab: "connections" }),
    },
    {
      done: email,
      title: "Email for verification codes",
      detail: email ? "Codes and confirmation emails are read automatically." : "Workday, Oracle and iCIMS email a code before you can apply. Connect the inbox you apply with.",
      action: email ? "Settings" : "Connect email",
      onClick: () => go({ view: "settings", tab: "email" }),
    },
    {
      done: p.target_roles.length > 0,
      title: "What to look for",
      detail: p.target_roles.length ? `${p.target_roles.join(", ")} · ${p.target_locations.join(", ") || "Anywhere in the USA"}` : "Choose the roles discovery searches for.",
      action: "Adjust",
      onClick: () => go({ view: "profile", tab: "experience", filter: "preferences" }),
    },
    {
      done: submitting,
      title: "Automatic submission",
      detail: submitting ? "Applications send once every required answer is checked." : "Off: applications fill every page and stop before the final send.",
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
      detail: "Add a job link, pick jobs in Discovered, or start a batch above.",
      action: "Add a job",
      onClick: () => window.dispatchEvent(new Event(OPEN_JOB_LINK)),
    },
  ];
  return (
    <Panel title="Get started" actions={<span className="mono">{steps.filter((s) => s.done).length} of {steps.length}</span>}>
      <ul className="ax-rows">
        {steps.map((s) => (
          <li key={s.title} className="ax-row">
            <i className={"ax-lamp " + (s.done ? "is-ok" : "is-off")} aria-hidden="true" />
            <span className="ax-row-main">
              <strong>{s.title}</strong>
              <small>{s.detail}</small>
            </span>
            <span className="ax-row-end">
              <span className="sr-only">{s.done ? "Done" : "Not done"}</span>
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
