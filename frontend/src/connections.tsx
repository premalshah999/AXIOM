import { useId, useState, type ReactNode } from "react";
import { api } from "./api";
import { routeUrl, useWorkspace } from "./context";
import { useAction, usePoll } from "./hooks";
import { Actions, FormGrid, RestartNotice, SaveBar, SecretInput } from "./setup-kit";
import type { MailStatus } from "./types";
import { Button, Empty, ErrorLine, External, Input, Loading, Panel, Pill, Select } from "./ui/index";

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

// `tag` is the one thing a person needs to know before filling a group in: whether it can wait.
const GROUPS = [
  { id: "Model", title: "AI model", tag: "Required" },
  { id: "Email", title: "Gmail", tag: "Required" },
  { id: "Accounts", title: "Employer accounts", tag: "" },
  { id: "Captcha", title: "Captcha solving", tag: "Optional" },
  { id: "Telegram", title: "Telegram", tag: "Optional" },
  { id: "Discovery", title: "Job search", tag: "Optional" },
  { id: "Limits", title: "Limits", tag: "" },
  { id: "Vision", title: "Screenshot model", tag: "Optional" },
  { id: "Workers", title: "Workers", tag: "Applies after a restart" },
];

const PROVIDERS = [
  { id: "deepseek", label: "DeepSeek (recommended, lowest cost)" },
  { id: "haiku", label: "Anthropic Claude Haiku" },
  { id: "mimo", label: "Xiaomi MiMo" },
  { id: "openai", label: "OpenAI GPT-6 Luna (cheapest setting)" },
  { id: "openrouter", label: "OpenRouter: DeepSeek V4.1 Flash (choose its host below)" },
];

type TestResult = { ok: boolean; message: string };

/** The value a field shows when nothing is typed: what the next start will use. */
const shown = (f: ConnectionField) => (f.pending || f.value || "") as string;

/** Settings › Connections: every service key and setting is entered here; nothing needs editing in .env. */
export function ConnectionSettings() {
  const state = usePoll<Connections>((signal) => api("/connections", "GET", undefined, signal), 60000);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, TestResult>>({});
  const { pending, run } = useAction();
  const data = state.data && Array.isArray(state.data.fields) ? state.data : null;
  const fields = data?.fields ?? [];
  const byName = (name: string) => fields.find((f) => f.name === name);

  // A field is changed only when its draft differs from what is saved, so typing a value back is not a change.
  const same = (name: string, value: string) => {
    const f = byName(name);
    if (!f) return false;
    if (name === "llm_provider") return value === data?.provider;
    return f.kind === "secret" ? value === "" : value === shown(f);
  };
  const setField = (name: string, value: string) =>
    setDraft((d) => {
      const next = { ...d };
      if (same(name, value)) delete next[name];
      else next[name] = value;
      return next;
    });
  const changes = Object.keys(draft);
  const changed = changes.length > 0;
  const restartNeeded = changes.some((name) => byName(name)?.restart);

  const save = () =>
    run(
      "save",
      async () => {
        await api<Connections>("/connections", "PUT", { values: draft });
        setDraft({});
        await state.refresh();
      },
      restartNeeded ? "Saved. Worker settings apply after the app restarts." : "Settings saved.",
    );

  const test = (name: string) =>
    run(`test:${name}`, async () => {
      const result = await api<TestResult>(`/connections/${name}/test`, "POST");
      setResults((r) => ({ ...r, [name]: result }));
      return result;
    });

  if (state.error && !data)
    return (
      <Panel title="Connections">
        <Empty
          title="Could not load the connections"
          hint={state.error.message}
          action={
            <Button size="sm" onClick={state.refresh}>
              Try again
            </Button>
          }
        />
      </Panel>
    );
  if (!data)
    return (
      <Panel title="Connections">
        <Loading />
      </Panel>
    );

  const known = new Set(GROUPS.map((g) => g.id));
  const groups = [
    ...GROUPS,
    // A setting in a group this page does not know yet still gets a panel rather than vanishing.
    ...[...new Set(fields.map((f) => f.group).filter((g) => g && !known.has(g)))].map((g) => ({
      id: g,
      title: g,
      tag: "",
    })),
  ];
  const needsRestart = fields.some((f) => f.pending);

  return (
    <>
      {!data.model_ready && (
        <div className="ax-notice is-bad" role="alert">
          <span>
            Add a key for the selected AI model below. Until then the guide cannot read your resume and no application
            can run.
          </span>
        </div>
      )}
      {needsRestart && <RestartNotice message="New worker settings are saved. They take effect when the app restarts." />}
      {groups.map((group) => {
        const list = fields.filter((f) => f.group === group.id);
        if (group.id === "Email")
          return (
            <Panel key={group.id} title={group.title} actions={group.tag && <span className="ax-hint">{group.tag}</span>}>
              <GmailConnection />
            </Panel>
          );
        if (!list.length) return null;
        return (
          <Panel key={group.id} title={group.title} actions={group.tag && <span className="ax-hint">{group.tag}</span>}>
            <FormGrid>
              {list.map((f) => (
                <ConnField
                  key={f.name}
                  f={f}
                  provider={data.provider}
                  value={draft[f.name]}
                  onChange={(v) => setField(f.name, v)}
                  result={results[f.name]}
                  testing={pending === `test:${f.name}`}
                  busy={pending !== null}
                  onTest={() => test(f.name)}
                />
              ))}
            </FormGrid>
          </Panel>
        );
      })}
      <SaveBar
        dirty={changed}
        saving={pending === "save"}
        onSave={save}
        onDiscard={() => setDraft({})}
        label={changed ? `Save ${changes.length} change${changes.length > 1 ? "s" : ""}` : "Save"}
        note={restartNeeded ? "Unsaved changes. Worker settings apply after a restart." : undefined}
      />
    </>
  );
}

function ConnField({
  f,
  provider,
  value,
  onChange,
  result,
  testing,
  busy,
  onTest,
}: {
  f: ConnectionField;
  provider: string;
  value: string | undefined;
  onChange: (value: string) => void;
  result?: TestResult;
  testing: boolean;
  busy: boolean;
  onTest: () => void;
}) {
  const id = useId();
  const options = f.options ?? [];
  const dirty = value !== undefined;
  const pendingNote = f.pending ? (
    <strong>
      {" "}
      Running {f.kind === "bool" ? (f.value === "true" ? "on" : "off") : f.value || "unset"}; saved{" "}
      {f.kind === "bool" ? (f.pending === "true" ? "on" : "off") : f.pending}, used after a restart.
    </strong>
  ) : f.restart && dirty ? (
    <strong> Applies after a restart.</strong>
  ) : null;
  const outcome = result && (
    <span role="status">
      {" "}
      <Pill tone={result.ok ? "neutral" : "bad"} dot>
        {result.message}
      </Pill>
    </span>
  );
  const hint: ReactNode = (
    <>
      {f.help}
      {pendingNote}
      {outcome}
    </>
  );

  if (f.name === "llm_provider")
    return (
      <Select label={f.label} hint={f.help} value={value ?? provider} onChange={(e) => onChange(e.target.value)}>
        {PROVIDERS.map((p) => (
          <option key={p.id} value={p.id}>
            {p.label}
          </option>
        ))}
      </Select>
    );
  if (options.length)
    return (
      <Select label={f.label} hint={hint} value={value ?? shown(f)} onChange={(e) => onChange(e.target.value)}>
        {[...new Set([...options, shown(f)].filter(Boolean))].map((o) => (
          <option key={o} value={o}>
            {f.option_labels?.[o] || o}
          </option>
        ))}
      </Select>
    );
  if (f.kind === "bool")
    return (
      <Select
        label={f.label}
        hint={hint}
        value={value ?? (shown(f) === "true" ? "true" : "false")}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="true">On</option>
        <option value="false">Off</option>
      </Select>
    );
  if (f.kind === "secret") {
    // Saved keys are masked by the API ("····5d30") and cannot be revealed; the control either replaces them or sets
    // a first one. Show/Hide reveals only what is being typed.
    return (
      <div className="ax-field">
        <label className="ax-label" htmlFor={id}>
          {f.label} <Pill tone={f.set ? "ok" : "neutral"}>{f.set ? `Saved ${f.value || ""}`.trim() : "Not set"}</Pill>
        </label>
        <SecretInput
          id={id}
          label={f.label}
          describedBy={id + "-msg"}
          placeholder={f.set ? "Paste a new key to replace it" : "Paste here"}
          value={value ?? ""}
          onChange={onChange}
          extra={
            f.set ? (
              <Button
                size="sm"
                loading={testing}
                disabled={busy || dirty}
                title={dirty ? "Save first, then test" : undefined}
                onClick={onTest}
              >
                Test
              </Button>
            ) : null
          }
        />
        <p className="ax-hint" id={id + "-msg"}>
          {hint}
        </p>
      </div>
    );
  }
  return (
    <Input
      label={f.label}
      hint={hint}
      type={f.kind === "number" ? "number" : "text"}
      inputMode={f.kind === "number" ? "decimal" : undefined}
      step={f.kind === "number" ? "any" : undefined}
      autoComplete="off"
      spellCheck={false}
      placeholder="Not set"
      value={value ?? shown(f)}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

// Gmail inside Connections: its status, and the same app-password connection as Setup › Email.
function GmailConnection() {
  const { snapshot, go } = useWorkspace();
  const mail = usePoll<MailStatus>((signal) => api("/mail", "GET", undefined, signal), 60000);
  const [form, setForm] = useState({ email: "", app_password: "" });
  const { pending, run } = useAction();
  const boxes = mail.data?.mailboxes || [];
  const profileEmail = snapshot.profile?.email || "";
  const connect = () =>
    run(
      "gmail",
      async () => {
        await api("/mail/imap", "POST", { ...form, email: form.email || profileEmail });
        setForm({ email: "", app_password: "" });
        await mail.refresh();
      },
      "Gmail connected. Verification codes and receipts are read automatically.",
    );
  return (
    <div className="ax-stack">
      {mail.error && !mail.data && <ErrorLine error={mail.error} retry={mail.refresh} />}
      {boxes.length > 0 && (
        <ul className="ax-rows">
          {boxes.map((b) => (
            <li className="ax-row" key={b.id}>
              <span className="ax-row-main">
                <strong>{b.email}</strong>
                <small>
                  {b.provider === "gmail_imap" ? "App password" : b.provider === "gmail_oauth" ? "Google sign-in" : ""}
                </small>
              </span>
              <span className="ax-row-end">
                <Pill tone={b.state === "connected" ? "ok" : "warn"} dot>
                  {(b.state || "unknown").replaceAll("_", " ")}
                </Pill>
                <a
                  className="ax-btn is-sm"
                  href={routeUrl({ view: "settings", tab: "email" })}
                  onClick={(e) => {
                    e.preventDefault();
                    go({ view: "settings", tab: "email" });
                  }}
                >
                  Manage
                </a>
              </span>
            </li>
          ))}
        </ul>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          connect();
        }}
      >
        <FormGrid>
          <Input
            label={boxes.length ? "Connect another Gmail" : "Gmail address"}
            hint="Empty: the email in your profile."
            type="email"
            autoComplete="off"
            placeholder={profileEmail || "you@gmail.com"}
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
          />
          <GmailPassword value={form.app_password} onChange={(v) => setForm({ ...form, app_password: v })} />
        </FormGrid>
        <Actions>
          <Button
            type="submit"
            variant="primary"
            loading={pending === "gmail"}
            disabled={!form.app_password.trim() || (pending !== null && pending !== "gmail")}
          >
            Connect
          </Button>
        </Actions>
      </form>
    </div>
  );
}

function GmailPassword({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const id = useId();
  return (
    <div className="ax-field">
      <label className="ax-label" htmlFor={id}>
        Gmail app password
      </label>
      <SecretInput
        id={id}
        label="app password"
        describedBy={id + "-msg"}
        placeholder="xxxx xxxx xxxx xxxx"
        value={value}
        onChange={onChange}
      />
      <p className="ax-hint" id={id + "-msg"}>
        16 letters from <External href="https://myaccount.google.com/apppasswords">myaccount.google.com/apppasswords</External>{" "}
        (needs 2-Step Verification).
      </p>
    </div>
  );
}
