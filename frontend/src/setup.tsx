import { useMemo, useState } from "react";
import { Download, Search } from "lucide-react";
import { api } from "./api";
import { routeUrl, useWorkspace } from "./context";
import { useAction, useDraft, usePoll } from "./hooks";
import { AccountSettings, ProfileSettings } from "./autonomy";
import { DiscoverySettings } from "./jobboard";
import { MailSettings } from "./mail";
import { ConnectionSettings } from "./connections";
import { SignInSettings } from "./account";
import { KnowledgeSection, TailoredResumes } from "./features";
import { Pagination, usePagination } from "./pagination";
import type {
  AgentModelProvider,
  AgentSettings,
  AgentSettingsUpdate,
  Limits,
  MailStatus,
  RuntimeHealth,
  Snapshot,
  Source,
} from "./types";
import {
  ago,
  Confirm,
  day,
  Empty,
  ErrorLine,
  External,
  Field,
  KeyValues,
  Loading,
  plural,
  StatePill,
} from "./ui";

const SECTIONS = [
  { id: "connections", label: "Connections", note: "AI key, Telegram, captcha" },
  { id: "profile", label: "Profile", note: "Facts every answer is built from" },
  {
    id: "answers",
    label: "Knowledge",
    note: "Facts, narratives, employer memory",
  },
  { id: "resumes", label: "Resumes", note: "Files attached to applications" },
  { id: "accounts", label: "Accounts", note: "Employer sign-ins" },
  { id: "email", label: "Email", note: "Codes, links and receipts" },
  { id: "discovery", label: "Discovery", note: "What to look for" },
  { id: "system", label: "System", note: "Controls and health" },
];

export function SetupView() {
  const { route, go } = useWorkspace();
  const section = SECTIONS.find((s) => s.id === route.tab) || SECTIONS[0];
  return (
    <div className="page setup">
      <header className="page-head">
        <h1>Setup</h1>
      </header>
      <div className="setup-grid">
        <nav className="setup-nav" aria-label="Setup sections">
          {SECTIONS.map((s) => (
            <a
              key={s.id}
              href={routeUrl({ view: "setup", tab: s.id })}
              aria-current={section.id === s.id ? "page" : undefined}
              onClick={(e) => {
                e.preventDefault();
                go({ view: "setup", tab: s.id });
              }}
            >
              <span>{s.label}</span>
              <small>{s.note}</small>
            </a>
          ))}
        </nav>
        <div className="setup-body">
          <h2 className="setup-title">{section.label}</h2>
          {section.id === "connections" && <ConnectionSettings />}
          {section.id === "profile" && <ProfileSettings />}
          {section.id === "answers" && <KnowledgeSection />}
          {section.id === "resumes" && (
            <>
              <Resumes />
              <TailoredResumes />
            </>
          )}
          {section.id === "accounts" && <AccountSettings />}
          {section.id === "email" && <MailSettings />}
          {section.id === "discovery" && (
            <>
              <DiscoverySettings />
              <CustomSources />
            </>
          )}
          {section.id === "system" && (
            <>
              <SignInSettings />
              <SystemSettings />
            </>
          )}
        </div>
      </div>
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
  const full = snapshot.resumes.length >= 10;
  return (
    <>
      <section className="panel">
        <header className="panel-head">
          <h2>Library</h2>
          <p className="muted">
            Your resume PDFs, up to 10. The exact file sent with each
            application is kept with it in the ledger.
          </p>
        </header>
        {snapshot.resumes.length ? (
          <ul className="list">
            {snapshot.resumes.map((r) => (
              <li key={r.id}>
                <span>
                  <strong>{r.name}</strong>
                  <small className="muted">
                    {r.roles.length ? r.roles.join(", ") + " · " : ""}added{" "}
                    {day(r.created_at)} ·{" "}
                    <span className="mono">{r.sha256.slice(0, 10)}</span>
                  </small>
                </span>
                <span className="row-actions">
                  <a className="btn sm" href={`/api/resumes/${r.id}/download`}>
                    <Download size={14} aria-hidden="true" /> Download
                  </a>
                  <Confirm
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
          <Empty title="No resumes yet">
            Add a text-based PDF. Ranking and applications need at least one.
          </Empty>
        )}
      </section>
      <form
        className="panel"
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
        <header className="panel-head">
          <h2>Add a resume</h2>
        </header>
        <div className="form-grid">
          <Field
            label="PDF file"
            hint="Text-based PDF, up to 5 MB and 20 pages."
            wide
          >
            <input
              key={inputKey}
              type="file"
              accept="application/pdf"
              required
              onChange={(e) => {
                const f = e.target.files?.[0] || null;
                setFile(f);
                if (f && !name) setName(f.name.replace(/\.pdf$/i, ""));
              }}
            />
          </Field>
          <Field label="Name">
            <input
              required
              maxLength={120}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Backend engineering"
            />
          </Field>
          <Field label="Role tags" hint="Comma separated">
            <input
              value={roles}
              onChange={(e) => setRoles(e.target.value)}
              placeholder="Backend, Data"
            />
          </Field>
        </div>
        <div className="form-actions">
          {full && (
            <span className="muted sm">
              The library is full. Remove a variant first.
            </span>
          )}
          <button
            className="btn primary"
            disabled={!file || full || pending !== null}
          >
            {pending === "upload" ? "Uploading…" : "Add to library"}
          </button>
        </div>
      </form>
    </>
  );
}

function LimitSettings() {
  const limits = usePoll<Limits>((signal) => api("/limits", "GET", undefined, signal), 60000);
  const [draft, setDraft] = useState<Limits | null>(null);
  const { pending, run } = useAction();
  const value = draft || limits.data;
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Limits</h2>
        <p className="muted">Spending and pacing for every application.</p>
      </header>
      <ErrorLine error={limits.error} retry={limits.refresh} />
      {!value ? (
        <Loading />
      ) : (
        <div className="form-grid">
          <Field
            label="Model budget per job"
            hint={`${value.job_token_cap.toLocaleString()} prompt tokens per job in any 24 hours. Hard portals need more; a higher budget costs more.`}
          >
            <input
              type="number"
              min={50000}
              max={2000000}
              step={50000}
              value={value.job_token_cap}
              onChange={(e) => setDraft({ ...value, job_token_cap: Number(e.target.value) })}
            />
          </Field>
          <Field label="Applications per employer" hint="Confirmed or in-progress applications allowed at one employer.">
            <input
              type="number"
              min={1}
              max={25}
              value={value.company_limit}
              onChange={(e) => setDraft({ ...value, company_limit: Number(e.target.value) })}
            />
          </Field>
        </div>
      )}
      <div className="form-actions start">
        <button
          className="btn sm"
          disabled={!draft || pending !== null}
          onClick={() =>
            run(
              "limits",
              async () => {
                await api("/limits", "PUT", draft);
                setDraft(null);
                await limits.refresh();
              },
              "Limits saved.",
            )
          }
        >
          {pending === "limits" ? "Saving…" : "Save limits"}
        </button>
        {draft && (
          <button className="btn ghost sm" disabled={pending !== null} onClick={() => setDraft(null)}>
            Discard
          </button>
        )}
      </div>
    </section>
  );
}

function SystemSettings() {
  const { snapshot, refresh } = useWorkspace();
  const { pending, run } = useAction();
  const health = usePoll<RuntimeHealth>(
    (signal) => api("/runtime-health", "GET", undefined, signal),
    30000,
  );
  const mail = usePoll<MailStatus>(
    (signal) => api("/mail", "GET", undefined, signal),
    60000,
  );
  const inboxes = (mail.data?.mailboxes || [])
    .filter((m) => m.state === "connected")
    .map((m) => m.email);
  const cfg = snapshot.config;
  const control = snapshot.control;
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
  const scheduler = health.data?.scheduler || snapshot.scheduler_health || {};
  return (
    <>
      <section className="panel">
        <header className="panel-head">
          <h2>Global controls</h2>
          <p className="muted">
            These apply to every job. To stop a single job, use its own Pause in
            Jobs.
          </p>
        </header>
        <div className="switch-row">
          <span>
            <strong>Automatic submission</strong>
            <small className="muted">
              Lets verified applications submit. Off means applications stop
              before submitting. Dry runs never submit either way.
            </small>
          </span>
          <button
            role="switch"
            aria-checked={control.auto_submit}
            aria-label="Automatic submission"
            className="switch"
            disabled={pending !== null}
            onClick={() =>
              toggle(
                "auto_submit",
                control.auto_submit
                  ? "Automatic submission turned off."
                  : "Automatic submission turned on.",
              )
            }
          >
            <i />
          </button>
        </div>
        <div className="switch-row">
          <span>
            <strong>Pause new applications</strong>
            <small className="muted">
              Stops new applications and holds running ones before their final
              submit. A submission already in progress finishes its
              confirmation.
            </small>
          </span>
          <button
            role="switch"
            aria-checked={control.paused}
            aria-label="Pause new applications"
            className="switch"
            disabled={pending !== null}
            onClick={() =>
              toggle(
                "paused",
                control.paused
                  ? "New applications resumed."
                  : "New applications paused.",
              )
            }
          >
            <i />
          </button>
        </div>
        <KeyValues
          items={[
            [
              "Applications at once",
              health.data?.application_workers
                ? `${health.data.application_workers.normal_workers} regular + ${health.data.application_workers.specialist_workers} difficult-flow`
                : `${cfg.workers} at most`,
            ],
            [
              "Time per application",
              `${cfg.timeout}s single page · ${cfg.multipage_timeout}s multi-page`,
            ],
            [
              "Daily submission cap",
              `${snapshot.stats.submissions_reserved} of ${cfg.daily_limit} used today`,
            ],
            [
              "Daily model budget",
              `$${snapshot.stats.spend_today.toFixed(2)} of $${cfg.budget.toFixed(2)} spent today`,
            ],
            ["Timezone", cfg.timezone],
          ]}
        />
        <p className="muted sm">
          These come from the server's .env and apply after a restart. The limits below change right away.
        </p>
      </section>
      <LimitSettings />
      <AgentControls />
      <section className="panel">
        <header className="panel-head">
          <h2>Services</h2>
          <p className="muted">
            Keys live only on the server and are never sent to this page.
          </p>
        </header>
        <ul className="list">
          {(
            [
              [
                "Language model",
                cfg.mimo,
                `${cfg.model_provider ? cfg.model_provider + " · " : ""}${cfg.model}`,
              ],
              [
                "Application workers",
                cfg.workers_enabled,
                cfg.workers_enabled
                  ? `Engine: ${cfg.engine}`
                  : "Disabled in configuration",
              ],
              [
                "Email inbox",
                cfg.imap || inboxes.length > 0,
                inboxes.length
                  ? inboxes.join(", ")
                  : cfg.imap
                    ? "GMAIL_ADDRESS + GMAIL_APP_PASSWORD"
                    : "Connect it in Setup → Email",
              ],
              [
                "Telegram (optional)",
                cfg.telegram,
                cfg.telegram
                  ? `Bot ${snapshot.telegram_health?.state || "configured"}. Answers sent there resume waiting jobs.`
                  : "Not configured. The app works without it.",
              ],
              ["2Captcha", cfg.twocaptcha, "TWOCAPTCHA_API_KEY"],
            ] as [string, boolean, string][]
          ).map(([name, ok, note]) => (
            <li key={name}>
              <span>
                <strong>{name}</strong>
                <small className="muted">{note}</small>
              </span>
              <StatePill
                state={ok ? "confirmed" : "job:new"}
                label={ok ? "Ready" : "Not set"}
              />
            </li>
          ))}
        </ul>
      </section>
      <section className="panel">
        <header className="panel-head">
          <h2>Technical details</h2>
        </header>
        <ErrorLine error={health.error} retry={health.refresh} />
        {Object.entries(health.data?.models || {}).map(([role, model]) => model && (
          <p key={role} className={model.state === "ready" ? "muted" : "warn-text"}>
            {role === "answers" ? "Answers" : "Navigation"}: {model.provider} · {model.state === "ready"
              ? "Requests enabled"
              : model.state === "recovery_ready" ? "Waiting for one recovery request"
              : model.state === "recovering" ? "One recovery request in progress"
              : `Requests paused until ${new Date(model.retry_at!).toLocaleTimeString()}`}
          </p>
        ))}
        <KeyValues
          items={[
            [
              "Scheduler",
              <span className={scheduler.degraded ? "warn-text" : ""}>
                {scheduler.degraded
                  ? "Needs repair"
                  : scheduler.state || "Unknown"}
                {scheduler.checked_at && (
                  <span className="muted">
                    {" "}
                    · checked {ago(scheduler.checked_at)}
                  </span>
                )}
              </span>,
            ],
            [
              "Last automation check",
              ago(
                health.data?.heartbeat?.at || snapshot.scheduler_heartbeat?.at,
              ),
            ],
            [
              "Notifications waiting",
              health.data ? `${health.data.outbox_pending ?? 0} pending` : "…",
            ],
            [
              "Engine",
              <span className="mono">
                {health.data?.engine_version || snapshot.engine_version || "—"}
              </span>,
            ],
          ]}
        />
      </section>
      <details className="panel fold">
        <summary>
          <strong>Application platforms</strong>
          <span className="muted sm">
            {" "}
            · {plural(snapshot.ats.length, "adapter")}
          </span>
        </summary>
        <ul className="list">
          {snapshot.ats.map((a) => (
            <li key={a.id}>
              <span>
                <strong>{a.name}</strong>
                <small className="muted">{a.guidance}</small>
              </span>
              <span className="muted sm">{a.tier}</span>
            </li>
          ))}
        </ul>
      </details>
    </>
  );
}

const AGENT_HELPERS: {
  key: keyof AgentSettingsUpdate["enabled"];
  label: string;
  note: string;
}[] = [
  {
    key: "answers",
    label: "Form answers",
    note: "Retrieve applicant facts and answer application questions.",
  },
  {
    key: "accounts",
    label: "Employer accounts",
    note: "Sign in, create accounts, and recover accepted credentials.",
  },
  {
    key: "email",
    label: "Email",
    note: "Find verification codes, account links, and application receipts.",
  },
  {
    key: "captcha",
    label: "CAPTCHA",
    note: "Use the configured 2Captcha service when a challenge appears.",
  },
  {
    key: "navigator",
    label: "Page navigation",
    note: "Interpret controls and recover when a page stops progressing.",
  },
  {
    key: "discovery",
    label: "Job discovery",
    note: "Find fresh positions that match the applicant's preferences.",
  },
];

const AGENT_PROVIDERS: { value: AgentModelProvider; label: string }[] = [
  { value: "haiku", label: "Haiku" },
  { value: "mimo", label: "MiMo" },
  { value: "deepseek", label: "DeepSeek" },
];

function AgentControls() {
  const { refresh } = useWorkspace();
  const settings = usePoll<AgentSettings>(
    (signal) => api("/agents", "GET", undefined, signal),
    30000,
  );
  const [draft, setDraft, clearDraft] = useDraft<AgentSettingsUpdate | null>(
    "agent-settings",
    null,
  );
  const [saveError, setSaveError] = useState<Error | null>(null);
  const [saved, setSaved] = useState(false);
  const { pending, run } = useAction();
  const current = draft || settings.data;
  const changed =
    !!draft &&
    !!settings.data &&
    (AGENT_HELPERS.some(
      ({ key }) => draft.enabled[key] !== settings.data!.enabled[key],
    ) ||
      draft.models.answers !== settings.data.models.answers ||
      draft.models.navigator !== settings.data.models.navigator);
  const unavailable =
    !!current &&
    !!settings.data &&
    (!settings.data.providers[current.models.answers].available ||
      !settings.data.providers[current.models.navigator].available);
  const update = (next: AgentSettingsUpdate) => {
    setDraft(next);
    setSaved(false);
    setSaveError(null);
  };

  return (
    <form
      className="panel"
      onSubmit={(e) => {
        e.preventDefault();
        if (!current || !changed || unavailable || pending) return;
        setSaveError(null);
        run(
          "agents",
          async () => {
            try {
              const result = await api<AgentSettings>("/agents", "PUT", {
                enabled: current.enabled,
                models: current.models,
              });
              settings.setData(result);
              clearDraft();
              setSaved(true);
            } catch (error) {
              setSaveError(error as Error);
              throw error;
            }
            await refresh();
          },
          "Agent settings saved.",
        );
      }}
    >
      <header className="panel-head">
        <h2>Agents and models</h2>
        <p className="muted">
          Choose the helpers available to application workers and the model used
          for answers and navigation. Credentials stay on the server.
        </p>
      </header>
      <ErrorLine error={settings.error} retry={settings.refresh} />
      {!current && !settings.error && (
        <Loading label="Loading agent settings…" />
      )}
      {current && settings.data && (
        <>
          {AGENT_HELPERS.map(({ key, label, note }) => (
            <div className="switch-row" key={key}>
              <span>
                <strong>{label}</strong>
                <small className="muted">{note}</small>
              </span>
              <button
                type="button"
                role="switch"
                aria-checked={current.enabled[key]}
                aria-label={label + " agent"}
                className="switch"
                disabled={pending !== null}
                onClick={() =>
                  update({
                    enabled: {
                      ...current.enabled,
                      [key]: !current.enabled[key],
                    },
                    models: { ...current.models },
                  })
                }
              >
                <i />
              </button>
            </div>
          ))}
          <div className="form-grid">
            {(["answers", "navigator"] as const).map((role) => {
              const provider = settings.data!.providers[current.models[role]];
              return (
                <Field
                  key={role}
                  label={
                    role === "answers" ? "Answer model" : "Navigation model"
                  }
                  hint={
                    provider.available
                      ? provider.model
                      : "Add its API key in Setup > Connections."
                  }
                >
                  <select
                    value={current.models[role]}
                    disabled={pending !== null}
                    onChange={(e) =>
                      update({
                        enabled: { ...current.enabled },
                        models: {
                          ...current.models,
                          [role]: e.target.value as AgentModelProvider,
                        },
                      })
                    }
                  >
                    {AGENT_PROVIDERS.map(({ value, label }) => (
                      <option
                        key={value}
                        value={value}
                        disabled={!settings.data!.providers[value].available}
                      >
                        {label}
                        {settings.data!.providers[value].available
                          ? ""
                          : " · not configured"}
                      </option>
                    ))}
                  </select>
                </Field>
              );
            })}
          </div>
          <ErrorLine error={saveError} />
          <div className="form-actions">
            <span className="muted sm" role="status">
              {pending
                ? "Saving agent settings…"
                : changed
                  ? "Unsaved changes"
                  : saved
                    ? "Agent settings saved."
                    : ""}
            </span>
            <button
              type="button"
              className="btn"
              disabled={!changed || pending !== null}
              onClick={() => {
                clearDraft();
                setSaveError(null);
                setSaved(false);
              }}
            >
              Discard changes
            </button>
            <button
              className="btn primary"
              disabled={!changed || unavailable || pending !== null}
            >
              {pending ? "Saving…" : "Save agent settings"}
            </button>
          </div>
        </>
      )}
    </form>
  );
}

// Advanced: hand-configured sources outside the managed employer catalog.
function CustomSources() {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="panel fold"
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary>
        <strong>Custom sources</strong>
        <span className="muted sm"> · advanced, with their own schedules</span>
      </summary>
      {open && <CustomSourceList />}
    </details>
  );
}

function CustomSourceList() {
  const all = usePoll<Snapshot>(
    (signal) => api("/snapshot?include_sources=true", "GET", undefined, signal),
    120000,
  );
  const managed = usePoll<{ sources: Source[] }>(
    (signal) =>
      api("/job-board?include_sources=true", "GET", undefined, signal),
    120000,
  );
  const { pending, run } = useAction();
  const [query, setQuery] = useState("");
  const [name, setName] = useDraft("src-name", "");
  const [url, setUrl] = useDraft("src-url", "");
  const [kind, setKind] = useDraft("src-kind", "greenhouse");
  const [cron, setCron] = useDraft("src-cron", "0 */3 * * *");
  const [auto, setAuto] = useDraft("src-auto", false);
  const managedIds = useMemo(
    () => new Set((managed.data?.sources || []).map((s) => s.id)),
    [managed.data],
  );
  const q = query.trim().toLowerCase();
  const sources = (all.data?.sources || []).filter(
    (s) =>
      !managedIds.has(s.id) &&
      (!q || `${s.name} ${s.kind} ${s.url}`.toLowerCase().includes(q)),
  );
  const paging = usePagination(sources, q, 10);
  const reload = async () => {
    await Promise.all([all.refresh(), managed.refresh()]);
  };
  if (!all.data || !managed.data)
    return all.error || managed.error ? (
      <ErrorLine error={all.error || managed.error} retry={reload} />
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
    <>
      <form
        className="form-grid"
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
        <Field label="Name">
          <input
            required
            maxLength={120}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="Platform">
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {[
              "greenhouse",
              "lever",
              "ashby",
              "smartrecruiters",
              "workday",
              "career_page",
            ].map((k) => (
              <option key={k} value={k}>
                {k.replace("_", " ")}
              </option>
            ))}
          </select>
        </Field>
        <Field
          label="Board URL"
          hint="The company's board, not a single job."
          wide
        >
          <input
            required
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </Field>
        <Field
          label="Schedule (cron)"
          hint="0 */3 * * * checks every three hours."
        >
          <input
            className="mono"
            required
            value={cron}
            onChange={(e) => setCron(e.target.value)}
          />
        </Field>
        <label className="check">
          <input
            type="checkbox"
            checked={auto}
            onChange={(e) => setAuto(e.target.checked)}
          />
          <span>Rank new jobs and queue eligible matches</span>
        </label>
        <div className="form-actions wide">
          <button className="btn" disabled={pending !== null}>
            {pending === "add" ? "Adding…" : "Add source"}
          </button>
        </div>
      </form>
      <div className="toolbar">
        <label className="search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="Search custom sources"
            placeholder="Search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
      </div>
      {sources.length ? (
        <>
          <ul className="list">
            {paging.items.map((s) => (
              <li key={s.id}>
                <span>
                  <strong>{s.name}</strong>
                  <small className="muted">
                    <External href={s.url}>{s.kind.replace("_", " ")}</External>{" "}
                    · <span className="mono">{s.cron}</span> · last run{" "}
                    {ago(s.last_run)} · {s.found} new
                    {s.error && <span className="warn-text"> · {s.error}</span>}
                  </small>
                </span>
                <span className="row-actions">
                  <button
                    className="btn sm"
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
                  </button>
                  <button
                    className="btn sm"
                    disabled={pending !== null || !s.enabled}
                    onClick={() =>
                      run(
                        "d" + s.id,
                        () => api(`/sources/${s.id}/discover`, "POST"),
                        "Check queued.",
                      )
                    }
                  >
                    Check now
                  </button>
                  <Confirm
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
        <p className="muted sm">
          {q
            ? "No custom sources match."
            : "No custom sources. The managed employer boards above cover discovery."}
        </p>
      )}
    </>
  );
}
