// Where AXIOM keeps its data: this computer (the default), Supabase, or a PostgreSQL server of your own. Self-contained: it
// talks to /api/database* only and uses the shared primitives (ui/index, setup-kit, hooks), so a page mounts it with
// <DatabaseSettings /> (a section body: an untitled Panel, the section brings the heading) or <DatabaseChoice /> (the setup
// guide's data step: a sentence until someone asks to change it, so Continue never waits on it).
import { useState } from "react";
import "./styles/database.css";
import { api } from "./api";
import { useAction, usePoll } from "./hooks";
import { Actions, RestartNotice, SecretInput } from "./setup-kit";
import { Button, Check, ConfirmButton, ErrorLine, External, Loading, Panel, Pill, Segmented, type PillTone } from "./ui/index";

type Mode = "local" | "supabase" | "server";
type Remote = Exclude<Mode, "local">;
type Probe = { id: string; label: string; status: "ok" | "warn" | "fail" | "skip"; detail: string };
type TestResult = {
  ok: boolean;
  checks: Probe[];
  workers: { workers: number; text: string } | null;
  facts: { existing?: { has_data: boolean; counts: Record<string, number> } };
};
type Move = {
  running?: boolean;
  status?: "starting" | "running" | "done" | "failed";
  phase?: string;
  message?: string;
  to?: Mode;
  tables?: Record<string, { source: number | null; target: number }>;
};
type State = {
  mode: Mode;
  chosen: Mode;
  restart_needed: boolean;
  source: "env" | "app" | "default";
  previous: Mode | "";
  options: Record<Mode, { configured: boolean; target: string }>;
  ping?: { median_ms: number; max_ms: number };
  workers?: { workers: number; text: string };
  configured_workers: number;
  running: boolean;
  move: Move;
};

const NAMES: Record<Mode, string> = { local: "This computer", supabase: "Supabase", server: "Your server" };
const SENTENCE: Record<Mode, string> = {
  local: "Your data is kept on this computer.",
  supabase: "Your data is kept on Supabase.",
  server: "Your data is kept on your own PostgreSQL server.",
};
const TONE: Record<Probe["status"], PillTone> = { ok: "ok", warn: "warn", fail: "bad", skip: "neutral" };
const WORD: Record<Probe["status"], string> = { ok: "Passed", warn: "Note", fail: "Failed", skip: "Skipped" };
const WHERE: Record<Remote, { hint: string; link?: { href: string; text: string } }> = {
  supabase: {
    hint: "In your Supabase project press Connect and copy the Session pooler string (port 5432), or the Direct connection. The transaction pooler (port 6543) does not work.",
    link: { href: "https://supabase.com/dashboard", text: "Open Supabase" },
  },
  server: { hint: "A PostgreSQL 14 or newer connection string: postgresql://user:password@host:5432/database" },
};

/** The whole screen, for Settings. */
export function DatabaseSettings() {
  const state = usePoll<State>((signal) => api("/database", "GET", undefined, signal), 15000);
  const moving = !!state.data?.running || state.data?.move?.status === "running";
  // While a move runs the screen follows it closely; otherwise a slow poll keeps the speed line honest.
  const live = usePoll<Move>((signal) => api("/database/switch", "GET", undefined, signal), 1500, [], moving);
  if (state.error && !state.data) return <ErrorLine error={state.error} retry={state.refresh} />;
  if (!state.data) return <Loading label="Reading the database settings" />;
  const s = state.data;
  const move = moving && live.data ? live.data : s.move;
  return (
    <Panel>
      <p>
        {SENTENCE[s.mode]}
        {s.mode !== "local" && s.options[s.mode].target && <small> {s.options[s.mode].target}</small>}
      </p>
      {s.mode !== "local" && s.ping && (
        <p className="ax-hint">
          A round trip to it takes {s.ping.median_ms.toFixed(0)} ms. {s.workers?.text}
          {s.workers && s.configured_workers > s.workers.workers && ` You run ${s.configured_workers} workers.`}
        </p>
      )}
      {s.restart_needed && <RestartNotice message={`AXIOM will use ${NAMES[s.chosen].toLowerCase()} once it restarts.`} />}
      {move.status && move.status !== "starting" && <MoveProgress move={move} />}
      {!moving && <Change state={s} onDone={state.refresh} />}
    </Panel>
  );
}

/** The setup guide's question. Local is chosen already; nothing here needs answering. */
export function DatabaseChoice() {
  const [open, setOpen] = useState(false);
  const state = usePoll<State>((signal) => api("/database", "GET", undefined, signal), 30000);
  if (!state.data) return null;
  const s = state.data;
  return (
    <div className="ax-hint">
      <p>
        {SENTENCE[s.mode]}{" "}
        {!open && (
          <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
            Change
          </Button>
        )}
      </p>
      {open && <Change state={s} onDone={state.refresh} fresh />}
    </div>
  );
}

function Change({ state, onDone, fresh }: { state: State; onDone: () => void; fresh?: boolean }) {
  const { pending, run } = useAction();
  const [mode, setMode] = useState<Mode>(state.mode);
  const [url, setUrl] = useState("");
  const [result, setResult] = useState<TestResult | null>(null);
  const [bring, setBring] = useState(!fresh);
  const [replace, setReplace] = useState(false);
  const other = mode !== state.mode;
  const saved = mode !== "local" && state.options[mode].configured;
  const existing = result?.facts.existing;
  const pick = (m: Mode) => {
    setMode(m);
    setResult(null);
    setUrl("");
    setReplace(false);
  };
  const test = () => run("test", async () => setResult(await api<TestResult>("/database/test", "POST", { mode, url })));
  const start = () =>
    run(
      "switch",
      async () => {
        await api("/database/switch", "POST", { mode, url, with_data: bring, replace });
        setUrl("");
        onDone();
      },
      "Switching. New applications are held until it finishes.",
    );
  return (
    <>
      <Segmented
        label="Database"
        value={mode}
        onChange={pick}
        options={(["local", "supabase", "server"] as Mode[]).map((m) => ({ value: m, label: NAMES[m] }))}
      />
      {other && mode !== "local" && (
        <>
          <p className="ax-hint">
            {WHERE[mode].hint} {WHERE[mode].link && <External href={WHERE[mode].link.href}>{WHERE[mode].link.text}</External>}
          </p>
          <SecretInput
            label="connection string"
            value={url}
            onChange={(v) => {
              setUrl(v);
              setResult(null);
            }}
            placeholder={saved ? `Saved: ${state.options[mode].target}. Paste a new one to replace it.` : "postgresql://…"}
          />
        </>
      )}
      {other && mode === "local" && <p className="ax-hint">Switching back copies what changed since, if you ask it to.</p>}
      {other && (
        <>
          <Actions start>
            {mode !== "local" && (
              <Button loading={pending === "test"} disabled={!url && !saved} onClick={test}>
                Test connection
              </Button>
            )}
          </Actions>
          {result && <TestChecks result={result} />}
          {(result?.ok || mode === "local") && (
            <>
              <Check
                checked={bring}
                onChange={setBring}
                label="Bring my data along"
                note={
                  fresh
                    ? "Nothing is here yet; this only matters if you already have jobs."
                    : "Copies everything, checks every table and keeps the old database untouched. New applications are held while it runs."
                }
              />
              {bring && existing?.has_data && (
                <Check
                  checked={replace}
                  onChange={setReplace}
                  label="Replace the data already there"
                  note={`That database already holds ${existing.counts.jobs ?? 0} jobs and ${existing.counts.runs ?? 0} runs. Replacing overwrites them with the data from here.`}
                />
              )}
              <Actions start note={!bring ? "The other database is used as it is. Nothing is copied." : undefined}>
                <ConfirmButton
                  variant="secondary"
                  size="md"
                  label={`Switch to ${NAMES[mode].toLowerCase()}`}
                  confirm={`Yes, switch`}
                  note="Waits for running applications to finish, then restarts."
                  pending={pending === "switch"}
                  disabled={bring && !!existing?.has_data && !replace}
                  onConfirm={start}
                />
              </Actions>
            </>
          )}
        </>
      )}
      {state.previous && !other && state.mode !== "local" && (
        <p className="ax-hint">Before this it was {NAMES[state.previous].toLowerCase()}, and that copy is still there.</p>
      )}
    </>
  );
}

function TestChecks({ result }: { result: TestResult }) {
  return (
    <ul className="ax-dbchecks" aria-label="Connection test">
      {result.checks.map((c) => (
        <li key={c.id}>
          <Pill tone={TONE[c.status]}>{WORD[c.status]}</Pill> <strong>{c.label}.</strong> {c.detail}
        </li>
      ))}
      {result.ok && result.workers && (
        <li>
          <Pill tone="info">Speed</Pill> {result.workers.text}
        </li>
      )}
    </ul>
  );
}

function MoveProgress({ move }: { move: Move }) {
  const tables = Object.entries(move.tables || {}).filter(([, t]) => (t.source ?? 0) > 0);
  const rows = tables.reduce((n, [, t]) => n + t.target, 0);
  const all = tables.reduce((n, [, t]) => n + (t.source ?? 0), 0);
  const failed = move.status === "failed";
  return (
    <div className={"ax-notice" + (failed ? " is-bad" : "")} role="status">
      <strong>{failed ? "Not switched." : move.status === "done" ? "Done." : "Switching…"}</strong>{" "}
      {failed || move.status === "done" ? move.message : move.phase}
      {move.status === "running" && all > 0 && <> {rows.toLocaleString()} of {all.toLocaleString()} rows copied.</>}
      {failed && <> Your data is where it was.</>}
    </div>
  );
}
