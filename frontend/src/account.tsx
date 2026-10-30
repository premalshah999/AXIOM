import React, { useEffect, useId, useRef, useState } from "react";
import { Check as Tick, Copy, Download } from "lucide-react";
import { api, ApiError, signedOut } from "./api";
import { useAction, useNotify } from "./hooks";
import { guideLater } from "./guide";
import type { Profile } from "./types";
import { day } from "./format";
import { Wordmark } from "./shell/Brand";
import { Facts, FormGrid } from "./setup-kit";
import { Button, Check, ConfirmButton, ErrorLine, Field, Loading, Panel, Pill, Segmented } from "./ui/index";

// ---------------------------------------------------------------------------
// The owner's account (server: owner_account.py, api/routes/owner.py): sign-up with recovery codes, sign-in,
// forgot password (recovery code, access token or the host command), and Settings › System › Your sign-in.
// ---------------------------------------------------------------------------

export type AccountStatus = {
  exists: boolean;
  signup_allowed: boolean;
  signed_in: boolean;
  installation?: "fresh" | "has_data" | "has_account";
  name?: string;
  email?: string;
  onboarding?: { completed: boolean; step: string };
  password_changed_at?: string | null;
  recovery_codes?: { left: number; total: number; created_at: string | null } | null;
  session?: { via: "session" | "token"; keep: boolean; idle_hours: number | null };
};

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
// The server's policy (owner_account.password_problem), checked while the owner types.
const RULES: { label: string; test: (p: string) => boolean }[] = [
  { label: "At least 10 characters", test: (p) => p.length >= 10 },
  { label: "Upper and lower case letters", test: (p) => p.toLowerCase() !== p && p.toUpperCase() !== p },
  { label: "At least one number", test: (p) => /\d/.test(p) },
];
const TOKEN_COMMAND = "docker compose exec app jobpilot token";
const RESET_COMMAND = "docker compose exec app jobpilot reset-password";

type Errors = Partial<Record<string, string>>;
type NewPasswordValue = { password: string; confirm: string };
const NO_PASSWORD: NewPasswordValue = { password: "", confirm: "" };

function newPasswordErrors(value: NewPasswordValue, label = "a password"): Errors {
  const unmet = RULES.some((r) => !r.test(value.password));
  return {
    password: !value.password
      ? `Choose ${label}`
      : value.password.length > 200
        ? "Use at most 200 characters"
        : unmet
          ? "Meet every rule listed below"
          : undefined,
    confirm: !value.confirm
      ? "Type the password again"
      : value.confirm !== value.password
        ? "The two passwords don't match"
        : undefined,
  };
}

const hasErrors = (errors: Errors) => Object.values(errors).some(Boolean);

/** Focus the first refused control after the render that marks it. */
function focusFirstInvalid(form: HTMLFormElement | null) {
  requestAnimationFrame(() => form?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus());
}

/** Pending state, the server's refusal, and the throttle's countdown for one form. */
function useSubmit() {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [waitUntil, setWaitUntil] = useState(0);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!waitUntil) return;
    const timer = window.setInterval(() => {
      if (Date.now() >= waitUntil) {
        setWaitUntil(0);
        setError("");
      }
      setNow(Date.now());
    }, 1000);
    return () => window.clearInterval(timer);
  }, [waitUntil]);
  const wait = waitUntil ? Math.max(1, Math.ceil((waitUntil - now) / 1000)) : 0;
  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setError("");
    try {
      return await fn();
    } catch (e) {
      const err = e as Error;
      setError(err.message);
      if (err instanceof ApiError && err.status === 429) {
        const seconds = Number(/in (\d+) second/.exec(err.message)?.[1] || 60);
        setNow(Date.now());
        setWaitUntil(Date.now() + seconds * 1000);
      }
      return undefined;
    } finally {
      setBusy(false);
    }
  }
  const message = wait
    ? `Too many failed attempts. Try again in ${wait} second${wait === 1 ? "" : "s"}.`
    : error;
  return { error: message, setError, busy, wait, run };
}

function FormError({ message }: { message: string }) {
  return message ? (
    <p className="ax-error" role="alert">
      {message}
    </p>
  ) : null;
}

function PasswordRules({ password, id }: { password: string; id: string }) {
  return (
    <ul className="ax-rules-list" id={id}>
      {RULES.map((rule) => {
        const met = rule.test(password);
        return (
          <li key={rule.label} className={met ? "is-met" : ""}>
            <Tick size={13} aria-hidden="true" />
            {rule.label}
            <span className="sr-only">{met ? ", met" : ", not met yet"}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** A new password, its confirmation and the rules it must meet, shown before the owner types. */
function NewPassword({
  value,
  onChange,
  errors,
  label = "Password",
}: {
  value: NewPasswordValue;
  onChange: (value: NewPasswordValue) => void;
  errors: Errors;
  label?: string;
}) {
  const [show, setShow] = useState(false);
  const rules = useId();
  return (
    <>
      <Field label={label} error={errors.password}>
        <input
          type={show ? "text" : "password"}
          value={value.password}
          onChange={(e) => onChange({ ...value, password: e.target.value })}
          autoComplete="new-password"
          maxLength={200}
          aria-invalid={!!errors.password}
          aria-describedby={rules}
        />
      </Field>
      <PasswordRules password={value.password} id={rules} />
      <Field label="Confirm password" error={errors.confirm}>
        <input
          type={show ? "text" : "password"}
          value={value.confirm}
          onChange={(e) => onChange({ ...value, confirm: e.target.value })}
          autoComplete="new-password"
          maxLength={200}
          aria-invalid={!!errors.confirm}
        />
      </Field>
      <Check checked={show} onChange={setShow} label="Show passwords" />
    </>
  );
}

function KeepSignedIn({ value, onChange }: { value: boolean; onChange: (value: boolean) => void }) {
  return (
    <Check checked={value} onChange={onChange} label="Keep me signed in on this browser" note="For 30 days without use. Otherwise 24 hours." />
  );
}

/** Recovery codes, shown once: copy, download, and confirm they are saved before moving on. */
export function RecoveryCodes({
  codes,
  doneLabel,
  onDone,
}: {
  codes: string[];
  doneLabel: string;
  onDone: () => void;
}) {
  const [saved, setSaved] = useState(false);
  const [copy, setCopy] = useState<"" | "copied" | "unavailable">("");
  const text =
    "AXIOM recovery codes\nEach code resets the dashboard password once.\n\n" + codes.join("\n") + "\n";
  return (
    <div className="ax-stack">
      <ol className="ax-codes" aria-label="Recovery codes">
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ol>
      <div className="ax-actions is-start">
        <Button
          size="sm"
          icon={<Copy size={14} aria-hidden="true" />}
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(text);
              setCopy("copied");
            } catch {
              setCopy("unavailable");
            }
          }}
        >
          {copy === "copied" ? "Copied" : "Copy"}
        </Button>
        <Button
          size="sm"
          icon={<Download size={14} aria-hidden="true" />}
          onClick={() => {
            const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
            const link = document.createElement("a");
            link.href = url;
            link.download = "axiom-recovery-codes.txt";
            link.click();
            window.setTimeout(() => URL.revokeObjectURL(url), 1000);
          }}
        >
          Download .txt
        </Button>
        <span className="ax-hint" role="status">
          {copy === "unavailable" ? "This browser blocked copying. Download them or write them down." : ""}
        </span>
      </div>
      <Check checked={saved} onChange={setSaved} label="I saved these codes somewhere safe" />
      <Button variant="primary" disabled={!saved} onClick={onDone}>
        {doneLabel}
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sign-in screen
// ---------------------------------------------------------------------------

type Mode = "signup" | "password" | "token" | "forgot";

function AuthCard({
  title,
  lead,
  notice,
  children,
  onSubmit,
  formRef,
}: {
  title: string;
  lead?: React.ReactNode;
  notice?: string;
  children: React.ReactNode;
  onSubmit?: (e: React.FormEvent<HTMLFormElement>) => void;
  formRef?: React.Ref<HTMLFormElement>;
}) {
  const body = (
    <>
      <div className="ax-brand">
        <Wordmark width={120} />
      </div>
      <h1>{title}</h1>
      {lead && <p className="ax-hint">{lead}</p>}
      {notice && (
        <div className="ax-notice is-warn" role="status">
          <span>{notice}</span>
        </div>
      )}
      {children}
    </>
  );
  return (
    <main className="ax-auth">
      {onSubmit ? (
        <form className="ax-auth-card" onSubmit={onSubmit} noValidate ref={formRef}>
          {body}
        </form>
      ) : (
        <div className="ax-auth-card">{body}</div>
      )}
    </main>
  );
}

export function AuthScreen({ onSignedIn, notice = "" }: { onSignedIn: () => Promise<void>; notice?: string }) {
  const [status, setStatus] = useState<AccountStatus | null>(null);
  const [mode, setMode] = useState<Mode>("password");
  const [email, setEmail] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [reset, setReset] = useState<{ email: string; left: number; method: string } | null>(null);

  useEffect(() => {
    api<AccountStatus>("/account")
      .then((s) => {
        setStatus(s);
        setMode(s.exists ? "password" : s.signup_allowed ? "signup" : "token");
      })
      .catch(() => {
        setStatus({ exists: false, signup_allowed: false, signed_in: false });
        setMode("token");
      });
  }, []);

  if (!status)
    return (
      <main className="ax-auth">
        <Loading label="Opening AXIOM…" />
      </main>
    );

  if (codes)
    return (
      <AuthCard
        title="Save your recovery codes"
        lead="If you forget your password, one of these codes lets you choose a new one. Each code works once, and this is the only time they're shown."
      >
        <RecoveryCodes codes={codes} doneLabel="Continue to setup" onDone={() => onSignedIn()} />
      </AuthCard>
    );

  if (reset)
    return (
      <AuthCard
        title="Password changed"
        lead={
          <>
            You're signed in{reset.email ? <> as {reset.email}</> : null}. Every other browser was signed out.
          </>
        }
      >
        {reset.method === "recovery_code" && reset.left <= 3 && (
          <div className="ax-notice is-warn">
            <span>
              {reset.left === 0
                ? "That was your last recovery code."
                : `${reset.left} recovery code${reset.left === 1 ? "" : "s"} left.`}{" "}
              Generate new ones in Settings › System.
            </span>
          </div>
        )}
        <Button variant="primary" block onClick={() => onSignedIn()} autoFocus>
          Continue
        </Button>
      </AuthCard>
    );

  if (mode === "signup")
    return <SignUp notice={notice} onCreated={setCodes} toToken={() => setMode("token")} />;
  if (mode === "forgot")
    return (
      <Forgot
        onReset={setReset}
        back={() => setMode(status.exists ? "password" : "token")}
      />
    );
  if (mode === "token")
    return (
      <TokenSignIn
        status={status}
        notice={notice}
        onSignedIn={onSignedIn}
        toPassword={() => setMode("password")}
        forgot={() => setMode("forgot")}
      />
    );
  return (
    <PasswordSignIn
      notice={notice}
      email={email}
      setEmail={setEmail}
      onSignedIn={onSignedIn}
      forgot={() => setMode("forgot")}
      toToken={() => setMode("token")}
    />
  );
}

function identityErrors(name: string, email: string): Errors {
  return {
    name: name.trim() ? undefined : "Enter your full name",
    email: !email.trim()
      ? "Enter your email address"
      : EMAIL.test(email.trim())
        ? undefined
        : "Enter an email address like name@example.com",
  };
}

function SignUp({
  notice,
  onCreated,
  toToken,
}: {
  notice: string;
  onCreated: (codes: string[]) => void;
  toToken: () => void;
}) {
  const [form, setForm] = useState({ name: "", email: "", keep: false });
  const [secret, setSecret] = useState(NO_PASSWORD);
  const [tried, setTried] = useState(false);
  const submit = useSubmit();
  const ref = useRef<HTMLFormElement>(null);
  const problems = { ...identityErrors(form.name, form.email), ...newPasswordErrors(secret) };
  const errors = tried ? problems : {};
  return (
    <AuthCard
      title="Create your account"
      lead="AXIOM applies to jobs for you, answering every form from facts you confirm once. This is the only account on this installation."
      notice={notice}
      formRef={ref}
      onSubmit={async (e) => {
        e.preventDefault();
        setTried(true);
        if (hasErrors(problems)) return focusFirstInvalid(ref.current);
        await submit.run(async () => {
          const created = await api<{ recovery_codes: string[] }>("/account/signup", "POST", {
            name: form.name,
            email: form.email,
            password: secret.password,
            keep: form.keep,
          });
          onCreated(created.recovery_codes);
        });
      }}
    >
      <Field label="Full name" error={errors.name}>
        <input
          autoFocus
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          autoComplete="name"
          maxLength={120}
          aria-invalid={!!errors.name}
        />
      </Field>
      <Field label="Email" error={errors.email} hint="You sign in with it. Employers also see it on applications.">
        <input
          type="email"
          value={form.email}
          onChange={(e) => setForm({ ...form, email: e.target.value })}
          autoComplete="email"
          maxLength={254}
          aria-invalid={!!errors.email}
        />
      </Field>
      <NewPassword value={secret} onChange={setSecret} errors={errors} />
      <KeepSignedIn value={form.keep} onChange={(keep) => setForm({ ...form, keep })} />
      <FormError message={submit.error} />
      <Button type="submit" variant="primary" block disabled={submit.busy || submit.wait > 0}>
        {submit.busy ? "Creating your account…" : "Create account"}
      </Button>
      <p className="ax-auth-help">
        <button type="button" className="ax-link" onClick={toToken}>
          Use the access token instead
        </button>
      </p>
    </AuthCard>
  );
}

function PasswordSignIn({
  notice,
  email,
  setEmail,
  onSignedIn,
  forgot,
  toToken,
}: {
  notice: string;
  email: string;
  setEmail: (email: string) => void;
  onSignedIn: () => Promise<void>;
  forgot: () => void;
  toToken: () => void;
}) {
  const [password, setPassword] = useState("");
  const [keep, setKeep] = useState(false);
  const [tried, setTried] = useState(false);
  const submit = useSubmit();
  const ref = useRef<HTMLFormElement>(null);
  const errors: Errors = tried
    ? {
        email: email.trim() ? undefined : "Enter your email address",
        password: password ? undefined : "Enter your password",
      }
    : {};
  return (
    <AuthCard
      title="Sign in"
      lead="Welcome back."
      notice={submit.error ? "" : notice}
      formRef={ref}
      onSubmit={async (e) => {
        e.preventDefault();
        setTried(true);
        if (!email.trim() || !password) return focusFirstInvalid(ref.current);
        const ok = await submit.run(async () => {
          await api("/account/login", "POST", { email, password, keep });
          return true;
        });
        if (ok) {
          setPassword("");
          await onSignedIn();
        }
      }}
    >
      <Field label="Email" error={errors.email}>
        <input
          type="email"
          autoFocus
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          aria-invalid={!!errors.email}
        />
      </Field>
      <Field label="Password" error={errors.password}>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          aria-invalid={!!errors.password}
        />
      </Field>
      <p className="ax-auth-help">
        <button type="button" className="ax-link" onClick={forgot}>
          Forgot password?
        </button>
      </p>
      <KeepSignedIn value={keep} onChange={setKeep} />
      <FormError message={submit.error} />
      <Button type="submit" variant="primary" block disabled={submit.busy || submit.wait > 0}>
        {submit.wait ? `Try again in ${submit.wait}s` : submit.busy ? "Signing in…" : "Sign in"}
      </Button>
      <p className="ax-auth-help">
        <button type="button" className="ax-link" onClick={toToken}>
          Use the access token instead
        </button>
      </p>
    </AuthCard>
  );
}

function TokenSignIn({
  status,
  notice,
  onSignedIn,
  toPassword,
  forgot,
}: {
  status: AccountStatus;
  notice: string;
  onSignedIn: () => Promise<void>;
  toPassword: () => void;
  forgot: () => void;
}) {
  const [token, setToken] = useState("");
  const [keep, setKeep] = useState(false);
  const [tried, setTried] = useState(false);
  const submit = useSubmit();
  const ref = useRef<HTMLFormElement>(null);
  const error = tried && !token.trim() ? "Paste the access token" : undefined;
  const lead =
    status.installation === "has_data" ? (
      <>
        This installation already holds a profile or jobs, so it can't be claimed by signing up here. Sign in
        with its access token, then add an email and password in Settings › System.
      </>
    ) : (
      "Sign in with this installation's access token."
    );
  return (
    <AuthCard
      title="Sign in with the access token"
      lead={lead}
      notice={submit.error ? "" : notice}
      formRef={ref}
      onSubmit={async (e) => {
        e.preventDefault();
        setTried(true);
        if (!token.trim()) return focusFirstInvalid(ref.current);
        const ok = await submit.run(async () => {
          await api("/login", "POST", { token, keep });
          return true;
        });
        if (ok) {
          setToken("");
          await onSignedIn();
        }
      }}
    >
      <Field
        label="Access token"
        error={error}
        hint={
          <>
            Print it on the computer running AXIOM: <code>{TOKEN_COMMAND}</code>
          </>
        }
      >
        <input
          type="password"
          autoFocus
          value={token}
          onChange={(e) => setToken(e.target.value)}
          autoComplete="current-password"
          spellCheck={false}
          aria-invalid={!!error}
        />
      </Field>
      <KeepSignedIn value={keep} onChange={setKeep} />
      <FormError message={submit.error} />
      <Button type="submit" variant="primary" block disabled={submit.busy || submit.wait > 0}>
        {submit.wait ? `Try again in ${submit.wait}s` : submit.busy ? "Signing in…" : "Sign in"}
      </Button>
      {status.exists && (
        <p className="ax-auth-help">
          <button type="button" className="ax-link" onClick={toPassword}>
            Sign in with email and password
          </button>
          {" · "}
          <button type="button" className="ax-link" onClick={forgot}>
            Forgot password?
          </button>
        </p>
      )}
    </AuthCard>
  );
}

const METHODS = [
  { id: "recovery_code", label: "Recovery code" },
  { id: "access_token", label: "Access token" },
  { id: "cli", label: "Command line" },
] as const;

function Forgot({
  onReset,
  back,
}: {
  onReset: (result: { email: string; left: number; method: string }) => void;
  back: () => void;
}) {
  const [method, setMethod] = useState<(typeof METHODS)[number]["id"]>("recovery_code");
  const [proof, setProof] = useState("");
  const [secret, setSecret] = useState(NO_PASSWORD);
  const [keep, setKeep] = useState(false);
  const [tried, setTried] = useState(false);
  const submit = useSubmit();
  const ref = useRef<HTMLFormElement>(null);
  const problems: Errors = {
    proof: proof.trim()
      ? undefined
      : method === "recovery_code"
        ? "Enter one of your recovery codes"
        : "Paste the access token",
    ...newPasswordErrors(secret, "a new password"),
  };
  const errors = tried ? problems : {};
  const choose = (id: (typeof METHODS)[number]["id"]) => {
    setMethod(id);
    setProof("");
    setTried(false);
    submit.setError("");
  };
  return (
    <AuthCard
      title="Reset your password"
      lead="This installation can't send email, so prove it's yours first, then choose a new password."
      formRef={ref}
      onSubmit={async (e) => {
        e.preventDefault();
        if (method === "cli") return;
        setTried(true);
        if (hasErrors(problems)) return focusFirstInvalid(ref.current);
        const done = await submit.run(() =>
          api<{ email: string; recovery_codes_left: number }>("/account/reset", "POST", {
            method,
            secret: proof,
            password: secret.password,
            keep,
          }),
        );
        if (done) onReset({ email: done.email, left: done.recovery_codes_left, method });
      }}
    >
      <Segmented
        block
        label="How to prove it's your installation"
        value={method}
        options={METHODS.map((m) => ({ value: m.id, label: m.label }))}
        onChange={choose}
      />
      {method === "cli" ? (
        <>
          <p className="ax-hint">
            On the computer running AXIOM, run <code>{RESET_COMMAND}</code> (or <code>jobpilot reset-password</code>{" "}
            without Docker). It asks for the new password without showing it, then signs every browser out.
          </p>
          <Button variant="primary" block onClick={back}>
            Back to sign in
          </Button>
        </>
      ) : (
        <>
          {method === "recovery_code" ? (
            <Field label="Recovery code" error={errors.proof} hint="One of the ten codes you saved at sign-up. Each works once.">
              <input
                autoFocus
                value={proof}
                onChange={(e) => setProof(e.target.value)}
                placeholder="7KQ2M-X9D4F"
                autoComplete="one-time-code"
                spellCheck={false}
                className="mono"
                aria-invalid={!!errors.proof}
              />
            </Field>
          ) : (
            <Field
              label="Access token"
              error={errors.proof}
              hint={
                <>
                  Print it on the computer running AXIOM: <code>{TOKEN_COMMAND}</code>
                </>
              }
            >
              <input
                type="password"
                autoFocus
                value={proof}
                onChange={(e) => setProof(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                aria-invalid={!!errors.proof}
              />
            </Field>
          )}
          <NewPassword value={secret} onChange={setSecret} errors={errors} label="New password" />
          <KeepSignedIn value={keep} onChange={setKeep} />
          <FormError message={submit.error} />
          <Button type="submit" variant="primary" block disabled={submit.busy || submit.wait > 0}>
            {submit.wait ? `Try again in ${submit.wait}s` : submit.busy ? "Saving…" : "Set new password"}
          </Button>
          <p className="ax-auth-help">
            <button type="button" className="ax-link" onClick={back}>
              Back to sign in
            </button>
          </p>
        </>
      )}
    </AuthCard>
  );
}

// ---------------------------------------------------------------------------
// Settings › System › Your sign-in
// ---------------------------------------------------------------------------

/** A labelled row of Settings: what it is and when it was last true, with its action at the right. */
function SettingRow({ title, note, children }: { title: string; note?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="ax-row">
      <span className="ax-row-main">
        <strong>{title}</strong>
        {note && <small>{note}</small>}
      </span>
      {children && <span className="ax-row-end">{children}</span>}
    </div>
  );
}

export function SignInSettings() {
  const [status, setStatus] = useState<AccountStatus | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const load = () =>
    api<AccountStatus>("/account")
      .then((s) => {
        setStatus(s);
        setError(null);
      })
      .catch((e) => setError(e as Error));
  useEffect(() => {
    load();
  }, []);
  return (
    <Panel title="Your sign-in">
      <ErrorLine error={error} retry={load} />
      {!status ? (
        !error && <Loading />
      ) : status.exists ? (
        <SignedInAccount status={status} reload={load} />
      ) : (
        <AddSignIn onAdded={load} />
      )}
      <GuideRow />
    </Panel>
  );
}

function browserSession(status: AccountStatus) {
  const session = status.session;
  if (!session || session.via === "token") return "Signed in with the access token";
  return session.keep ? "Stays signed in until 30 days without use" : "Signs out after 24 hours without use";
}

function SignedInAccount({ status, reload }: { status: AccountStatus; reload: () => void }) {
  const [open, setOpen] = useState<"" | "password" | "codes">("");
  const { pending, run } = useAction();
  const codes = status.recovery_codes;
  const fewCodes = !codes || codes.left <= 3;
  return (
    <>
      <Facts
        items={[
          ["Email", status.email || "—"],
          ["Name", status.name || "—"],
          ["This browser", browserSession(status)],
        ]}
      />
      <SettingRow
        title="Password"
        note={`${status.password_changed_at ? `Set ${day(status.password_changed_at)}. ` : ""}Changing it signs out every other browser.`}
      >
        {open !== "password" && <Button onClick={() => setOpen("password")}>Change password</Button>}
      </SettingRow>
      {open === "password" && (
        <ChangePassword
          close={() => setOpen("")}
          done={() => {
            setOpen("");
            reload();
          }}
        />
      )}
      <SettingRow
        title="Recovery codes"
        note={
          !codes
            ? "None yet. Without them, a forgotten password needs the access token or the host command."
            : `${codes.left} of ${codes.total} unused${codes.created_at ? `, created ${day(codes.created_at)}` : ""}. Each resets a forgotten password once.`
        }
      >
        {fewCodes && <Pill tone="warn" dot>{codes ? "Few left" : "None"}</Pill>}
        {open !== "codes" && <Button onClick={() => setOpen("codes")}>{codes ? "Generate new codes" : "Generate codes"}</Button>}
      </SettingRow>
      {open === "codes" && (
        <RegenerateCodes
          close={() => setOpen("")}
          done={() => {
            setOpen("");
            reload();
          }}
          replacing={!!codes}
        />
      )}
      <SettingRow title="Sign out of this browser" note="Applications keep running on the server.">
        <Button
          disabled={pending !== null}
          onClick={() =>
            run("logout", async () => {
              await api("/logout", "POST");
              signedOut("You signed out.");
            })
          }
        >
          Sign out
        </Button>
      </SettingRow>
      <SettingRow title="Sign out everywhere" note="Every browser, this one included, must sign in again. The access token keeps working.">
        <ConfirmButton
          label="Sign out everywhere"
          confirm="Sign out every browser"
          variant="secondary"
          pending={pending === "everywhere"}
          onConfirm={() =>
            run("everywhere", async () => {
              await api("/account/sessions", "DELETE");
              signedOut("You signed out of every browser.");
            })
          }
        />
      </SettingRow>
    </>
  );
}

function ChangePassword({ close, done }: { close: () => void; done: () => void }) {
  const notify = useNotify();
  const [current, setCurrent] = useState("");
  const [secret, setSecret] = useState(NO_PASSWORD);
  const [tried, setTried] = useState(false);
  const submit = useSubmit();
  const ref = useRef<HTMLFormElement>(null);
  const problems: Errors = {
    current: current ? undefined : "Enter your current password",
    ...newPasswordErrors(secret, "a new password"),
    ...(secret.password && secret.password === current
      ? { password: "Choose a password that differs from the current one" }
      : {}),
  };
  const errors = tried ? problems : {};
  return (
    <form
      ref={ref}
      className="ax-stack ax-inline"
      noValidate
      onSubmit={async (e) => {
        e.preventDefault();
        setTried(true);
        if (hasErrors(problems)) return focusFirstInvalid(ref.current);
        const ok = await submit.run(async () => {
          await api("/account/password", "POST", { current, new: secret.password });
          return true;
        });
        if (ok) {
          notify("ok", "Password changed. Other browsers were signed out.");
          done();
        }
      }}
    >
      <Field label="Current password" error={errors.current}>
        <input
          type="password"
          autoFocus
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          autoComplete="current-password"
          aria-invalid={!!errors.current}
        />
      </Field>
      <NewPassword value={secret} onChange={setSecret} errors={errors} label="New password" />
      <FormError message={submit.error} />
      <div className="ax-actions is-start">
        <Button type="submit" variant="primary" disabled={submit.busy || submit.wait > 0}>
          {submit.busy ? "Saving…" : "Change password"}
        </Button>
        <Button variant="ghost" onClick={close}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function RegenerateCodes({
  close,
  done,
  replacing,
}: {
  close: () => void;
  done: () => void;
  replacing: boolean;
}) {
  const [password, setPassword] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [tried, setTried] = useState(false);
  const submit = useSubmit();
  if (codes)
    return (
      <div className="ax-stack ax-inline">
        <p className="ax-hint">These replace every earlier code. Save them now; they won't be shown again.</p>
        <RecoveryCodes codes={codes} doneLabel="Done" onDone={done} />
      </div>
    );
  const error = tried && !password ? "Enter your password" : undefined;
  return (
    <form
      className="ax-stack ax-inline"
      noValidate
      onSubmit={async (e) => {
        e.preventDefault();
        setTried(true);
        if (!password) return;
        const result = await submit.run(() => api<{ codes: string[] }>("/account/recovery-codes", "POST", { password }));
        if (result) {
          setPassword("");
          setCodes(result.codes);
        }
      }}
    >
      <Field
        label="Your password"
        error={error}
        hint={replacing ? "New codes replace the old ones, which stop working." : "Codes are shown once."}
      >
        <input
          type="password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          aria-invalid={!!error}
        />
      </Field>
      <FormError message={submit.error} />
      <div className="ax-actions is-start">
        <Button type="submit" variant="primary" disabled={submit.busy || submit.wait > 0}>
          {submit.busy ? "Generating…" : "Generate codes"}
        </Button>
        <Button variant="ghost" onClick={close}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/** A token-only installation adds the owner's email and password (POST /api/account/signup, signed in). */
function AddSignIn({ onAdded }: { onAdded: () => void }) {
  const [form, setForm] = useState({ name: "", email: "" });
  const [secret, setSecret] = useState(NO_PASSWORD);
  const [tried, setTried] = useState(false);
  const [codes, setCodes] = useState<string[] | null>(null);
  const submit = useSubmit();
  const ref = useRef<HTMLFormElement>(null);
  useEffect(() => {
    api<Partial<Profile>>("/profile")
      .then((p) => setForm((f) => ({ name: f.name || p.name || "", email: f.email || p.email || "" })))
      .catch(() => undefined);
  }, []);
  if (codes)
    return (
      <div className="ax-stack ax-inline">
        <p className="ax-hint">
          Email and password saved. If you forget the password, one of these codes lets you choose a new one. Each works
          once; they won't be shown again.
        </p>
        <RecoveryCodes codes={codes} doneLabel="Done" onDone={onAdded} />
      </div>
    );
  const problems = { ...identityErrors(form.name, form.email), ...newPasswordErrors(secret) };
  const errors = tried ? problems : {};
  return (
    <form
      ref={ref}
      noValidate
      onSubmit={async (e) => {
        e.preventDefault();
        setTried(true);
        if (hasErrors(problems)) return focusFirstInvalid(ref.current);
        const created = await submit.run(() =>
          api<{ recovery_codes: string[] }>("/account/signup", "POST", {
            name: form.name,
            email: form.email,
            password: secret.password,
          }),
        );
        if (created) setCodes(created.recovery_codes);
      }}
    >
      <p className="ax-hint">This installation opens only with the access token. Add an email and password to sign in with them.</p>
      <FormGrid>
        <Field label="Full name" error={errors.name}>
          <input
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            autoComplete="name"
            aria-invalid={!!errors.name}
          />
        </Field>
        <Field label="Email" error={errors.email}>
          <input
            type="email"
            value={form.email}
            onChange={(e) => setForm({ ...form, email: e.target.value })}
            autoComplete="email"
            aria-invalid={!!errors.email}
          />
        </Field>
        <div className="ax-stack">
          <NewPassword value={secret} onChange={setSecret} errors={errors} />
        </div>
      </FormGrid>
      <FormError message={submit.error} />
      <div className="ax-actions is-start">
        <Button type="submit" variant="primary" disabled={submit.busy || submit.wait > 0}>
          {submit.busy ? "Saving…" : "Set email and password"}
        </Button>
      </div>
    </form>
  );
}

function GuideRow() {
  const { pending, run } = useAction();
  return (
    <SettingRow title="Setup guide" note="Walk through your resume, contact details and the screening questions again.">
      <Button
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
      </Button>
    </SettingRow>
  );
}
