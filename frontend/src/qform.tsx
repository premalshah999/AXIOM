// Voluntary questionnaires (tax-credit screening, supervisors and references, frequently asked questions).
// Shared by Setup › Knowledge and the setup guide's optional step. Every answer is optional and saves on its own;
// nothing here is required to apply. API: GET/PUT /api/knowledge/questionnaires (jobpilot/features/knowledge).
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Check, Plus, Trash2 } from "lucide-react";
import { api, ApiError } from "./api";
import { Button, Input, Pill } from "./ui/index";
import "./styles/qform.css";

// ---------- types (mirror jobpilot/features/knowledge/questionnaires.py) ----------
type Field = { key: string; prompt: string; type: string; options: string[] };
type WorkRow = {
  row_id: string;
  index: number;
  company: string;
  title: string;
  start: string;
  end: string;
  current: boolean;
  answer: Record<string, string>;
};
export type Question = {
  key: string;
  prompt: string;
  type: "yes_no" | "choice" | "text" | "date" | "work_rows" | "references";
  options: string[];
  help: string;
  suggestion: unknown;
  section: string;
  answer?: unknown;
  answered: boolean;
  fields?: Field[];
  rows?: WorkRow[];
  max?: number;
  option_labels?: Record<string, Record<string, string>>;
};
export type Module = {
  id: string;
  title: string;
  purpose: string;
  optional: boolean;
  answered: number;
  total: number;
  percent: number;
  status: "not_started" | "partial" | "complete";
  questions: Question[];
};
export type Questionnaires = { modules: Module[]; completion: { answered: number; total: number; percent: number } };

/** Later answers win; a supervisor card sent twice keeps the fields of both. */
function mergeAnswers(before: Record<string, unknown> | undefined, next: Record<string, unknown>) {
  const out: Record<string, unknown> = { ...(before || {}) };
  for (const [key, value] of Object.entries(next)) {
    const old = out[key];
    const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
    out[key] = object(old) && object(value) ? { ...old, ...value } : value;
  }
  return out;
}

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const fail = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : "Could not save");

// ---------- data ----------
export function useQuestionnaires() {
  const [data, setData] = useState<Questionnaires | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const load = useCallback(() => {
    setError(null);
    api<Questionnaires>("/knowledge/questionnaires")
      .then(setData)
      .catch((e) => setError(e as Error));
  }, []);
  useEffect(load, [load]);
  // A save returns the module's fresh view: swap it in and recompute the totals.
  const setModule = useCallback((module: Module) => {
    setData((d) => {
      if (!d) return d;
      const modules = d.modules.map((m) => (m.id === module.id ? module : m));
      const answered = modules.reduce((n, m) => n + m.answered, 0);
      const total = modules.reduce((n, m) => n + m.total, 0);
      return { modules, completion: { answered, total, percent: total ? Math.round((100 * answered) / total) : 0 } };
    });
  }, []);
  return { data, error, reload: load, setModule };
}

/** The facts a module's saved answers amount to, so a caller holding a profile copy can stay current. */
export function moduleFacts(module: Module): Record<string, unknown> {
  const facts: Record<string, unknown> = {};
  for (const q of module.questions) {
    if (q.type === "work_rows") {
      const saved: Record<string, unknown> = {};
      for (const r of q.rows || []) if (r.answer && Object.keys(r.answer).length) saved[r.row_id] = r.answer;
      facts[q.key] = Object.keys(saved).length ? saved : undefined;
    } else if (q.type === "references") facts[q.key] = (q.answer as unknown[])?.length ? q.answer : undefined;
    else facts[q.key] = q.answer === null || q.answer === "" ? undefined : q.answer;
  }
  if (module.id === "wotc")
    facts.tax_credit_screening_no = module.questions.every((q) => q.answer === false) ? true : undefined;
  return facts;
}

// ---------- controls ----------
/** Two or three buttons that act as one radio group. Arrow keys move the choice. */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  block,
}: {
  value: T | "" | undefined;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
  block?: boolean;
}) {
  const move = (e: React.KeyboardEvent, i: number) => {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = options[(i + step + options.length) % options.length];
    onChange(next.value);
    (e.currentTarget.parentElement?.children[(i + step + options.length) % options.length] as HTMLElement)?.focus();
  };
  const tab = options.findIndex((o) => o.value === value);
  return (
    <div className={"ax-seg" + (block ? " is-block" : "")} role="radiogroup" aria-label={label}>
      {options.map((o, i) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          tabIndex={tab === i || (tab < 0 && i === 0) ? 0 : -1}
          onClick={() => onChange(o.value)}
          onKeyDown={(e) => move(e, i)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function YesNo({
  value,
  onChange,
  label,
}: {
  value: boolean | undefined | null;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <Segmented
      label={label}
      value={value === true ? "yes" : value === false ? "no" : ""}
      options={[
        { value: "yes", label: "Yes" },
        { value: "no", label: "No" },
      ]}
      onChange={(v) => onChange(v === "yes")}
    />
  );
}

type Status = { state: "idle" | "saving" | "saved" | "error"; message?: string };
export function SaveState({ status, retry }: { status: Status; retry?: () => void }) {
  return (
    <span className={"ax-save is-" + status.state} role="status" aria-live="polite">
      {status.state === "saving" && "Saving…"}
      {status.state === "saved" && (
        <>
          <Check size={13} aria-hidden="true" /> Saved
        </>
      )}
      {status.state === "error" && (
        <>
          <span className="ax-save-msg">{status.message || "Could not save."}</span>
          {retry && (
            <button type="button" className="ax-link" onClick={retry}>
              Retry
            </button>
          )}
        </>
      )}
      {status.state === "idle" && "Changes save as you go"}
    </span>
  );
}

// ---------- module list ----------
const verb = (m: Module) => (m.status === "not_started" ? "Start" : m.status === "complete" ? "Review" : "Continue");
const tone = (m: Module) => (m.status === "complete" ? "ok" : m.status === "partial" ? "brand" : "neutral");
const stateWord = (m: Module) => (m.status === "complete" ? "Complete" : m.status === "partial" ? "In progress" : "Not started");

export function Meter({ percent, label }: { percent: number; label: string }) {
  return (
    <div className="ax-meter" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
      <i style={{ width: `${Math.max(percent, percent > 0 ? 3 : 0)}%` }} />
    </div>
  );
}

export function ModuleList({ data, onOpen }: { data: Questionnaires; onOpen: (id: string) => void }) {
  return (
    <ul className="ax-modules">
      {data.modules.map((m) => (
        <li key={m.id}>
          <button type="button" className="ax-module" onClick={() => onOpen(m.id)}>
            <span className="ax-module-main">
              <span className="ax-module-title">
                <strong>{m.title}</strong>
                <Pill tone="neutral">Optional</Pill>
                <Pill tone={tone(m)} dot>
                  {stateWord(m)}
                </Pill>
              </span>
              <span className="ax-module-purpose">{m.purpose}</span>
            </span>
            <span className="ax-module-progress">
              <Meter percent={m.percent} label={`${m.title}: ${m.answered} of ${m.total} answered`} />
              <small>
                {m.answered} of {m.total} answered
              </small>
            </span>
            <span className="ax-module-go">
              {verb(m)} <ArrowRight size={14} aria-hidden="true" />
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

// ---------- a module's form ----------
type Local = Record<string, unknown>;

const blank = (fields: Field[] = []) => Object.fromEntries(fields.map((f) => [f.key, ""])) as Record<string, string>;
const entryOf = (fields: Field[] | undefined, saved: Record<string, string> | undefined) => ({
  ...blank(fields),
  ...(saved || {}),
});

function initial(module: Module): Local {
  const local: Local = {};
  for (const q of module.questions) {
    if (q.type === "work_rows")
      local[q.key] = Object.fromEntries((q.rows || []).map((r) => [r.row_id, entryOf(q.fields, r.answer)]));
    else if (q.type === "references")
      local[q.key] = ((q.answer as Record<string, string>[]) || []).map((r) => entryOf(q.fields, r));
    else if (q.type === "yes_no") local[q.key] = q.answer ?? undefined;
    // Stored text can be a number (a notice period in days, saved by the setup guide): show it as words, since the
    // field asks for "Immediately, 2 weeks, 1 month". Showing it changes nothing until the person edits it.
    else if (typeof q.answer === "number") local[q.key] = q.answer === 0 ? "Immediately" : `${q.answer} day${q.answer === 1 ? "" : "s"}`;
    else local[q.key] = q.answer === null || q.answer === undefined ? "" : String(q.answer);
  }
  return local;
}

// What each question looks like as last saved, keyed the way the form compares before saving again.
function seed(module: Module): Record<string, string> {
  const out: Record<string, string> = {};
  const start = initial(module);
  for (const q of module.questions) {
    if (q.type === "work_rows") for (const r of q.rows || []) out[q.key + ":" + r.row_id] = JSON.stringify(entryOf(q.fields, r.answer));
    else out[q.key] = JSON.stringify(start[q.key]);
  }
  return out;
}

export function ModuleForm({
  module,
  onModule,
  onClose,
  closeLabel = "All questionnaires",
}: {
  module: Module;
  onModule: (m: Module) => void;
  onClose?: () => void;
  closeLabel?: string;
}) {
  const [local, setLocal] = useState<Local>(() => initial(module));
  const [status, setStatus] = useState<Status>({ state: "idle" });
  const [problems, setProblems] = useState<Record<string, string>>({});
  const [confirmAll, setConfirmAll] = useState(false);
  // Saves go one at a time and in order, so a slow answer never lands after a newer one.
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const sent = useRef<Record<string, string>>({});
  // Answers the server has not accepted yet. Every save sends them again with the new one, so one failure is never
  // forgotten when a later answer saves, and Retry resends all of them.
  const failed = useRef<{ answers: Record<string, unknown>; accept: boolean } | null>(null);
  const timer = useRef(0);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => {
    sent.current = seed(module);
    // Only when switching modules: a save must not reset what is being typed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [module.id]);

  const put = useCallback(
    (answers: Record<string, unknown>, accept = false) => {
      setStatus({ state: "saving" });
      const run = async () => {
        const body = { answers: mergeAnswers(failed.current?.answers, answers), accept: accept || !!failed.current?.accept };
        try {
          const res = await api<{ module: Module }>(`/knowledge/questionnaires/${module.id}`, "PUT", {
            answers: body.answers,
            accept_suggestions: body.accept,
          });
          failed.current = null;
          onModule(res.module);
          if (body.accept) {
            setLocal(initial(res.module));
            sent.current = seed(res.module);
          }
          setStatus({ state: "saved" });
          window.clearTimeout(timer.current);
          timer.current = window.setTimeout(() => setStatus((s) => (s.state === "saved" ? { state: "idle" } : s)), 2600);
        } catch (e) {
          failed.current = body;
          setStatus({ state: "error", message: fail(e) });
        }
      };
      queue.current = queue.current.then(run);
      return queue.current;
    },
    [module.id, onModule],
  );

  const retry = () => failed.current && put({});

  // Save one question when its value really changed since the last save.
  const commit = (key: string, value: unknown, payload: unknown = value) => {
    const text = JSON.stringify(value);
    if (sent.current[key] === text) return;
    sent.current[key] = text;
    put({ [key]: payload === "" || payload === undefined ? null : payload });
  };
  const set = (key: string, value: unknown) => setLocal((l) => ({ ...l, [key]: value }));
  const setNow = (key: string, value: unknown) => {
    set(key, value);
    commit(key, value);
  };

  const sections = useMemo(() => {
    const groups: { name: string; items: Question[] }[] = [];
    for (const q of module.questions) {
      const g = groups.find((x) => x.name === q.section);
      if (g) g.items.push(q);
      else groups.push({ name: q.section, items: [q] });
    }
    return groups;
  }, [module.questions]);

  // The server repeats one help line on every question of a module (tax-credit): show it once, not per row.
  const shared = useMemo(() => {
    const seen: Record<string, number> = {};
    for (const q of module.questions) if (q.help) seen[q.help] = (seen[q.help] || 0) + 1;
    return new Set(Object.keys(seen).filter((h) => seen[h] > 1));
  }, [module.questions]);

  const hasSuggestions = module.questions.some((q) => q.suggestion !== null && q.suggestion !== undefined && q.type !== "work_rows" && q.type !== "references");
  const open = module.questions.filter((q) => (q.suggestion ?? null) !== null && !q.answered && q.type !== "work_rows" && q.type !== "references").length;

  return (
    <section className="ax-qf" aria-label={module.title}>
      <header className="ax-qf-head">
        {onClose && (
          <Button variant="ghost" size="sm" icon={<ArrowLeft size={14} aria-hidden="true" />} onClick={onClose}>
            {closeLabel}
          </Button>
        )}
        <div className="ax-qf-title">
          <h2>{module.title}</h2>
          <Pill tone="neutral">Optional</Pill>
        </div>
        <p className="ax-qf-purpose">{module.purpose} Skip any question; nothing here is required to apply.</p>
        <div className="ax-qf-progress">
          <Meter percent={module.percent} label={`${module.title}: ${module.answered} of ${module.total} answered`} />
          <small>
            {module.answered} of {module.total} answered
          </small>
          <SaveState status={status} retry={failed.current ? retry : undefined} />
        </div>
      </header>

      {hasSuggestions && open > 0 && module.id === "wotc" && (
        <div className="ax-qf-bulk">
          {!confirmAll ? (
            <>
              <span>{[...shared][0] || "Most people answer No to everything here."}</span>
              <Button size="sm" onClick={() => setConfirmAll(true)}>
                Answer No to all {open}
              </Button>
            </>
          ) : (
            <div className="ax-qf-confirm" role="group" aria-label="Confirm No to all">
              <p>
                This saves <strong>No</strong> to the {open} question{open === 1 ? "" : "s"} you have not answered. Do it only if none
                of them applies to you; your own answers stay as they are, and you can change any of them afterwards. The screening
                never affects hiring.
              </p>
              <span className="ax-qf-confirm-actions">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => {
                    setConfirmAll(false);
                    put({}, true);
                  }}
                >
                  Yes, answer No to {open}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setConfirmAll(false)}>
                  Cancel
                </Button>
              </span>
            </div>
          )}
        </div>
      )}

      {sections.map((g) => (
        <div key={g.name || "all"} className="ax-qf-section">
          {g.name && <h3>{g.name}</h3>}
          {g.items.map((q) => {
            if (q.type === "work_rows")
              return (
                <Supervisors
                  key={q.key}
                  q={q}
                  value={(local[q.key] as Record<string, Record<string, string>>) || {}}
                  problems={problems}
                  setProblem={(id, msg) => setProblems((p) => ({ ...p, [id]: msg }))}
                  onEdit={(row, entry) => set(q.key, { ...(local[q.key] as object), [row]: entry })}
                  onSave={(row, entry) => {
                    const text = JSON.stringify(entry);
                    const id = q.key + ":" + row;
                    if (sent.current[id] === text) return;
                    sent.current[id] = text;
                    put({ [q.key]: { [row]: entry } });
                  }}
                />
              );
            if (q.type === "references")
              return (
                <References
                  key={q.key}
                  q={q}
                  value={(local[q.key] as Record<string, string>[]) || []}
                  problems={problems}
                  setProblem={(id, msg) => setProblems((p) => ({ ...p, [id]: msg }))}
                  onEdit={(list) => set(q.key, list)}
                  onSave={(list) => {
                    const text = JSON.stringify(list);
                    if (sent.current[q.key] === text) return;
                    sent.current[q.key] = text;
                    put({ [q.key]: list });
                  }}
                />
              );
            return (
              <Row key={q.key} q={shared.has(q.help) ? { ...q, help: "" } : q} value={local[q.key]} set={(v) => set(q.key, v)} setNow={(v) => setNow(q.key, v)} commit={(v) => commit(q.key, v)} />
            );
          })}
        </div>
      ))}
    </section>
  );
}

function Row({
  q,
  value,
  set,
  setNow,
  commit,
}: {
  q: Question;
  value: unknown;
  set: (v: unknown) => void;
  setNow: (v: unknown) => void;
  commit: (v: unknown) => void;
}) {
  const answered = value !== undefined && value !== "" && value !== null;
  const suggested = !answered && q.suggestion !== null && q.suggestion !== undefined;
  const id = "q-" + q.key;
  let control: React.ReactNode = null;
  if (q.type === "yes_no") control = <YesNo label={q.prompt} value={value as boolean | undefined} onChange={setNow} />;
  else if (q.type === "choice")
    control =
      q.options.length <= 4 ? (
        <Segmented label={q.prompt} value={(value as string) || ""} options={q.options.map((o) => ({ value: o, label: o }))} onChange={setNow} />
      ) : (
        <select id={id} className="ax-control" aria-label={q.prompt} value={(value as string) || ""} onChange={(e) => setNow(e.target.value)}>
          <option value="">Skip</option>
          {q.options.map((o) => (
            <option key={o}>{o}</option>
          ))}
        </select>
      );
  else if (q.type === "date")
    control = (
      <input
        id={id}
        type="date"
        className="ax-control ax-qf-input"
        aria-label={q.prompt}
        value={(value as string) || ""}
        onChange={(e) => setNow(e.target.value)}
      />
    );
  else
    control = (
      <input
        id={id}
        className="ax-control ax-qf-input"
        aria-label={q.prompt}
        value={(value as string) || ""}
        maxLength={200}
        onChange={(e) => set(e.target.value)}
        onBlur={(e) => commit(e.target.value.trim())}
        onKeyDown={(e) => e.key === "Enter" && commit((e.target as HTMLInputElement).value.trim())}
      />
    );
  return (
    <div className="ax-qf-row">
      <div className="ax-qf-text">
        <span className="ax-qf-prompt">{q.prompt}</span>
        {q.help && <small className="ax-qf-help">{q.help}</small>}
      </div>
      <div className="ax-qf-control">
        {control}
        {answered ? (
          <button type="button" className="ax-link" onClick={() => setNow(q.type === "yes_no" ? undefined : "")} aria-label={`Clear answer: ${q.prompt}`}>
            Clear
          </button>
        ) : (
          suggested && <small className="ax-qf-suggest">Usually {q.suggestion === false ? "No" : q.suggestion === true ? "Yes" : String(q.suggestion)}</small>
        )}
      </div>
    </div>
  );
}

function span(r: WorkRow) {
  const when = [r.start, r.current ? "Present" : r.end].filter(Boolean).join(" – ");
  return when;
}

function Supervisors({
  q,
  value,
  problems,
  setProblem,
  onEdit,
  onSave,
}: {
  q: Question;
  value: Record<string, Record<string, string>>;
  problems: Record<string, string>;
  setProblem: (id: string, msg: string) => void;
  onEdit: (row: string, entry: Record<string, string>) => void;
  onSave: (row: string, entry: Record<string, string>) => void;
}) {
  const rows = q.rows || [];
  const fields = (q.fields || []).filter((f) => f.key !== "may_contact");
  const contact = (q.option_labels?.may_contact || {}) as Record<string, string>;
  if (!rows.length)
    return (
      <div className="ax-qf-block">
        <strong>{q.prompt}</strong>
        <p className="ax-qf-help">Add your work history in Setup › Profile first; each job gets its own supervisor card here.</p>
      </div>
    );
  const done = (row: WorkRow) => Object.keys(value[row.row_id] || {}).some((k) => (value[row.row_id] || {})[k]);
  return (
    <div className="ax-qf-block">
      <div className="ax-qf-block-head">
        <strong>{q.prompt}</strong>
        {q.help && <small className="ax-qf-help">{q.help}</small>}
      </div>
      <div className="ax-qf-cards">
        {rows.map((r) => {
          const entry = entryOf(q.fields, value[r.row_id]);
          const pid = q.key + ":" + r.row_id + ":email";
          const save = (next: Record<string, string>) => {
            const e = next.email.trim();
            if (e && !EMAIL.test(e)) return setProblem(pid, "Enter a valid email address");
            setProblem(pid, "");
            onSave(r.row_id, Object.fromEntries(Object.entries(next).map(([k, v]) => [k, v.trim()])));
          };
          return (
            <article key={r.row_id} className="ax-qf-card" aria-label={`${r.title} at ${r.company}`}>
              <header>
                <span>
                  <strong>{r.title || "Role"}</strong>
                  <span className="ax-qf-dim"> · {r.company || "Employer"}</span>
                </span>
                <span className="ax-qf-card-meta">
                  {span(r) && <small>{span(r)}</small>}
                  <Pill tone={done(r) ? "ok" : "neutral"}>{done(r) ? "Added" : "Not added"}</Pill>
                </span>
              </header>
              <div className="ax-qf-grid">
                {fields.map((f) => (
                  <Input
                    key={f.key}
                    label={f.prompt}
                    type={f.key === "email" ? "email" : f.key === "phone" ? "tel" : "text"}
                    autoComplete="off"
                    maxLength={200}
                    value={entry[f.key] || ""}
                    error={f.key === "email" ? problems[pid] : undefined}
                    onChange={(e) => onEdit(r.row_id, { ...entry, [f.key]: e.target.value })}
                    onBlur={() => save(entry)}
                  />
                ))}
              </div>
              <div className="ax-qf-contact">
                <span className="ax-label">May we contact this supervisor?</span>
                <Segmented
                  label="May we contact this supervisor?"
                  value={entry.may_contact || ""}
                  options={Object.entries(contact).map(([v, label]) => ({ value: v, label }))}
                  onChange={(v) => {
                    const next = { ...entry, may_contact: v };
                    onEdit(r.row_id, next);
                    save(next);
                  }}
                />
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}

function References({
  q,
  value,
  problems,
  setProblem,
  onEdit,
  onSave,
}: {
  q: Question;
  value: Record<string, string>[];
  problems: Record<string, string>;
  setProblem: (id: string, msg: string) => void;
  onEdit: (list: Record<string, string>[]) => void;
  onSave: (list: Record<string, string>[]) => void;
}) {
  const max = q.max || 3;
  const fields = q.fields || [];
  const clean = (list: Record<string, string>[]) =>
    list.map((r) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, v.trim()]))).filter((r) => Object.values(r).some(Boolean));
  const save = (list: Record<string, string>[]) => {
    const bad = list.findIndex((r) => r.email.trim() && !EMAIL.test(r.email.trim()));
    list.forEach((r, i) => setProblem(`ref:${i}:email`, i === bad ? "Enter a valid email address" : ""));
    if (bad < 0) onSave(clean(list));
  };
  return (
    <div className="ax-qf-block">
      <div className="ax-qf-block-head">
        <strong>{q.prompt}</strong>
        {q.help && <small className="ax-qf-help">{q.help}</small>}
      </div>
      <div className="ax-qf-cards">
        {value.map((entry, i) => (
          <article key={i} className="ax-qf-card" aria-label={`Reference ${i + 1}`}>
            <header>
              <strong>Reference {i + 1}</strong>
              <Button
                variant="ghost"
                size="sm"
                icon={<Trash2 size={13} aria-hidden="true" />}
                aria-label={`Remove reference ${i + 1}`}
                onClick={() => {
                  const next = value.filter((_, j) => j !== i);
                  onEdit(next);
                  save(next);
                }}
              >
                Remove
              </Button>
            </header>
            <div className="ax-qf-grid">
              {fields.map((f) => (
                <Input
                  key={f.key}
                  label={f.prompt}
                  type={f.key === "email" ? "email" : f.key === "phone" ? "tel" : "text"}
                  autoComplete="off"
                  maxLength={200}
                  value={entry[f.key] || ""}
                  error={f.key === "email" ? problems[`ref:${i}:email`] : undefined}
                  onChange={(e) => onEdit(value.map((r, j) => (j === i ? { ...r, [f.key]: e.target.value } : r)))}
                  onBlur={() => save(value)}
                />
              ))}
            </div>
          </article>
        ))}
      </div>
      {value.length < max && (
        <Button size="sm" icon={<Plus size={14} aria-hidden="true" />} onClick={() => onEdit([...value, blank(fields)])}>
          {value.length ? "Add another reference" : "Add a reference"}
        </Button>
      )}
    </div>
  );
}

// Used by both hosts when the endpoint is not there yet (an older server) or fails.
export function Unavailable({ error, retry }: { error: Error; retry: () => void }) {
  const gone = error instanceof ApiError && (error.status === 404 || error.status === 405);
  return (
    <div className="ax-qf-gone" role={gone ? undefined : "alert"}>
      <p>
        {gone
          ? "Questionnaires arrive with the next update of this installation. Nothing else is affected."
          : `Could not load the questionnaires: ${error.message}`}
      </p>
      {!gone && (
        <Button size="sm" onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  );
}
