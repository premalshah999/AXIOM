// The first screen: the Terms of Use and Privacy Policy, accepted once per version before anything else (jobpilot/legal.py).
import { useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import { Wordmark } from "./shell/Brand";
import { Button, Dialog, ErrorLine, Loading } from "./ui/index";
import "./styles/onboarding.css";

type Legal = { version: string; accepted: boolean; terms: string; privacy: string };

/** Shows the children once this version is accepted; until then the acceptance is all there is. A new version asks again. */
export function LegalGate({ children }: { children: ReactNode }) {
  const [legal, setLegal] = useState<Legal | null>(null);
  const [error, setError] = useState("");
  const [reading, setReading] = useState<"" | "terms" | "privacy">("");
  const [agreed, setAgreed] = useState(false);
  const [saving, setSaving] = useState(false);
  const load = () =>
    api<Legal>("/legal")
      .then(setLegal)
      .catch((e) => setError((e as Error).message));
  useEffect(() => {
    load();
  }, []);
  if (legal?.accepted) return <>{children}</>;
  if (!legal)
    return (
      <div className="ax-screen">
        <div className="ax-boot">
          <Wordmark width={108} />
          {error ? (
            <div className="ax-boot-error" role="alert">
              <p>{error}</p>
              <Button onClick={() => (setError(""), load())}>Try again</Button>
            </div>
          ) : (
            <Loading label="Opening…" />
          )}
        </div>
      </div>
    );
  const accept = async () => {
    setSaving(true);
    setError("");
    try {
      await api("/legal/accept", "POST", { version: legal.version, adult: true });
      setLegal({ ...legal, accepted: true });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
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
        <div className="ax-ob-form"><div className="ax-ob-col">
          <section className="ax-ob-card is-hero" aria-labelledby="legal-title">
            <header>
              <h1 id="legal-title">Before you start</h1>
              <p>AXIOM is a beta that applies to jobs in your name. Read how it works and what it keeps, then agree to continue.</p>
            </header>
            <div className="ax-ob-body">
              <p className="ax-hint">
                <button type="button" className="ax-link" onClick={() => setReading("terms")}>
                  Terms of Use
                </button>{" "}
                and{" "}
                <button type="button" className="ax-link" onClick={() => setReading("privacy")}>
                  Privacy Policy
                </button>{" "}
                (version {legal.version})
              </p>
              <label className="ax-check">
                <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
                <span>I've read and agree, and I'm 18 or older</span>
              </label>
            </div>
            <ErrorLine error={error ? new Error(error) : null} />
            <div className="ax-ask">
              <Button variant="primary" disabled={!agreed || saving} onClick={accept}>
                Continue
              </Button>
            </div>
          </section>
        </div></div>
      </main>
      <Dialog
        open={reading !== ""}
        onClose={() => setReading("")}
        title={reading === "terms" ? "Terms of Use" : "Privacy Policy"}
        width={720}
        footer={<Button onClick={() => setReading("")}>Close</Button>}
      >
        <pre className="ax-legal-text">{reading === "terms" ? legal.terms : legal.privacy}</pre>
      </Dialog>
    </div>
  );
}
