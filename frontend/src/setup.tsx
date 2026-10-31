import { useEffect, useMemo, useState } from "react";
import { Download } from "lucide-react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, usePoll } from "./hooks";
import { AccountSettings } from "./autonomy";
import { DiscoverySettings } from "./discovery";
import { MailSettings } from "./mail";
import { ConnectionSettings } from "./connections";
import { SignInSettings } from "./account";
import { TailoredResumes } from "./features";
import { KnowledgeView } from "./knowledge";
import { ProfileProvider, ProfileSettings } from "./profile";
import { Pagination, usePagination } from "./pagination";
import { Actions, Facts, FormGrid, RestartNotice, SwitchRow } from "./setup-kit";
import type {
  AgentModelProvider,
  AgentSettings,
  AgentSettingsUpdate,
  AutopilotRuntime,
  Limits,
  MailStatus,
  RuntimeHealth,
  SchedulerHealth,
  Snapshot,
  Source,
} from "./types";
import { ago, day, plural } from "./format";
import { PAGE_TABS } from "./shell/nav";
import { SearchBox } from "./worktools";
import {
  Button,
  Check,
  ConfirmButton,
  Empty,
  ErrorLine,
  External,
  Input,
  Loading,
  Panel,
  Pill,
  RouteTabs,
  Select,
  Table,
} from "./ui/index";

// Profile and Settings are one page each with tabs (nav.ts lists them; context.ts sends old Setup links to the right one).
export function ProfileView() {
  const { route } = useWorkspace();
  const tabs = PAGE_TABS.profile!;
  const tab = tabs.some((t) => t.id === route.tab) ? route.tab : tabs[0]!.id;
  // Links such as ?view=profile&tab=experience&filter=preferences land on that section.
  useEffect(() => {
    if (tab === "experience" && route.filter === "preferences")
      document.getElementById("profile-preferences")?.scrollIntoView({ block: "start" });
  }, [tab, route.filter]);
  return (
    <div className="ax-page">
      <RouteTabs view="profile" tabs={tabs} label="Profile" current={tab} />
      <ProfileProvider>
        {tab === "identity" && <ProfileSettings sections={["identity"]} />}
        {tab === "experience" && <ProfileSettings sections={["experience"]} />}
        {tab === "knowledge" && <KnowledgeView />}
        {tab === "resumes" && (
          <>
            <Resumes />
            <TailoredResumes />
          </>
        )}
      </ProfileProvider>
    </div>
  );
}

export function SettingsView() {
  const { route } = useWorkspace();
  const tabs = PAGE_TABS.settings!;
  const tab = tabs.some((t) => t.id === route.tab) ? route.tab : tabs[0]!.id;
  return (
    <div className="ax-page">
      <RouteTabs view="settings" tabs={tabs} label="Settings" current={tab} />
      {tab === "connections" && <ConnectionSettings />}
      {tab === "accounts" && <AccountSettings />}
      {tab === "email" && <MailSettings />}
      {tab === "discovery" && (
        <>
          <DiscoverySettings />
          <CustomSources />
        </>
      )}
      {tab === "system" && <SystemSettings />}
    </div>
  );
}

function Resumes() {
  const { snapshot, refresh } = useWorkspace();
  const { pending, run } = useAction();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [roles, setRoles] = useState("");
  const [inputKey, setInputKey] = useState(0);
  const resumes = snapshot.resumes || [];
  const full = resumes.length >= 10;
  return (
    <>
      <Panel title={`Library · ${resumes.length} of 10`}>
        {resumes.length ? (
          <ul className="ax-rows">
            {resumes.map((r) => (
              <li className="ax-row" key={r.id}>
                <span className="ax-row-main">
                  <strong>{r.name}</strong>
                  <small>
                    {(r.roles || []).length ? (r.roles || []).join(", ") + " · " : ""}added {day(r.created_at)} ·{" "}
                    <span className="mono">{(r.sha256 || "").slice(0, 10)}</span>
                  </small>
                </span>
                <span className="ax-row-end">
                  <a className="ax-btn is-sm" href={`/api/resumes/${r.id}/download`}>
                    <Download size={14} aria-hidden="true" /> Download
                  </a>
                  <ConfirmButton
                    label="Remove"
                    confirm="Remove resume"
                    note="The file stays with past receipts."
                    pending={pending === r.id}
                    onConfirm={() =>
                      run(
                        r.id,
                        async () => {
                          await api("/resumes/" + r.id, "DELETE");
                          await refresh();
                        },
                        "Resume removed from the library.",
                      )
                    }
                  />
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <Empty title="No resumes yet" hint="Add a text-based PDF below. Ranking and applications need at least one." />
        )}
      </Panel>
      <Panel title="Add a resume">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!file) return;
            const f = new FormData();
            f.append("file", file);
            f.append("name", name.trim());
            f.append("roles", roles);
            run(
              "upload",
              async () => {
                await api("/resumes", "POST", f);
                setFile(null);
                setName("");
                setRoles("");
                setInputKey((k) => k + 1);
                await refresh();
              },
              "Resume uploaded.",
            );
          }}
        >
          <FormGrid>
            <Input
              key={inputKey}
              label="PDF file"
              hint="Text-based PDF, up to 5 MB and 20 pages."
              type="file"
              accept="application/pdf"
              required
              onChange={(e) => {
                const f = e.target.files?.[0] || null;
                setFile(f);
                if (f && !name) setName(f.name.replace(/\.pdf$/i, ""));
              }}
            />
            <Input
              label="Name"
              required
              maxLength={120}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Backend engineering"
            />
            <Input label="Role tags" hint="Comma separated" value={roles} onChange={(e) => setRoles(e.target.value)} placeholder="Backend, Data" />
          </FormGrid>
          <Actions note={full ? "The library is full. Remove a resume first." : undefined}>
            <Button
              type="submit"
              variant="primary"
              loading={pending === "upload"}
              disabled={!file || full || (pending !== null && pending !== "upload")}
            >
              Add to library
            </Button>
          </Actions>
        </form>
      </Panel>
    </>
  );
}

function LimitSettings() {
  const limits = usePoll<Limits>((signal) => api("/limits", "GET", undefined, signal), 60000);
  const [draft, setDraft] = useState<Limits | null>(null);
  const { pending, run } = useAction();
  const value = draft || limits.data;
  const tokens = Number(value?.job_token_cap ?? 0);
  const company = Number(value?.company_limit ?? 0);
  const tokenError = value && (tokens < 50000 || tokens > 50000000) ? "Between 50,000 and 50,000,000." : undefined;
  const companyError =
    value && (!Number.isInteger(company) || company < 1 || company > 25) ? "A whole number from 1 to 25." : undefined;
  return (
    <Panel title="Limits">
      {limits.error && !value ? (
        <Empty
          title="Could not load the limits"
          hint={limits.error.message}
          action={
            <Button size="sm" onClick={limits.refresh}>
              Try again
            </Button>
          }
        />
      ) : !value ? (
        <Loading />
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!draft || tokenError || companyError) return;
            run(
              "limits",
              async () => {
                await api("/limits", "PUT", draft);
                setDraft(null);
                await limits.refresh();
              },
              "Limits saved.",
            );
          }}
        >
          <FormGrid>
            <Input
              label="Model budget per job"
              type="number"
              min={50000}
              max={50000000}
              step={50000}
              value={Number.isFinite(tokens) ? tokens : ""}
              error={tokenError}
              hint={`${tokens.toLocaleString()} prompt tokens per job in any 24 hours. Hard portals need more; a higher budget costs more.`}
              onChange={(e) => setDraft({ ...value, job_token_cap: Number(e.target.value) })}
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
          </FormGrid>
          <Actions note={draft ? "Unsaved changes" : undefined}>
            {draft && (
              <Button variant="ghost" disabled={pending !== null} onClick={() => setDraft(null)}>
                Discard
              </Button>
            )}
            <Button type="submit" variant="primary" loading={pending === "limits"} disabled={!draft || !!tokenError || !!companyError}>
              Save limits
            </Button>
          </Actions>
        </form>
      )}
    </Panel>
  );
}

// What the app is doing right now, in plain words: the worst thing that is true, first.
function automation(rt: AutopilotRuntime, paused: boolean, sched: SchedulerHealth, workers: number) {
  const done = rt.confirmed ?? 0;
  const goal = rt.target ?? 100;
  const progress = done > goal ? `${done} confirmed, target ${goal}` : `${done} of ${goal} confirmed`;
  if (sched.degraded) return { tone: "bad" as const, text: "Needs a restart", note: "Automation is degraded." };
  if (paused) return { tone: "warn" as const, text: "Paused", note: "New applications are paused." };
  if (rt.state === "complete") return { tone: "neutral" as const, text: "Target reached", note: progress };
  if (rt.intake_enabled)
    return { tone: "neutral" as const, text: "Running", note: workers ? `${progress}, ${plural(workers, "worker")}` : progress };
  if (rt.state === "active") return { tone: "warn" as const, text: "Held", note: rt.intake_reason || progress };
  return { tone: "neutral" as const, text: "No batch", note: workers ? `${plural(workers, "worker")} ready` : "Start one on Overview." };
}

function modelState(model: NonNullable<RuntimeHealth["models"]>["answers"]) {
  if (!model) return "";
  return model.state === "ready"
    ? "Requests enabled"
    : model.state === "recovery_ready"
      ? "Waiting for one recovery request"
      : model.state === "recovering"
        ? "One recovery request in progress"
        : `Requests paused${model.retry_at ? " until " + new Date(model.retry_at).toLocaleTimeString() : ""}`;
}

/** Worker and host status: what is running, how many workers, and whether it needs a restart. */
function SystemStatus() {
  const { snapshot } = useWorkspace();
  const health = usePoll<RuntimeHealth>((signal) => api("/runtime-health", "GET", undefined, signal), 30000);
  const conn = usePoll<{ fields: { name: string; label: string; pending: string; value: string }[] }>(
    (signal) => api("/connections", "GET", undefined, signal),
    60000,
  );
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
            "Automation",
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
              ? `${pool.normal_workers} regular, ${pool.specialist_workers} difficult-flow${pool.custom_workers ? ` and ${pool.custom_workers} custom-site` : ""}, ${busy} busy`
              : snapshot.config?.workers_enabled
                ? `${snapshot.config.workers ?? "—"} at most`
                : "Disabled in configuration",
          ],
          [
            "Scheduler",
            <>
              {sched.degraded ? (
                <Pill tone="bad" dot>
                  Needs a restart
                </Pill>
              ) : (
                sched.state || "Unknown"
              )}
              {sched.checked_at && <span className="ax-hint"> checked {ago(sched.checked_at)}</span>}
            </>,
          ],
          ["Last automation check", ago(health.data?.heartbeat?.at || snapshot.scheduler_heartbeat?.at)],
          ["Notifications waiting", health.data ? `${health.data.outbox_pending ?? 0} pending` : "…"],
          ...Object.entries(health.data?.models || {}).map(
            ([role, model]): [string, string] => [
              role === "answers" ? "Answer model" : "Navigation model",
              model ? `${model.provider}. ${modelState(model)}` : "",
            ],
          ),
          ["Engine", <span key="e" className="mono">{health.data?.engine_version || snapshot.engine_version || "—"}</span>],
          ["Timezone", snapshot.config?.timezone || "—"],
        ]}
      />
      {restartFor && <RestartNotice message={restartFor} />}
      {slots.length > 0 && (
        <details className="ax-fold">
          <summary>
            <strong>Worker slots</strong>
            <span className="ax-hint">{slots.length}</span>
          </summary>
          <Table
            label="Worker slots"
            rows={slots}
            rowKey={(s) => `${s.lane}-${s.slot}`}
            minWidth={420}
            columns={[
              { key: "slot", header: "Slot", cell: (s) => s.slot },
              { key: "lane", header: "Type", cell: (s) => (s.lane === "specialist" ? "Difficult-flow" : "Regular") },
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

function SystemSettings() {
  const { snapshot, refresh } = useWorkspace();
  const { pending, run } = useAction();
  const mail = usePoll<MailStatus>((signal) => api("/mail", "GET", undefined, signal), 60000);
  const inboxes = (mail.data?.mailboxes || []).filter((m) => m.state === "connected").map((m) => m.email);
  const cfg = snapshot.config || ({} as Snapshot["config"]);
  const control = snapshot.control || { paused: false, auto_submit: false };
  const stats = snapshot.stats || ({} as Snapshot["stats"]);
  const money = (n: unknown) => `$${Number(n ?? 0).toFixed(2)}`;
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
      <SystemStatus />
      <Panel title="Global controls">
        <SwitchRow
          label="Automatic submission"
          note="Off means applications stop before submitting. Dry runs never submit either way."
          checked={!!control.auto_submit}
          disabled={pending !== null}
          onChange={() =>
            toggle("auto_submit", control.auto_submit ? "Automatic submission turned off." : "Automatic submission turned on.")
          }
        />
        <SwitchRow
          label="Pause new applications"
          note="Holds running ones before their final submit. A submission already in progress finishes."
          checked={!!control.paused}
          disabled={pending !== null}
          onChange={() => toggle("paused", control.paused ? "New applications resumed." : "New applications paused.")}
        />
        <Facts
          items={[
            ["Time per application", `${cfg.timeout ?? "—"}s single page, ${cfg.multipage_timeout ?? "—"}s multi-page`],
            ["Daily submission cap", `${stats.submissions_reserved ?? 0} of ${cfg.daily_limit ?? "—"} used today`],
            ["Daily model budget", `${money(stats.spend_today)} of ${money(cfg.budget)} spent today`],
          ]}
        />
      </Panel>
      <LimitSettings />
      <AgentControls />
      <Panel title="Services">
        <ul className="ax-rows">
          {(
            [
              ["Language model", !!cfg.mimo, `${cfg.model_provider ? cfg.model_provider + " · " : ""}${cfg.model || "not set"}`],
              ["Application workers", !!cfg.workers_enabled, cfg.workers_enabled ? `Engine: ${cfg.engine}` : "Disabled in configuration"],
              [
                "Email inbox",
                !!cfg.imap || inboxes.length > 0,
                inboxes.length ? inboxes.join(", ") : cfg.imap ? "Gmail address and app password" : "Connect it in Settings › Email",
              ],
              [
                "Telegram (optional)",
                !!cfg.telegram,
                cfg.telegram
                  ? `Bot ${snapshot.telegram_health?.state || "configured"}. Answers sent there resume waiting jobs.`
                  : "Not configured. The app works without it.",
              ],
              ["2Captcha (optional)", !!cfg.twocaptcha, cfg.twocaptcha ? "Key saved." : "Add a key in Settings › Connections."],
            ] as [string, boolean, string][]
          ).map(([name, ok, note]) => (
            <li className="ax-row" key={name}>
              <span className="ax-row-main">
                <strong>{name}</strong>
                <small>{note}</small>
              </span>
              <Pill tone="neutral">{ok ? "Ready" : "Not set"}</Pill>
            </li>
          ))}
        </ul>
      </Panel>
      <SignInSettings />
      <details className="ax-panel ax-fold">
        <summary>
          <strong>Application platforms</strong>
          <span className="ax-hint">{plural((snapshot.ats || []).length, "adapter")}</span>
        </summary>
        <div className="ax-panel-body">
          <ul className="ax-rows">
            {(snapshot.ats || []).map((a) => (
              <li className="ax-row" key={a.id}>
                <span className="ax-row-main">
                  <strong>{a.name}</strong>
                  <small>{a.guidance}</small>
                </span>
                <span className="ax-hint">{a.tier}</span>
              </li>
            ))}
          </ul>
        </div>
      </details>
    </>
  );
}

const AGENT_HELPERS: {
  key: keyof AgentSettingsUpdate["enabled"];
  label: string;
  note: string;
}[] = [
  { key: "answers", label: "Form answers", note: "Retrieve applicant facts and answer application questions." },
  { key: "accounts", label: "Employer accounts", note: "Sign in, create accounts, and recover accepted credentials." },
  { key: "email", label: "Email", note: "Find verification codes, account links, and application receipts." },
  { key: "captcha", label: "CAPTCHA", note: "Use the configured 2Captcha service when a challenge appears." },
  { key: "navigator", label: "Page navigation", note: "Interpret controls and recover when a page stops progressing." },
  { key: "discovery", label: "Job discovery", note: "Find fresh positions that match the applicant's preferences." },
];

const AGENT_PROVIDERS: { value: AgentModelProvider; label: string }[] = [
  { value: "haiku", label: "Haiku" },
  { value: "mimo", label: "MiMo" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "openai", label: "OpenAI GPT-6 Luna" },
  { value: "openrouter", label: "OpenRouter DeepSeek V4.1 Flash (Relace)" },
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
  const update = (next: AgentSettingsUpdate) => setDraft(next);

  return (
    <Panel title="Agents and models">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!current || !changed || unavailable || pending) return;
          run(
            "agents",
            async () => {
              const result = await api<AgentSettings>("/agents", "PUT", {
                enabled: current.enabled,
                models: current.models,
              });
              settings.setData(result);
              clearDraft();
              await refresh();
            },
            "Agent settings saved.",
          );
        }}
      >
        {settings.error && !usable && (
          <Empty
            title="Could not load agent settings"
            hint={settings.error.message}
            action={
              <Button size="sm" onClick={settings.refresh}>
                Try again
              </Button>
            }
          />
        )}
        {!current && !settings.error && <Loading label="Loading agent settings…" />}
        {current && usable && (
          <>
            {AGENT_HELPERS.map(({ key, label, note }) => (
              <SwitchRow
                key={key}
                label={label}
                note={note}
                checked={!!current.enabled[key]}
                disabled={pending !== null}
                onChange={() =>
                  update({
                    enabled: { ...current.enabled, [key]: !current.enabled[key] },
                    models: { ...current.models },
                  })
                }
              />
            ))}
            <FormGrid>
              {(["answers", "navigator"] as const).map((role) => {
                const provider = saved!.providers[current.models[role]];
                return (
                  <Select
                    key={role}
                    label={role === "answers" ? "Answer model" : "Navigation model"}
                    hint={provider?.available ? provider.model : undefined}
                    error={provider?.available ? undefined : "Add its API key in Settings › Connections."}
                    value={current.models[role]}
                    disabled={pending !== null}
                    onChange={(e) =>
                      update({
                        enabled: { ...current.enabled },
                        models: { ...current.models, [role]: e.target.value as AgentModelProvider },
                      })
                    }
                  >
                    {AGENT_PROVIDERS.map(({ value, label }) => (
                      <option key={value} value={value} disabled={!saved!.providers[value]?.available}>
                        {label}
                        {saved!.providers[value]?.available ? "" : " · not configured"}
                      </option>
                    ))}
                  </Select>
                );
              })}
            </FormGrid>
            <Actions note={changed ? "Unsaved changes" : undefined}>
              {changed && (
                <Button variant="ghost" disabled={pending !== null} onClick={clearDraft}>
                  Discard
                </Button>
              )}
              <Button type="submit" variant="primary" loading={pending === "agents"} disabled={!changed || unavailable}>
                Save agent settings
              </Button>
            </Actions>
          </>
        )}
      </form>
    </Panel>
  );
}

// Advanced: hand-configured sources outside the managed employer catalog.
function CustomSources() {
  const [open, setOpen] = useState(false);
  return (
    <details className="ax-panel ax-fold" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>
        <strong>Custom sources</strong>
        <span className="ax-hint">advanced, with their own schedules</span>
      </summary>
      <div className="ax-panel-body">{open && <CustomSourceList />}</div>
    </details>
  );
}

function CustomSourceList() {
  const all = usePoll<Snapshot>((signal) => api("/snapshot?include_sources=true", "GET", undefined, signal), 120000);
  const managed = usePoll<{ sources: Source[] }>(
    (signal) => api("/job-board?include_sources=true&page=1&size=1", "GET", undefined, signal),
    120000,
  );
  const { pending, run } = useAction();
  const [query, setQuery] = useState("");
  const [name, setName] = useDraft("src-name", "");
  const [url, setUrl] = useDraft("src-url", "");
  const [kind, setKind] = useDraft("src-kind", "greenhouse");
  const [cron, setCron] = useDraft("src-cron", "0 */3 * * *");
  const [auto, setAuto] = useDraft("src-auto", false);
  const managedIds = useMemo(() => new Set((managed.data?.sources || []).map((s) => s.id)), [managed.data]);
  const q = query.trim().toLowerCase();
  const sources = (all.data?.sources || []).filter(
    (s) => !managedIds.has(s.id) && (!q || `${s.name} ${s.kind} ${s.url}`.toLowerCase().includes(q)),
  );
  const paging = usePagination(sources, q, 10);
  const reload = async () => {
    await Promise.all([all.refresh(), managed.refresh()]);
  };
  if (!all.data || !managed.data)
    return all.error || managed.error ? (
      <Empty
        title="Could not load the sources"
        hint={(all.error || managed.error)?.message}
        action={
          <Button size="sm" onClick={reload}>
            Try again
          </Button>
        }
      />
    ) : (
      <Loading />
    );
  const put = (s: Source, patch: Partial<Source>) =>
    api("/sources/" + s.id, "PUT", {
      name: s.name,
      kind: s.kind,
      url: s.url,
      cron: s.cron,
      enabled: s.enabled,
      auto_queue: s.auto_queue,
      ...patch,
    });
  return (
    <div className="ax-stack">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          run(
            "add",
            async () => {
              await api("/sources", "POST", {
                name: name.trim(),
                url: url.trim(),
                kind,
                cron: cron.trim(),
                enabled: true,
                auto_queue: auto,
              });
              setName("");
              setUrl("");
              await reload();
            },
            "Source added.",
          );
        }}
      >
        <FormGrid>
          <Input label="Name" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
          <Select label="Platform" value={kind} onChange={(e) => setKind(e.target.value)}>
            {["greenhouse", "lever", "ashby", "smartrecruiters", "workday", "career_page"].map((k) => (
              <option key={k} value={k}>
                {k.replace("_", " ")}
              </option>
            ))}
          </Select>
          <Input
            label="Board URL"
            hint="The company's board, not a single job."
            required
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <Input
            label="Schedule (cron)"
            hint="0 */3 * * * checks every three hours."
            required
            value={cron}
            onChange={(e) => setCron(e.target.value)}
          />
          <Check checked={auto} onChange={setAuto} label="Rank new jobs and queue eligible matches" />
        </FormGrid>
        <Actions>
          <Button type="submit" loading={pending === "add"} disabled={pending !== null && pending !== "add"}>
            Add source
          </Button>
        </Actions>
      </form>
      <div className="ax-toolbar">
        <SearchBox label="Search custom sources" placeholder="Search" value={query} onChange={setQuery} />
      </div>
      {sources.length ? (
        <>
          <ul className="ax-rows">
            {paging.items.map((s) => (
              <li className="ax-row" key={s.id}>
                <span className="ax-row-main">
                  <strong>{s.name}</strong>
                  <small>
                    <External href={s.url}>{(s.kind || "").replace("_", " ")}</External> · <span className="mono">{s.cron}</span>{" "}
                    · last run {ago(s.last_run)} · {s.found ?? 0} new
                    {s.error && (
                      <>
                        {" · "}
                        <Pill tone="warn" dot>
                          {s.error}
                        </Pill>
                      </>
                    )}
                  </small>
                </span>
                <span className="ax-row-end">
                  <Button
                    size="sm"
                    disabled={pending !== null}
                    onClick={() =>
                      run(
                        "t" + s.id,
                        async () => {
                          await put(s, { enabled: !s.enabled });
                          await reload();
                        },
                        s.enabled ? "Source paused." : "Source enabled.",
                      )
                    }
                  >
                    {s.enabled ? "Pause" : "Enable"}
                  </Button>
                  <Button
                    size="sm"
                    disabled={pending !== null || !s.enabled}
                    onClick={() => run("d" + s.id, () => api(`/sources/${s.id}/discover`, "POST"), "Check queued.")}
                  >
                    Check now
                  </Button>
                  <ConfirmButton
                    label="Remove"
                    confirm="Remove source"
                    pending={pending === "r" + s.id}
                    onConfirm={() =>
                      run(
                        "r" + s.id,
                        async () => {
                          await api("/sources/" + s.id, "DELETE");
                          await reload();
                        },
                        "Source removed.",
                      )
                    }
                  />
                </span>
              </li>
            ))}
          </ul>
          <Pagination paging={paging} label="Source pages" />
        </>
      ) : (
        <p className="ax-hint">{q ? "No custom sources match." : "No custom sources. The managed employer boards above cover discovery."}</p>
      )}
    </div>
  );
}
