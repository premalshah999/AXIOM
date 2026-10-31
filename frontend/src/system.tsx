// Settings › Applying and Advanced. Applying is what AXIOM does on its own and where it stops; Advanced is what is running and the
// engine's own switches, in plain words and out of the way.
import { useState, type ReactNode } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, usePoll } from "./hooks";
import { ConnectionFields, ConnectionsGate, FieldInput, useConnections } from "./connections";
import { AutoApplySettings } from "./discovery";
import { ago, plural } from "./format";
import { Facts, RestartNotice, SaveBar, SwitchRow } from "./setup-kit";
import type {
  AgentModelProvider,
  AgentSettings,
  AgentSettingsUpdate,
  AutopilotRuntime,
  Limits,
  RuntimeHealth,
  SchedulerHealth,
  Snapshot,
} from "./types";
import "./styles/settings.css";
import { Button, Empty, ErrorLine, Input, Listbox, Loading, Panel, Pill, Table } from "./ui/index";

// ---------- Applying ----------

export function ApplyingSettings() {
  const { snapshot, refresh } = useWorkspace();
  const { pending, run } = useAction();
  const control = snapshot.control || { paused: false, auto_submit: false };
  const toggle = (key: "auto_submit" | "paused", label: string) =>
    run(
      key,
      async () => {
        // Read current values right before writing both back.
        const fresh = await api<Snapshot["control"]>("/control");
        await api("/control", "PUT", { ...fresh, [key]: !fresh[key] });
        await refresh();
      },
      label,
    );
  return (
    <>
      <section className="st-group" aria-labelledby="st-controls">
        <div className="st-head">
          <div>
            <h2 id="st-controls">Applying</h2>
            <p>These take effect as soon as you change them.</p>
          </div>
        </div>
        <Panel>
          <SwitchRow
            label="Send applications"
            note="Off: AXIOM fills in each application and stops before sending. Practice runs never send either way."
            checked={!!control.auto_submit}
            disabled={pending !== null}
            onChange={() => toggle("auto_submit", control.auto_submit ? "AXIOM will stop before sending." : "AXIOM will send applications.")}
          />
          <SwitchRow
            label="Pause new applications"
            note="Running ones stop before their final send. One that is already sending finishes."
            checked={!!control.paused}
            disabled={pending !== null}
            onChange={() => toggle("paused", control.paused ? "New applications resumed." : "New applications paused.")}
          />
        </Panel>
      </section>
      <section className="st-group" aria-labelledby="st-auto">
        <div className="st-head">
          <div>
            <h2 id="st-auto">Automatic applications</h2>
            <p>What AXIOM applies to without asking you first.</p>
          </div>
        </div>
        <AutoApplySettings />
      </section>
      <Limits />
    </>
  );
}

const usd = (n: unknown) => `$${Number(n ?? 0).toFixed(2)}`;

/** Daily and per-job limits. Two endpoints on the server, one form here, one save bar. */
function Limits() {
  const { snapshot } = useWorkspace();
  const c = useConnections();
  const limits = usePoll<Limits>((signal) => api("/limits", "GET", undefined, signal), 60000);
  const [draft, setDraft] = useState<Limits | null>(null);
  const { pending, run } = useAction();
  const stats = snapshot.stats || ({} as Snapshot["stats"]);
  const value = draft || limits.data;
  const tokens = Number(value?.job_token_cap ?? 0);
  const company = Number(value?.company_limit ?? 0);
  const tokenError = value && (tokens < 50000 || tokens > 50000000) ? "Between 50,000 and 50,000,000." : undefined;
  const companyError = value && (!Number.isInteger(company) || company < 1 || company > 25) ? "A whole number from 1 to 25." : undefined;
  const invalid = !!tokenError || !!companyError;
  const dirty = !!draft || c.changes.length > 0;
  const save = async () => {
    if (draft && !invalid)
      await run(
        "limits",
        async () => {
          await api("/limits", "PUT", draft);
          setDraft(null);
          await limits.refresh();
        },
        "Limits saved.",
      );
    if (c.changes.length) await c.save();
  };
  return (
    <section className="st-group" aria-labelledby="st-limits">
      <div className="st-head">
        <div>
          <h2 id="st-limits">Limits</h2>
          <p>Where AXIOM stops, so a bad day cannot run up a bill or flood one employer.</p>
        </div>
      </div>
      <ConnectionsGate c={c}>
        {() => (
          <>
            <Panel>
              {limits.error && !value ? (
                <ErrorLine error={limits.error} retry={limits.refresh} />
              ) : !value ? (
                <Loading />
              ) : (
                <div className="st-fields">
                  <FieldInput
                    c={c}
                    name="daily_application_limit"
                    label="Applications per day"
                    hint={`${stats.submissions_reserved ?? 0} used today. The batch stops starting new ones at this number.`}
                  />
                  <FieldInput
                    c={c}
                    name="daily_budget_usd"
                    label="AI spending per day, in dollars"
                    hint={`${usd(stats.spend_today)} spent today. AI calls stop for the day at this amount.`}
                  />
                  <Input
                    label="Applications per employer"
                    type="number"
                    min={1}
                    max={25}
                    value={Number.isFinite(company) ? company : ""}
                    error={companyError}
                    hint="Confirmed or in-progress applications allowed at one employer."
                    onChange={(e) => setDraft({ ...value, company_limit: Number(e.target.value) })}
                  />
                  <Input
                    label="AI budget per job"
                    type="number"
                    min={50000}
                    max={50000000}
                    step={50000}
                    value={Number.isFinite(tokens) ? tokens : ""}
                    error={tokenError}
                    hint={`${tokens.toLocaleString()} tokens per job in any 24 hours. Harder sites need more; more costs more.`}
                    onChange={(e) => setDraft({ ...value, job_token_cap: Number(e.target.value) })}
                  />
                  <FieldInput c={c} name="max_model_calls" label="AI calls per application" hint="An application that needs more stops and is repaired later." />
                  <FieldInput c={c} name="timezone" label="Time zone" hint="For dates on forms and for the daily limits, such as America/New_York." />
                </div>
              )}
            </Panel>
            <SaveBar
              dirty={dirty}
              saving={pending === "limits" || c.pending === "save"}
              disabled={invalid}
              onSave={save}
              onDiscard={() => {
                setDraft(null);
                c.discard();
              }}
              label="Save limits"
            />
          </>
        )}
      </ConnectionsGate>
    </section>
  );
}

// ---------- Advanced ----------

// What the app is doing right now, in plain words: the worst thing that is true, first.
function automation(rt: AutopilotRuntime, paused: boolean, sched: SchedulerHealth, workers: number) {
  const done = rt.confirmed ?? 0;
  const goal = rt.target ?? 100;
  const progress = done > goal ? `${done} confirmed, target ${goal}` : `${done} of ${goal} confirmed`;
  if (sched.degraded) return { tone: "bad" as const, text: "Needs a restart", note: "Automation is degraded." };
  if (paused) return { tone: "warn" as const, text: "Paused", note: "New applications are paused." };
  if (rt.state === "complete") return { tone: "neutral" as const, text: "Target reached", note: progress };
  if (rt.intake_enabled)
    return { tone: "ok" as const, text: "Running", note: workers ? `${progress}, ${plural(workers, "worker")}` : progress };
  if (rt.state === "active") return { tone: "warn" as const, text: "Held", note: rt.intake_reason || progress };
  return { tone: "neutral" as const, text: "No batch", note: workers ? `${plural(workers, "worker")} ready` : "Start one on Overview." };
}

function modelState(model: NonNullable<RuntimeHealth["models"]>["answers"]) {
  if (!model) return "";
  return model.state === "ready"
    ? "ready"
    : model.state === "recovery_ready"
      ? "waiting for one recovery request"
      : model.state === "recovering"
        ? "one recovery request in progress"
        : `paused${model.retry_at ? " until " + new Date(model.retry_at).toLocaleTimeString() : ""}`;
}

/** What is running, how many workers, and whether it needs a restart. */
function SystemStatus() {
  const { snapshot } = useWorkspace();
  const health = usePoll<RuntimeHealth>((signal) => api("/runtime-health", "GET", undefined, signal), 30000);
  const conn = usePoll<{ fields: { name: string; label: string; pending: string }[] }>((signal) => api("/connections", "GET", undefined, signal), 60000);
  const rt = snapshot.autopilot_runtime || {};
  const sched = health.data?.scheduler || rt.scheduler || snapshot.scheduler_health || {};
  const pool = health.data?.application_workers || rt.application_workers;
  const slots = pool?.assignments || [];
  const busy = slots.filter((s) => s.run_id || s.job_id).length;
  const total = pool ? pool.normal_workers + pool.specialist_workers + (pool.custom_workers ?? 0) : 0;
  const now = automation(rt, !!snapshot.control?.paused, sched, total);
  const waiting = (Array.isArray(conn.data?.fields) ? conn.data!.fields : []).filter((f) => f.pending);
  const restartFor = sched.degraded
    ? "Automation is degraded. A restart fixes it."
    : waiting.length
      ? `Saved but not running yet: ${waiting.map((f) => f.label).join(", ")}. They apply after a restart.`
      : "";
  return (
    <Panel title="Status">
      <div className="ax-stack">
        {health.error && !health.data && <ErrorLine error={health.error} retry={health.refresh} />}
        <Facts
          items={[
            [
              "AXIOM",
              <>
                <Pill tone={now.tone} dot>
                  {now.text}
                </Pill>
                <span className="ax-hint"> {now.note}</span>
              </>,
            ],
            [
              "Workers",
              pool
                ? `${plural(total, "worker")}: ${pool.normal_workers} regular, ${pool.specialist_workers} for difficult sites${pool.custom_workers ? `, ${pool.custom_workers} for custom career sites` : ""}. ${busy} busy.`
                : snapshot.config?.workers_enabled
                  ? `${snapshot.config.workers ?? "—"} at most`
                  : "Turned off in the configuration",
            ],
            [
              "Scheduler",
              <>
                {sched.degraded ? (
                  <Pill tone="bad" dot>
                    Needs a restart
                  </Pill>
                ) : (
                  (sched.state || "Unknown").replace(/^./, (m) => m.toUpperCase())
                )}
                {sched.checked_at && <span className="ax-hint"> checked {ago(sched.checked_at)}</span>}
              </>,
            ],
            ["Last check-in", ago(health.data?.heartbeat?.at || snapshot.scheduler_heartbeat?.at)],
            ["Alerts waiting to send", health.data ? String(health.data.outbox_pending ?? 0) : "…"],
            ...Object.entries(health.data?.models || {}).map(
              ([role, model]): [string, string] => [
                role === "answers" ? "Model for answers" : "Model for moving through pages",
                model ? `${model.provider}, ${modelState(model)}` : "",
              ],
            ),
            [
              "Version",
              <span key="v" className="mono" title="The build AXIOM is running">
                {(health.data?.engine_version || snapshot.engine_version || "—").replace(/^execution-v\d+-/, "")}
              </span>,
            ],
          ]}
        />
        {restartFor && <RestartNotice message={restartFor} />}
        {slots.length > 0 && (
          <details className="ax-fold">
            <summary>
              <strong>What each worker is doing</strong>
              <span className="ax-hint">{slots.length}</span>
            </summary>
            <Table
              label="Workers"
              rows={slots}
              rowKey={(s) => `${s.lane}-${s.slot}`}
              minWidth={420}
              columns={[
                { key: "slot", header: "Worker", cell: (s) => s.slot },
                { key: "lane", header: "Kind", cell: (s) => (s.lane === "specialist" ? "Difficult sites" : "Regular") },
                { key: "ats", header: "Platform", cell: (s) => s.ats || "—" },
                {
                  key: "state",
                  header: "Doing",
                  cell: (s) => (s.run_id || s.job_id ? [s.state, s.phase].filter(Boolean).join(", ") || "Working" : "Idle"),
                },
              ]}
            />
          </details>
        )}
      </div>
    </Panel>
  );
}

const AGENT_HELPERS: { key: keyof AgentSettingsUpdate["enabled"]; label: string; note: string }[] = [
  { key: "answers", label: "Answer form questions", note: "Look up your facts and answer what an application asks." },
  { key: "accounts", label: "Sign in to employers", note: "Create accounts, sign in and recover passwords it saved." },
  { key: "email", label: "Read your email", note: "Find verification codes, account links and receipts." },
  { key: "captcha", label: "Solve captchas", note: "Use the 2Captcha service when a challenge appears." },
  { key: "navigator", label: "Find its way around pages", note: "Work out the controls and recover when a page stops moving." },
  { key: "discovery", label: "Find new jobs", note: "Look for fresh positions that match your preferences." },
];

const AGENT_PROVIDERS: { value: AgentModelProvider; label: string }[] = [
  { value: "haiku", label: "Haiku" },
  { value: "mimo", label: "MiMo" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "openai", label: "OpenAI GPT-6 Luna" },
  { value: "openrouter", label: "OpenRouter DeepSeek V4.1 Flash" },
];

function AgentControls() {
  const { refresh } = useWorkspace();
  const settings = usePoll<AgentSettings>((signal) => api("/agents", "GET", undefined, signal), 30000);
  const [draft, setDraft, clearDraft] = useDraft<AgentSettingsUpdate | null>("agent-settings", null);
  const { pending, run } = useAction();
  const saved = settings.data;
  // A thin or partial answer is treated as not loaded rather than crashing the page.
  const usable = !!saved && !!saved.enabled && !!saved.models && !!saved.providers;
  const current = draft || (usable ? saved : null);
  const available = (role: "answers" | "navigator") => !!saved?.providers?.[current!.models[role]]?.available;
  const changed =
    !!draft &&
    usable &&
    (AGENT_HELPERS.some(({ key }) => draft.enabled[key] !== saved!.enabled[key]) ||
      draft.models.answers !== saved!.models.answers ||
      draft.models.navigator !== saved!.models.navigator);
  const unavailable = !!current && usable && (!available("answers") || !available("navigator"));
  const save = () => {
    if (!current || !changed || unavailable || pending) return;
    run(
      "agents",
      async () => {
        const result = await api<AgentSettings>("/agents", "PUT", { enabled: current.enabled, models: current.models });
        settings.setData(result);
        clearDraft();
        await refresh();
      },
      "Saved.",
    );
  };
  let body: ReactNode;
  if (settings.error && !usable)
    body = (
      <Empty
        title="Could not load these settings"
        hint={settings.error.message}
        action={
          <Button size="sm" onClick={settings.refresh}>
            Try again
          </Button>
        }
      />
    );
  else if (!current || !usable) body = <Loading label="Loading…" />;
  else
    body = (
      <>
        {AGENT_HELPERS.map(({ key, label, note }) => (
          <SwitchRow
            key={key}
            label={label}
            note={note}
            checked={!!current.enabled[key]}
            disabled={pending !== null}
            onChange={() => setDraft({ enabled: { ...current.enabled, [key]: !current.enabled[key] }, models: { ...current.models } })}
          />
        ))}
        {(["answers", "navigator"] as const).map((role) => {
          const provider = saved!.providers[current.models[role]];
          const name = role === "answers" ? "Model for answers" : "Model for moving through pages";
          return (
            <div className="ax-switch-row st-choice" key={role}>
              <span className="ax-row-main">
                <strong>{name}</strong>
                <small>{provider?.available ? provider.model : "Add its API key under Connections first."}</small>
              </span>
              <Listbox
                label={name}
                align="end"
                value={current.models[role]}
                options={AGENT_PROVIDERS.filter((p) => saved!.providers[p.value]?.available || p.value === current.models[role]).map((p) => ({
                  value: p.value,
                  label: p.label + (saved!.providers[p.value]?.available ? "" : " (no key)"),
                }))}
                onChange={(v) => setDraft({ enabled: { ...current.enabled }, models: { ...current.models, [role]: v } })}
              />
            </div>
          );
        })}
      </>
    );
  return (
    <>
      <Panel title="What AXIOM may do on its own">{body}</Panel>
      <SaveBar dirty={changed} saving={pending === "agents"} disabled={unavailable} onSave={save} onDiscard={clearDraft} label="Save" />
    </>
  );
}

function Platforms() {
  const { snapshot } = useWorkspace();
  const ats = snapshot.ats || [];
  return (
    <details className="ax-fold st-adv">
      <summary>
        <strong>Application platforms</strong>
        <span>{plural(ats.length, "platform")} AXIOM knows how to apply on</span>
      </summary>
      <div className="st-adv-body">
        <ul className="ax-rows">
          {ats.map((a) => (
            <li className="ax-row" key={a.id}>
              <span className="ax-row-main">
                <strong>{a.name}</strong>
                <small>{a.guidance}</small>
              </span>
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}

export function AdvancedSettings() {
  return (
    <>
      <SystemStatus />
      <section className="st-group" aria-labelledby="st-workers">
        <div className="st-head">
          <div>
            <h2 id="st-workers">Workers</h2>
            <p>How many applications run at once. These are read when AXIOM starts, so a change waits for a restart.</p>
          </div>
        </div>
        <Panel>
          <ConnectionFields names={["normal_workers", "specialist_workers", "custom_ats_worker", "headless"]} restart />
        </Panel>
      </section>
      <AgentControls />
      <Platforms />
    </>
  );
}
