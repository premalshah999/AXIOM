import { useState } from "react";
import { api } from "./api";
import { useAction, usePoll } from "./hooks";
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
};

type Connections = { fields: ConnectionField[]; provider: string; model_ready: boolean };

const GROUPS = [
  { id: "Model", title: "AI model", note: "Required. Reads job pages and writes your answers. One key is enough." },
  { id: "Telegram", title: "Telegram", note: "Optional. Questions and alerts on your phone." },
  { id: "Captcha", title: "Captcha solving", note: "Optional. Solves puzzles some employer sites show." },
  { id: "Discovery", title: "Job search", note: "Optional. More job sources." },
];

const PROVIDERS = [
  { id: "deepseek", label: "DeepSeek (recommended, lowest cost)" },
  { id: "haiku", label: "Anthropic Claude Haiku" },
  { id: "mimo", label: "Xiaomi MiMo" },
  { id: "openai", label: "OpenAI GPT-6 Luna (cheapest setting)" },
];

/** Setup > Connections: every service key is entered here; nothing needs editing in .env. */
export function ConnectionSettings() {
  const state = usePoll<Connections>((signal) => api("/connections", "GET", undefined, signal), 60000);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [results, setResults] = useState<Record<string, { ok: boolean; message: string }>>({});
  const { pending, run } = useAction();
  const data = state.data;
  const changed = Object.keys(draft).length > 0;

  const save = () =>
    run(
      "save",
      async () => {
        const next = await api<Connections>("/connections", "PUT", { values: draft });
        setDraft({});
        await state.refresh();
        return next;
      },
      "Connections saved.",
    );

  const test = (name: string) =>
    run(`test:${name}`, async () => {
      const result = await api<{ ok: boolean; message: string }>(`/connections/${name}/test`, "POST");
      setResults((r) => ({ ...r, [name]: result }));
      return result;
    });

  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Connections</h2>
        <p className="muted">
          Paste each key once. Keys are encrypted on this computer and never shown again; use Test to check one.
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
              <div className="form-grid">
                {data.fields
                  .filter((f) => f.group === group.id)
                  .map((f) =>
                    f.name === "llm_provider" ? (
                      <Field key={f.name} label={f.label} hint={f.help}>
                        <select
                          value={draft.llm_provider ?? data.provider}
                          onChange={(e) => setDraft({ ...draft, llm_provider: e.target.value })}
                        >
                          {PROVIDERS.map((p) => (
                            <option key={p.id} value={p.id}>
                              {p.label}
                            </option>
                          ))}
                        </select>
                      </Field>
                    ) : (
                      <Field
                        key={f.name}
                        label={f.label}
                        hint={
                          <>
                            {f.help}{" "}
                            {f.set ? (
                              <strong>
                                Saved {f.secret ? f.value : `(${f.value})`}
                                {f.source === "env" ? " from the .env file" : ""}.
                              </strong>
                            ) : (
                              "Not set."
                            )}
                            {results[f.name] && (
                              <span className={results[f.name].ok ? "ok-text" : "error-text"}>
                                {" "}
                                {results[f.name].message}
                              </span>
                            )}
                          </>
                        }
                      >
                        <span className="input-row">
                          <input
                            type={f.secret ? "password" : "text"}
                            autoComplete="off"
                            spellCheck={false}
                            placeholder={f.set ? "Replace…" : "Paste here"}
                            value={draft[f.name] ?? ""}
                            onChange={(e) => setDraft({ ...draft, [f.name]: e.target.value })}
                          />
                          {f.secret && f.set && (
                            <button
                              type="button"
                              className="btn ghost sm"
                              disabled={pending !== null}
                              onClick={() => test(f.name)}
                            >
                              {pending === `test:${f.name}` ? "Testing…" : "Test"}
                            </button>
                          )}
                        </span>
                      </Field>
                    ),
                  )}
              </div>
            </div>
          ))}
        </>
      )}
      <div className="form-actions start">
        <button className="btn sm" disabled={!changed || pending !== null} onClick={save}>
          {pending === "save" ? "Saving…" : "Save connections"}
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
