import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { useAction } from "./hooks";
import { Field, KeyValues, Loading } from "./ui";
import type { EducationEntry, Profile, WorkEntry } from "./types";

// ---------------------------------------------------------------------------
// Account: first-run sign-up, email/password sign-in, access-token fallback.
// ---------------------------------------------------------------------------

export type AccountStatus = {
  exists: boolean;
  signup_allowed: boolean;
  signed_in: boolean;
  name?: string;
  email?: string;
  onboarding?: { completed: boolean; step: string };
};

function Brand() {
  return (
    <div className="brand">
      <svg className="mark" viewBox="0 0 20 20" aria-hidden="true">
        <rect x="2" y="3" width="4" height="14" rx="1" className="mark-on" />
        <rect x="8" y="3" width="4" height="14" rx="1" className="mark-on" />
        <rect x="14" y="3" width="4" height="14" rx="1" className="mark-off" />
      </svg>
      <span>ApplyPilot</span>
    </div>
  );
}

export function AuthScreen({ onSignedIn }: { onSignedIn: () => Promise<void> }) {
  const [status, setStatus] = useState<AccountStatus | null>(null);
  const [mode, setMode] = useState<"signup" | "password" | "token">("password");
  const [form, setForm] = useState({ name: "", email: "", password: "", token: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<AccountStatus>("/account")
      .then((s) => {
        setStatus(s);
        setMode(s.signup_allowed && !s.exists ? "signup" : s.exists ? "password" : "token");
      })
      .catch(() => {
        setStatus({ exists: false, signup_allowed: false, signed_in: false });
        setMode("token");
      });
  }, []);

  if (!status)
    return (
      <main className="login">
        <Loading label="Opening ApplyPilot…" />
      </main>
    );

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (mode === "signup")
        await api("/account/signup", "POST", { name: form.name, email: form.email, password: form.password });
      else if (mode === "password")
        await api("/account/login", "POST", { email: form.email, password: form.password });
      else await api("/login", "POST", { token: form.token });
      setForm({ name: "", email: "", password: "", token: "" });
      await onSignedIn();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const title = mode === "signup" ? "Create your account" : "Sign in";
  const lead =
    mode === "signup"
      ? "ApplyPilot applies to jobs for you, answering every form from facts you confirm once."
      : mode === "password"
        ? "Welcome back."
        : "Sign in with this installation's access token.";

  return (
    <main className="login">
      <form className="login-card" onSubmit={submit}>
        <Brand />
        <h1>{title}</h1>
        <p className="muted">{lead}</p>
        {mode === "signup" && (
          <Field label="Full name">
            <input autoFocus value={form.name} onChange={set("name")} required autoComplete="name" />
          </Field>
        )}
        {mode !== "token" && (
          <Field label="Email">
            <input
              type="email"
              autoFocus={mode === "password"}
              value={form.email}
              onChange={set("email")}
              required
              autoComplete="email"
            />
          </Field>
        )}
        {mode !== "token" && (
          <Field
            label="Password"
            hint={mode === "signup" ? "At least 10 characters, upper and lower case, and a number." : undefined}
          >
            <input
              type="password"
              value={form.password}
              onChange={set("password")}
              required
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
            />
          </Field>
        )}
        {mode === "token" && (
          <Field label="Access token">
            <input
              type="password"
              autoFocus
              value={form.token}
              onChange={set("token")}
              required
              autoComplete="current-password"
            />
          </Field>
        )}
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <button className="btn primary block" disabled={busy}>
          {busy ? "Please wait…" : mode === "signup" ? "Create account" : "Sign in"}
        </button>
        <p className="login-help">
          {mode === "token" && status.exists && (
            <button type="button" className="link" onClick={() => setMode("password")}>
              Sign in with email and password
            </button>
          )}
          {mode !== "token" && (
            <button type="button" className="link" onClick={() => setMode("token")}>
              Use an access token instead
            </button>
          )}
          {mode === "token" && !status.exists && (
            <>
              Print the token with <code>jobpilot token</code>. After signing in you can set an email and password in
              Setup → System.
            </>
          )}
        </p>
      </form>
    </main>
  );
}

// "Finish later" holds for this browser: the app opens with a Continue setup banner instead of the guide.
const GUIDE_LATER = "applypilot:guide-later";
export function guideLater(value?: boolean) {
  try {
    if (value === true) localStorage.setItem(GUIDE_LATER, "1");
    else if (value === false) localStorage.removeItem(GUIDE_LATER);
    return localStorage.getItem(GUIDE_LATER) === "1";
  } catch {
    return false;
  }
}
/** Setup → System: add an email/password to a token-only installation, or reopen the setup guide. */
export function SignInSettings() {
  const { pending, run } = useAction();
  const [status, setStatus] = useState<AccountStatus | null>(null);
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [change, setChange] = useState({ current: "", new: "" });
  const load = () =>
    api<AccountStatus>("/account")
      .then(setStatus)
      .catch(() => setStatus(null));
  useEffect(() => {
    load();
    api<Partial<Profile>>("/profile")
      .then((p) => setForm((f) => ({ ...f, name: p.name || "", email: p.email || "" })))
      .catch(() => undefined);
  }, []);
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Your sign-in</h2>
        <p className="muted">
          {status?.exists
            ? "Sign in with this email and password. The access token keeps working for scripts."
            : "Add an email and password so you can sign in without the access token."}
        </p>
      </header>
      {status?.exists ? (
        <>
          <KeyValues
            items={[
              ["Name", status.name || "—"],
              ["Email", status.email || "—"],
            ]}
          />
          <form
            className="ob-grid"
            onSubmit={(e) => {
              e.preventDefault();
              run(
                "password",
                async () => {
                  await api("/account/password", "POST", change);
                  setChange({ current: "", new: "" });
                },
                "Password changed.",
              );
            }}
          >
            <Field label="Current password">
              <input
                type="password"
                value={change.current}
                onChange={(e) => setChange((c) => ({ ...c, current: e.target.value }))}
                required
                autoComplete="current-password"
              />
            </Field>
            <Field label="New password" hint="At least 10 characters, upper and lower case, and a number.">
              <input
                type="password"
                value={change.new}
                onChange={(e) => setChange((c) => ({ ...c, new: e.target.value }))}
                required
                autoComplete="new-password"
              />
            </Field>
            <div className="ob-form-end">
              <button className="btn" disabled={pending !== null}>
                {pending === "password" ? "Saving…" : "Change password"}
              </button>
            </div>
          </form>
        </>
      ) : (
        <form
          className="ob-grid"
          onSubmit={(e) => {
            e.preventDefault();
            run(
              "signin",
              async () => {
                await api("/account/signup", "POST", form);
                setForm((f) => ({ ...f, password: "" }));
                await load();
              },
              "Email and password saved.",
            );
          }}
        >
          <Field label="Full name">
            <input value={form.name} onChange={set("name")} required autoComplete="name" />
          </Field>
          <Field label="Email">
            <input type="email" value={form.email} onChange={set("email")} required autoComplete="email" />
          </Field>
          <Field label="Password" hint="At least 10 characters, upper and lower case, and a number.">
            <input type="password" value={form.password} onChange={set("password")} required autoComplete="new-password" />
          </Field>
          <div className="ob-form-end">
            <button className="btn primary" disabled={pending !== null}>
              {pending === "signin" ? "Saving…" : "Set email and password"}
            </button>
          </div>
        </form>
      )}
      <div className="switch-row">
        <span>
          <strong>Setup guide</strong>
          <small className="muted">Walk through your resume, contact details and the screening questions again.</small>
        </span>
        <button
          type="button"
          className="btn"
          disabled={pending !== null}
          onClick={() =>
            run("guide", async () => {
              await api("/onboarding", "PUT", { step: "contact", completed: false });
              guideLater(false);
              window.location.assign("/");
            })
          }
        >
          Open setup guide
        </button>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Onboarding: the facts employers actually ask for, in the order a person thinks about them.
// Frequencies come from 2,837 applications ApplyPilot ran (Oct 2026): how often an employer form
// asked a question that this fact answers.
// ---------------------------------------------------------------------------

type Step = { id: string; title: string; lead: string };
const STEPS: Step[] = [
  { id: "resume", title: "Resume", lead: "Start from your resume. We read it once to prefill your profile; you confirm everything." },
  { id: "contact", title: "Contact", lead: "Filled into every application exactly as written." },
  { id: "authorization", title: "Work authorization", lead: "The questions that decide eligibility. Answer them once, accurately." },
  { id: "preferences", title: "Roles and logistics", lead: "What to look for, where, and when you can start." },
  { id: "background", title: "Background", lead: "Standard screening questions. Your answers are used only when a form asks." },
  { id: "identity", title: "Self-identification", lead: "Voluntary equal-opportunity questions." },
  { id: "permissions", title: "Permissions", lead: "What ApplyPilot may do on your behalf." },
  { id: "review", title: "Review", lead: "Check your answers. You can change any of them later in Setup." },
];

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
  for (const key of ["phone_country_code", "phone_type", "application_source"] as const)
    if (!String(draft[key] || "").trim()) draft[key] = EMPTY_PROFILE[key];
  if (!draft.address.country.trim()) draft.address.country = EMPTY_PROFILE.address.country;
  // The authorization step shows the residence prefilled; save what it shows.
  if (!String(draft.facts.country_of_residence || "").trim()) draft.facts.country_of_residence = draft.address.country;
  return draft;
}

function YesNo({
  value,
  onChange,
  yes = "Yes",
  no = "No",
}: {
  value: unknown;
  onChange: (v: boolean) => void;
  yes?: string;
  no?: string;
}) {
  return (
    <div className="ob-choices" role="radiogroup">
      <button type="button" role="radio" aria-checked={value === true} className="ob-choice" onClick={() => onChange(true)}>
        {yes}
      </button>
      <button type="button" role="radio" aria-checked={value === false} className="ob-choice" onClick={() => onChange(false)}>
        {no}
      </button>
    </div>
  );
}

function Choice<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T | "";
  options: readonly T[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="ob-choices wrap" role="radiogroup">
      {options.map((o) => (
        <button key={o} type="button" role="radio" aria-checked={value === o} className="ob-choice" onClick={() => onChange(o)}>
          {o}
        </button>
      ))}
    </div>
  );
}

function Question({
  label,
  asked,
  hint,
  children,
}: {
  label: string;
  asked?: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="ob-q">
      <div className="ob-q-label">
        <span>{label}</span>
        {asked && <small className="ob-asked">{asked}</small>}
      </div>
      {children}
      {hint && <small className="field-hint">{hint}</small>}
    </div>
  );
}

function Tags({
  values,
  onChange,
  placeholder,
  suggestions = [],
}: {
  values: string[];
  onChange: (v: string[]) => void;
  placeholder: string;
  suggestions?: string[];
}) {
  const [text, setText] = useState("");
  const add = (v: string) => {
    const clean = v.trim();
    if (clean && !values.some((x) => x.toLowerCase() === clean.toLowerCase())) onChange([...values, clean]);
    setText("");
  };
  return (
    <div className="ob-tags">
      <div className="chips">
        {values.map((v) => (
          <button key={v} type="button" className="chip" aria-pressed="true" onClick={() => onChange(values.filter((x) => x !== v))}>
            {v} <span aria-hidden="true">×</span>
          </button>
        ))}
        {suggestions
          .filter((s) => !values.includes(s))
          .slice(0, 6)
          .map((s) => (
            <button key={s} type="button" className="chip" aria-pressed="false" onClick={() => add(s)}>
              + {s}
            </button>
          ))}
      </div>
      <input
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

/** What a step needs before moving on: only what nearly every application requires. */
function stepProblem(step: string, d: Draft): string {
  if (step === "contact") {
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

export function Onboarding({ onDone, onExit }: { onDone: () => void; onExit: () => void }) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [step, setStep] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  // Automatic submission lives in the installation's controls, not the profile.
  const [submit, setSubmit] = useState<{ saved: boolean; value: boolean } | null>(null);

  useEffect(() => {
    Promise.all([
      api<Partial<Draft>>("/profile"),
      api<{ step: string }>("/onboarding"),
      api<{ auto_submit: boolean }>("/control").catch(() => null),
    ])
      .then(([profile, state, control]) => {
        setDraft(withDefaults(profile));
        if (control) setSubmit({ saved: control.auto_submit, value: control.auto_submit });
        const at = STEPS.findIndex((s) => s.id === state.step);
        if (at > 0) setStep(at);
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  if (error && !draft)
    return (
      <main className="ob">
        <p className="error-text">{error}</p>
      </main>
    );
  if (!draft)
    return (
      <main className="login">
        <Loading label="Loading your profile…" />
      </main>
    );

  const current = STEPS[step];
  const setTop = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft((d) => (d ? { ...d, [key]: value } : d));
  const setFact = (key: string, value: unknown) =>
    setDraft((d) => (d ? { ...d, facts: { ...d.facts, [key]: value } } : d));
  const setAddress = (key: keyof Draft["address"], value: string) =>
    setDraft((d) => (d ? { ...d, address: { ...d.address, [key]: value } } : d));

  async function save(nextStep: number, completed = false) {
    if (!draft) return;
    const problem = stepProblem(STEPS[step].id, draft);
    if (problem) {
      setError(problem);
      return;
    }
    setSaving(true);
    setError("");
    try {
      const name = draft.name.trim() || [draft.first_name, draft.last_name].filter(Boolean).join(" ");
      // The address entered here is more deliberate than the location a resume happened to list.
      const location =
        [draft.address.city.trim(), draft.address.state.trim()].filter(Boolean).join(", ") || draft.location.trim();
      const { approved_answers: _a, reviewed_answers: _r, ...rest } = draft;
      await api("/profile", "PUT", { ...rest, name, location });
      if (submit && submit.value !== submit.saved) {
        // Read the current controls right before writing them back (Pause may have changed).
        const fresh = await api<{ paused: boolean; auto_submit: boolean }>("/control");
        await api("/control", "PUT", { ...fresh, auto_submit: submit.value });
        setSubmit({ saved: submit.value, value: submit.value });
      }
      await api("/onboarding", "PUT", { step: STEPS[Math.min(nextStep, STEPS.length - 1)].id, completed });
      if (completed) onDone();
      else {
        setStep(nextStep);
        window.scrollTo({ top: 0 });
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="ob">
      <header className="ob-head">
        <Brand />
        <button type="button" className="btn quiet sm" onClick={onExit}>
          Finish later
        </button>
      </header>
      <div className="ob-body">
        <ol className="ob-steps" aria-label="Setup steps">
          {STEPS.map((s, i) => (
            <li key={s.id} aria-current={i === step ? "step" : undefined} className={i < step ? "ob-done" : ""}>
              <button
                type="button"
                onClick={() => {
                  setError("");
                  setStep(i);
                }}
                disabled={i > step}
              >
                <span className="ob-n">{i + 1}</span>
                {s.title}
              </button>
            </li>
          ))}
        </ol>
        <section className="ob-main">
          <h1>{current.title}</h1>
          <p className="muted ob-lead">{current.lead}</p>
          {current.id === "resume" && <ResumeStep draft={draft} setDraft={setDraft} />}
          {current.id === "contact" && <ContactStep draft={draft} setTop={setTop} setAddress={setAddress} />}
          {current.id === "authorization" && <AuthorizationStep draft={draft} setFact={setFact} />}
          {current.id === "preferences" && <PreferencesStep draft={draft} setTop={setTop} setFact={setFact} />}
          {current.id === "background" && <BackgroundStep draft={draft} setFact={setFact} />}
          {current.id === "identity" && <IdentityStep draft={draft} setTop={setTop} setFact={setFact} />}
          {current.id === "permissions" && (
            <PermissionsStep
              draft={draft}
              setTop={setTop}
              submit={submit?.value ?? null}
              setSubmit={(value) => setSubmit((v) => (v ? { ...v, value } : v))}
            />
          )}
          {current.id === "review" && <ReviewStep draft={draft} go={setStep} submit={submit?.value ?? null} />}
          {error && (
            <p className="error-text" role="alert">
              {error}
            </p>
          )}
          <div className="ob-nav">
            {step > 0 && (
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setError("");
                  setStep(step - 1);
                }}
                disabled={saving}
              >
                Back
              </button>
            )}
            {step < STEPS.length - 1 ? (
              <button type="button" className="btn primary" onClick={() => save(step + 1)} disabled={saving}>
                {saving ? "Saving…" : "Save and continue"}
              </button>
            ) : (
              <button type="button" className="btn primary" onClick={() => save(step, true)} disabled={saving}>
                {saving ? "Saving…" : "Finish setup"}
              </button>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}

type StepProps = { draft: Draft };
type SetTop = <K extends keyof Draft>(key: K, value: Draft[K]) => void;
type SetFact = (key: string, value: unknown) => void;

function ResumeStep({ draft, setDraft }: StepProps & { setDraft: React.Dispatch<React.SetStateAction<Draft | null>> }) {
  const [resumes, setResumes] = useState<{ id: string; name: string }[] | null>(null);
  const [state, setState] = useState<"idle" | "uploading" | "reading" | "done" | "error">("idle");
  const [message, setMessage] = useState("");
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api<{ id: string; name: string }[]>("/resumes").then(setResumes).catch(() => setResumes([]));
  }, []);

  async function read(resumeId: string) {
    setState("reading");
    setMessage("Reading your resume…");
    try {
      const extracted = await api<Partial<Draft>>("/profile/extract?resume_id=" + encodeURIComponent(resumeId), "POST");
      setDraft((d) => (d ? mergeExtract(d, extracted) : d));
      setState("done");
      const parts = [
        extracted.work?.length ? `${extracted.work.length} roles` : "",
        extracted.education?.length ? `${extracted.education.length} schools` : "",
        extracted.skills?.length ? `${extracted.skills.length} skills` : "",
      ].filter(Boolean);
      setMessage(parts.length ? "Found " + parts.join(", ") + ". Review them on the next steps." : "Resume read.");
    } catch (e) {
      setState("error");
      setMessage((e as Error).message);
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
    <div className="stack">
      <div
        className="ob-drop"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const file = e.dataTransfer.files?.[0];
          if (file) upload(file);
        }}
      >
        <strong>Upload your resume (PDF)</strong>
        <p className="muted sm">Text-based PDF up to 5 MB. It is attached to applications exactly as uploaded.</p>
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
        <button type="button" className="btn primary" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? "Working…" : "Choose PDF"}
        </button>
      </div>
      {message && <p className={state === "error" ? "error-text" : "muted"}>{message}</p>}
      {resumes && resumes.length > 0 && (
        <div className="ob-resumes">
          <span className="field-label">Uploaded</span>
          {resumes.map((r) => (
            <div key={r.id} className="ob-resume">
              <span>{r.name}</span>
              <button type="button" className="btn sm" disabled={busy} onClick={() => read(r.id)}>
                Prefill from this resume
              </button>
            </div>
          ))}
        </div>
      )}
      {(draft.work.length > 0 || draft.education.length > 0) && (
        <div className="ob-summary">
          {draft.work.slice(0, 4).map((w, i) => (
            <div key={"w" + i}>
              <strong>{w.title}</strong> · {w.company}
            </div>
          ))}
          {draft.education.slice(0, 3).map((e, i) => (
            <div key={"e" + i} className="muted">
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
    <div className="stack">
      <div className="ob-grid">
        <Field label="Legal first name">
          <input {...t("first_name")} autoComplete="given-name" />
        </Field>
        <Field label="Legal last name">
          <input {...t("last_name")} autoComplete="family-name" />
        </Field>
        <Field label="Preferred first name" hint="Optional. Used where a form asks for a preferred name.">
          <input {...t("preferred_name")} />
        </Field>
        <Field label="Email">
          <input type="email" {...t("email")} autoComplete="email" />
        </Field>
        <Field label="Phone">
          <input type="tel" {...t("phone")} autoComplete="tel-national" />
        </Field>
        <Field label="Country code" hint="For example +1.">
          <input {...t("phone_country_code")} />
        </Field>
      </div>
      <Question label="Phone type" asked={ASKED.phoneType}>
        <Choice value={draft.phone_type as "Mobile"} options={["Mobile", "Home", "Work"] as const} onChange={(v) => setTop("phone_type", v)} />
      </Question>
      <div className="ob-grid">
        <Field label="Street address" wide>
          <input {...a("line1")} autoComplete="address-line1" />
        </Field>
        <Field label="City">
          <input {...a("city")} autoComplete="address-level2" />
        </Field>
        <Field label="State">
          <input {...a("state")} autoComplete="address-level1" />
        </Field>
        <Field label="Postal code">
          <input {...a("postal_code")} autoComplete="postal-code" />
        </Field>
        <Field label="Country">
          <input {...a("country")} autoComplete="country-name" />
        </Field>
      </div>
      <div className="ob-grid">
        <Field label="LinkedIn URL" hint={ASKED.linkedin}>
          <input {...t("linkedin")} placeholder="https://www.linkedin.com/in/…" />
        </Field>
        <Field label="GitHub URL">
          <input {...t("github")} placeholder="https://github.com/…" />
        </Field>
        <Field label="Portfolio or website">
          <input {...t("website")} />
        </Field>
      </div>
    </div>
  );
}

function AuthorizationStep({ draft, setFact }: StepProps & { setFact: SetFact }) {
  const f = draft.facts;
  const status = (f.current_immigration_status_US as string) || "";
  const statusChoice = (STATUS_OPTIONS as readonly string[]).includes(status) ? (status as Status) : "";
  const opt = /OPT/.test(status);
  return (
    <div className="stack">
      <Question label="Are you legally authorized to work in the United States?" asked={ASKED.authorization}>
        <YesNo value={f.work_authorization_us} onChange={(v) => setFact("work_authorization_us", v)} />
      </Question>
      <Question
        label="Will you now or in the future require visa sponsorship?"
        asked={ASKED.sponsorship}
        hint="Include future needs such as an H-1B petition after OPT."
      >
        <YesNo value={f.requires_future_sponsorship_us} onChange={(v) => setFact("requires_future_sponsorship_us", v)} />
      </Question>
      <Question label="Current status" asked={ASKED.status}>
        <Choice
          value={statusChoice}
          options={STATUS_OPTIONS}
          onChange={(v) => {
            setFact("current_immigration_status_US", v);
            setFact("us_citizen", v === "U.S. citizen");
          }}
        />
      </Question>
      {opt && (
        <Field label="OPT end date">
          <input type="date" value={String(f.opt_end_date || "")} onChange={(e) => setFact("opt_end_date", e.target.value)} />
        </Field>
      )}
      <div className="ob-grid">
        <Field label="Country of citizenship">
          <input value={String(f.country_of_citizenship || "")} onChange={(e) => setFact("country_of_citizenship", e.target.value)} />
        </Field>
        <Field label="Country of residence">
          <input value={String(f.country_of_residence || "")} onChange={(e) => setFact("country_of_residence", e.target.value)} />
        </Field>
      </div>
      <Question label="Are you at least 18 years old?" asked={ASKED.over18}>
        <YesNo value={f.over_18} onChange={(v) => setFact("over_18", v)} />
      </Question>
      <Question label="Do you hold an active U.S. security clearance?">
        <YesNo value={f.has_security_clearance} onChange={(v) => setFact("has_security_clearance", v)} />
      </Question>
    </div>
  );
}

const ROLE_SUGGESTIONS = ["Software Engineer", "Machine Learning Engineer", "Data Scientist", "Data Engineer", "AI Engineer", "Data Analyst"];
const START_OPTIONS = ["Immediately", "In 2 weeks", "In 1 month", "In 2 months"] as const;
const TRAVEL_OPTIONS = ["None", "Up to 25%", "Up to 50%", "75% or more"] as const;
const TRAVEL_PERCENT: Record<string, number> = { None: 0, "Up to 25%": 25, "Up to 50%": 50, "75% or more": 75 };

function PreferencesStep({ draft, setTop, setFact }: StepProps & { setTop: SetTop; setFact: SetFact }) {
  const f = draft.facts;
  const travel = Object.entries(TRAVEL_PERCENT).find(([, n]) => n === f.travel_percentage_willingness)?.[0] || "";
  const anywhere = draft.target_locations.some((l) => /anywhere/i.test(l));
  // Recent job titles first (not internships or assistantships), then common roles.
  const fromResume = draft.work
    .map((w) => (w.title || "").trim())
    .filter((t) => t && !/\b(?:intern|internship|assistant|student|volunteer|fellow)\b/i.test(t));
  const roleSuggestions = Array.from(new Set([...fromResume, ...ROLE_SUGGESTIONS]));
  return (
    <div className="stack">
      <Question label="Roles to look for">
        <Tags
          values={draft.target_roles}
          onChange={(v) => setTop("target_roles", v)}
          placeholder="Type a role and press Enter"
          suggestions={roleSuggestions}
        />
      </Question>
      <Question label="Where would you work?">
        <Tags
          values={draft.target_locations}
          onChange={(v) => setTop("target_locations", v)}
          placeholder="City, state, or Remote"
          suggestions={anywhere ? ["Remote"] : ["Anywhere in the USA", "Remote"]}
        />
      </Question>
      <Question label="Are you willing to relocate?" asked={ASKED.relocate}>
        <YesNo value={f.willing_to_relocate} onChange={(v) => setFact("willing_to_relocate", v)} />
      </Question>
      {f.willing_to_relocate === true && (
        <Question label="Can you relocate at your own expense?" hint="Answers “Do you require relocation assistance?”">
          <YesNo value={f.relocation_at_own_expense} onChange={(v) => setFact("relocation_at_own_expense", v)} />
        </Question>
      )}
      <Question label="Are you open to working on-site or hybrid?">
        <YesNo value={f.willing_onsite} onChange={(v) => setFact("willing_onsite", v)} />
      </Question>
      <Question label="How much travel can you do?">
        <Choice
          value={travel as (typeof TRAVEL_OPTIONS)[number] | ""}
          options={TRAVEL_OPTIONS}
          onChange={(v) => {
            setFact("travel_percentage_willingness", TRAVEL_PERCENT[v]);
            setFact("willing_to_travel", TRAVEL_PERCENT[v] > 0);
          }}
        />
      </Question>
      <Question label="Earliest start" asked={ASKED.start}>
        <Choice
          value={(START_OPTIONS as readonly string[]).includes(draft.availability) ? (draft.availability as (typeof START_OPTIONS)[number]) : ""}
          options={START_OPTIONS}
          onChange={(v) => {
            setTop("availability", v);
            setFact("availability", v);
          }}
        />
      </Question>
      <div className="ob-grid">
        <Field label="Desired annual base salary (USD)" hint={ASKED.salary + ". Leave empty to answer “negotiable” or pick the posted range."}>
          <input
            inputMode="numeric"
            value={draft.salary_expectation}
            onChange={(e) => setTop("salary_expectation", e.target.value.replace(/[^\d]/g, ""))}
            placeholder="e.g. 95000"
          />
        </Field>
        <Field label="Notice period (days)">
          <input
            inputMode="numeric"
            value={f.notice_period === undefined ? "" : String(f.notice_period)}
            onChange={(e) => setFact("notice_period", e.target.value === "" ? undefined : Number(e.target.value.replace(/\D/g, "")))}
          />
        </Field>
      </div>
    </div>
  );
}

function BackgroundStep({ draft, setFact }: StepProps & { setFact: SetFact }) {
  const f = draft.facts;
  const yn = (key: string) => ({ value: f[key], onChange: (v: boolean) => setFact(key, v) });
  return (
    <div className="stack">
      <p className="ob-note">
        “Have you worked for this company before?” ({ASKED.previously}) is answered from your work history: Yes only when
        the employer appears in it.
      </p>
      <Question label="Do you have relatives working at companies you apply to?" asked={ASKED.relatives} hint="Answer No unless you know of one; you can add exceptions later.">
        <YesNo {...yn("has_relatives_at_employer")} />
      </Question>
      <Question label="Have you been employed by a government agency in the last 5 years?" asked={ASKED.government}>
        <YesNo {...yn("government_employment_history")} />
      </Question>
      <Question label="Are you, or a close relative, a government or public official?" asked={ASKED.official}>
        <YesNo {...yn("government_official")} />
      </Question>
      <Question label="Are you bound by a non-compete or other restrictive agreement?" asked={ASKED.noncompete}>
        <YesNo
          value={f.has_non_compete}
          onChange={(v) => {
            setFact("has_non_compete", v);
            setFact("has_employment_restrictions", v);
          }}
        />
      </Question>
      <Question label="Have you ever been convicted of a crime (excluding minor traffic violations)?">
        <YesNo {...yn("criminal_conviction")} />
      </Question>
      <Question label="Have you ever been terminated or asked to resign?">
        <YesNo
          value={f.ever_fired}
          onChange={(v) => {
            setFact("ever_fired", v);
            setFact("asked_to_resign", v);
            setFact("involuntarily_discharged", v);
          }}
        />
      </Question>
      <Question label="Do you have a valid driver's license?">
        <YesNo {...yn("valid_drivers_license")} />
      </Question>
      <Question label="Were you referred by a current employee?" hint="Usually No; referral codes are entered per application.">
        <YesNo {...yn("referred_by_employee")} />
      </Question>
      <Question
        label="Tax-credit screening: have you received SNAP, TANF, SSI or unemployment benefits recently, or are you a veteran or a vocational-rehabilitation referral?"
        hint="Employers ask this for the Work Opportunity Tax Credit. It never affects hiring."
      >
        <YesNo value={f.tax_credit_screening_no === undefined ? undefined : !f.tax_credit_screening_no} onChange={(v) => setFact("tax_credit_screening_no", !v)} />
      </Question>
    </div>
  );
}

const GENDER = ["Male", "Female", "Non-binary"] as const;
const VETERAN = ["Not a veteran", "Protected veteran"] as const;
const DISABILITY = ["No disability", "Yes, I have a disability"] as const;

function IdentityStep({ draft, setTop, setFact }: StepProps & { setTop: SetTop; setFact: SetFact }) {
  const f = draft.facts;
  return (
    <div className="stack">
      <Question label="How should ApplyPilot answer self-identification questions?" asked={ASKED.demographics}>
        <YesNo
          value={draft.decline_demographics}
          onChange={(v) => setTop("decline_demographics", v)}
          yes="Decline to self-identify (recommended)"
          no="Answer with my information"
        />
      </Question>
      {!draft.decline_demographics && (
        <>
          <Question label="Gender">
            <Choice value={(f.gender as (typeof GENDER)[number]) || ""} options={GENDER} onChange={(v) => setFact("gender", v)} />
          </Question>
          <Question label="Are you Hispanic or Latino?">
            <YesNo value={f.hispanic_or_latino} onChange={(v) => setFact("hispanic_or_latino", v)} />
          </Question>
          <Field label="Race (as you would select it)" hint="For example Asian, Black or African American, White, Two or more races.">
            <input
              value={Array.isArray(f.race_preference) ? (f.race_preference as string[]).join(", ") : String(f.race_preference || "")}
              onChange={(e) => setFact("race_preference", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
            />
          </Field>
          <Question label="Veteran status">
            <Choice
              value={f.veteran === true ? "Protected veteran" : f.veteran === false ? "Not a veteran" : ""}
              options={VETERAN}
              onChange={(v) => setFact("veteran", v === "Protected veteran")}
            />
          </Question>
          <Question label="Disability status">
            <Choice
              value={f.disability === true ? "Yes, I have a disability" : f.disability === false ? "No disability" : ""}
              options={DISABILITY}
              onChange={(v) => setFact("disability", v === "Yes, I have a disability")}
            />
          </Question>
        </>
      )}
      <Field label="Pronouns (optional)">
        <input value={String(f.pronouns || "")} onChange={(e) => setFact("pronouns", e.target.value)} />
      </Field>
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
    <label className="ob-toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>
        <strong>
          {title}
          {badge && <em className="ob-badge">{badge}</em>}
        </strong>
        <small className="muted">{detail}</small>
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
    <div className="stack">
      {submit !== null && (
        <Toggle
          checked={submit}
          onChange={setSubmit}
          title="Submit applications for me"
          badge="Recommended"
          detail="Applications submit once every required answer is verified. When this is off, ApplyPilot fills each application and stops before the final submit so you can review it."
        />
      )}
      <Toggle
        checked={draft.allow_account_creation}
        onChange={(v) => setTop("allow_account_creation", v)}
        title="Create employer accounts for me"
        badge="Recommended"
        detail="Workday, Oracle and similar portals need an account per employer. ApplyPilot generates a strong password for each and stores it encrypted. When this is off, those applications stop at the sign-in page."
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
      <Question label="“How did you hear about us?”" asked={ASKED.source} hint="Used when the form offers this option; otherwise the closest one.">
        <Choice value={source} options={SOURCES.includes(source) || !source ? SOURCES : [...SOURCES, source]} onChange={(v) => setTop("application_source", v)} />
      </Question>
      <Toggle
        checked={draft.autonomous}
        onChange={(v) => setTop("autonomous", v)}
        title="Apply automatically to strong matches"
        detail="ApplyPilot discovers jobs, ranks them against your profile, and applies without asking each time."
      />
      {draft.autonomous && (
        <Field label={`Minimum match score: ${draft.minimum_fit}`} hint="Jobs below this score are skipped.">
          <input type="range" min={40} max={90} step={1} value={draft.minimum_fit} onChange={(e) => setTop("minimum_fit", Number(e.target.value))} />
        </Field>
      )}
    </div>
  );
}

function yesno(v: unknown) {
  return v === true ? "Yes" : v === false ? "No" : "Not answered";
}

const STOPS = "Off: those applications stop for you";
const SUBMIT_OFF = "Off: applications stop before submitting";
const WARN = new Set(["Missing", "Not answered", STOPS, SUBMIT_OFF]);

function ReviewStep({ draft, go, submit }: StepProps & { go: (i: number) => void; submit: boolean | null }) {
  const f = draft.facts;
  const groups = useMemo(
    () => [
      {
        step: 1,
        title: "Contact",
        rows: [
          ["Name", [draft.first_name, draft.last_name].filter(Boolean).join(" ") || "Missing"],
          ["Email", draft.email || "Missing"],
          ["Phone", [draft.phone_country_code, draft.phone].filter(Boolean).join(" ") || "Missing"],
          ["Address", [draft.address.line1, draft.address.city, draft.address.state, draft.address.postal_code].filter(Boolean).join(", ") || "Missing"],
          ["LinkedIn", draft.linkedin || "—"],
        ],
      },
      {
        step: 2,
        title: "Work authorization",
        rows: [
          ["Authorized to work in the US", yesno(f.work_authorization_us)],
          ["Requires sponsorship", yesno(f.requires_future_sponsorship_us)],
          ["Status", String(f.current_immigration_status_US || "Not answered")],
          ["18 or older", yesno(f.over_18)],
        ],
      },
      {
        step: 3,
        title: "Roles and logistics",
        rows: [
          ["Roles", draft.target_roles.join(", ") || "Missing"],
          ["Locations", draft.target_locations.join(", ") || "Anywhere in the USA"],
          ["Relocate", yesno(f.willing_to_relocate)],
          ["Start", draft.availability || "Not answered"],
          ["Salary", draft.salary_expectation ? "$" + Number(draft.salary_expectation).toLocaleString() : "Negotiable"],
        ],
      },
      {
        step: 4,
        title: "Background",
        rows: [
          ["Relatives at employer", yesno(f.has_relatives_at_employer)],
          ["Government employment (5 yrs)", yesno(f.government_employment_history)],
          ["Non-compete", yesno(f.has_non_compete)],
          ["Criminal conviction", yesno(f.criminal_conviction)],
        ],
      },
      {
        step: 5,
        title: "Self-identification",
        rows: [["Answers", draft.decline_demographics ? "Decline to self-identify" : "Use my information"]],
      },
      {
        step: 6,
        title: "Permissions",
        rows: [
          ...(submit === null ? [] : [["Submit applications", submit ? "On" : SUBMIT_OFF]]),
          ["Create employer accounts", draft.allow_account_creation ? "Allowed" : STOPS],
          ["Accept application terms", draft.accept_all_application_terms ? "Allowed" : STOPS],
          ["Apply automatically", draft.autonomous ? `On, minimum score ${draft.minimum_fit}` : "Off"],
        ],
      },
    ],
    [draft, f, submit],
  );
  const open = groups.flatMap((g) => g.rows).filter(([, v]) => WARN.has(v)).length;
  return (
    <div className="ob-review">
      {open > 0 && (
        <p className="ob-note">
          {open === 1 ? "1 item needs" : `${open} items need`} an answer. You can finish now: when a form asks for something
          missing, ApplyPilot uses your profile if the answer is certain, otherwise it pauses that application and asks you.
        </p>
      )}
      {groups.map((g) => (
        <section key={g.title} className="ob-group">
          <div className="ob-group-head">
            <strong>{g.title}</strong>
            <button type="button" className="link" onClick={() => go(g.step)}>
              Edit
            </button>
          </div>
          <dl>
            {g.rows.map(([k, v]) => (
              <React.Fragment key={k}>
                <dt>{k}</dt>
                <dd className={WARN.has(v) ? "ob-warn" : ""}>{v}</dd>
              </React.Fragment>
            ))}
          </dl>
        </section>
      ))}
    </div>
  );
}
