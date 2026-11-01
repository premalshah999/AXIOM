import { useMemo, useState, type ReactNode } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNow, usePoll } from "./hooks";
import { DayChart, DotPatterns } from "./identity/DayChart";
import { LiveWork, type LiveRow } from "./identity/LiveWork";
import { MasterKey } from "./identity/MasterKey";
import { MoreLink, Results } from "./identity/Results";
import { confirmedToday, panelState, type PanelState } from "./identity/state";
import { progressFrom, progressOf } from "./identity/stages";
import "./identity/overview.css";
import { appliedState, evidenceLabel, STATES, type JobRow } from "./model";
import { OPEN_JOB_LINK } from "./jobintake";
import { groupQuestions, QuestionCard, type QuestionGroup } from "./questions";
import type { ApplicationWorkers, MailStatus, RuntimeHealth, Snapshot, Success } from "./types";
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
  const health = usePoll<RuntimeHealth>((signal) => api("/runtime-health", "GET", undefined, signal), 30000);
  const mail = usePoll<MailStatus>((signal) => api("/mail", "GET", undefined, signal), 30000);
  const ledger = usePoll<{ applications: Success[] }>((signal) => api("/successful-applications", "GET", undefined, signal), 60000);
  const applications = ledger.data?.applications ?? null;
  const work = useWorking(mail.data, health.data?.application_workers);
  return (
    <div className="ax-page ov-page">
      <DotPatterns />
      <Hero work={work} applications={applications} models={health.data?.models} />
      <FirstRun mail={mail.data} />
      <Live work={work} checking={!health.data && !work.known} />
      <div className="ov-columns">
        <div className="ov-column">
          <Questions />
          <Recent applications={applications} />
        </div>
        <div className="ov-column">
          <Results ledger={applications} />
        </div>
      </div>
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
function Hero({ work, applications, models }: { work: WorkingState; applications: Success[] | null; models?: RuntimeHealth["models"] }) {
  const { snapshot, counts, link, refresh, go } = useWorkspace();
  const { pending, run } = useAction();
  const now = useNow();
  const today = useMemo(() => confirmedToday(applications || [], now), [applications, now]);

  const rt = snapshot.autopilot_runtime || {};
  const batch = snapshot.autopilot || {};
  const state = rt.state || batch.state || "idle";
  const active = state === "active";
  const target = rt.target || batch.target || 100;
  const confirmed = rt.confirmed ?? 0;
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
  const said: Record<PanelState, ["info" | "bad", string, string?]> = {
    lost: ["bad", "Connection lost", `Showing data from ${ago(link.updatedAt, now)}; retrying.`],
    restart: ["bad", "Needs a restart", "Automation has stopped responding. Restart it to keep applying."],
    paused: ["info", "Paused", "No new applications start. Progress is kept."],
    complete: ["info", "Target reached"],
    running: ["info", n ? `Applying to ${plural(n, "job")} right now` : "Applying, between jobs"],
    held: ["info", "Held", (rt.intake_reason || "New applications are not starting").replace(/\.$/, "") + "."],
    none: ["info", "No batch running", work.limit ? "Start one to apply until a target you choose." : undefined],
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
        <span>confirmed</span>
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

  const questions = groupQuestions(snapshot.reviews, () => undefined).length;
  const count = today.length.toLocaleString();
  const showProgress = active || state === "complete";
  const share = Math.min(100, target ? (confirmed / target) * 100 : 0);
  return (
    <>
      <section className="ax-panel ov-hero" aria-label="Today">
        <a
          className="ov-big"
          href="?view=ledger"
          onClick={(e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
            e.preventDefault();
            go({ view: "ledger" });
          }}
        >
          <span className="ax-read" style={{ "--chars": count.length } as React.CSSProperties}>
            {applications ? count : "–"}
          </span>
          <span className="ov-label">Confirmed today</span>
        </a>
        <div className="ov-say">
          <div className="ov-state">
            <p className={"ov-sentence is-" + tone} aria-live="polite">
              {sentence}
            </p>
            {sub && <p className="ov-sub">{sub}</p>}
          </div>
          {showProgress && (
            <div className="ov-progress" title={rt.confirmed_by_owner ? `${rt.confirmed_by_batch ?? 0} by the batch, ${rt.confirmed_by_owner} you started` : undefined}>
              <p>
                <button type="button" className="ax-quiet-link" onClick={() => go({ view: "ledger" })}>
                  <b className="mono">{confirmed.toLocaleString()}</b> of <span className="mono">{target.toLocaleString()}</span> confirmed
                </button>
                <span>
                  {active && <span className="mono">{(rt.remaining ?? Math.max(0, target - confirmed)).toLocaleString()}</span>}
                  {active && " to go"}
                  {active && (rt.attempted_jobs ?? 0) > 0 && " · "}
                  {(rt.attempted_jobs ?? 0) > 0 && (
                    <button type="button" className="ax-quiet-link" onClick={() => go({ view: "jobs", tab: "pipeline" })}>
                      <span className="mono">{(rt.attempted_jobs ?? 0).toLocaleString()}</span> tried
                    </button>
                  )}
                  {batch.started_at && <span title={`Started ${day(batch.started_at)}`} />}
                </span>
              </p>
              <span className="ov-bar" role="progressbar" aria-valuemin={0} aria-valuemax={target} aria-valuenow={Math.min(confirmed, target)} aria-label="Progress to target">
                <i style={{ width: `${share}%` }} />
              </span>
            </div>
          )}
          <DayChart times={today} now={now} />
        </div>
        <a
          className="ov-wait"
          href="?view=needs"
          onClick={(e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
            e.preventDefault();
            go({ view: "needs" });
          }}
        >
          <span className={"ax-window ov-lit" + (questions ? "" : " is-dark")}>
            <span className="ax-read" style={{ "--chars": String(questions).length } as React.CSSProperties}>
              {questions}
            </span>
          </span>
          <span className="ov-label">{questions === 1 ? "Question waiting" : "Questions waiting"}</span>
        </a>
        {key}
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
  const { snapshot, link, refresh, markJobs, go, openJob } = useWorkspace();
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

  const empty: Record<PanelState, string> = {
    lost: "Can't see what's running until the connection is back.",
    restart: "Nothing new starts until automation restarts.",
    paused: "Nothing is running. Resume to start the next application.",
    complete: "Nothing is running. The batch reached its target.",
    running: "Between applications. The next one starts as soon as a job is ready.",
    held: "Nothing is running while the batch is held.",
    none: "Nothing is running. Applications appear here with the step they're on.",
  };

  // Only what is there, each opening its list. Questions have their own panel.
  const { waiting } = work;
  const tally = (count: number, label: string, to?: { tab: string; filter?: string }): ReactNode =>
    count > 0 ? (
      to ? (
        <button type="button" className="ov-foot-link" onClick={() => go({ view: "jobs", ...to })}>
          <b>{count.toLocaleString()}</b> {label}
        </button>
      ) : (
        <span>
          <b>{count.toLocaleString()}</b> {label}
        </span>
      )
    ) : null;
  const foot = [
    tally(waiting.queued + waiting.starting, "queued", { tab: "pipeline", filter: "active" }),
    tally(waiting.repairing, "being fixed", { tab: "pipeline", filter: "repair" }),
    active ? tally(rt.blocked ?? 0, "blocked in this batch", { tab: "pipeline", filter: "repair" }) : null,
    active ? tally(rt.failed ?? 0, "not sent in this batch", { tab: "pipeline", filter: "repair" }) : null,
  ].filter(Boolean);
  return (
    <LiveWork
      rows={rows}
      parked={parked}
      busy={work.busy.length}
      limit={work.limit}
      checking={checking}
      empty={empty[panel]}
      foot={foot.length ? foot.map((item, i) => <span key={i}>{item}</span>) : undefined}
    />
  );
}

// ---------- the top few questions, one click from answered ----------
const names = (g: QuestionGroup) => [g.job, ...g.others].map((j) => employerOf(j?.company, j?.url));

function Questions() {
  const { snapshot, go } = useWorkspace();
  const now = useNow(60000);
  const [open, setOpen] = useState<QuestionGroup | null>(null);
  const groups = useMemo(() => {
    const runs = new Map(snapshot.runs.map((r) => [r.id, r.job_id]));
    const jobs = new Map(snapshot.jobs.map((j) => [j.id, j]));
    return groupQuestions(snapshot.reviews, (id) => jobs.get(runs.get(id) || ""));
  }, [snapshot.reviews, snapshot.runs, snapshot.jobs]);
  // The ones that free the most applications come first.
  const top = useMemo(
    () => [...groups].sort((a, b) => b.others.length - a.others.length || (a.review.created_at || "").localeCompare(b.review.created_at || "")).slice(0, 4),
    [groups],
  );
  return (
    <Panel
      title="Questions waiting"
      flush
      actions={groups.length > top.length ? <MoreLink onClick={() => go({ view: "needs" })}>All {groups.length.toLocaleString()}</MoreLink> : undefined}
    >
      {top.length ? (
        <ul className="ov-list">
          {top.map((g) => {
            const who = names(g);
            return (
              <li key={g.review.id} className="ov-q">
                <MonogramStack names={who} max={2} />
                <div className="ov-q-text">
                  <p>{g.review.display_question || g.review.question}</p>
                  <small>
                    {who.length > 1 ? `${who[0]} and ${plural(who.length - 1, "other")}` : who[0]}
                    {g.review.created_at && ` · ${ago(g.review.created_at, now)}`}
                  </small>
                </div>
                <Button size="sm" onClick={() => setOpen(g)}>
                  Answer
                </Button>
              </li>
            );
          })}
        </ul>
      ) : (
        <Empty compact title="Nothing waiting on you" hint="When a form asks something only you know, the question appears here." />
      )}
      {open && (
        <Dialog
          open
          onClose={() => setOpen(null)}
          width={560}
          title={names(open)[0] ?? "A question"}
          sub={open.others.length ? `Answering this frees ${plural(open.others.length + 1, "application")}.` : "Answering this frees the application."}
        >
          <QuestionCard group={open} compact onSaved={() => setOpen(null)} />
        </Dialog>
      )}
    </Panel>
  );
}

// ---------- the newest proof ----------
function Recent({ applications }: { applications: Success[] | null }) {
  const { go, openJob } = useWorkspace();
  const now = useNow(30000);
  const rows = useMemo(() => [...(applications || [])].sort((a, b) => b.applied_at.localeCompare(a.applied_at)).slice(0, 5), [applications]);
  return (
    <Panel title="Recent confirmations" flush actions={applications?.length ? <MoreLink onClick={() => go({ view: "ledger" })}>All {applications.length.toLocaleString()}</MoreLink> : undefined}>
      {!applications ? (
        <div className="ov-pad">
          <Skeleton rows={4} />
        </div>
      ) : rows.length ? (
        <ul className="ov-list">
          {rows.map((a) => {
                        const { company, role: title } = jobWords(a);
            return (
            <li key={a.run_id}>
              <button type="button" className="ov-ok" onClick={() => openJob(a.job_id, a.run_id)}>
                <Monogram name={company} url={a.url} />
                <span className="ov-ok-who">
                  <b title={company}>{company}</b>
                  <span title={title}>{title}</span>
                </span>
                <span className="ov-ok-proof">{evidenceLabel(a)}</span>
                <time dateTime={a.applied_at} title={new Date(a.applied_at).toLocaleString()}>
                  {ago(a.applied_at, now)}
                </time>
              </button>
            </li>
            );
          })}
        </ul>
      ) : (
        <Empty compact title="No confirmations yet" hint="Each application an employer confirms appears here, with its proof." />
      )}
    </Panel>
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
