// Settings › Connections: the services AXIOM talks to, one card each, with what is true about it (a key is saved, it works, it is in
// use). Keys are masked by the server and are never read back: a card replaces one, it cannot show one.
import { useEffect, useId, useState, type ReactNode } from "react";
import { KeyRound, ListChecks, Mail, Search, Send, ShieldCheck, type LucideIcon } from "lucide-react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, usePoll } from "./hooks";
import { mailHealth } from "./mail";
import { RestartNotice, SaveBar, SecretInput, SwitchRow } from "./setup-kit";
import type { MailStatus } from "./types";
import "./styles/settings.css";
import { Button, Check, ConfirmButton, Empty, Input, Monogram, Pill, Select } from "./ui/index";

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
type TestResult = { ok: boolean; message: string };

/** The value a field shows when nothing is typed: what the next start will use. */
const shown = (f: ConnectionField) => (f.pending || f.value || "") as string;

/**
 * One form over /api/connections. Several parts of Settings edit different fields of it (the cards here, the limits under
 * Applying, the workers under Advanced), each with its own copy of this and its own save bar. A field counts as changed only
 * when its draft differs from what is saved, so typing a value back is not a change.
 */
export function useConnections() {
  // Read again whenever the workspace refreshes, so a value changed on another screen (the dashboard's daily limit) shows here
  // without a reload.
  const { version, refresh: refreshWorkspace } = useWorkspace();
  const state = usePoll<Connections>((signal) => api("/connections", "GET", undefined, signal), 60000, [version]);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, TestResult>>({});
  // Bumped when a save or discard settles, so a card that was open for typing closes.
  const [epoch, setEpoch] = useState(0);
  const { pending, run } = useAction();
  const data = state.data && Array.isArray(state.data.fields) ? state.data : null;
  const fields = data?.fields ?? [];
  const by = (name: string) => fields.find((f) => f.name === name);
  const same = (name: string, value: string) => {
    const f = by(name);
    if (!f) return false;
    if (name === "llm_provider") return value === data?.provider;
    return f.kind === "secret" ? value === "" : value === shown(f);
  };
  const set = (name: string, value: string) =>
    setDraft((d) => {
      const next = { ...d };
      if (same(name, value)) delete next[name];
      else next[name] = value;
      return next;
    });
  // The daily limit is one value shared with the dashboard: when it changes there, an unsaved edit here is out of date.
  const limitField = by("daily_application_limit");
  const savedLimit = limitField ? shown(limitField) : "";
  useEffect(() => {
    setDraft((d) => {
      if (!("daily_application_limit" in d)) return d;
      const next = { ...d };
      delete next.daily_application_limit;
      return next;
    });
  }, [savedLimit]);
  const changes = Object.keys(draft);
  const restartNeeded = changes.some((name) => by(name)?.restart);
  return {
    state,
    data,
    fields,
    by,
    draft,
    set,
    results,
    pending,
    epoch,
    changes,
    restartNeeded,
    value: (name: string) => draft[name] ?? (by(name) ? shown(by(name)!) : ""),
    discard: () => {
      setDraft({});
      setEpoch((e) => e + 1);
    },
    save: () =>
      run(
        "save",
        async () => {
          const sent = draft;
          await api<Connections>("/connections", "PUT", { values: sent });
          // What was typed while this save was on its way is still a change.
          setDraft((d) => Object.fromEntries(Object.entries(d).filter(([k, v]) => sent[k] !== v)));
          // A "Works" from before this save may be about a key that is gone.
          setResults({});
          setEpoch((e) => e + 1);
          await state.refresh();
          await refreshWorkspace(); // the dashboard reads the daily limit from the workspace snapshot
        },
        restartNeeded ? "Saved. Worker settings apply after the app restarts." : "Settings saved.",
      ),
    /** Take a saved value away now: a blank value removes it on the server. Typing a new one is Replace. */
    remove: (name: string) =>
      run(
        `remove:${name}`,
        async () => {
          await api<Connections>("/connections", "PUT", { values: { [name]: "" } });
          setResults((r) => Object.fromEntries(Object.entries(r).filter(([k]) => k !== name)));
          await state.refresh();
          await refreshWorkspace();
        },
        "Removed.",
      ),
    test: (name: string) =>
      run(`test:${name}`, async () => {
        const result = await api<TestResult>(`/connections/${name}/test`, "POST");
        setResults((r) => ({ ...r, [name]: result }));
        return result;
      }),
  };
}
export type Conn = ReturnType<typeof useConnections>;

/** The sticky Save for any form built on useConnections. */
function ConnectionBar({ c }: { c: Conn }) {
  const n = c.changes.length;
  return (
    <SaveBar
      dirty={n > 0}
      saving={c.pending === "save"}
      onSave={c.save}
      onDiscard={c.discard}
      label={n > 1 ? `Save ${n} changes` : "Save"}
      note={c.restartNeeded ? "Unsaved changes. Worker settings apply after a restart." : undefined}
    />
  );
}

/** What a failed load or a first load looks like, in the shape of the page it replaces. */
export function ConnectionsGate({ c, children }: { c: Conn; children: (data: Connections) => ReactNode }) {
  if (c.state.error && !c.data)
    return (
      <Empty
        title="Could not load the connections"
        hint={c.state.error.message}
        action={
          <Button size="sm" onClick={c.state.refresh}>
            Try again
          </Button>
        }
      />
    );
  if (!c.data)
    return (
      <div className="st-cards" role="status" aria-label="Loading">
        {[0, 1, 2, 3].map((i) => (
          <div className="st-card" key={i}>
            <span className="ax-skeleton" style={{ width: "42%" }} />
            <span className="ax-skeleton" style={{ width: "78%" }} />
            <span className="ax-skeleton" style={{ height: 34 }} />
          </div>
        ))}
      </div>
    );
  return <>{children(c.data)}</>;
}

// ---------- Field editors ----------

/** A saved secret: the masked value, Test, and Replace. Typing a new one never shows the old. */
function SecretField({ c, name, lead, label }: { c: Conn; name: string; lead?: ReactNode; label?: string }) {
  const f = c.by(name);
  const id = useId();
  const [editing, setEditing] = useState(false);
  useEffect(() => setEditing(false), [c.epoch]);
  if (!f) return null;
  const typed = c.draft[name];
  const open = editing || typed !== undefined;
  const result = c.results[name];
  const cancel = () => {
    c.set(name, "");
    setEditing(false);
  };
  return (
    <>
      {open ? (
        <div className="ax-field">
          <label className="ax-label" htmlFor={id}>
            {label ?? f.label}
          </label>
          <SecretInput
            id={id}
            label={f.label}
            placeholder={f.set ? "Paste the new key" : "Paste the key"}
            value={typed ?? ""}
            onChange={(v) => c.set(name, v)}
            extra={
              <Button size="sm" variant="ghost" onClick={cancel}>
                Cancel
              </Button>
            }
          />
          {f.restart && <p className="ax-hint">Applies after a restart.</p>}
        </div>
      ) : f.set ? (
        <div className="st-key" aria-label={`${f.label}, saved`}>
          <span>{f.value}</span>
          <small>{f.source === "env" ? "From the .env file" : "Saved in AXIOM"}</small>
        </div>
      ) : (
        <div className="st-key is-empty">No key saved</div>
      )}
      <div className="st-card-foot">
        {lead}
        {!open && f.set && (
          <Button size="sm" loading={c.pending === `test:${name}`} disabled={c.pending !== null && c.pending !== `test:${name}`} onClick={() => c.test(name)}>
            Test
          </Button>
        )}
        {!open && (
          <Button size="sm" variant={f.set ? "ghost" : "secondary"} onClick={() => setEditing(true)}>
            {f.set ? "Replace key" : "Add key"}
          </Button>
        )}
        {!open && f.set && f.source === "app" && (
          <ConfirmButton
            label="Remove"
            confirm="Remove"
            size="sm"
            variant="secondary"
            pending={c.pending === `remove:${name}`}
            onConfirm={() => c.remove(name)}
          />
        )}
        {result && !open && (
          <span className={"st-result" + (result.ok ? "" : " is-bad")} role="status">
            <Pill tone={result.ok ? "ok" : "bad"} dot>
              {result.ok ? "Works" : "Failed"}
            </Pill>
            <span title={result.message}>{result.message}</span>
          </span>
        )}
      </div>
    </>
  );
}

/** Any other field, by its own type. `hint` replaces the server's help line. */
export function FieldInput({ c, name, hint, label }: { c: Conn; name: string; hint?: ReactNode; label?: string }) {
  const f = c.by(name);
  if (!f) return null;
  const value = c.draft[name] ?? shown(f);
  const dirty = c.draft[name] !== undefined;
  const text = f.kind === "bool" ? (v: string) => (v === "true" ? "on" : "off") : (v: string) => v || "unset";
  const note = f.pending ? (
    <>
      {hint ?? f.help} <strong>Running {text(f.value)}; saved {text(f.pending)}, used after a restart.</strong>
    </>
  ) : f.restart && dirty ? (
    <>
      {hint ?? f.help} <strong>Applies after a restart.</strong>
    </>
  ) : (
    (hint ?? f.help)
  );
  if (f.options?.length)
    return (
      <Select label={label ?? f.label} hint={note} value={value} onChange={(e) => c.set(name, e.target.value)}>
        {[...new Set([...f.options, shown(f)].filter(Boolean))].map((o) => (
          <option key={o} value={o}>
            {f.option_labels?.[o] || o}
          </option>
        ))}
      </Select>
    );
  if (f.kind === "bool")
    return <Check checked={value === "true"} onChange={(v) => c.set(name, v ? "true" : "false")} label={label ?? f.label} note={note} />;
  return (
    <Input
      label={label ?? f.label}
      hint={note}
      type={f.kind === "number" ? "number" : "text"}
      inputMode={f.kind === "number" ? "decimal" : undefined}
      step={f.kind === "number" ? "any" : undefined}
      autoComplete="off"
      spellCheck={false}
      placeholder="Not set"
      value={value}
      onChange={(e) => c.set(name, e.target.value)}
    />
  );
}

/** Fields that belong to Settings but not to a card (the limits, the workers): a plain grid under one save bar. */
export function ConnectionFields({
  names,
  hints,
  restart,
}: {
  names: string[];
  hints?: Record<string, ReactNode>;
  restart?: boolean;
}) {
  const c = useConnections();
  return (
    <ConnectionsGate c={c}>
      {() => (
        <>
          {restart && c.fields.some((f) => f.pending) && (
            <RestartNotice message="New worker settings are saved. They take effect when the app restarts." />
          )}
          <div className="st-fields">
            {names.map((n) => (
              <FieldInput key={n} c={c} name={n} hint={hints?.[n]} />
            ))}
          </div>
          <ConnectionBar c={c} />
        </>
      )}
    </ConnectionsGate>
  );
}

// ---------- Cards ----------

export function Card({
  icon,
  mono,
  name,
  sub,
  state,
  current,
  wide,
  children,
}: {
  icon?: LucideIcon;
  mono?: string;
  name: string;
  sub: ReactNode;
  state?: ReactNode;
  current?: boolean;
  wide?: boolean;
  children?: ReactNode;
}) {
  const Icon = icon;
  return (
    <article className={"st-card" + (current ? " is-current" : "") + (wide ? " is-wide" : "")} aria-label={name}>
      <header className="st-card-head">
        {mono ? (
          <Monogram name={mono} size="md" />
        ) : (
          Icon && (
            <span className="st-ico">
              <Icon size={16} aria-hidden="true" />
            </span>
          )
        )}
        <div className="st-card-title">
          <h3>{name}</h3>
          <p>{sub}</p>
        </div>
        {state && <div className="st-card-state">{state}</div>}
      </header>
      {children}
    </article>
  );
}

// Which company runs OpenRouter's model, and what to do when it is busy. Read by the AI model card's owner, set under Advanced.
const OPENROUTER = ["openrouter_provider", "openrouter_model", "openrouter_fallback_hosts"];

const MODELS = [
  { id: "deepseek", key: "deepseek_api_key", name: "DeepSeek", sub: "Recommended: the lowest cost." },
  {
    id: "openrouter",
    key: "openrouter_api_key",
    name: "OpenRouter",
    sub: "DeepSeek V4.1 Flash. Choose which company runs it under Advanced.",
  },
  { id: "haiku", key: "haiku_api_key", name: "Anthropic Claude", sub: "Claude Haiku." },
  { id: "openai", key: "openai_api_key", name: "OpenAI", sub: "GPT-6 Luna at its cheapest setting." },
  { id: "mimo", key: "mimo_api_key", name: "Xiaomi MiMo", sub: "An optional third provider." },
];

function ModelCard({ c, m, current }: { c: Conn; m: (typeof MODELS)[number]; current: string }) {
  const f = c.by(m.key);
  const picked = current === m.id;
  const unsaved = picked && c.data?.provider !== m.id;
  const state = picked ? (
    f?.set ? (
      <Pill tone="ok" dot>
        {unsaved ? "Selected" : "In use"}
      </Pill>
    ) : (
      <Pill tone="warn" dot>
        Needs a key
      </Pill>
    )
  ) : (
    <Pill tone="neutral" dot>
      {f?.set ? "Key saved" : "No key"}
    </Pill>
  );
  return (
    <Card name={m.name} mono={m.name} sub={m.sub} state={state} current={picked}>
      <SecretField
        c={c}
        name={m.key}
        lead={
          !picked && (
            <Button
              size="sm"
              disabled={!f?.set}
              title={f?.set ? undefined : "Add a key first"}
              onClick={() => c.set("llm_provider", m.id)}
            >
              Use this one
            </Button>
          )
        }
      />
    </Card>
  );
}

type Service = {
  id: string;
  name: string;
  icon: LucideIcon;
  sub: string;
  key: string;
  fields?: string[];
  more?: string[];
  toggle?: { name: string; label: string; note: string };
};

const SERVICES: Service[] = [
  {
    id: "telegram",
    name: "Telegram",
    icon: Send,
    sub: "Optional. Questions and alerts reach your phone; an answer sent there resumes the waiting job.",
    key: "telegram_bot_token",
    fields: ["telegram_user_id"],
    toggle: { name: "telegram_enabled", label: "Send messages", note: "Questions and alerts go to Telegram." },
  },
  {
    id: "captcha",
    name: "2Captcha",
    icon: ShieldCheck,
    sub: "Optional, paid per solve. Solves the captchas that stop an application.",
    key: "twocaptcha_api_key",
    more: ["captcha_image_rounds", "captcha_timeout"],
  },
  {
    id: "serper",
    name: "Serper",
    icon: Search,
    sub: "Optional. Adds Google results when AXIOM searches for new employers.",
    key: "serper_api_key",
  },
  {
    id: "typesafe",
    name: "TypeSafe",
    icon: ListChecks,
    sub: "Optional. Answers multiple-choice questions before the AI model does.",
    key: "typesafe_api_key",
  },
  {
    id: "employer",
    name: "Employer sign-in",
    icon: KeyRound,
    sub: "The email and shared password AXIOM uses when it creates an employer account.",
    key: "application_password",
    fields: ["account_email"],
  },
];

function ServiceCard({ c, s }: { c: Conn; s: Service }) {
  const f = c.by(s.key);
  if (!f) return null;
  const on = s.toggle ? c.value(s.toggle.name) === "true" : true;
  // A bot with no one to message reads "On" otherwise, and nothing ever arrives.
  const noOne = s.id === "telegram" && f.set && !c.value("telegram_user_id");
  const state = noOne ? (
    <Pill tone="warn" dot>
      Needs your user ID
    </Pill>
  ) : f.set ? (
    <Pill tone={on ? "ok" : "neutral"} dot>
      {on ? "On" : "Off"}
    </Pill>
  ) : (
    <Pill tone="neutral" dot>
      Not set
    </Pill>
  );
  return (
    <Card name={s.name} icon={s.icon} sub={s.sub} state={state}>
      {s.fields?.map((n) => (
        <FieldInput
          key={n}
          c={c}
          name={n}
          hint={n === "account_email" ? "Empty: the email on your profile." : n === "telegram_user_id" ? "A number. Message @userinfobot in Telegram and it replies with yours." : undefined}
        />
      ))}
      <SecretField c={c} name={s.key} />
      {s.toggle && c.by(s.toggle.name) && (
        <SwitchRow
          label={s.toggle.label}
          note={s.toggle.note}
          checked={on}
          onChange={() => c.set(s.toggle!.name, on ? "false" : "true")}
        />
      )}
      {s.more && (
        <details className="ax-fold st-more">
          <summary>
            <strong>Options</strong>
          </summary>
          <div className="st-fields">
            {s.more.map((n) => (
              <FieldInput key={n} c={c} name={n} />
            ))}
          </div>
        </details>
      )}
    </Card>
  );
}

/** Gmail's card tells the mailbox's real state; the mailbox itself is managed under Mailbox. */
function GmailCard() {
  const { go } = useWorkspace();
  const mail = usePoll<MailStatus>((signal) => api("/mail", "GET", undefined, signal), 60000);
  const boxes = mail.data?.mailboxes ?? [];
  const health = boxes.length ? mailHealth(boxes[0]) : null;
  const worst = boxes.map(mailHealth).find((h) => h.tone !== "ok") ?? health;
  return (
    <Card
      name="Gmail"
      icon={Mail}
      sub="Required. Reads the verification codes and receipts employers send."
      state={
        !mail.data ? null : worst ? (
          <Pill tone={worst.tone} dot>
            {worst.label}
          </Pill>
        ) : (
          <Pill tone="neutral" dot>
            Not connected
          </Pill>
        )
      }
    >
      {mail.error && !mail.data ? (
        <div className="st-key is-empty">Could not read the mailbox</div>
      ) : boxes.length ? (
        boxes.map((b) => {
          const h = mailHealth(b);
          return (
            <div className="st-key" key={b.id}>
              <span>{b.email}</span>
              <small>{h.when ? `${h.tone === "ok" ? "Checked" : "Worked"} ${h.when}` : ""}</small>
            </div>
          );
        })
      ) : (
        <div className="st-key is-empty">{mail.data ? "No mailbox connected" : "Loading…"}</div>
      )}
      {worst && worst.tone !== "ok" && (
        <div className="st-state">
          <i className="ax-lamp is-ask" aria-hidden="true" />
          <div>
            <strong>{worst.detail}</strong>
          </div>
        </div>
      )}
      <div className="st-card-foot">
        <Button size="sm" variant={boxes.length ? "secondary" : "primary"} onClick={() => go({ view: "settings", tab: "email" })}>
          {boxes.length ? "Manage mailbox" : "Connect Gmail"}
        </Button>
      </div>
    </Card>
  );
}

/** Settings › Connections. */
export function ConnectionSettings() {
  const c = useConnections();
  return (
    <ConnectionsGate c={c}>
      {(data) => {
        const claimed = new Set([
          "llm_provider",
          ...MODELS.map((m) => m.key),
          ...OPENROUTER,
          ...SERVICES.flatMap((s) => [s.key, ...(s.fields ?? []), ...(s.more ?? []), ...(s.toggle ? [s.toggle.name] : [])]),
        ]);
        // Limits, workers and logos are edited under Applying and Advanced; anything else the server adds still gets a field here.
        const rest = c.fields.filter((f) => !claimed.has(f.name) && f.group !== "Limits" && f.group !== "Workers" && f.group !== "Appearance");
        const current = c.draft.llm_provider ?? data.provider;
        return (
          <>
            {!data.model_ready && (
              <div className="ax-notice is-bad" role="alert">
                <span>
                  Add a key for the AI model in use. Until then AXIOM cannot read your resume and no application can run.
                </span>
              </div>
            )}
            <section className="st-group" aria-labelledby="st-ai">
              <div className="st-head">
                <div>
                  <h2 id="st-ai">AI model</h2>
                  <p>Reads job pages and writes your answers. One is in use at a time; the others stay ready as a backup.</p>
                </div>
              </div>
              <div className="st-cards">
                {MODELS.map((m) => (
                  <ModelCard key={m.id} c={c} m={m} current={current} />
                ))}
              </div>
            </section>
            <section className="st-group" aria-labelledby="st-services">
              <div className="st-head">
                <div>
                  <h2 id="st-services">Services</h2>
                  <p>What AXIOM uses beside the AI model.</p>
                </div>
              </div>
              <div className="st-cards">
                <GmailCard />
                {SERVICES.map((s) => (
                  <ServiceCard key={s.id} c={c} s={s} />
                ))}
              </div>
            </section>
            <details className="ax-fold st-adv">
              <summary>
                <strong>Advanced</strong>
                <span>OpenRouter hosting{rest.length ? " and a screenshot model" : ""}</span>
              </summary>
              <div className="st-adv-body">
                <div>
                  <h3>OpenRouter hosting</h3>
                  <p className="ax-hint">Which company runs the model for OpenRouter, and what happens when it is busy. Prices are per million tokens.</p>
                </div>
                <div className="st-fields">
                  {OPENROUTER.map((n) => (
                    <FieldInput key={n} c={c} name={n} />
                  ))}
                </div>
                {rest.length > 0 && (
                  <>
                    <div>
                      <h3>Screenshot model</h3>
                      <p className="ax-hint">Only needed if your AI model cannot read images.</p>
                    </div>
                    <div className="st-fields">
                      {rest.map((f) =>
                        f.kind === "secret" ? (
                          <div className="ax-field" key={f.name}>
                            <span className="ax-label">{f.label}</span>
                            <SecretField c={c} name={f.name} />
                          </div>
                        ) : (
                          <FieldInput key={f.name} c={c} name={f.name} />
                        ),
                      )}
                    </div>
                  </>
                )}
              </div>
            </details>
            <ConnectionBar c={c} />
          </>
        );
      }}
    </ConnectionsGate>
  );
}
