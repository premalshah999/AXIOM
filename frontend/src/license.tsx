// The license key screen (jobpilot/licensing.py). It exists only in a build that asks for a key. This Mac's code is shown (hashed
// from its hardware id, so the id itself is never shared); the person sends it to the owner and pastes the key that comes back.
// A Mac with no key yet sees only this screen. After a key has run out the app stays open and a banner offers the same screen:
// new applications are held, applications already started finish, and nothing is deleted.
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import { Wordmark } from "./shell/Brand";
import { Button, ErrorLine, Loading, Textarea } from "./ui/index";
import "./styles/onboarding.css";
import "./styles/license.css";

export type LicenseStatus = {
  required: boolean;
  state: "off" | "none" | "valid" | "expired" | "invalid";
  starts_allowed: boolean;
  /** No key has ever worked on this Mac: nothing but this screen. */
  blocking: boolean;
  activated: boolean;
  /** This Mac's code, like ABCD-EFGH-JKLM; empty when the system would not say. */
  code: string;
  reason: string;
  expires: string | null;
  days_left: number | null;
  name: string;
  clock_note: string;
};

type Ctx = { status: LicenseStatus | null; open: () => void };
const LicenseContext = createContext<Ctx>({ status: null, open: () => {} });
export const useLicense = () => useContext(LicenseContext);

/** "March 13, 2027" from the server's date, read as that calendar day wherever the Mac is. */
export function longDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" });
}

/** Around the whole app, outside sign-in: a Mac without a key has no account to sign in to yet. Off builds render the app at once. */
export function LicenseGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<LicenseStatus | null>(null);
  const [failed, setFailed] = useState(false);
  const [manual, setManual] = useState(false);
  const load = useCallback(
    () =>
      api<LicenseStatus>("/license")
        .then((s) => (setStatus(s), setFailed(false)))
        .catch(() => setFailed(true)),
    [],
  );
  useEffect(() => {
    load();
    // A key can run out while the app is open; the server answers from a cache, so this is cheap.
    const timer = window.setInterval(load, 5 * 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  // The server did not answer: the app's own boot reports that, with Try again. Waiting here would hide it.
  if (!status && !failed)
    return (
      <div className="ax-screen">
        <div className="ax-boot">
          <Wordmark width={108} />
          <Loading label="Opening…" />
        </div>
      </div>
    );
  if (status?.required && status.blocking)
    return <LicenseScreen status={status} onEntered={(next) => setStatus(next)} />;
  // Reopened from the banner: the app stays mounted underneath (its place and unsaved work are kept) and is only hidden.
  return (
    <LicenseContext.Provider value={{ status, open: () => setManual(true) }}>
      <div style={{ display: manual ? "none" : "contents" }}>{children}</div>
      {status?.required && manual && (
        <LicenseScreen
          status={status}
          onBack={() => setManual(false)}
          onEntered={(next) => (setStatus(next), setManual(false))}
        />
      )}
    </LicenseContext.Provider>
  );
}

/** The one line above a page when the key has run out or is about to. Nothing when all is well, or when the build asks for no key. */
export function LicenseBanner() {
  const { status, open } = useLicense();
  if (!status?.required) return null;
  let words = "";
  let bad = true;
  if (status.state === "expired" && status.expires)
    words = `Your key ran out on ${longDate(status.expires)}. New applications are paused; ones already started finish, and everything you have is kept.`;
  else if (status.state === "invalid") words = status.reason;
  else if (status.state === "none") words = "AXIOM has no key, so new applications are paused. Everything you have is kept.";
  else if (status.state === "valid" && status.days_left !== null && status.days_left <= 7 && status.expires) {
    bad = false;
    words =
      status.days_left <= 0
        ? `Your key runs out today (${longDate(status.expires)}).`
        : `Your key runs out in ${status.days_left} day${status.days_left === 1 ? "" : "s"}, on ${longDate(status.expires)}.`;
  } else return null;
  return (
    <div className={"ax-banner" + (bad ? " is-bad" : "")} role="status">
      <span>{words}</span>
      <button type="button" className="ax-link" onClick={open}>
        Enter a new key
      </button>
    </div>
  );
}

function LicenseScreen({
  status,
  onBack,
  onEntered,
}: {
  status: LicenseStatus;
  onBack?: () => void;
  onEntered: (next: LicenseStatus) => void;
}) {
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const resumed = !!onBack;
  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      onEntered(await api<LicenseStatus>("/license/key", "POST", { key }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(status.code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2500);
    } catch {
      setError("Copying did not work here. Select the code and copy it.");
    }
  };
  return (
    <div className="ax-screen">
      <main className="ax-ob">
        <header className="ax-ob-top">
          <div className="ax-brand">
            <Wordmark />
          </div>
        </header>
        <div className="ax-ob-form">
          <div className="ax-ob-col">
            <section className="ax-ob-card is-hero" aria-labelledby="license-title">
              <header>
                <h1 id="license-title">{resumed ? "Enter a new key" : "Your key"}</h1>
                <p>
                  {resumed
                    ? "Send this Mac's code to the person who gave you AXIOM. They send back a key; paste it below."
                    : "AXIOM needs a key made for this Mac. Send the code below to the person who gave you AXIOM; they will send a key back."}
                </p>
              </header>
              <div className="ax-ob-body">
                {status.code ? (
                  <div className="ax-license-code">
                    <span className="ax-label" id="license-code-label">
                      This Mac's code
                    </span>
                    <div className="ax-license-row">
                      <code className="mono" aria-labelledby="license-code-label">
                        {status.code}
                      </code>
                      <Button size="sm" onClick={copy}>
                        {copied ? "Copied" : "Copy"}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <ErrorLine error={status.reason} />
                )}
                <Textarea
                  label="Your key"
                  hint="Paste the whole key, starting with AXIOM1-."
                  mono
                  rows={4}
                  spellCheck={false}
                  autoComplete="off"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                />
                {status.clock_note && <p className="ax-hint">{status.clock_note}</p>}
              </div>
              <ErrorLine error={error} />
              <div className="ax-ask">
                <Button variant="primary" loading={busy} disabled={!key.trim() || busy} onClick={submit}>
                  Use this key
                </Button>
                {onBack && <Button variant="ghost" onClick={onBack}>Back to AXIOM</Button>}
              </div>
              <p className="ax-hint">
                The key is checked on this Mac. Nothing is sent anywhere, and the code can't be turned back into your Mac's identity.
              </p>
            </section>
          </div>
        </div>
      </main>
    </div>
  );
}
