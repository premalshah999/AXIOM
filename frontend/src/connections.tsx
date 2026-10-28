import { useState } from "react";
import { api } from "./api";
import { routeUrl, useWorkspace } from "./context";
import { useAction, usePoll } from "./hooks";
import type { MailStatus } from "./types";
import { ErrorLine, Field, Loading } from "./ui";

type ConnectionField = {
  name: string;
  group: string;
  label: string;
  secret: boolean;
  help: string;
  set: boolean;
  source: "app" | "env" | "";
  value: string;
  // How the field is edited: from the setting's own type on the server.
  kind: "secret" | "bool" | "number" | "text";
  // Read once at startup: saved now, used after the app restarts.
  restart: boolean;
  // A fixed choice (normal workers 4, 6 or 8; difficult mode 1 or 2).
  options: string[];
  // Saved but not running yet: the value the next start uses.
  pending: string;
  // How each choice reads (OpenRouter hosts with their prices).
  option_labels?: Record<string, string>;
};

type Connections = { fields: ConnectionField[]; provider: string; model_ready: boolean };

const GROUPS = [
  { id: "Model", title: "AI model", note: "Required. Reads job pages and writes your answers. One key is enough." },
  { id: "Email", title: "Gmail", note: "Required. Reads verification codes, sign-in links and receipts." },
  { id: "Accounts", title: "Employer accounts", note: "The login the app uses on career sites." },
  { id: "Captcha", title: "Captcha solving", note: "Optional. Solves puzzles some employer sites show." },
  { id: "Telegram", title: "Telegram", note: "Optional. Questions and alerts on your phone." },
  { id: "Discovery", title: "Job search", note: "Optional. More job sources." },
  { id: "Limits", title: "Limits", note: "Daily caps on applications and AI spending." },
  { id: "Vision", title: "Screenshot model", note: "Optional. Only needed if your AI model cannot read images." },
  { id: "Workers", title: "Workers", note: "How many applications run at once." },
];

const PROVIDERS = [
  { id: "deepseek", label: "DeepSeek (recommended, lowest cost)" },
  { id: "haiku", label: "Anthropic Claude Haiku" },
  { id: "mimo", label: "Xiaomi MiMo" },
  { id: "openai", label: "OpenAI GPT-6 Luna (cheapest setting)" },
  { id: "openrouter", label: "OpenRouter: DeepSeek V4.1 Flash (choose its host below)" },
];

/** Setup > Connections: every service key and setting is entered here; nothing needs editing in .env. */
export function ConnectionSettings() {
  const state = usePoll<Connections>((signal) => api("/connections", "GET", undefined, signal), 60000);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, { ok: boolean; message: string }>>({});
  const { pending, run } = useAction();
  const data = state.data;
  const changed = Object.keys(draft).length > 0;
  const restartNeeded = Object.keys(draft).some((name) => data?.fields.find((f) => f.name === name)?.restart);

  const save = () =>
    run(
      "save",
      async () => {
        const next = await api<Connections>("/connections", "PUT", { values: draft });
        setDraft({});
        await state.refresh();
        return next;
      },
      restartNeeded ? "Saved. Worker settings apply after the app restarts." : "Settings saved.",
    );

  const test = (name: string) =>
    run(`test:${name}`, async () => {
      const result = await api<{ ok: boolean; message: string }>(`/connections/${name}/test`, "POST");
      setResults((r) => ({ ...r, [name]: result }));
      return result;
    });

  const editor = (f: ConnectionField) => {
    if (f.name === "llm_provider")
      return (
        <select
          value={draft.llm_provider ?? data!.provider}
          onChange={(e) => setDraft({ ...draft, llm_provider: e.target.value })}
        >
          {PROVIDERS.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
      );
    if (f.options.length)
      return (
        <select
          value={draft[f.name] ?? (f.pending || f.value)}
          onChange={(e) => setDraft({ ...draft, [f.name]: e.target.value })}
        >
          {[...new Set([...f.options, f.pending || f.value].filter(Boolean))].map((o) => (
            <option key={o} value={o}>
              {f.option_labels?.[o] || o}
            </option>
          ))}
        </select>
      );
    if (f.kind === "bool")
      return (
        <select
          value={draft[f.name] ?? ((f.pending || f.value) === "true" ? "true" : "false")}
          onChange={(e) => setDraft({ ...draft, [f.name]: e.target.value })}
        >
          <option value="true">On</option>
          <option value="false">Off</option>
        </select>
      );
    return (
      <span className="input-row">
        <input
          type={f.kind === "secret" ? "password" : f.kind === "number" ? "number" : "text"}
          inputMode={f.kind === "number" ? "decimal" : undefined}
          step={f.kind === "number" ? "any" : undefined}
          autoComplete="off"
          spellCheck={false}
          placeholder={
            f.kind === "secret" ? (f.set ? "Replace…" : "Paste here") : f.set ? f.value : "Not set"
          }
          value={draft[f.name] ?? ""}
          onChange={(e) => setDraft({ ...draft, [f.name]: e.target.value })}
        />
        {f.kind === "secret" && (f.set || draft[f.name]) && (
          <button
            type="button"
            className="btn ghost sm"
            disabled={pending !== null || !!draft[f.name]}
            title={draft[f.name] ? "Save first, then test" : undefined}
            onClick={() => test(f.name)}
          >
            {pending === `test:${f.name}` ? "Testing…" : "Test"}
          </button>
        )}
      </span>
    );
  };

  const hint = (f: ConnectionField) => (
    <>
      {f.help}{" "}
      {f.kind === "bool" || f.options.length || f.name === "llm_provider" ? null : f.set ? (
        <strong>
          {f.kind === "secret" ? `Saved ${f.value}` : `Now: ${f.value}`}
          {f.source === "env" ? " (from the .env file)" : ""}.
        </strong>
      ) : (
        "Not set."
      )}
      {f.pending ? (
        <strong>
          {" "}
          Running {f.kind === "bool" ? (f.value === "true" ? "on" : "off") : f.value}; saved{" "}
          {f.kind === "bool" ? (f.pending === "true" ? "on" : "off") : f.pending}, used after a restart.
        </strong>
      ) : (
        f.restart && draft[f.name] !== undefined && <strong> Applies after a restart.</strong>
      )}
      {results[f.name] && (
        <span className={results[f.name].ok ? "ok-text" : "error-text"}> {results[f.name].message}</span>
      )}
    </>
  );

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Connections</h2>
        <p className="muted">
          Everything the app needs, in one place. Keys are encrypted on this computer and never shown again; use Test
          to check one. Nothing needs editing in the .env file.
        </p>
      </header>
      <ErrorLine error={state.error} retry={state.refresh} />
      {!data ? (
        <Loading />
      ) : (
        <>
          {!data.model_ready && (
            <p className="callout warn">
              Add a key for the selected AI model below. Until then the guide cannot read your resume and no application
              can run.
            </p>
          )}
          {GROUPS.map((group) => (
            <div key={group.id} className="connection-group">
              <h3>{group.title}</h3>
              <p className="muted small">{group.note}</p>
              {group.id === "Email" ? (
                <GmailConnection />
              ) : group.id === "Workers" ? (
                <>
                  <div className="form-grid">
                    {data.fields
                      .filter((f) => f.group === group.id)
                      .map((f) => (
                        <Field key={f.name} label={f.label} hint={hint(f)}>
                          {editor(f)}
                        </Field>
                      ))}
                  </div>
                  <RestartBar pending={data.fields.some((f) => f.pending)} />
                </>
              ) : (
                <div className="form-grid">
                  {data.fields
                    .filter((f) => f.group === group.id)
                    .map((f) => (
                      <Field key={f.name} label={f.label} hint={hint(f)}>
                        {editor(f)}
                      </Field>
                    ))}
                </div>
              )}
            </div>
          ))}
        </>
      )}
      <div className="form-actions start connections-save">
        <button className="btn primary sm" disabled={!changed || pending !== null} onClick={save}>
          {pending === "save" ? "Saving…" : changed ? `Save ${Object.keys(draft).length} change${Object.keys(draft).length > 1 ? "s" : ""}` : "Save"}
        </button>
        {changed && (
          <button className="btn ghost sm" disabled={pending !== null} onClick={() => setDraft({})}>
            Discard
          </button>
        )}
      </div>
    </section>
  );
}

// Worker counts are sized when the app starts: saving keeps them for the next start, and this restarts now. New
// starts pause, a final submit in progress finishes, then the app restarts on its own in under a minute.
function RestartBar({ pending }: { pending: boolean }) {
  const { pending: busy, run } = useAction();
  const [restarting, setRestarting] = useState(false);
  const restart = () =>
    run(
      "restart",
      async () => {
        await api("/system/restart", "POST");
        setRestarting(true);
        // Reload once the app answers again.
        const started = Date.now();
        const wait = async () => {
          await new Promise((r) => setTimeout(r, 4000));
          try {
            await api("/runtime-health", "GET");
            if (Date.now() - started > 8000) return location.reload();
          } catch {
            /* still restarting */
          }
          if (Date.now() - started < 180000) void wait();
        };
        void wait();
      },
      "Restarting. Running applications resume after the restart.",
    );
  if (!pending && !restarting) return null;
  return (
    <p className="callout restart-bar">
      {restarting
        ? "Restarting… this page reloads when the app is back (under a minute)."
        : "New worker settings are saved. They take effect when the app restarts."}{" "}
      {!restarting && (
        <button className="btn sm primary" disabled={busy !== null} onClick={restart}>
          {busy === "restart" ? "Restarting…" : "Restart now"}
        </button>
      )}
    </p>
  );
}

// Gmail inside Connections: its status, and the same app-password connection as Setup › Email.
function GmailConnection() {
  const { snapshot, go } = useWorkspace();
  const mail = usePoll<MailStatus>((signal) => api("/mail", "GET", undefined, signal), 60000);
  const [form, setForm] = useState({ email: "", app_password: "" });
  const { pending, run } = useAction();
  const boxes = mail.data?.mailboxes || [];
  const connect = () =>
    run(
      "gmail",
      async () => {
        await api("/mail/imap", "POST", { ...form, email: form.email || snapshot.profile.email });
        setForm({ email: "", app_password: "" });
        await mail.refresh();
      },
      "Gmail connected. Verification codes and receipts are read automatically.",
    );
  return (
    <>
      <ErrorLine error={mail.error} retry={mail.refresh} />
      {boxes.length > 0 && (
        <ul className="list gmail-list">
          {boxes.map((b) => (
            <li key={b.id}>
              <span>
                <strong>{b.email}</strong>{" "}
                <span className="muted">
                  {b.provider === "gmail_imap" ? "app password" : b.provider === "gmail_oauth" ? "Google sign-in" : ""}
                  {b.state && ` · ${b.state}`}
                </span>
              </span>
              <a
                className="link sm"
                href={routeUrl({ view: "setup", tab: "email" })}
                onClick={(e) => {
                  e.preventDefault();
                  go({ view: "setup", tab: "email" });
                }}
              >
                Manage
              </a>
            </li>
          ))}
        </ul>
      )}
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          connect();
        }}
      >
        <Field label={boxes.length ? "Connect another Gmail" : "Gmail address"} hint="Empty: the email in your profile.">
          <input
            type="email"
            autoComplete="off"
            placeholder={snapshot.profile.email || "you@gmail.com"}
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
          />
        </Field>
        <Field
          label="Gmail app password"
          hint={
            <>
              16 letters from{" "}
              <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer">
                myaccount.google.com/apppasswords
              </a>{" "}
              (needs 2-Step Verification). Read-only use: codes, links and receipts.
            </>
          }
        >
          <span className="input-row">
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder="xxxx xxxx xxxx xxxx"
              value={form.app_password}
              onChange={(e) => setForm({ ...form, app_password: e.target.value })}
            />
            <button className="btn sm" type="submit" disabled={!form.app_password.trim() || pending !== null}>
              {pending === "gmail" ? "Checking…" : "Connect"}
            </button>
          </span>
        </Field>
      </form>
    </>
  );
}
