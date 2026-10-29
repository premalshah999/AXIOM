import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Check, Upload } from "lucide-react";
import { api, ApiError } from "./api";
import { Loading } from "./ui";
import { BrandMark } from "./shell/Brand";
import { Button, Input, Pill } from "./ui/index";
import {
  Meter,
  ModuleForm,
  ModuleList,
  moduleFacts,
  Segmented,
  Unavailable,
  useQuestionnaires,
  YesNo,
  type Module,
} from "./qform";
import type { EducationEntry, Profile, WorkEntry } from "./types";
import "./styles/onboarding.css";

// The sign-in screens and Setup → System › Your sign-in live in account.tsx.

// "Finish later" (guide.ts, so the shell reads it without loading this module).
export { guideLater } from "./guide";

// ---------------------------------------------------------------------------
// Setup guide: the facts employers actually ask for, in the order a person thinks about them (DESIGN.md §6).
// One focused card per step; progress is saved on the server (/api/onboarding), so it resumes where it stopped.
// Frequencies come from 2,837 applications the engine ran (Oct 2026): how often an employer form
// asked a question that this fact answers.
// ---------------------------------------------------------------------------

type StepId =
  | "account"
  | "resume"
  | "profile"
  | "authorization"
  | "preferences"
  | "screening"
  | "permissions"
  | "connections"
  | "questionnaires"
  | "review";
// `server` is the id the backend stores (ONBOARDING_STEPS); two steps share one where the server has no id of its own.
type Step = { id: StepId; title: string; lead: string; server: string; optional?: boolean; time: string };
const STEPS: Step[] = [
  {
    id: "account",
    title: "Account",
    server: "resume",
    time: "Done",
    lead: "You are signed in. Setup takes about five minutes and saves as you go.",
  },
  {
    id: "resume",
    title: "Resume",
    server: "resume",
    time: "1 min",
    lead: "We read your resume once to prefill your profile. You confirm everything on the next steps.",
  },
  {
    id: "profile",
    title: "Profile check",
    server: "contact",
    time: "1 min",
    lead: "Filled into every application exactly as written. Check what we read from your resume.",
  },
  {
    id: "authorization",
    title: "Work authorization",
    server: "authorization",
    time: "1 min",
    lead: "The questions that decide eligibility. Answer them once, accurately.",
  },
  {
    id: "preferences",
    title: "Preferences",
    server: "preferences",
    time: "1 min",
    lead: "What to look for, where, and when you can start.",
  },
  {
    id: "screening",
    title: "Screening",
    server: "background",
    time: "2 min",
    lead: "Standard screening questions. Your answers are used only when a form asks.",
  },
  {
    id: "permissions",
    title: "Permissions",
    server: "permissions",
    time: "1 min",
    lead: "What AXIOM may do on your behalf.",
  },
  {
    id: "connections",
    title: "Connections",
    server: "permissions",
    time: "1 min",
    lead: "The model key and the inbox the engine reads verification codes from. Both can wait until later.",
  },
  {
    id: "questionnaires",
    title: "Questionnaires",
    server: "questionnaires",
    optional: true,
    time: "Optional",
    lead: "Answer a few things once so applications never stop to ask. Skip any or all of this.",
  },
  {
    id: "review",
    title: "Review",
    server: "review",
    time: "1 min",
    lead: "Check your answers. You can change any of them later in Setup.",
  },
];

const EDITS_PROFILE = new Set<StepId>(["resume", "profile", "authorization", "preferences", "screening", "permissions"]);

// Which step a stored server step reopens. "identity" belonged to its own step before Screening absorbed it.
const OPEN_AT: Record<string, StepId> = {
  resume: "resume",
  contact: "profile",
  authorization: "authorization",
  preferences: "preferences",
  background: "screening",
  identity: "screening",
  permissions: "permissions",
  questionnaires: "questionnaires",
  review: "review",
};

const ASKED: Record<string, string> = {
  authorization: "asked in about 44% of applications",
  sponsorship: "asked in about 40% of applications",
  status: "asked in about 8% of applications",
  over18: "asked in about 13% of applications",
  previously: "asked in about 29% of applications",
  relatives: "asked in about 17% of applications",
  government: "asked in about 9% of applications",
  official: "asked in about 6% of applications",
  noncompete: "asked in about 9% of applications",
  relocate: "asked in about 15% of applications",
  start: "asked in about 22% of applications",
  salary: "asked in about 20% of applications",
  source: "asked in about 26% of applications",
  demographics: "asked in about 37% of applications",
  consent: "asked in about 37% of applications",
  linkedin: "asked in about 19% of applications",
  phoneType: "asked in about 15% of applications",
};

type Draft = Profile;

const EMPTY_PROFILE: Draft = {
  name: "",
  email: "",
  phone: "",
  location: "",
  linkedin: "",
  website: "",
  github: "",
  first_name: "",
  last_name: "",
  middle_name: "",
  preferred_name: "",
  phone_country_code: "+1",
  phone_type: "Mobile",
  address: { line1: "", line2: "", city: "", state: "", postal_code: "", county: "", country: "United States" },
  work: [],
  education: [],
  skills: [],
  languages: [],
  salary_expectation: "",
  availability: "",
  facts: {},
  evidence: [],
  target_roles: [],
  job_priority: "new_grad_first",
  target_locations: [],
  excluded_keywords: [],
  minimum_fit: 60,
  decline_demographics: true,
  approved_consents: [],
  approved_answers: {},
  reviewed_answers: [],
  application_source: "LinkedIn",
  allow_account_creation: false,
  allow_application_consents: false,
  accept_all_application_terms: false,
  accept_required_marketing: false,
  autonomous: false,
};

// ---------- facts ----------
// The server matches fact keys without regard to case, and facts saved elsewhere (Telegram, an import) are often
// cased differently or written as a sentence ("work_authorization_US": "Yes, authorized ..."). The guide reads
// them the same way, so a person who runs it again sees their existing answers, and saves over them, not beside them.
type Facts = Record<string, unknown>;
function foldFacts(facts: Facts): Facts {
  const out: Facts = {};
  const keys = Object.keys(facts);
  // Keys already in lower case are applied last, so they win over another casing of the same name.
  for (const key of keys.filter((k) => k !== k.toLowerCase())) out[key.toLowerCase()] = facts[key];
  for (const key of keys.filter((k) => k === k.toLowerCase())) out[key] = facts[key];
  return out;
}
function asBool(value: unknown): boolean | undefined {
  if (typeof value === "boolean") return value;
  if (typeof value !== "string") return undefined;
  if (/^\s*(yes|true)\b/i.test(value)) return true;
  if (/^\s*(no|false)\b/i.test(value)) return false;
  return undefined;
}

/** The server stores "" for fields nobody set; show the usual US answer instead of an empty box. */
function withDefaults(profile: Partial<Draft>): Draft {
  const draft = {
    ...EMPTY_PROFILE,
    ...profile,
    address: { ...EMPTY_PROFILE.address, ...(profile.address || {}) },
    facts: { ...(profile.facts || {}) },
    approved_answers: {},
    reviewed_answers: [],
  } as Draft;
  // A profile read from an older or partial server can hold null where a list belongs.
  for (const key of ["work", "education", "skills", "languages", "target_roles", "target_locations", "excluded_keywords", "approved_consents", "evidence"] as const)
    if (!Array.isArray(draft[key])) draft[key] = [] as never;
  for (const key of ["first_name", "last_name", "name", "email", "phone", "location", "linkedin", "github", "website", "salary_expectation", "availability", "preferred_name", "middle_name"] as const)
    if (typeof draft[key] !== "string") draft[key] = "" as never;
  for (const key of ["phone_country_code", "phone_type", "application_source"] as const)
    if (!String(draft[key] || "").trim()) draft[key] = EMPTY_PROFILE[key];
  if (!draft.address.country.trim()) draft.address.country = EMPTY_PROFILE.address.country;
  // The authorization step shows the residence prefilled; save what it shows.
  if (!String(foldFacts(draft.facts).country_of_residence || "").trim()) draft.facts.country_of_residence = draft.address.country;
  return draft;
}

// ---------- small controls ----------
function Choice<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T | "";
  options: readonly T[];
  onChange: (v: T) => void;
  label: string;
}) {
  return <Segmented label={label} value={value} options={options.map((o) => ({ value: o, label: o }))} onChange={onChange} />;
}

/** One question: the prompt (with how often forms ask it) and its control. `stack` puts the control under a long prompt. */
function Question({
  label,
  asked,
  hint,
  stack,
  children,
}: {
  label: string;
  asked?: string;
  hint?: React.ReactNode;
  stack?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={"ax-ob-q" + (stack ? " is-stack" : "")}>
      <div className="ax-ob-q-text">
        <span className="ax-ob-q-label">{label}</span>
        {asked && <small className="ax-ob-q-asked">{asked}</small>}
        {hint && <small className="ax-ob-q-hint">{hint}</small>}
      </div>
      <div className="ax-ob-q-control">{children}</div>
    </div>
  );
}

function Tags({
  values,
  onChange,
  placeholder,
  label,
  suggestions = [],
}: {
  values: string[];
  onChange: (v: string[]) => void;
  placeholder: string;
  label: string;
  suggestions?: string[];
}) {
  const [text, setText] = useState("");
  const add = (v: string) => {
    const clean = v.trim();
    if (clean && !values.some((x) => x.toLowerCase() === clean.toLowerCase())) onChange([...values, clean]);
    setText("");
  };
  return (
    <div className="ax-ob-tags">
      {(values.length > 0 || suggestions.length > 0) && (
        <div className="ax-ob-chips">
          {values.map((v) => (
            <button key={v} type="button" className="ax-ob-chip is-on" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>
              {v} <span aria-hidden="true">×</span>
            </button>
          ))}
          {suggestions
            .filter((s) => !values.includes(s))
            .slice(0, 6)
            .map((s) => (
              <button key={s} type="button" className="ax-ob-chip" onClick={() => add(s)}>
                + {s}
              </button>
            ))}
        </div>
      )}
      <input
        className="ax-control"
        aria-label={label}
        value={text}
        placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add(text);
          }
        }}
        onBlur={() => text && add(text)}
      />
    </div>
  );
}

/** A comma-separated field that keeps what is typed, commas and spaces included, and reports the finished list. */
function CsvInput({
  label,
  hint,
  values,
  onChange,
}: {
  label: string;
  hint?: React.ReactNode;
  values: string[];
  onChange: (v: string[]) => void;
}) {
  const [text, setText] = useState(values.join(", "));
  return (
    <Input
      label={label}
      hint={hint}
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        onChange(e.target.value.split(",").map((x) => x.trim()).filter(Boolean));
      }}
    />
  );
}

const STATUS_OPTIONS = [
  "U.S. citizen",
  "Permanent resident (green card)",
  "F-1 student (CPT)",
  "F-1 OPT",
  "F-1 STEM OPT",
  "H-1B",
  "TN",
  "Other visa",
] as const;
type Status = (typeof STATUS_OPTIONS)[number];

/** The option a stored status stands for: facts saved elsewhere are often a sentence ("OPT; expires ..."). Display only. */
function matchStatus(text: string): Status | "" {
  if ((STATUS_OPTIONS as readonly string[]).includes(text)) return text as Status;
  if (/stem\s*opt/i.test(text)) return "F-1 STEM OPT";
  if (/\bopt\b/i.test(text)) return "F-1 OPT";
  if (/\bcpt\b/i.test(text)) return "F-1 student (CPT)";
  if (/\bh-?1b\b/i.test(text)) return "H-1B";
  if (/^\s*tn\b/i.test(text)) return "TN";
  if (/green card|permanent resident/i.test(text)) return "Permanent resident (green card)";
  if (/citizen/i.test(text) && !/non[- ]?citizen/i.test(text)) return "U.S. citizen";
  return "";
}

/** What a step needs before moving on: only what nearly every application requires. */
function stepProblem(step: StepId, d: Draft): string {
  if (step === "profile") {
    const missing = [
      !d.first_name.trim() && "legal first name",
      !d.last_name.trim() && "legal last name",
      !d.email.trim() && "email",
      !d.phone.trim() && "phone",
    ].filter(Boolean);
    if (missing.length) return `Add your ${missing.join(", ")}. Nearly every application requires ${missing.length > 1 ? "them" : "it"}.`;
  }
  if (step === "preferences" && !d.target_roles.length) return "Add at least one role so discovery knows what to search for.";
  return "";
}

function mergeExtract(draft: Draft, x: Partial<Draft>): Draft {
  const next = { ...draft };
  for (const key of ["first_name", "last_name", "phone", "location", "linkedin", "github", "website"] as const)
    if (!String(next[key] || "").trim() && x[key]) next[key] = x[key] as string;
  if (!next.work.length && x.work?.length) next.work = x.work as WorkEntry[];
  if (!next.education.length && x.education?.length) next.education = x.education as EducationEntry[];
  if (x.skills?.length) next.skills = Array.from(new Set([...(next.skills || []), ...x.skills]));
  if (!next.name.trim()) next.name = [next.first_name, next.last_name].filter(Boolean).join(" ");
  return next;
}

// ---------- connections state (the Connections step and the review read the same copy) ----------
type ConnectionField = { name: string; set: boolean; value: string };
type Connections = { fields: ConnectionField[]; provider: string; model_ready: boolean };
type Mailbox = { id: string; email: string; state?: string; provider?: string };
type Linked = { model: Connections | null; mailboxes: Mailbox[] | null };

const PROVIDERS = [
  { id: "deepseek", label: "DeepSeek (recommended, lowest cost)" },
  { id: "haiku", label: "Anthropic Claude Haiku" },
  { id: "mimo", label: "Xiaomi MiMo" },
  { id: "openai", label: "OpenAI" },
  { id: "openrouter", label: "OpenRouter" },
];

// ---------- the guide ----------
type Account = { email?: string; name?: string };

export function Onboarding({ onDone, onExit }: { onDone: () => void; onExit: () => void }) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [account, setAccount] = useState<Account>({});
  const [at, setAt] = useState(0);
  // The furthest step reached: the rail lets you go back to any of these, and forward again.
  const [reached, setReached] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  // Automatic submission lives in the installation's controls, not the profile.
  const [submit, setSubmit] = useState<{ saved: boolean; value: boolean } | null>(null);
  const [linked, setLinked] = useState<Linked>({ model: null, mailboxes: null });
  const [summary, setSummary] = useState<{ id: string; title: string; answered: number; total: number }[] | null>(null);
  const dirty = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);

  const refreshLinked = useCallback(
    () =>
      Promise.all([
        api<Connections>("/connections").catch(() => null),
        api<{ mailboxes: Mailbox[] }>("/mail").catch(() => null),
      ]).then(([model, mail]) => setLinked({ model, mailboxes: mail ? mail.mailboxes || [] : null })),
    [],
  );

  useEffect(() => {
    Promise.all([
      api<Partial<Draft>>("/profile"),
      api<{ step: string; updated_at?: string; questionnaires?: { modules: { id: string; title: string; answered: number; total: number }[] } }>("/onboarding"),
      api<{ auto_submit: boolean }>("/control").catch(() => null),
      api<Account>("/account").catch(() => ({}) as Account),
    ])
      .then(([profile, state, control, who]) => {
        setDraft(withDefaults(profile));
        setAccount(who);
        if (control) setSubmit({ saved: control.auto_submit, value: control.auto_submit });
        setSummary(state.questionnaires?.modules ?? null);
        // A brand-new account has never saved a step: it starts at the welcome card.
        const id = state.updated_at ? OPEN_AT[state.step] || "resume" : "account";
        const index = Math.max(0, STEPS.findIndex((s) => s.id === id));
        setAt(index);
        setReached(index);
      })
      .catch((e) => setError((e as Error).message));
    refreshLinked();
  }, [refreshLinked]);

  const go = useCallback((index: number) => {
    setError("");
    setAt(index);
    setReached((r) => Math.max(r, index));
    // The setup guide is its own scroll region (.ax-screen); the document never scrolls.
    document.querySelector(".ax-screen")?.scrollTo({ top: 0 });
  }, []);
  // Move focus to the new step's title so keyboard and screen-reader users land on it.
  useEffect(() => {
    if (draft) heading.current?.focus({ preventScroll: true });
  }, [at, draft === null]);

  if (error && !draft)
    return (
      <main className="ax-ob">
        <div className="ax-ob-fatal" role="alert">
          <p>Could not open the setup guide: {error}</p>
          <span className="ax-ob-fatal-actions">
            <Button variant="primary" size="sm" onClick={() => window.location.reload()}>
              Try again
            </Button>
            <Button variant="ghost" size="sm" onClick={onExit}>
              Skip for now
            </Button>
          </span>
        </div>
      </main>
    );
  if (!draft)
    return (
      <main className="ax-ob">
        <Loading label="Loading your profile…" />
      </main>
    );

  const current = STEPS[at];
  const last = at === STEPS.length - 1;
  const edit = (fn: (d: Draft) => Draft) => {
    dirty.current = true;
    setDraft((d) => (d ? fn(d) : d));
  };
  const setTop = <K extends keyof Draft>(key: K, value: Draft[K]) => edit((d) => ({ ...d, [key]: value }));
  const setFact = (key: string, value: unknown) =>
    edit((d) => {
      const facts = { ...d.facts };
      const same = Object.keys(facts).filter((k) => k.toLowerCase() === key.toLowerCase());
      for (const k of same.length ? same : [key]) facts[k] = value;
      return { ...d, facts };
    });
  const setAddress = (key: keyof Draft["address"], value: string) =>
    edit((d) => ({ ...d, address: { ...d.address, [key]: value } }));
  const setPeople = (updater: (d: Draft) => Draft) => {
    dirty.current = true;
    setDraft((d) => (d ? updater(d) : d));
  };
  // A questionnaire saved on its own: keep this copy of the profile current so a later profile save cannot undo it.
  const learn = (module: Module) =>
    setDraft((d) => {
      if (!d) return d;
      const facts = { ...d.facts };
      for (const [k, v] of Object.entries(moduleFacts(module))) {
        if (v === undefined) delete facts[k];
        else facts[k] = v;
      }
      return { ...d, facts };
    });

  async function persist(toIndex: number, completed = false) {
    if (!draft) return;
    const problem = stepProblem(current.id, draft);
    if (problem) {
      setError(problem);
      return;
    }
    setSaving(true);
    setError("");
    try {
      // Steps that edit the profile always save it (the defaults they show are part of what the owner confirmed).
      if (dirty.current || EDITS_PROFILE.has(current.id)) {
        const name = draft.name.trim() || [draft.first_name, draft.last_name].filter(Boolean).join(" ");
        // The address entered here is more deliberate than the location a resume happened to list.
        const location =
          [draft.address.city.trim(), draft.address.state.trim()].filter(Boolean).join(", ") || draft.location.trim();
        const { approved_answers: _a, reviewed_answers: _r, ...rest } = draft;
        await api("/profile", "PUT", { ...rest, name, location });
        dirty.current = false;
      }
      if (submit && submit.value !== submit.saved) {
        // Read the current controls right before writing them back (Pause may have changed).
        const fresh = await api<{ paused: boolean; auto_submit: boolean }>("/control");
        await api("/control", "PUT", { ...fresh, auto_submit: submit.value });
        setSubmit({ saved: submit.value, value: submit.value });
      }
      const state = await api<{ questionnaires?: { modules: { id: string; title: string; answered: number; total: number }[] } }>(
        "/onboarding",
        "PUT",
        { step: STEPS[Math.min(toIndex, STEPS.length - 1)].server, completed },
      );
      if (state?.questionnaires?.modules) setSummary(state.questionnaires.modules);
      if (completed) onDone();
      else go(toIndex);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="ax-ob">
      <header className="ax-ob-top">
        <div className="ax-ob-brand">
          <BrandMark size={22} />
          <span>AXIOM</span>
        </div>
        <div
          className="ax-ob-progress"
          role="progressbar"
          aria-label="Setup progress"
          aria-valuemin={1}
          aria-valuemax={STEPS.length}
          aria-valuenow={at + 1}
          aria-valuetext={`Step ${at + 1} of ${STEPS.length}: ${current.title}`}
        >
          <span className="ax-ob-progress-text">
            Step {at + 1} of {STEPS.length}
            <span className="ax-ob-progress-name"> · {current.title}</span>
          </span>
          <span className="ax-ob-ticks" aria-hidden="true">
            {STEPS.map((s, i) => (
              <i key={s.id} className={i < at ? "is-done" : i === at ? "is-now" : ""} />
            ))}
          </span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onExit}
          title="Steps you saved are kept. Continue any time from the banner or Setup → System."
        >
          Finish later
        </Button>
      </header>
      <div className="ax-ob-wrap">
        <nav className="ax-ob-rail" aria-label="Setup steps">
          <ol>
            {STEPS.map((s, i) => (
              <li key={s.id} aria-current={i === at ? "step" : undefined} className={i < at ? "is-done" : i === at ? "is-now" : ""}>
                <button type="button" onClick={() => go(i)} disabled={i > reached || saving}>
                  <span className="ax-ob-n">{i < at ? <Check size={12} strokeWidth={3} aria-hidden="true" /> : i + 1}</span>
                  <span className="ax-ob-rail-title">{s.title}</span>
                  {s.optional && <small>Optional</small>}
                </button>
              </li>
            ))}
          </ol>
        </nav>
        <section className="ax-ob-card" aria-labelledby="ob-title">
          <header className="ax-ob-head">
            <div className="ax-ob-meta" hidden={current.time === "Done"}>
              {current.optional ? <Pill tone="brand">Optional</Pill> : current.time !== "Done" && <span>About {current.time}</span>}
            </div>
            <h1 id="ob-title" ref={heading} tabIndex={-1}>
              {current.title}
            </h1>
            <p className="ax-ob-lead">{current.lead}</p>
          </header>
          <div className="ax-ob-body" key={current.id}>
            {current.id === "account" && <AccountStep account={account} />}
            {current.id === "resume" && <ResumeStep draft={draft} setDraft={(fn) => setPeople(fn)} />}
            {current.id === "profile" && <ContactStep draft={draft} setTop={setTop} setAddress={setAddress} />}
            {current.id === "authorization" && <AuthorizationStep draft={draft} setFact={setFact} />}
            {current.id === "preferences" && <PreferencesStep draft={draft} setTop={setTop} setFact={setFact} />}
            {current.id === "screening" && <ScreeningStep draft={draft} setTop={setTop} setFact={setFact} />}
            {current.id === "permissions" && (
              <PermissionsStep
                draft={draft}
                setTop={setTop}
                submit={submit?.value ?? null}
                setSubmit={(value) => setSubmit((v) => (v ? { ...v, value } : v))}
              />
            )}
            {current.id === "connections" && <ConnectionsStep linked={linked} email={draft.email} refresh={refreshLinked} />}
            {current.id === "questionnaires" && (
              <QuestionnairesStep
                onModule={(m) => {
                  learn(m);
                  setSummary((list) => list && list.map((x) => (x.id === m.id ? { ...x, answered: m.answered, total: m.total } : x)));
                }}
              />
            )}
            {current.id === "review" && <ReviewStep draft={draft} go={(id) => go(STEPS.findIndex((s) => s.id === id))} submit={submit?.value ?? null} linked={linked} summary={summary} />}
          </div>
          {error && (
            <p className="ax-ob-error" role="alert">
              {error}
            </p>
          )}
          <footer className="ax-ob-foot">
            {at > 0 && (
              <Button variant="ghost" icon={<ArrowLeft size={15} aria-hidden="true" />} onClick={() => go(at - 1)} disabled={saving}>
                Back
              </Button>
            )}
            <span className="ax-ob-foot-end">
              {current.optional && (
                <Button variant="ghost" onClick={() => persist(at + 1)} disabled={saving}>
                  Skip for now
                </Button>
              )}
              {!last ? (
                <Button variant="primary" loading={saving} onClick={() => persist(at + 1)}>
                  {current.id === "account" ? "Get started" : "Continue"}
                  <ArrowRight size={15} aria-hidden="true" />
                </Button>
              ) : (
                <Button variant="primary" loading={saving} onClick={() => persist(at, true)}>
                  Finish setup
                </Button>
              )}
            </span>
          </footer>
        </section>
      </div>
    </main>
  );
}

type StepProps = { draft: Draft };
type SetTop = <K extends keyof Draft>(key: K, value: Draft[K]) => void;
type SetFact = (key: string, value: unknown) => void;

function AccountStep({ account }: { account: Account }) {
  return (
    <div className="ax-ob-stack">
      <div className="ax-ob-who">
        <span className="ax-ob-avatar" aria-hidden="true">
          {(account.email || account.name || "A").trim().charAt(0).toUpperCase()}
        </span>
        <span>
          <strong>{account.email || account.name || "Your account"}</strong>
          <small>Signed in</small>
        </span>
        <Pill tone="ok" dot>
          Account ready
        </Pill>
      </div>
      <ul className="ax-ob-points">
        <li>Only your contact details and the roles you want are needed to start applying.</li>
        <li>Everything is saved when you continue. Close this page and you will pick up at the same step.</li>
        <li>You decide what AXIOM may do for you. Nothing is submitted until you allow it.</li>
      </ul>
    </div>
  );
}

function ResumeStep({ draft, setDraft }: StepProps & { setDraft: (fn: (d: Draft) => Draft) => void }) {
  const [resumes, setResumes] = useState<{ id: string; name: string }[] | null>(null);
  const [state, setState] = useState<"idle" | "uploading" | "reading" | "done" | "error">("idle");
  const [message, setMessage] = useState("");
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api<{ id: string; name: string }[]>("/resumes").then(setResumes).catch(() => setResumes([]));
  }, []);

  async function read(resumeId: string) {
    setState("reading");
    setMessage("Reading your resume…");
    try {
      const extracted = await api<Partial<Draft>>("/profile/extract?resume_id=" + encodeURIComponent(resumeId), "POST");
      setDraft((d) => mergeExtract(d, extracted));
      setState("done");
      const parts = [
        extracted.work?.length ? `${extracted.work.length} roles` : "",
        extracted.education?.length ? `${extracted.education.length} schools` : "",
        extracted.skills?.length ? `${extracted.skills.length} skills` : "",
      ].filter(Boolean);
      setMessage(parts.length ? "Found " + parts.join(", ") + ". Review them on the next steps." : "Resume read.");
    } catch (e) {
      setState("error");
      setMessage(`${(e as Error).message} Your resume is saved; you can fill in the next steps by hand.`);
    }
  }

  async function upload(file: File) {
    setState("uploading");
    setMessage("Uploading…");
    const form = new FormData();
    form.append("file", file);
    form.append("name", file.name.replace(/\.pdf$/i, "").slice(0, 100) || "Resume");
    form.append("roles", "");
    try {
      const row = await api<{ id: string; name: string }>("/resumes", "POST", form);
      setResumes((list) => [row, ...(list || [])]);
      await read(row.id);
    } catch (e) {
      setState("error");
      setMessage((e as Error).message);
    }
  }

  const busy = state === "uploading" || state === "reading";
  return (
    <div className="ax-ob-stack">
      <div
        className={"ax-ob-drop" + (over ? " is-over" : "")}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          const file = e.dataTransfer.files?.[0];
          if (file) upload(file);
        }}
      >
        <Upload size={20} aria-hidden="true" />
        <strong>Drop your resume here (PDF)</strong>
        <p>Text-based PDF up to 5 MB. It is attached to applications exactly as uploaded.</p>
        <input
          ref={input}
          type="file"
          accept="application/pdf"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload(file);
            e.target.value = "";
          }}
        />
        <Button variant="primary" loading={busy} onClick={() => input.current?.click()}>
          {busy ? "Working…" : "Choose PDF"}
        </Button>
      </div>
      {message && (
        <p className={state === "error" ? "ax-ob-error" : "ax-ob-status"} role={state === "error" ? "alert" : "status"}>
          {message}
        </p>
      )}
      {resumes && resumes.length > 0 && (
        <div className="ax-ob-resumes">
          <span className="ax-label">Uploaded</span>
          {resumes.map((r) => (
            <div key={r.id} className="ax-ob-resume">
              <span>{r.name}</span>
              <Button size="sm" disabled={busy} onClick={() => read(r.id)}>
                Prefill from this resume
              </Button>
            </div>
          ))}
        </div>
      )}
      {(draft.work.length > 0 || draft.education.length > 0) && (
        <div className="ax-ob-summary">
          {draft.work.slice(0, 4).map((w, i) => (
            <div key={"w" + i}>
              <strong>{w.title}</strong> · {w.company}
            </div>
          ))}
          {draft.education.slice(0, 3).map((e, i) => (
            <div key={"e" + i} className="is-dim">
              {e.degree} {e.field_of_study ? "· " + e.field_of_study : ""} · {e.school}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ContactStep({
  draft,
  setTop,
  setAddress,
}: StepProps & { setTop: SetTop; setAddress: (key: keyof Draft["address"], value: string) => void }) {
  const t = (key: keyof Draft) => ({
    value: String(draft[key] ?? ""),
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setTop(key, e.target.value as never),
  });
  const a = (key: keyof Draft["address"]) => ({
    value: String(draft.address[key] ?? ""),
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setAddress(key, e.target.value),
  });
  return (
    <div className="ax-ob-stack">
      <div className="ax-ob-grid">
        <Input label="Legal first name" {...t("first_name")} autoComplete="given-name" />
        <Input label="Legal last name" {...t("last_name")} autoComplete="family-name" />
        <Input label="Preferred first name" hint="Optional. Used where a form asks for a preferred name." {...t("preferred_name")} />
        <Input label="Email" type="email" {...t("email")} autoComplete="email" />
        <Input label="Phone" type="tel" {...t("phone")} autoComplete="tel-national" />
        <Input label="Country code" hint="For example +1." {...t("phone_country_code")} />
      </div>
      <Question label="Phone type" asked={ASKED.phoneType}>
        <Choice label="Phone type" value={draft.phone_type as "Mobile"} options={["Mobile", "Home", "Work"] as const} onChange={(v) => setTop("phone_type", v)} />
      </Question>
      <div className="ax-ob-grid">
        <Input label="Street address" className="is-wide" {...a("line1")} autoComplete="address-line1" />
        <Input label="City" {...a("city")} autoComplete="address-level2" />
        <Input label="State" {...a("state")} autoComplete="address-level1" />
        <Input label="Postal code" {...a("postal_code")} autoComplete="postal-code" />
        <Input label="Country" {...a("country")} autoComplete="country-name" />
      </div>
      <div className="ax-ob-grid">
        <Input label="LinkedIn URL" hint={ASKED.linkedin} placeholder="https://www.linkedin.com/in/…" {...t("linkedin")} />
        <Input label="GitHub URL" placeholder="https://github.com/…" {...t("github")} />
        <Input label="Portfolio or website" {...t("website")} />
      </div>
    </div>
  );
}

function AuthorizationStep({ draft, setFact }: StepProps & { setFact: SetFact }) {
  const f = foldFacts(draft.facts);
  const status = (f.current_immigration_status_us as string) || "";
  const statusChoice = matchStatus(status);
  const opt = /OPT/.test(status);
  const yn = (key: string, label: string) => (
    <YesNo label={label} value={asBool(f[key])} onChange={(v) => setFact(key, v)} />
  );
  return (
    <div className="ax-ob-stack">
      <div className="ax-ob-list">
        <Question label="Are you legally authorized to work in the United States?" asked={ASKED.authorization}>
          {yn("work_authorization_us", "Authorized to work in the United States")}
        </Question>
        <Question
          label="Will you now or in the future require visa sponsorship?"
          asked={ASKED.sponsorship}
          hint="Include future needs such as an H-1B petition after OPT."
        >
          {yn("requires_future_sponsorship_us", "Requires visa sponsorship")}
        </Question>
        <Question label="Are you at least 18 years old?" asked={ASKED.over18}>
          {yn("over_18", "At least 18 years old")}
        </Question>
        <Question label="Do you hold an active U.S. security clearance?">{yn("has_security_clearance", "Holds a security clearance")}</Question>
      </div>
      <Question label="Current status" asked={ASKED.status} stack>
        <Choice
          label="Current status"
          value={statusChoice}
          options={STATUS_OPTIONS}
          onChange={(v) => {
            setFact("current_immigration_status_US", v);
            setFact("us_citizen", v === "U.S. citizen");
          }}
        />
      </Question>
      {opt && (
        <Input label="OPT end date" type="date" value={String(f.opt_end_date || "")} onChange={(e) => setFact("opt_end_date", e.target.value)} />
      )}
      <div className="ax-ob-grid">
        <Input label="Country of citizenship" value={String(f.country_of_citizenship || "")} onChange={(e) => setFact("country_of_citizenship", e.target.value)} />
        <Input label="Country of residence" value={String(f.country_of_residence || "")} onChange={(e) => setFact("country_of_residence", e.target.value)} />
      </div>
    </div>
  );
}

const ROLE_SUGGESTIONS = ["Software Engineer", "Machine Learning Engineer", "Data Scientist", "Data Engineer", "AI Engineer", "Data Analyst"];
const START_OPTIONS = ["Immediately", "In 2 weeks", "In 1 month", "In 2 months"] as const;
const TRAVEL_OPTIONS = ["None", "Up to 25%", "Up to 50%", "75% or more"] as const;
const TRAVEL_PERCENT: Record<string, number> = { None: 0, "Up to 25%": 25, "Up to 50%": 50, "75% or more": 75 };

function PreferencesStep({ draft, setTop, setFact }: StepProps & { setTop: SetTop; setFact: SetFact }) {
  const f = foldFacts(draft.facts);
  const travel = Object.entries(TRAVEL_PERCENT).find(([, n]) => n === f.travel_percentage_willingness)?.[0] || "";
  const anywhere = draft.target_locations.some((l) => /anywhere/i.test(l));
  // Recent job titles first (not internships or assistantships), then common roles.
  const fromResume = draft.work
    .map((w) => (w.title || "").trim())
    .filter((t) => t && !/\b(?:intern|internship|assistant|student|volunteer|fellow)\b/i.test(t));
  const roleSuggestions = Array.from(new Set([...fromResume, ...ROLE_SUGGESTIONS]));
  return (
    <div className="ax-ob-stack">
      <Question label="Roles to look for" stack>
        <Tags
          label="Roles to look for"
          values={draft.target_roles}
          onChange={(v) => setTop("target_roles", v)}
          placeholder="Type a role and press Enter"
          suggestions={roleSuggestions}
        />
      </Question>
      <Question label="Where would you work?" stack>
        <Tags
          label="Where would you work?"
          values={draft.target_locations}
          onChange={(v) => setTop("target_locations", v)}
          placeholder="City, state, or Remote"
          suggestions={anywhere ? ["Remote"] : ["Anywhere in the USA", "Remote"]}
        />
      </Question>
      <div className="ax-ob-list">
        <Question label="Are you willing to relocate?" asked={ASKED.relocate}>
          <YesNo label="Willing to relocate" value={asBool(f.willing_to_relocate)} onChange={(v) => setFact("willing_to_relocate", v)} />
        </Question>
        {asBool(f.willing_to_relocate) === true && (
          <Question label="Can you relocate at your own expense?" hint="Answers “Do you require relocation assistance?”">
            <YesNo label="Relocate at own expense" value={asBool(f.relocation_at_own_expense)} onChange={(v) => setFact("relocation_at_own_expense", v)} />
          </Question>
        )}
        <Question label="Are you open to working on-site or hybrid?">
          <YesNo label="Open to on-site or hybrid" value={asBool(f.willing_onsite)} onChange={(v) => setFact("willing_onsite", v)} />
        </Question>
      </div>
      <Question label="How much travel can you do?" stack>
        <Choice
          label="Travel"
          value={travel as (typeof TRAVEL_OPTIONS)[number] | ""}
          options={TRAVEL_OPTIONS}
          onChange={(v) => {
            setFact("travel_percentage_willingness", TRAVEL_PERCENT[v]);
            setFact("willing_to_travel", TRAVEL_PERCENT[v] > 0);
          }}
        />
      </Question>
      <Question label="Earliest start" asked={ASKED.start} stack>
        <Choice
          label="Earliest start"
          value={(START_OPTIONS as readonly string[]).includes(draft.availability) ? (draft.availability as (typeof START_OPTIONS)[number]) : ""}
          options={START_OPTIONS}
          onChange={(v) => {
            setTop("availability", v);
            setFact("availability", v);
          }}
        />
      </Question>
      <div className="ax-ob-grid">
        <Input
          label="Desired annual base salary (USD)"
          hint={ASKED.salary + ". Leave empty to answer “negotiable” or pick the posted range."}
          inputMode="numeric"
          value={draft.salary_expectation}
          onChange={(e) => setTop("salary_expectation", e.target.value.replace(/[^\d]/g, ""))}
          placeholder="e.g. 95000"
        />
        <Input
          label="Notice period (days)"
          inputMode="numeric"
          value={f.notice_period === undefined ? "" : String(f.notice_period)}
          onChange={(e) => setFact("notice_period", e.target.value === "" ? undefined : Number(e.target.value.replace(/\D/g, "")))}
        />
      </div>
    </div>
  );
}

const GENDER = ["Male", "Female", "Non-binary"] as const;
const VETERAN = ["Not a veteran", "Protected veteran"] as const;
const DISABILITY = ["No disability", "Yes, I have a disability"] as const;

function ScreeningStep({ draft, setTop, setFact }: StepProps & { setTop: SetTop; setFact: SetFact }) {
  const f = foldFacts(draft.facts);
  const yn = (key: string, label: string) => (
    <YesNo label={label} value={asBool(f[key])} onChange={(v) => setFact(key, v)} />
  );
  return (
    <div className="ax-ob-stack">
      <p className="ax-ob-note">
        Answer only what is true. A question you leave unanswered is never answered for you: AXIOM asks you when a form
        needs it. Tax-credit screening and more questions have their own optional step later.
      </p>
      <div className="ax-ob-list">
        <Question
          label="Apart from the employers in your work history, have you ever worked for a company you apply to?"
          asked={ASKED.previously}
          hint="“Have you worked for this company before?” is answered Yes for an employer in your work history. This answers it for every other employer."
        >
          {yn("previously_employed_here", "Worked for another company you apply to")}
        </Question>
        <Question label="Do you have relatives working at companies you apply to?" asked={ASKED.relatives} hint="Answer No unless you know of one; you can add exceptions later.">
          {yn("has_relatives_at_employer", "Relatives at companies you apply to")}
        </Question>
        <Question label="Have you been employed by a government agency in the last 5 years?" asked={ASKED.government}>
          {yn("government_employment_history", "Government employment in the last 5 years")}
        </Question>
        <Question label="Are you, or a close relative, a government or public official?" asked={ASKED.official}>
          {yn("government_official", "Government or public official")}
        </Question>
        <Question label="Are you bound by a non-compete or other restrictive agreement?" asked={ASKED.noncompete}>
          <YesNo
            label="Bound by a non-compete"
            value={asBool(f.has_non_compete)}
            onChange={(v) => {
              setFact("has_non_compete", v);
              setFact("has_employment_restrictions", v);
            }}
          />
        </Question>
        <Question label="Are you bound by a non-solicitation agreement (not to contact a former employer's clients or staff)?">
          {yn("has_non_solicitation", "Bound by a non-solicitation agreement")}
        </Question>
        <Question
          label="Do you have a business, client or personal relationship with companies you apply to?"
          hint="For example as a client, vendor, partner or friend of an owner."
        >
          {yn("company_relationships", "Relationship with companies you apply to")}
        </Question>
        <Question label="Have you ever been convicted of a crime (excluding minor traffic violations)?">
          {yn("criminal_conviction", "Convicted of a crime")}
        </Question>
        <Question label="Have you ever been terminated or asked to resign?">
          <YesNo
            label="Terminated or asked to resign"
            value={asBool(f.ever_fired)}
            onChange={(v) => {
              setFact("ever_fired", v);
              setFact("asked_to_resign", v);
              setFact("involuntarily_discharged", v);
            }}
          />
        </Question>
        <Question
          label="Are you a citizen or national of, or have you lived in, a country under U.S. export-control restrictions or sanctions?"
          hint="Defense and technology employers ask this."
        >
          {yn("export_control_restricted_country_history", "Export-control restricted country")}
        </Question>
        <Question label="Do you hold U.S. asylee or refugee status?">
          <YesNo
            label="Asylee or refugee status"
            value={asBool(f.has_asylee_status)}
            onChange={(v) => {
              setFact("has_asylee_status", v);
              setFact("has_refugee_status", v);
            }}
          />
        </Question>
        <Question label="Do you have a valid driver's license?">{yn("valid_drivers_license", "Valid driver's license")}</Question>
        <Question label="Were you referred by a current employee?" hint="Usually No; referral codes are entered per application.">
          {yn("referred_by_employee", "Referred by an employee")}
        </Question>
      </div>

      <h2 className="ax-ob-sub">Self-identification</h2>
      <p className="ax-ob-sub-lead">Voluntary equal-opportunity questions.</p>
      <Question label="How should AXIOM answer self-identification questions?" asked={ASKED.demographics} stack>
        <Segmented
          label="Self-identification answers"
          value={draft.decline_demographics ? "decline" : "answer"}
          options={[
            { value: "decline", label: "Decline to self-identify (recommended)" },
            { value: "answer", label: "Answer with my information" },
          ]}
          onChange={(v) => setTop("decline_demographics", v === "decline")}
        />
      </Question>
      {!draft.decline_demographics && (
        <>
          <Question label="Gender" stack>
            <Choice label="Gender" value={(f.gender as (typeof GENDER)[number]) || ""} options={GENDER} onChange={(v) => setFact("gender", v)} />
          </Question>
          <Question label="Are you Hispanic or Latino?">
            <YesNo label="Hispanic or Latino" value={asBool(f.hispanic_or_latino)} onChange={(v) => setFact("hispanic_or_latino", v)} />
          </Question>
          <CsvInput
            label="Race (as you would select it)"
            hint="For example Asian, Black or African American, White, Two or more races. Separate several with commas."
            values={Array.isArray(f.race_preference) ? (f.race_preference as string[]) : f.race_preference ? [String(f.race_preference)] : []}
            onChange={(list) => setFact("race_preference", list)}
          />
          <Question label="Veteran status" stack>
            <Choice
              label="Veteran status"
              value={asBool(f.veteran) === true ? "Protected veteran" : asBool(f.veteran) === false ? "Not a veteran" : ""}
              options={VETERAN}
              onChange={(v) => setFact("veteran", v === "Protected veteran")}
            />
          </Question>
          <Question label="Disability status" stack>
            <Choice
              label="Disability status"
              value={asBool(f.disability) === true ? "Yes, I have a disability" : asBool(f.disability) === false ? "No disability" : ""}
              options={DISABILITY}
              onChange={(v) => setFact("disability", v === "Yes, I have a disability")}
            />
          </Question>
        </>
      )}
      <Input label="Pronouns (optional)" value={String(f.pronouns || "")} onChange={(e) => setFact("pronouns", e.target.value)} />
    </div>
  );
}

function Toggle({
  checked,
  onChange,
  title,
  detail,
  badge,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  title: string;
  detail: string;
  badge?: string;
}) {
  return (
    <label className="ax-ob-toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>
        <strong>
          {title}
          {badge && <Pill tone="ok">{badge}</Pill>}
        </strong>
        <small>{detail}</small>
      </span>
    </label>
  );
}

const SOURCES = ["LinkedIn", "Company website", "Indeed", "Handshake", "Glassdoor"];

function PermissionsStep({
  draft,
  setTop,
  submit,
  setSubmit,
}: StepProps & { setTop: SetTop; submit: boolean | null; setSubmit: (v: boolean) => void }) {
  const source = draft.application_source;
  return (
    <div className="ax-ob-stack">
      <div className="ax-ob-toggles">
        {submit !== null && (
          <Toggle
            checked={submit}
            onChange={setSubmit}
            title="Submit applications for me"
            badge="Recommended"
            detail="Applications submit once every required answer is verified. When this is off, AXIOM fills each application and stops before the final submit so you can review it."
          />
        )}
        <Toggle
          checked={draft.allow_account_creation}
          onChange={(v) => setTop("allow_account_creation", v)}
          title="Create employer accounts for me"
          badge="Recommended"
          detail="Workday, Oracle and similar portals need an account per employer. AXIOM generates a strong password for each and stores it encrypted. When this is off, those applications stop at the sign-in page."
        />
        <Toggle
          checked={draft.accept_all_application_terms}
          onChange={(v) => {
            setTop("accept_all_application_terms", v);
            setTop("allow_application_consents", v);
          }}
          title="Accept application terms and privacy notices"
          badge="Recommended"
          detail={`Most forms require it before they submit (${ASKED.consent}). Required attestations are signed with your legal name. When this is off, those applications stop for you.`}
        />
        <Toggle
          checked={!draft.accept_required_marketing}
          onChange={(v) => setTop("accept_required_marketing", !v)}
          title="Decline marketing and talent-community opt-ins"
          detail="Newsletters, job alerts and sharing your data for other roles are declined whenever the form allows it."
        />
        <Toggle
          checked={draft.autonomous}
          onChange={(v) => setTop("autonomous", v)}
          title="Apply automatically to strong matches"
          detail="AXIOM discovers jobs, ranks them against your profile, and applies without asking each time."
        />
      </div>
      {draft.autonomous && (
        <Input
          label={`Minimum match score: ${draft.minimum_fit}`}
          hint="Jobs below this score are skipped."
          type="range"
          min={40}
          max={90}
          step={1}
          value={draft.minimum_fit}
          onChange={(e) => setTop("minimum_fit", Number(e.target.value))}
        />
      )}
      <Question label="“How did you hear about us?”" asked={ASKED.source} hint="Used when the form offers this option; otherwise the closest one." stack>
        <Choice label="How did you hear about us" value={source} options={SOURCES.includes(source) || !source ? SOURCES : [...SOURCES, source]} onChange={(v) => setTop("application_source", v)} />
      </Question>
    </div>
  );
}

// ---------- Connections: the AI model key and the inbox for verification codes ----------
function ConnectionsStep({ linked, email, refresh }: { linked: Linked; email: string; refresh: () => Promise<unknown> }) {
  const { model, mailboxes } = linked;
  const [provider, setProvider] = useState("");
  const [key, setKey] = useState("");
  const [box, setBox] = useState({ email: "", app_password: "" });
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ where: "model" | "mail"; ok: boolean; text: string } | null>(null);
  const chosen = provider || (PROVIDERS.some((p) => p.id === model?.provider) ? (model?.provider as string) : "deepseek");
  const keyName = `${chosen}_api_key`;
  const run = async (what: "model" | "mail", job: () => Promise<unknown>, ok: string) => {
    setBusy(what);
    setMsg(null);
    try {
      await job();
      await refresh();
      setMsg({ where: what, ok: true, text: ok });
    } catch (e) {
      setMsg({ where: what, ok: false, text: (e as Error).message });
    } finally {
      setBusy("");
    }
  };
  return (
    <div className="ax-ob-stack">
      <section className="ax-ob-conn" aria-labelledby="conn-model">
        <header>
          <h2 id="conn-model">AI model</h2>
          {model ? <Pill tone={model.model_ready ? "ok" : "warn"} dot>{model.model_ready ? "Ready" : "Key needed"}</Pill> : <Pill>Unknown</Pill>}
        </header>
        <p>Reads job pages and writes your answers. One key is enough; it is stored encrypted and never shown again.</p>
        {model && !model.model_ready && (
          <form
            className="ax-ob-grid"
            onSubmit={(e) => {
              e.preventDefault();
              run("model", () => api("/connections", "PUT", { values: { llm_provider: chosen, [keyName]: key.trim() } }).then(() => setKey("")), "Key saved.");
            }}
          >
            <div className="ax-field">
              <label className="ax-label" htmlFor="ob-provider">
                Provider
              </label>
              <select id="ob-provider" className="ax-control" value={chosen} onChange={(e) => setProvider(e.target.value)}>
                {PROVIDERS.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>
            <Input label="API key" type="password" autoComplete="off" spellCheck={false} placeholder="Paste here" value={key} onChange={(e) => setKey(e.target.value)} />
            <div className="ax-ob-form-end">
              <Button type="submit" variant="primary" size="sm" loading={busy === "model"} disabled={!key.trim()}>
                Save key
              </Button>
            </div>
          </form>
        )}
        {msg?.where === "model" && <p className={msg.ok ? "ax-ob-status" : "ax-ob-error"} role={msg.ok ? "status" : "alert"}>{msg.text}</p>}
      </section>

      <section className="ax-ob-conn" aria-labelledby="conn-mail">
        <header>
          <h2 id="conn-mail">Gmail</h2>
          {mailboxes ? <Pill tone={mailboxes.length ? "ok" : "neutral"} dot>{mailboxes.length ? "Connected" : "Not connected"}</Pill> : <Pill>Unknown</Pill>}
        </header>
        <p>Reads verification codes, sign-in links and receipts. Read-only use; nothing is sent from it.</p>
        {mailboxes && mailboxes.length > 0 && (
          <ul className="ax-ob-boxes">
            {mailboxes.map((b) => (
              <li key={b.id}>
                <strong>{b.email}</strong>
                <small>{b.provider === "gmail_oauth" ? "Google sign-in" : "app password"}</small>
              </li>
            ))}
          </ul>
        )}
        <form
          className="ax-ob-grid"
          onSubmit={(e) => {
            e.preventDefault();
            run("mail", () => api("/mail/imap", "POST", { email: box.email || email, app_password: box.app_password }).then(() => setBox({ email: "", app_password: "" })), "Gmail connected.");
          }}
        >
          <Input label={mailboxes?.length ? "Connect another Gmail" : "Gmail address"} type="email" autoComplete="off" placeholder={email || "you@gmail.com"} hint="Empty: the email in your profile." value={box.email} onChange={(e) => setBox({ ...box, email: e.target.value })} />
          <Input
            label="Gmail app password"
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="xxxx xxxx xxxx xxxx"
            hint={
              <>
                16 letters from{" "}
                <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer">
                  myaccount.google.com/apppasswords
                </a>{" "}
                (needs 2-Step Verification).
              </>
            }
            value={box.app_password}
            onChange={(e) => setBox({ ...box, app_password: e.target.value })}
          />
          <div className="ax-ob-form-end">
            <Button type="submit" size="sm" loading={busy === "mail"} disabled={!box.app_password.trim()}>
              Connect Gmail
            </Button>
          </div>
        </form>
        {msg?.where === "mail" && <p className={msg.ok ? "ax-ob-status" : "ax-ob-error"} role={msg.ok ? "status" : "alert"}>{msg.text}</p>}
      </section>
      <p className="ax-ob-note">Telegram alerts, captcha solving and extra job sources are in Setup › Connections whenever you want them.</p>
    </div>
  );
}

// ---------- Optional questionnaires: the same modules as Setup › Knowledge, embedded ----------
function QuestionnairesStep({ onModule }: { onModule: (m: Module) => void }) {
  const { data, error, reload, setModule } = useQuestionnaires();
  const [open, setOpen] = useState("");
  const module = data?.modules.find((m) => m.id === open);
  const close = () => {
    setOpen("");
    document.querySelector(".ax-screen")?.scrollTo({ top: 0 });
  };
  if (error && !data) return <Unavailable error={error} retry={reload} />;
  if (!data) return <Loading label="Loading the questionnaires…" />;
  if (module)
    return (
      <ModuleForm
        module={module}
        onModule={(m) => {
          setModule(m);
          onModule(m);
        }}
        onClose={close}
        closeLabel="Back to the list"
      />
    );
  return (
    <div className="ax-ob-stack">
      <p className="ax-ob-note">
        Nothing here is required. Each one is saved as you answer and used by every application; change or clear any answer
        later in Setup › Knowledge.
      </p>
      <ModuleList data={data} onOpen={setOpen} />
      <div className="ax-ob-overall">
        <Meter percent={data.completion.percent} label="Questionnaires answered" />
        <small>
          {data.completion.answered} of {data.completion.total} answered
        </small>
      </div>
    </div>
  );
}

function yesno(v: unknown) {
  const b = asBool(v);
  return b === true ? "Yes" : b === false ? "No" : "Not answered";
}

const STOPS = "Off: those applications stop for you";
const SUBMIT_OFF = "Off: applications stop before submitting";
const NOT_CONNECTED = "Not connected";
const WARN = new Set(["Missing", "Not answered", STOPS, SUBMIT_OFF, NOT_CONNECTED]);

function ReviewStep({
  draft,
  go,
  submit,
  linked,
  summary,
}: StepProps & {
  go: (id: StepId) => void;
  submit: boolean | null;
  linked: Linked;
  summary: { id: string; title: string; answered: number; total: number }[] | null;
}) {
  const f = foldFacts(draft.facts);
  const groups = useMemo(
    () => [
      {
        step: "profile" as StepId,
        title: "Profile",
        rows: [
          ["Name", [draft.first_name, draft.last_name].filter(Boolean).join(" ") || "Missing"],
          ["Email", draft.email || "Missing"],
          ["Phone", [draft.phone_country_code, draft.phone].filter(Boolean).join(" ") || "Missing"],
          ["Address", [draft.address.line1, draft.address.city, draft.address.state, draft.address.postal_code].filter(Boolean).join(", ") || "Missing"],
          ["LinkedIn", draft.linkedin || "—"],
        ],
      },
      {
        step: "authorization" as StepId,
        title: "Work authorization",
        rows: [
          ["Authorized to work in the US", yesno(f.work_authorization_us)],
          ["Requires sponsorship", yesno(f.requires_future_sponsorship_us)],
          ["Status", String(f.current_immigration_status_us || "Not answered")],
          ["18 or older", yesno(f.over_18)],
        ],
      },
      {
        step: "preferences" as StepId,
        title: "Preferences",
        rows: [
          ["Roles", draft.target_roles.join(", ") || "Missing"],
          ["Locations", draft.target_locations.join(", ") || "Anywhere in the USA"],
          ["Relocate", yesno(f.willing_to_relocate)],
          ["Start", draft.availability || "Not answered"],
          ["Salary", draft.salary_expectation ? "$" + Number(draft.salary_expectation).toLocaleString() : "Negotiable"],
        ],
      },
      {
        step: "screening" as StepId,
        title: "Screening",
        rows: [
          ["Relatives at employer", yesno(f.has_relatives_at_employer)],
          ["Government employment (5 yrs)", yesno(f.government_employment_history)],
          ["Worked for other employers you apply to", yesno(f.previously_employed_here)],
          ["Non-compete", yesno(f.has_non_compete)],
          ["Non-solicitation", yesno(f.has_non_solicitation)],
          ["Company relationships", yesno(f.company_relationships)],
          ["Criminal conviction", yesno(f.criminal_conviction)],
          ["Export-control country", yesno(f.export_control_restricted_country_history)],
          ["Asylee or refugee", yesno(f.has_asylee_status)],
          ["Self-identification", draft.decline_demographics ? "Decline to self-identify" : "Use my information"],
        ],
      },
      {
        step: "permissions" as StepId,
        title: "Permissions",
        rows: [
          ...(submit === null ? [] : [["Submit applications", submit ? "On" : SUBMIT_OFF]]),
          ["Create employer accounts", draft.allow_account_creation ? "Allowed" : STOPS],
          ["Accept application terms", draft.accept_all_application_terms ? "Allowed" : STOPS],
          ["Apply automatically", draft.autonomous ? `On, minimum score ${draft.minimum_fit}` : "Off"],
        ],
      },
      {
        step: "connections" as StepId,
        title: "Connections",
        rows: [
          ["AI model", linked.model ? (linked.model.model_ready ? "Ready" : NOT_CONNECTED) : "Unknown"],
          ["Gmail", linked.mailboxes ? (linked.mailboxes.length ? linked.mailboxes.map((b) => b.email).join(", ") : NOT_CONNECTED) : "Unknown"],
        ],
      },
    ],
    [draft, f, submit, linked],
  );
  const open = groups.flatMap((g) => g.rows).filter(([, v]) => WARN.has(v)).length;
  return (
    <div className="ax-ob-review">
      {open > 0 && (
        <p className="ax-ob-note">
          {open === 1 ? "1 item needs" : `${open} items need`} attention. You can finish now: when a form asks for something
          missing, AXIOM uses your profile if the answer is certain, otherwise it pauses that application and asks you.
        </p>
      )}
      {groups.map((g) => (
        <section key={g.title} className="ax-ob-group">
          <div className="ax-ob-group-head">
            <h2>{g.title}</h2>
            <button type="button" className="ax-link" onClick={() => go(g.step)}>
              Edit
            </button>
          </div>
          <dl>
            {g.rows.map(([k, v]) => (
              <React.Fragment key={k}>
                <dt>{k}</dt>
                <dd className={WARN.has(v) ? "is-warn" : ""}>{v}</dd>
              </React.Fragment>
            ))}
          </dl>
        </section>
      ))}
      <section className="ax-ob-group">
        <div className="ax-ob-group-head">
          <h2>
            Questionnaires <Pill tone="neutral">Optional</Pill>
          </h2>
          <button type="button" className="ax-link" onClick={() => go("questionnaires")}>
            {summary && summary.some((m) => m.answered) ? "Edit" : "Start"}
          </button>
        </div>
        <dl>
          {(summary || []).map((m) => (
            <React.Fragment key={m.id}>
              <dt>{m.title}</dt>
              <dd>{m.answered ? `${m.answered} of ${m.total} answered` : "Skipped"}</dd>
            </React.Fragment>
          ))}
          {!summary && (
            <>
              <dt>Questionnaires</dt>
              <dd>Skipped</dd>
            </>
          )}
        </dl>
      </section>
    </div>
  );
}
