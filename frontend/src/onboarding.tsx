import React, { useCallback, useEffect, useRef, useState } from "react";
import { ConsentWords } from "./permissions";
import { ArrowLeft, ArrowRight, Check as Tick, FileText, Mail, SlidersHorizontal, Sparkles, UserRound, X } from "lucide-react";
import { api, ApiError } from "./api";
import { routeUrl } from "./context";
import { Wordmark } from "./shell/Brand";
import { resumeFileRefusal } from "./format";
import { SwitchRow } from "./setup-kit";
import { MainPassword } from "./mainpassword";
import { YesNo } from "./qform";
import { Button, Dropzone, ErrorLine, External, Input, Loading, Monogram, Pill, Segmented, Select } from "./ui/index";
import type { EducationEntry, Profile, WorkEntry } from "./types";
import "./styles/onboarding.css";

// The sign-in screens and Settings › Your account live in account.tsx.

// "Finish later" (guide.ts, so the shell reads it without loading this module).

// ---------------------------------------------------------------------------
// Setup guide (DESIGN.md §7): seven short steps between a welcome and a finish. Each one is a task a person can do in under
// two minutes, with the usual answer already chosen where one exists. Progress is saved on the server (/api/onboarding), so it
// resumes where it stopped. The first screen of a new installation: it has to read as finished.
// ---------------------------------------------------------------------------

type StepId = "welcome" | "connections" | "resume" | "profile" | "authorization" | "preferences" | "automation" | "connections" | "ready";
// `server` is the id the backend stores (ONBOARDING_STEPS); steps the server has no id of its own for share a neighbour's.
// `name` labels the step in the progress line, `minutes` is what it usually takes (the "about N min left" is their sum),
// `skip` is the words on the button that moves on without doing it.
type Step = { id: StepId; name: string; title: string; lead?: string; server: string; minutes: number; skip?: string };
const STEPS: Step[] = [
  {
    id: "welcome",
    name: "Welcome",
    title: "Welcome to AXIOM",
    lead: "AXIOM finds jobs that fit you, fills in each application and sends it. It asks you only when it isn't sure.",
    server: "resume",
    minutes: 0,
  },
  {
    id: "connections",
    name: "Connect",
    title: "Connect AI and Gmail",
    lead: "AXIOM needs an AI key to read job pages and write answers, and your Gmail to read the codes employers email. Both stay on this Mac. This is the hardest step, and you only do it once.",
    // The AI key comes before the resume because reading the resume is the first thing that uses it. It shares the welcome's saved
    // step, so a guide closed here reopens at the resume.
    server: "resume",
    minutes: 3,
    skip: "Set up later",
  },
  {
    id: "resume",
    name: "Resume",
    title: "Start with your resume",
    lead: "We read it once to fill in most of the next steps. Check what it found before you go on.",
    server: "resume",
    minutes: 0.75,
    skip: "I'll type it instead",
  },
  {
    id: "profile",
    name: "Contact",
    title: "Contact details",
    lead: "Filled into every application exactly as written.",
    server: "contact",
    minutes: 1,
  },
  {
    id: "authorization",
    name: "Eligibility",
    title: "Work eligibility",
    lead: "The questions nearly every application asks.",
    server: "authorization",
    minutes: 1,
  },
  {
    id: "preferences",
    name: "Search",
    title: "What you're looking for",
    lead: "AXIOM looks for these jobs. You can change them any time.",
    server: "preferences",
    minutes: 1,
  },
  {
    id: "automation",
    name: "Automation",
    title: "How AXIOM works for you",
    lead: "What it may do without asking. You can change any of this later in Settings.",
    server: "permissions",
    minutes: 0.5,
  },
  {
    id: "ready",
    name: "Ready",
    title: "AXIOM is ready",
    lead: "Your profile is saved. Press Start on the next screen. AXIOM looks for jobs first, which can take a while, then applies one at a time. You can close this window while it works.",
    server: "review",
    minutes: 0,
  },
];
// The steps in the progress line: everything between the welcome and the finish.
const WORK = STEPS.slice(1, -1);
const FIRST_WORK = 1;

const EDITS_PROFILE = new Set<StepId>(["resume", "profile", "authorization", "preferences", "automation"]);

// Which step a stored server step reopens. Background and self-identification became part of Eligibility.
const OPEN_AT: Record<string, StepId> = {
  resume: "resume",
  contact: "profile",
  authorization: "authorization",
  preferences: "preferences",
  background: "authorization",
  identity: "authorization",
  permissions: "automation",
  questionnaires: "ready",
  review: "ready",
};

type Draft = Profile;

// Shown and switchable on the permissions step, on by default like the other recommended choices. Without them every form that
// needs an exact day for a month-only date, or offers no listed source, stopped to ask: a new person met what the owner had
// long since answered once (2026-11-01). AXIOM really does find its jobs on employers' sites, job boards and web searches.
const ROUTINE_ANSWERS: NonNullable<Profile["answer_rules"]> = {
  history_day: "first",
  source_order: ["company website", "job board", "internet search"],
};

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
  admit_by_title: false,
  accept_experience_years: 0,
  answer_rules: ROUTINE_ANSWERS,
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
  // The server's empty rules mean nobody chose yet: show the recommended ones, which this step saves once confirmed.
  if (!Object.keys(draft.answer_rules || {}).length) draft.answer_rules = ROUTINE_ANSWERS;
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

/** One question: the prompt and its control. `stack` puts the control under a long prompt. */
function Question({
  label,
  hint,
  stack,
  children,
}: {
  label: string;
  hint?: React.ReactNode;
  stack?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={"ax-ob-q" + (stack ? " is-stack" : "")}>
      <div className="ax-ob-q-text">
        <span>{label}</span>
        {hint && <small className="ax-hint">{hint}</small>}
      </div>
      <div className="ax-ob-q-control">{children}</div>
    </div>
  );
}

/** A named part of a step: a section title, an optional line under it, then its fields. */
function Section({ title, hint, children }: { title: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="ax-ob-sect">
      <header>
        <h2>{title}</h2>
        {hint && <p className="ax-hint">{hint}</p>}
      </header>
      {children}
    </section>
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
      {(values.length > 0 || suggestions.length > 0) && (
        <div className="ax-ob-chips">
          {values.map((v) => (
            <button key={v} type="button" className="ax-ob-chip is-on" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>
              {v}
              <X size={12} aria-hidden="true" />
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

/** A folded part of a step that most people leave alone: its name, what it holds, and the words on the right. */
function Optional({ title, meta, children }: { title: string; meta?: React.ReactNode; children: React.ReactNode }) {
  return (
    <details className="ax-ob-fold">
      <summary>
        <strong>{title}</strong>
        {meta && <span className="ax-hint">{meta}</span>}
      </summary>
      <div className="ax-ob-fold-body">{children}</div>
    </details>
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
    // These go onto every application exactly as written, and the employer's reply goes to that address.
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(d.email.trim())) return "That email doesn't look right. Employers send their replies there, so check it.";
    if (d.phone.replace(/\D/g, "").length < 7) return "That phone number doesn't look right. Check it, with the area code.";
  }
  if (step === "preferences" && !d.target_roles.length) return "Add at least one role so AXIOM knows what to look for.";
  return "";
}

// What the last resume read gave, so reading a different resume replaces it rather than stacking on top (work and schooling are only
// filled when empty; without this, choosing another resume left the first one's jobs in place under a message saying the new
// ones were found). Entries the person has since edited no longer match, so they are kept.
let lastRead: { projects?: { name: string; description: string }[] } & Partial<Draft> | null = null;
const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

const US_STATES = new Set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split(" "));

// What a resume read adds beyond the profile's fields. The notice says when a long resume was read only in part.
type Extract = Partial<Draft> & { projects?: { name: string; description: string }[]; notice?: string };

/** A resume's projects as the notes Profile shows under Work: "Project: name", then its bullets, which is how tailoring reads them (evidence.py). */
const projectNotes = (x: Extract | null) =>
  (x?.projects ?? []).filter((p) => p.name?.trim()).map((p) => `Project: ${p.name.trim()}\n${(p.description || "").trim()}`.trim());

function mergeExtract(draft: Draft, x: Extract, before: Extract | null): Draft {
  const next = { ...draft };
  // The same replace-unless-edited as work and schooling below.
  const stale = new Set(projectNotes(before));
  const kept = (next.evidence || []).filter((e) => !stale.has(e.text));
  const added = projectNotes(x).filter((text) => !kept.some((e) => e.text === text));
  const used = new Set(kept.map((e) => e.id));
  next.evidence = [...kept];
  for (const text of added) {
    let n = 1;
    while (used.has(`resume-project-${n}`)) n += 1;
    used.add(`resume-project-${n}`);
    next.evidence.push({ id: `resume-project-${n}`, text });
  }
  for (const key of ["first_name", "last_name", "email", "phone", "location", "linkedin", "github", "website"] as const) {
    const unchanged = before && String(next[key] || "") === String(before[key] || "");
    if ((!String(next[key] || "").trim() || unchanged) && x[key]) next[key] = x[key] as string;
  }
  if (x.work?.length && (!next.work.length || (before && same(next.work, before.work)))) next.work = x.work as WorkEntry[];
  if (x.education?.length && (!next.education.length || (before && same(next.education, before.education))))
    next.education = x.education as EducationEntry[];
  if (x.skills?.length) {
    const old = new Set(before?.skills ?? []);
    next.skills = Array.from(new Set([...(next.skills || []).filter((k) => !old.has(k)), ...x.skills]));
  }
  // The Contact step has a field for the country code: a resume's "+1 (555) 010-0100" would otherwise send "+1" twice.
  const code = (next.phone_country_code || "").trim();
  if (code && next.phone !== draft.phone && next.phone.trim().startsWith(code)) next.phone = next.phone.trim().slice(code.length).replace(/^[\s.-]+/, "");
  // The resume lists "Austin, TX": the Contact step asks for city and state, so hand it those instead of leaving them blank.
  const place = /^\s*([^,]+),\s*([A-Z]{2})(?:\s+(\d{5}(?:-\d{4})?))?\s*(?:,|$)/.exec(x.location || "");
  if (place && US_STATES.has(place[2]!) && !next.address.city.trim() && !next.address.state.trim())
    next.address = { ...next.address, city: place[1]!.trim(), state: place[2]!, postal_code: next.address.postal_code.trim() || place[3] || "" };
  if (!next.name.trim() || (before && next.name === [before.first_name, before.last_name].filter(Boolean).join(" ")))
    next.name = [next.first_name, next.last_name].filter(Boolean).join(" ");
  return next;
}

// ---------- connections state (the Connections step and the finish read the same copy) ----------
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
type Summary = { id: string; title: string; answered: number; total: number }[];

/** "About 4 min left": the minutes of every step from this one to the last. */
function timeLeft(at: number): string {
  if (at <= 0) return "About " + Math.ceil(WORK.reduce((n, s) => n + s.minutes, 0)) + " minutes";
  const left = STEPS.slice(at, -1).reduce((n, s) => n + s.minutes, 0);
  return left <= 0.5 ? "Almost done" : `About ${Math.ceil(left)} min left`;
}

export function Onboarding({ onDone, onExit }: { onDone: () => void; onExit: () => void }) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [account, setAccount] = useState<Account>({});
  const [at, setAt] = useState(0);
  // The furthest step reached: the progress line lets you go back to any of these, and forward again.
  const [reached, setReached] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  // Automatic submission lives in the installation's controls, not the profile.
  const [submit, setSubmit] = useState<{ saved: boolean; value: boolean } | null>(null);
  const [linked, setLinked] = useState<Linked>({ model: null, mailboxes: null });
  const [summary, setSummary] = useState<Summary | null>(null);
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
      api<{ step: string; updated_at?: string; questionnaires?: { modules: Summary } }>("/onboarding"),
      api<{ auto_submit: boolean }>("/control").catch(() => null),
      api<Account>("/account").catch(() => ({}) as Account),
    ])
      .then(([profile, state, control, who]) => {
        setDraft(withDefaults(profile));
        setAccount(who);
        if (control) setSubmit({ saved: control.auto_submit, value: control.auto_submit });
        setSummary(state.questionnaires?.modules ?? null);
        // A brand-new installation has never saved a step: it starts at the welcome.
        const id = state.updated_at ? OPEN_AT[state.step] || "resume" : "welcome";
        const index = Math.max(0, STEPS.findIndex((s) => s.id === id));
        setAt(index);
        setReached(index);
      })
      .catch((e) => setError((e as Error).message));
    refreshLinked();
  }, [refreshLinked]);

  // The Gmail connected on the first step is the likeliest contact email: don't make anyone type it twice. It stays editable.
  const connectedEmail = linked.mailboxes?.[0]?.email;
  // Also when the profile arrives after the mailbox does: reopened on a later step, the email stayed empty (2026-11-01).
  const drafted = !!draft;
  useEffect(() => {
    if (connectedEmail) setDraft((d) => (d && !d.email.trim() ? { ...d, email: connectedEmail } : d));
  }, [connectedEmail, drafted]);

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
      <main className="ax-ob is-boot">
        <Wordmark width={108} />
        <div className="ax-boot-error" role="alert">
          <p>Could not open the setup guide: {error}</p>
          <span className="ax-ask">
            <Button variant="primary" onClick={() => window.location.reload()}>
              Try again
            </Button>
            <Button variant="ghost" onClick={onExit}>
              Skip for now
            </Button>
          </span>
        </div>
      </main>
    );
  if (!draft)
    return (
      <main className="ax-ob is-boot">
        <Wordmark width={108} />
        <Loading label="Opening setup…" />
      </main>
    );

  const current = STEPS[at];
  const first = at === 0;
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

  async function persist(toIndex: number, completed = false, after?: () => void) {
    if (!draft) return;
    // Going back never needs the step you are leaving to be complete.
    const problem = toIndex > at ? stepProblem(current.id, draft) : "";
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
        // Only this switch: writing back `paused: false` counts as pressing Resume, which delivers every queued run again.
        await api("/control", "PUT", { auto_submit: submit.value });
        setSubmit({ saved: submit.value, value: submit.value });
      }
      const state = await api<{ questionnaires?: { modules: Summary } }>("/onboarding", "PUT", {
        step: STEPS[Math.min(toIndex, STEPS.length - 1)].server,
        completed,
      });
      if (state?.questionnaires?.modules) setSummary(state.questionnaires.modules);
      if (after) after();
      else if (completed) onDone();
      else go(toIndex);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const next = () => (last ? persist(at, true) : persist(at + 1));
  // The finish can open Profile › Knowledge instead of the Overview: the guide is complete either way.
  const finishAt = () => persist(at, true, () => window.location.assign(routeUrl({ view: "profile", tab: "knowledge" })));
  const hello = (account.name || "").trim().split(/\s+/)[0];
  // The finish says "ready" only when nothing AXIOM needs is missing.
  const lines = last ? readyLines(draft, submit?.value ?? null, linked) : [];
  const unfinished = lines.filter((l) => !l.ok && !l.optional).length;
  const title = current.id === "welcome" && hello ? `Welcome, ${hello}` : last && unfinished ? "Almost ready" : current.title;
  const lead = last && unfinished ? "Your profile is saved. You can finish the rest here, or any time later, then press Start on the next screen." : current.lead;
  const stepNo = at - FIRST_WORK + 1;
  // The resume step moves on once something was read from a resume; "I'll type it instead" is the way past it without one.
  const waiting = current.id === "resume" && !draft.work.length && !draft.education.length && !draft.skills.length;

  return (
    <main className="ax-ob">
      <header className="ax-ob-top">
        <div className="ax-brand">
          <Wordmark />
        </div>
        {!last && (
          <Button
            variant="ghost"
            size="sm"
            // What is typed on this step is kept too: a dozen fields lost to "Finish later" is a reason to leave for good.
            onClick={() => (first ? onExit() : persist(at, false, onExit))}
            title="What you've entered is kept. Continue any time from the banner or Settings › Your account."
          >
            Finish later
          </Button>
        )}
      </header>

      <form
        className="ax-ob-form"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          if (!saving) next();
        }}
      >
        <div className="ax-ob-col">
          <nav aria-label="Setup steps" className="ax-ob-progress">
            <p className="ax-ob-caption" role="status">
              <span>{first ? `${WORK.length} short steps` : last ? (unfinished ? "Nearly done" : "Setup complete") : `Step ${stepNo} of ${WORK.length}`}</span>
              {!last && <span>{timeLeft(at)}</span>}
            </p>
            <ol className="ax-ob-steps">
              {WORK.map((s, k) => {
                const i = k + FIRST_WORK;
                return (
                  <li key={s.id} aria-current={i === at ? "step" : undefined} className={i < at ? "is-done" : i === at ? "is-now" : ""}>
                    <button
                      type="button"
                      aria-label={`${s.name}, step ${k + 1} of ${WORK.length}`}
                      title={s.name}
                      disabled={i > reached || saving}
                      onClick={() => go(i)}
                    >
                      <i aria-hidden="true" />
                      <span aria-hidden="true">{s.name}</span>
                    </button>
                  </li>
                );
              })}
            </ol>
          </nav>

          <section className={"ax-ob-card" + (first || last ? " is-hero" : "")} aria-labelledby="ob-title">
            <header>
              {(first || (last && unfinished > 0)) && (
                <span className="ax-ob-seal" aria-hidden="true">
                  <svg className="ax-mark" viewBox="0 0 16 16" width="26" height="26">
                    <circle className="o" cx="8" cy="8" r="6.6" />
                    <circle className="lamp" cx="8" cy="8" r="2.6" />
                  </svg>
                </span>
              )}
              {last && unfinished === 0 && (
                <span className="ax-ob-seal is-done" aria-hidden="true">
                  <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M20 6 9 17l-5-5" />
                  </svg>
                </span>
              )}
              <h1 id="ob-title" ref={heading} tabIndex={-1}>
                {title}
              </h1>
              {lead && <p>{lead}</p>}
            </header>
            <div className="ax-ob-body" key={current.id}>
              {current.id === "welcome" && <WelcomeStep />}
                            {current.id === "resume" && (
                <ResumeStep
                  draft={draft}
                  setDraft={(fn) => setPeople(fn)}
                  modelReady={linked.model ? linked.model.model_ready : null}
                  connect={() => go(STEPS.findIndex((s) => s.id === "connections"))}
                />
              )}
              {current.id === "profile" && <ContactStep draft={draft} setTop={setTop} setAddress={setAddress} />}
              {current.id === "authorization" && <AuthorizationStep draft={draft} setTop={setTop} setFact={setFact} />}
              {current.id === "preferences" && <PreferencesStep draft={draft} setTop={setTop} setFact={setFact} />}
              {current.id === "automation" && (
                <AutomationStep
                  draft={draft}
                  setTop={setTop}
                  submit={submit?.value ?? null}
                  setSubmit={(value) => setSubmit((v) => (v ? { ...v, value } : v))}
                />
              )}
              {current.id === "connections" && <ConnectionsStep linked={linked} email={draft.email} refresh={refreshLinked} />}
              {current.id === "ready" && (
                <ReadyStep
                  lines={lines}
                  go={(id) => go(STEPS.findIndex((s) => s.id === id))}
                  summary={summary}
                  more={finishAt}
                  busy={saving}
                />
              )}
            </div>
            <ErrorLine error={error} />
          </section>
        </div>

        <footer className="ax-ob-bar">
          <div className="ax-ob-bar-in">
            {at > 0 && (
              <Button variant="ghost" icon={<ArrowLeft size={15} aria-hidden="true" />} onClick={() => go(at - 1)} disabled={saving}>
                Back
              </Button>
            )}
            <span className="ax-ob-bar-end">
              {current.skip && (current.id !== "resume" || waiting) && (
                <Button variant="ghost" onClick={() => persist(at + 1)} disabled={saving}>
                  {current.skip}
                </Button>
              )}
              <Button type="submit" variant="primary" loading={saving} disabled={waiting}>
                {first ? "Get started" : last ? "Open AXIOM" : "Continue"}
                {!last && <ArrowRight size={15} aria-hidden="true" />}
              </Button>
            </span>
          </div>
        </footer>
      </form>
    </main>
  );
}

type StepProps = { draft: Draft };
type SetTop = <K extends keyof Draft>(key: K, value: Draft[K]) => void;
type SetFact = (key: string, value: unknown) => void;

// ---------- Welcome ----------
const PLAN = [
  { icon: Sparkles, title: "Connect your AI and Gmail", line: "An AI key from OpenRouter and a Gmail address. Setup shows you how to get both." },
  { icon: FileText, title: "Add your resume", line: "We read it once and fill in most of what follows." },
  { icon: UserRound, title: "Confirm your details", line: "Contact, work eligibility and the roles you want." },
  { icon: SlidersHorizontal, title: "Choose how much AXIOM does", line: "Send applications for you, or let you review each one first." },
];

function WelcomeStep() {
  return (
    <>
      <ol className="ax-ob-plan" aria-label="What setup covers">
        {PLAN.map((p) => (
          <li key={p.title}>
            <span className="ax-ob-tile">
              <p.icon size={16} aria-hidden="true" />
            </span>
            <span>
              <strong>{p.title}</strong>
              <small>{p.line}</small>
            </span>
          </li>
        ))}
      </ol>
      <p className="ax-hint">
        AXIOM applies to jobs in your name, using the answers you give it, so what it sends is yours to stand behind. Nothing goes to an employer until
        you allow it, and setup saves as you go.
      </p>
    </>
  );
}

/** "2022-06 to now": how long a job or course ran, as the resume read it. */
function span(start?: string, end?: string, current?: boolean) {
  const from = (start || "").trim();
  const to = current ? "now" : (end || "").trim();
  return from && to ? `${from} to ${to}` : from ? `from ${from}` : to ? `to ${to}` : "";
}

// ---------- Resume ----------
function ResumeStep({
  draft,
  setDraft,
  modelReady,
  connect,
}: StepProps & { setDraft: (fn: (d: Draft) => Draft) => void; modelReady: boolean | null; connect: () => void }) {
  const [resumes, setResumes] = useState<{ id: string; name: string }[] | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [state, setState] = useState<"idle" | "uploading" | "reading" | "done" | "error">("idle");
  const [message, setMessage] = useState("");
  // What AXIOM couldn't read of this file (a page that is a picture, a very long resume), said beside what it did find.
  const [cautions, setCautions] = useState<string[]>([]);
  // The resume a failed read can be tried on again, without choosing the file again.
  const [readId, setReadId] = useState("");

  useEffect(() => {
    api<{ id: string; name: string }[]>("/resumes").then(setResumes).catch(() => setResumes([]));
  }, []);

  async function read(resumeId: string) {
    setReadId(resumeId);
    setState("reading");
    setMessage("Reading your resume…");
    try {
      // Applications attach the newest resume unless a job chose another; picking one here makes it that one.
      await api(`/resumes/${encodeURIComponent(resumeId)}/prefer`, "POST").catch(() => null);
      const extracted = await api<Extract>("/profile/extract?resume_id=" + encodeURIComponent(resumeId), "POST");
      const before = lastRead;
      lastRead = extracted;
      setDraft((d) => mergeExtract(d, extracted, before));
      const parts = [
        extracted.work?.length ? `${extracted.work.length} role${extracted.work.length === 1 ? "" : "s"}` : "",
        extracted.education?.length ? `${extracted.education.length} school${extracted.education.length === 1 ? "" : "s"}` : "",
        extracted.projects?.length ? `${extracted.projects.length} project${extracted.projects.length === 1 ? "" : "s"}` : "",
        extracted.skills?.length ? `${extracted.skills.length} skills` : "",
      ].filter(Boolean);
      if (extracted.notice) setCautions((list) => [...list, extracted.notice!]);
      // Read, and nothing in it: not a success, and the person has to know that before they rely on it.
      setState(parts.length ? "done" : "error");
      setMessage(
        parts.length
          ? "Found " + parts.join(", ") + ". Check them below."
          : "AXIOM read this PDF and found no jobs, schools or skills in it. Employers would still get this file as it is. Try another copy of your resume, or type your details instead.",
      );
    } catch (e) {
      setState("error");
      // The server's words for a missing key name a settings file; here the fix is one step back.
      const raw = (e as Error).message;
      setMessage(
        /No API key/i.test(raw)
          ? "AXIOM needs your AI key to read the resume. Your resume is saved: connect the key, or type your details on the next steps."
          : `${raw} Your resume is saved; you can fill in the next steps by hand.`,
      );
    }
  }

  async function upload(chosen: File) {
    setFile(chosen);
    setCautions([]);
    setState("uploading");
    setMessage("Uploading…");
    const form = new FormData();
    form.append("file", chosen);
    form.append("name", chosen.name.replace(/\.pdf$/i, "").slice(0, 100) || "Resume");
    form.append("roles", "");
    try {
      // ?reuse: the same file again (after a failed read, or a guide closed half way) is that resume, not a mistake.
      const row = await api<{ id: string; name: string; warnings?: string[] }>("/resumes?reuse=1", "POST", form);
      setResumes((list) => [row, ...(list || []).filter((r) => r.id !== row.id)]);
      setCautions(row.warnings ?? []);
      await read(row.id);
    } catch (e) {
      setReadId("");
      setState("error");
      setMessage((e as Error).message);
    }
  }

  const busy = state === "uploading" || state === "reading";
  // The notes a resume's projects became: the person checks these with the rest.
  const projects = (draft.evidence || []).filter((e) => e.id.startsWith("resume-project-"));
  const found = draft.work.length > 0 || draft.education.length > 0 || projects.length > 0 || draft.skills.length > 0;
  return (
    <>
      <Dropzone
        file={file}
        onFile={(f) => {
          if (f) upload(f);
          else {
            setFile(null);
            setCautions([]);
            setState("idle");
            setMessage("");
          }
        }}
        accept="application/pdf,.pdf"
        title="Add your resume"
        hint="Drop your resume PDF here, or click to choose. It is attached to applications exactly as uploaded."
        refusal={resumeFileRefusal}
        label="Resume PDF"
        disabled={busy}
      />
      {modelReady === false && state === "idle" && (
        <p className="ax-hint">
          Reading a resume needs your AI key, which isn't connected yet.{" "}
          <button type="button" className="ax-link" onClick={connect}>
            Connect it
          </button>
        </p>
      )}
      {busy && <Loading label={message} />}
      {!busy && message && (
        <p className={state === "error" ? "ax-error" : "ax-ob-note"} role={state === "error" ? "alert" : "status"}>
          {state === "done" && <Tick size={14} aria-hidden="true" />}
          {message}{" "}
          {state === "error" && /AI key/.test(message) && (
            <button type="button" className="ax-link" onClick={connect}>
              Connect it
            </button>
          )}
          {state === "error" && readId && !/AI key/.test(message) && (
            <button type="button" className="ax-link" onClick={() => read(readId)}>
              Try again
            </button>
          )}
        </p>
      )}
      {!busy &&
        cautions.map((c) => (
          <p key={c} className="ax-error" role="alert">
            {c}
          </p>
        ))}
      {!file && resumes && resumes.length > 0 && (
        <Section title="Or use one you've already added">
          <ul className="ax-rows" aria-label="Uploaded resumes">
            {resumes.slice(0, 4).map((r) => (
              <li key={r.id} className="ax-row">
                <span className="ax-row-main">
                  <strong>{r.name}</strong>
                </span>
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() => {
                    setCautions([]);
                    read(r.id);
                  }}
                >
                  Use this one
                </Button>
              </li>
            ))}
          </ul>
        </Section>
      )}
      {found && (
        <Section title="What we found" hint="Remove anything that was read wrongly. You can edit the rest in Profile, under Work and education, after setup.">
          <ul className="ax-ob-found" aria-label="Found in your resume">
            {draft.work.map((w, i) => (
              <li key={"w" + i}>
                <Monogram name={w.company} size="md" />
                <span className="ax-ob-found-text">
                  <strong>{w.title || "Role"}</strong>
                  <small>{[w.company, span(w.start, w.end, w.current)].filter(Boolean).join(" · ")}</small>
                </span>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => setDraft((d) => ({ ...d, work: d.work.filter((_, k) => k !== i) }))}>
                  Remove<span className="sr-only"> {w.title || "role"} at {w.company}</span>
                </Button>
              </li>
            ))}
            {draft.education.map((e, i) => (
              <li key={"e" + i}>
                <Monogram name={e.school} size="md" />
                <span className="ax-ob-found-text">
                  <strong>
                    {e.degree}
                    {e.field_of_study ? " · " + e.field_of_study : ""}
                  </strong>
                  <small>{[e.school, span(e.start, e.end, false)].filter(Boolean).join(" · ")}</small>
                </span>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => setDraft((d) => ({ ...d, education: d.education.filter((_, k) => k !== i) }))}>
                  Remove<span className="sr-only"> {e.degree || "school"} at {e.school}</span>
                </Button>
              </li>
            ))}
            {projects.map((n) => {
              const name = n.text.replace(/^Project:\s*/, "").split("\n")[0] || "Project";
              return (
                <li key={n.id}>
                  <Monogram name={name} size="md" />
                  <span className="ax-ob-found-text">
                    <strong>{name}</strong>
                    <small>Project</small>
                  </span>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => setDraft((d) => ({ ...d, evidence: d.evidence.filter((x) => x.id !== n.id) }))}>
                    Remove<span className="sr-only"> project {name}</span>
                  </Button>
                </li>
              );
            })}
            {draft.skills.length > 0 && (
              <li>
                <Monogram name="Skills" size="md" />
                <span className="ax-ob-found-text">
                  <strong>Skills</strong>
                  <small>{draft.skills.join(", ")}</small>
                </span>
              </li>
            )}
          </ul>
        </Section>
      )}
    </>
  );
}

// ---------- Contact ----------
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
    <>
      <Section title="Name">
        <div className="ax-ob-grid">
          <Input label="Legal first name" {...t("first_name")} autoComplete="given-name" />
          <Input label="Legal last name" {...t("last_name")} autoComplete="family-name" />
          <Input label="Preferred first name (optional)" {...t("preferred_name")} />
        </div>
      </Section>
      <Section title="How to reach you">
        <div className="ax-ob-grid">
          <Input label="Email" type="email" className="is-wide" {...t("email")} autoComplete="email" />
          <div className="ax-ob-phone is-wide">
            <Input label="Code" placeholder="+1" {...t("phone_country_code")} />
            <Input label="Phone" type="tel" {...t("phone")} autoComplete="tel-national" />
          </div>
        </div>
        <Question label="Phone type">
          <Choice label="Phone type" value={draft.phone_type as "Mobile"} options={["Mobile", "Home", "Work"] as const} onChange={(v) => setTop("phone_type", v)} />
        </Question>
      </Section>
      <Section title="Address">
        <div className="ax-ob-grid">
          <Input label="Street address" className="is-wide" {...a("line1")} autoComplete="address-line1" />
          <Input label="City" {...a("city")} autoComplete="address-level2" />
          <Input label="State" {...a("state")} autoComplete="address-level1" />
          <Input label="Postal code" {...a("postal_code")} autoComplete="postal-code" />
          <Input label="Country" {...a("country")} autoComplete="country-name" />
        </div>
      </Section>
      <Section title="Links" hint="Optional. Used when a form asks for them.">
        <div className="ax-ob-grid">
          <Input label="LinkedIn" placeholder="https://www.linkedin.com/in/…" {...t("linkedin")} />
          <Input label="GitHub" placeholder="https://github.com/…" {...t("github")} />
          <Input label="Portfolio or website" className="is-wide" {...t("website")} />
        </div>
      </Section>
    </>
  );
}

// ---------- Eligibility: what nearly every form asks, then two folds most people leave closed ----------
// The yes/no questions the screening fold asks, so its summary can say how many are answered.
const BACKGROUND_KEYS = [
  "previously_employed_here",
  "has_relatives_at_employer",
  "government_employment_history",
  "government_official",
  "has_non_compete",
  "has_non_solicitation",
  "company_relationships",
  "criminal_conviction",
  "ever_fired",
  "export_control_restricted_country_history",
  "has_asylee_status",
  "referred_by_employee",
];

function AuthorizationStep({ draft, setTop, setFact }: StepProps & { setTop: SetTop; setFact: SetFact }) {
  const f = foldFacts(draft.facts);
  const status = (f.current_immigration_status_us as string) || "";
  const statusChoice = matchStatus(status);
  const opt = /OPT/.test(status);
  const yn = (key: string, label: string) => (
    <YesNo label={label} value={asBool(f[key])} onChange={(v) => setFact(key, v)} />
  );
  const answered = BACKGROUND_KEYS.filter((k) => asBool(f[k]) !== undefined).length;
  return (
    <>
      <Section title="Work authorization">
        <div className="ax-ob-list">
          <Question label="Are you legally authorized to work in the United States?">
            {yn("work_authorization_us", "Authorized to work in the United States")}
          </Question>
          <Question
            label="Will you now or in the future require visa sponsorship?"
            hint="Include future needs such as an H-1B petition after OPT."
          >
            {yn("requires_future_sponsorship_us", "Requires visa sponsorship")}
          </Question>
          <Question label="Are you at least 18 years old?">
            {yn("over_18", "At least 18 years old")}
          </Question>
          <Question label="Do you hold an active U.S. security clearance?">{yn("has_security_clearance", "Holds a security clearance")}</Question>
          <Question label="Do you have a valid driver's license?">{yn("valid_drivers_license", "Valid driver's license")}</Question>
        </div>
        <div className="ax-ob-grid">
          <Select
            label="Current status"
            value={statusChoice}
            onChange={(e) => {
              const v = e.target.value as Status;
              setFact("current_immigration_status_US", v);
              setFact("us_citizen", v === "U.S. citizen");
            }}
          >
            <option value="" disabled>
              Choose one
            </option>
            {STATUS_OPTIONS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </Select>
          {opt && <Input label="OPT end date" type="date" value={String(f.opt_end_date || "")} onChange={(e) => setFact("opt_end_date", e.target.value)} />}
          <Input label="Country of citizenship" value={String(f.country_of_citizenship || "")} onChange={(e) => setFact("country_of_citizenship", e.target.value)} />
          <Input label="Country of residence" value={String(f.country_of_residence || "")} onChange={(e) => setFact("country_of_residence", e.target.value)} />
        </div>
      </Section>
      <div className="ax-ob-folds">
        <Optional
          title="Background questions"
          meta={
            <>
              <span className="mono">{answered}</span> of <span className="mono">{BACKGROUND_KEYS.length}</span> answered
            </>
          }
        >
          <ScreeningStep draft={draft} setFact={setFact} />
        </Optional>
        <Optional title="Self-identification" meta={draft.decline_demographics ? "Declined" : "Using your answers"}>
          <IdentityStep draft={draft} setTop={setTop} setFact={setFact} />
        </Optional>
      </div>
    </>
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
  // A marketer's resume should not be answered with "Machine Learning Engineer": the generic list is only for a resume that gave none.
  const roleSuggestions = Array.from(new Set(fromResume.length ? fromResume : ROLE_SUGGESTIONS));
  return (
    <>
      <Section title="Roles" hint="Type a role and press Enter, or pick a suggestion.">
        <Tags
          label="Roles to look for"
          values={draft.target_roles}
          onChange={(v) => setTop("target_roles", v)}
          placeholder="e.g. Software Engineer"
          suggestions={roleSuggestions}
        />
      </Section>
      <Section title="Places">
        <Tags
          label="Where would you work?"
          values={draft.target_locations}
          onChange={(v) => setTop("target_locations", v)}
          placeholder="City, state, or Remote"
          suggestions={anywhere ? ["Remote"] : ["Anywhere in the USA", "Remote"]}
        />
        <div className="ax-ob-list">
          <Question label="Are you willing to relocate?">
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
      </Section>
      <Section title="Practical details">
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
        <Question label="Earliest start" stack>
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
            hint="Leave empty to answer “negotiable” or pick the posted range."
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
      </Section>
    </>
  );
}

// Questions whose usual answer is No, with the other keys the server reads for the same fact. "Answer No to the rest" fills only
// what the person left unanswered, and only when they press it: a question nobody answered is never stored as No on its own.
const USUALLY_NO: [string, string[]][] = [
  ["previously_employed_here", []],
  ["has_relatives_at_employer", []],
  ["government_employment_history", []],
  ["government_official", []],
  ["has_non_compete", ["has_employment_restrictions"]],
  ["has_non_solicitation", []],
  ["company_relationships", []],
  ["criminal_conviction", []],
  ["ever_fired", ["asked_to_resign", "involuntarily_discharged"]],
  ["export_control_restricted_country_history", []],
  ["has_asylee_status", ["has_refugee_status"]],
  ["referred_by_employee", []],
];

function ScreeningStep({ draft, setFact }: StepProps & { setFact: SetFact }) {
  const f = foldFacts(draft.facts);
  const yn = (key: string, label: string) => (
    <YesNo label={label} value={asBool(f[key])} onChange={(v) => setFact(key, v)} />
  );
  const open = USUALLY_NO.filter(([key]) => asBool(f[key]) === undefined);
  return (
    <>
      <div className="ax-ob-fold-head">
        <p className="ax-hint">Answer only what is true. A question you leave blank is asked of you when a form needs it.</p>
        {open.length > 0 && (
          <Button
            size="sm"
            onClick={() => {
              for (const [key, linked] of open) for (const k of [key, ...linked]) setFact(k, false);
            }}
          >
            Answer No to the rest
          </Button>
        )}
      </div>
      <div className="ax-ob-list">
        <Question
          label="Apart from the employers in your work history, have you ever worked for a company you apply to?"
          hint="“Have you worked for this company before?” is answered Yes for an employer in your work history. This answers it for every other employer."
        >
          {yn("previously_employed_here", "Worked for another company you apply to")}
        </Question>
        <Question label="Do you have relatives working at companies you apply to?" hint="Answer No unless you know of one; you can add exceptions later.">
          {yn("has_relatives_at_employer", "Relatives at companies you apply to")}
        </Question>
        <Question label="Have you been employed by a government agency in the last 5 years?">
          {yn("government_employment_history", "Government employment in the last 5 years")}
        </Question>
        <Question label="Are you, or a close relative, a government or public official?">
          {yn("government_official", "Government or public official")}
        </Question>
        <Question label="Are you bound by a non-compete or other restrictive agreement?">
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
        <Question label="Were you referred by a current employee?" hint="Usually No; referral codes are entered per application.">
          {yn("referred_by_employee", "Referred by an employee")}
        </Question>
      </div>
    </>
  );
}

const GENDER = ["Male", "Female", "Non-binary"] as const;
const VETERAN = ["Not a veteran", "Protected veteran"] as const;
const DISABILITY = ["No disability", "Yes, I have a disability"] as const;

function IdentityStep({ draft, setTop, setFact }: StepProps & { setTop: SetTop; setFact: SetFact }) {
  const f = foldFacts(draft.facts);
  return (
    <>
      <Question label="How should AXIOM answer self-identification questions?" hint="Voluntary equal-opportunity questions." stack>
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
    </>
  );
}

// ---------- Automation: what AXIOM may do without asking ----------
const SOURCES = ["LinkedIn", "Company website", "Indeed", "Handshake", "Glassdoor"];

function AutomationStep({
  draft,
  setTop,
  submit,
  setSubmit,
}: StepProps & { setTop: SetTop; submit: boolean | null; setSubmit: (v: boolean) => void }) {
  const source = draft.application_source;
  return (
    <>
      {submit !== null && (
        <Section title="Sending applications">
          <div className="ax-ob-choices" role="radiogroup" aria-label="Sending applications">
            {[
              {
                on: true,
                title: "Send them for me",
                line: "AXIOM sends each application once every required answer is checked. You can pause it any time.",
              },
              {
                on: false,
                title: "Don't send anything yet",
                line: "Nothing goes to an employer. Jobs you add wait until you turn sending on in Settings.",
              },
            ].map((c) => (
              <button
                key={String(c.on)}
                type="button"
                role="radio"
                aria-checked={submit === c.on}
                className={"ax-ob-choice" + (submit === c.on ? " is-on" : "")}
                onClick={() => {
                  setSubmit(c.on);
                  // Choosing to have them sent is the consent these two switches ask for: with either off, most applications stop
                  // half way and the choice would not do what it says. They turn on in plain sight and can be turned off here.
                  if (c.on) {
                    setTop("allow_account_creation", true);
                    setTop("accept_all_application_terms", true);
                    setTop("allow_application_consents", true);
                  }
                }}
              >
                <span className="ax-ob-choice-main">
                  <strong>{c.title}</strong>
                  <small>{c.line}</small>
                </span>
                <span className="ax-ob-tick" aria-hidden="true">
                  <Tick size={12} />
                </span>
              </button>
            ))}
          </div>
          {submit === true && (
            <ConsentWords />
          )}
        </Section>
      )}
      <Section title="On employer sites">
        <div className="ax-ob-switches">
          <SwitchRow
            label="Create employer accounts for me"
            note="Many employers make you create an account before you can apply. AXIOM makes a strong password and keeps it encrypted on this Mac; you can choose your own main password below. Off: those applications can't go through."
            checked={draft.allow_account_creation}
            onChange={() => setTop("allow_account_creation", !draft.allow_account_creation)}
          />
          <SwitchRow
            label="Accept application terms and privacy notices"
            note="Most forms won't send without this. Where a form asks you to sign that your answers are true, AXIOM types your legal name. Off: those applications stop and ask you."
            checked={draft.accept_all_application_terms}
            onChange={() => {
              const v = !draft.accept_all_application_terms;
              setTop("accept_all_application_terms", v);
              setTop("allow_application_consents", v);
            }}
          />
          <SwitchRow
            label="Say no to newsletters and job alerts"
            note="Newsletters, job alerts and sharing your data for other roles are declined whenever the form allows it."
            checked={!draft.accept_required_marketing}
            onChange={() => setTop("accept_required_marketing", !draft.accept_required_marketing)}
          />
          <SwitchRow
            label="Use the 1st when a form needs an exact day"
            note="Recommended. Resumes list months, and some forms require a day for employment and education dates. Off: those applications stop and ask you."
            checked={draft.answer_rules?.history_day === "first"}
            onChange={() =>
              setTop("answer_rules", {
                ...draft.answer_rules,
                history_day: draft.answer_rules?.history_day === "first" ? "" : "first",
              })
            }
          />
        </div>
        {draft.allow_account_creation && <MainPassword compact />}
      </Section>
      <Section title="Finding jobs">
        <div className="ax-ob-switches">
          <SwitchRow
            label="Look for jobs and apply on its own"
            note="AXIOM looks for jobs that match you and applies without asking each time."
            checked={draft.autonomous}
            onChange={() => setTop("autonomous", !draft.autonomous)}
          />
          <SwitchRow
            label="Also apply to my target roles below the minimum score"
            note="The score can undervalue a role you named outright. On: a posting whose title is one of your target roles qualifies whatever its score. Roles named only by a broad word, like Developer, don't count."
            checked={!!draft.admit_by_title}
            onChange={() => setTop("admit_by_title", !draft.admit_by_title)}
          />
        </div>
        {draft.autonomous && (
          <Input
            label={`How close a match: ${draft.minimum_fit} out of 100`}
            hint="Jobs that match you less than this are skipped."
            type="range"
            min={40}
            max={90}
            step={1}
            value={draft.minimum_fit}
            onChange={(e) => setTop("minimum_fit", Number(e.target.value))}
          />
        )}
        <Select
          label="Roles that ask for more experience than I have"
          hint="Many postings ask for a few years and still hire below that. This counts as met when the work is in your field."
          value={String(draft.accept_experience_years || 0)}
          onChange={(e) => setTop("accept_experience_years", Number(e.target.value))}
        >
          <option value="0">Judge them against my own experience</option>
          {[1, 2, 3, 5].map((years) => (
            <option key={years} value={String(years)}>
              Apply when they ask for up to {years} {years === 1 ? "year" : "years"}
            </option>
          ))}
        </Select>
        <Select
          label="“How did you hear about us?”"
          hint="Used when the form offers this option; otherwise its company website, job board or internet search option."
          value={source}
          onChange={(e) => setTop("application_source", e.target.value)}
        >
          {(SOURCES.includes(source) || !source ? SOURCES : [...SOURCES, source]).map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </Select>
      </Section>
    </>
  );
}

// ---------- Connections: the AI model key and the inbox for verification codes ----------
function ConnectionsStep({ linked, email, refresh }: { linked: Linked; email: string; refresh: () => Promise<unknown> }) {
  const { model, mailboxes } = linked;
  const [provider, setProvider] = useState("");
  const [key, setKey] = useState("");
  const [code, setCode] = useState("");
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
  const saveKey = () =>
    run("model", () => api("/connections", "PUT", { values: { llm_provider: chosen, [keyName]: key.trim() } }).then(() => setKey("")), "Key saved.");
  // OpenRouter's keys say so themselves, so one box is all most people need. It is tried before anyone is told it worked, because
  // a person new to this cannot tell a bad key from a broken app.
  const saveCode = () =>
    run(
      "model",
      async () => {
        await api("/connections", "PUT", { values: { llm_provider: "openrouter", openrouter_api_key: code.trim() } });
        const tried = await api<{ ok: boolean; message?: string }>("/connections/openrouter_api_key/test", "POST");
        if (!tried.ok) {
          await api("/connections", "PUT", { values: { openrouter_api_key: "" } });
          // The service's own answer when there is one (out of credit, refused, unreachable): "didn't work" fits none of them.
          throw new Error(tried.message || "That key didn't work. Check you copied all of it, and that your OpenRouter account has credit.");
        }
        setCode("");
      },
      "You're connected.",
    );
  const connectMail = () =>
    run("mail", () => api("/mail/imap", "POST", { email: box.email || email, app_password: box.app_password }).then(() => setBox({ email: "", app_password: "" })), "Gmail connected.");
  const enter = (fn: () => void, ready: boolean) => (e: React.KeyboardEvent) => {
    if (e.key !== "Enter") return;
    // Enter in a key field saves that key; it must not move the whole guide on.
    e.preventDefault();
    if (ready) fn();
  };
  const note = (where: "model" | "mail") =>
    msg?.where === where && (
      <p className={msg.ok ? "ax-ob-note" : "ax-error"} role={msg.ok ? "status" : "alert"}>
        {msg.ok && <Tick size={14} aria-hidden="true" />}
        {msg.text}
      </p>
    );
  return (
    <>
      <section className="ax-ob-conn">
        <header>
          <span className="ax-ob-tile">
            <Sparkles size={16} aria-hidden="true" />
          </span>
          <span className="ax-ob-conn-main">
            <h2>AI</h2>
            <p className="ax-hint">Reads job pages and writes your answers. Your key is kept encrypted on this Mac and never shown again.</p>
          </span>
          {model && <Pill tone={model.model_ready ? "ok" : "warn"} dot>{model.model_ready ? "Ready" : "Not connected"}</Pill>}
        </header>
        {model && !model.model_ready && (
          <>
            <ol className="ax-ob-gmail-steps">
              <li>
                Create a free account at <External href="https://openrouter.ai/">OpenRouter</External> and <External href="https://openrouter.ai/settings/credits">add $5 of credit</External>. That usually covers a few hundred applications. You pay OpenRouter directly, never AXIOM.
              </li>
              <li>
                Open <External href="https://openrouter.ai/settings/keys">your keys page</External>, press Create, and copy the key.
              </li>
              <li>Paste it below.</li>
            </ol>
            <div className="ax-ob-conn-form">
              <Input
                label="Your OpenRouter key"
                type="password"
                autoComplete="off"
                spellCheck={false}
                placeholder="sk-or-..."
                value={code}
                onChange={(e) => setCode(e.target.value)}
                onKeyDown={enter(saveCode, !!code.trim())}
              />
              <div>
                <Button size="sm" loading={busy === "model"} disabled={!code.trim()} onClick={saveCode}>
                  Connect
                </Button>
              </div>
            </div>
            <Optional title="Use a different AI provider">
              <div className="ax-ob-conn-form">
                <Select label="Provider" value={chosen} onChange={(e) => setProvider(e.target.value)}>
                  {PROVIDERS.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </Select>
                <Input
                  label="API key"
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="Paste here"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  onKeyDown={enter(saveKey, !!key.trim())}
                />
                <div>
                  <Button size="sm" loading={busy === "model"} disabled={!key.trim()} onClick={saveKey}>
                    Save key
                  </Button>
                </div>
              </div>
            </Optional>
          </>
        )}
        {note("model")}
      </section>

      <section className="ax-ob-conn">
        <header>
          <span className="ax-ob-tile">
            <Mail size={16} aria-hidden="true" />
          </span>
          <span className="ax-ob-conn-main">
            <h2>Gmail</h2>
            <p className="ax-hint">Employers email codes and confirmations. AXIOM reads those and never sends mail from your account.</p>
          </span>
          {mailboxes && <Pill tone={mailboxes.length ? "ok" : "neutral"} dot={!mailboxes.length}>{mailboxes.length ? "Connected" : "Not connected"}</Pill>}
        </header>
        {mailboxes && mailboxes.length > 0 && (
          <ul className="ax-rows">
            {mailboxes.map((b) => (
              <li key={b.id} className="ax-row">
                <span className="ax-row-main">
                  <strong>{b.email}</strong>
                  <small>{b.provider === "gmail_oauth" ? "Google sign-in" : "App password"}</small>
                </span>
              </li>
            ))}
          </ul>
        )}
        {!mailboxes?.length && (
          <ol className="ax-ob-gmail-steps">
            <li>
              Turn on <External href="https://myaccount.google.com/signinoptions/twosv">2-Step Verification</External>, if it isn't on already.
            </li>
            <li>
              Open <External href="https://myaccount.google.com/apppasswords">Google's app passwords page</External>, type <strong>AXIOM</strong> as the name
              and press Create.
            </li>
            <li>Copy the 16 letters Google shows you and paste them below. This is not your Gmail password.</li>
          </ol>
        )}
        <div className="ax-ob-conn-form">
          <Input
            label={mailboxes?.length ? "Connect another Gmail" : "Your Gmail address"}
            type="email"
            autoComplete="off"
            placeholder={email || "you@gmail.com"}
            hint={email ? "Empty: the email in your profile." : undefined}
            value={box.email}
            onChange={(e) => setBox({ ...box, email: e.target.value })}
          />
          <Input
            label="The 16 letters from Google"
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder="xxxx xxxx xxxx xxxx"
            value={box.app_password}
            onChange={(e) => setBox({ ...box, app_password: e.target.value })}
            onKeyDown={enter(connectMail, !!box.app_password.trim() && !!(box.email || email).trim())}
          />
          <div>
            <Button size="sm" loading={busy === "mail"} disabled={!box.app_password.trim() || !(box.email || email).trim()} onClick={connectMail}>
              Connect Gmail
            </Button>
          </div>
        </div>
        {note("mail")}
      </section>
      <p className="ax-hint">That's all you need. Phone alerts and extra job sources are in Settings if you ever want them.</p>
    </>
  );
}

// ---------- Ready: the finish ----------
function yesno(v: unknown) {
  const b = asBool(v);
  return b === true ? "Yes" : b === false ? "No" : "";
}

type Line = { id: string; step: StepId; label: string; detail: string; ok: boolean; optional?: boolean };

/** What the finish lists: each thing AXIOM will use, whether it is complete, and the step that fixes it. */
function readyLines(draft: Draft, submit: boolean | null, linked: Linked): Line[] {
  const f = foldFacts(draft.facts);
  const name = [draft.first_name, draft.last_name].filter(Boolean).join(" ");
  const authorized = yesno(f.work_authorization_us);
  const sponsor = yesno(f.requires_future_sponsorship_us);
  return [
    {
      id: "contact",
      step: "profile",
      label: "Contact details",
      detail: [name, draft.email].filter(Boolean).join(" · ") || "Add your name, email and phone.",
      ok: !!(draft.first_name && draft.last_name && draft.email && draft.phone),
    },
    {
      id: "eligibility",
      step: "authorization",
      label: "Work eligibility",
      detail: authorized && sponsor ? `Authorized to work: ${authorized}. Needs sponsorship: ${sponsor}.` : "Answer the work authorization questions.",
      ok: !!(authorized && sponsor),
    },
    {
      id: "roles",
      step: "preferences",
      label: "Roles and places",
      detail: draft.target_roles.length
        ? [draft.target_roles.join(", "), draft.target_locations.join(", ") || "Anywhere in the USA"].join(" · ")
        : "Add at least one role to search for.",
      ok: draft.target_roles.length > 0,
    },
    {
      id: "sending",
      step: "automation",
      label: "Sending",
      detail:
        submit === null
          ? "Choose in Settings."
          : submit
            ? "Applications are sent once every required answer is checked."
            : "Off. Nothing goes to an employer until you turn it on.",
      ok: true,
    },
    {
      id: "key",
      step: "connections",
      label: "AI key",
      detail: linked.model ? (linked.model.model_ready ? "Ready." : "Needed before AXIOM can read job pages. Add it in Settings › Connections.") : "Could not check.",
      ok: !!linked.model?.model_ready,
    },
    {
      id: "inbox",
      step: "connections",
      label: "Gmail",
      detail: linked.mailboxes?.length ? linked.mailboxes.map((b) => b.email).join(", ") : "Not connected. Applications that email you a code will stop until it is.",
      ok: !!linked.mailboxes?.length,
      optional: true,
    },
  ];
}

function ReadyStep({
  lines,
  go,
  summary,
  more,
  busy,
}: {
  lines: Line[];
  go: (id: StepId) => void;
  summary: Summary | null;
  more: () => void;
  busy: boolean;
}) {
  const open = lines.filter((l) => !l.ok && !l.optional).length;
  const modules = summary || [];
  const answered = modules.reduce((n, m) => n + m.answered, 0);
  const total = modules.reduce((n, m) => n + m.total, 0);
  return (
    <>
      <ul className="ax-ob-lines" aria-label="What AXIOM will use">
        {lines.map((l) => (
          <li key={l.id}>
            <span className={"ax-lamp " + (l.ok ? "is-ok" : l.optional ? "is-off" : "is-ask")} aria-hidden="true" />
            <span className="ax-ob-line-main">
              <strong>{l.label}</strong>
              <small>{l.detail}</small>
            </span>
            <button type="button" className="ax-link" onClick={() => go(l.step)}>
              {l.ok ? "Edit" : "Fix"}
              <span className="sr-only"> {l.label}</span>
            </button>
          </li>
        ))}
      </ul>
      {open > 0 && (
        <p className="ax-hint">
          {open === 1 ? "One thing is unfinished." : `${open} things are unfinished.`} You can open AXIOM now: when a form asks for something missing,
          AXIOM uses your profile if the answer is certain, otherwise it pauses that application and asks you.
        </p>
      )}
      {total > 0 && (
        <div className="ax-ob-more">
          <span>
            <strong>Answer more questions</strong>
            <small>
              Optional questionnaires employers sometimes ask. <span className="mono">{answered}</span> of <span className="mono">{total}</span> answered.
            </small>
          </span>
          <Button size="sm" onClick={more} disabled={busy}>
            Open Profile
          </Button>
        </div>
      )}
    </>
  );
}
