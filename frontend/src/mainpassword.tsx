// The main password: one optional password AXIOM tries first when it creates or resets an employer account. Used in the setup guide
// and in Settings › Accounts. The server keeps it encrypted and never sends it back, so this can set, replace or remove it, not show it.
import { useEffect, useId, useState } from "react";
import { api } from "./api";
import { useNotify } from "./hooks";
import { SecretInput } from "./setup-kit";
import { Button } from "./ui/index";
import "./styles/mainpassword.css";

export function MainPassword({ compact = false }: { compact?: boolean }) {
  const id = useId();
  const notify = useNotify();
  const [isSet, setIsSet] = useState<boolean | null>(null);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    api<{ fields: { name: string; set: boolean }[] }>("/connections")
      .then((c) => live && setIsSet(!!c.fields.find((f) => f.name === "application_password")?.set))
      .catch(() => live && setIsSet(false));
    return () => {
      live = false;
    };
  }, []);
  const save = async (next: string) => {
    setBusy(true);
    setError("");
    try {
      await api("/connections", "PUT", { values: { application_password: next } });
      setIsSet(!!next);
      setEditing(false);
      setValue("");
      notify("ok", next ? "Main password saved." : "Main password removed.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const open = editing || isSet === false;
  return (
    <div className="ax-field ax-mp">
      <label className="ax-label" htmlFor={id}>
        Main password <span className="ax-hint">(optional)</span>
      </label>
      <p className="ax-hint">
        AXIOM tries this first when it creates or resets an employer account. A site that refuses it gets a random password that
        meets its rules, kept for that site in Settings › Accounts, where you can see and copy it.
        {compact ? "" : " Left empty, AXIOM makes one strong password itself."}
      </p>
      <p className="ax-hint">
        Using one password on many sites is your choice, and it is a risk: if one employer leaks it, the others can be tried with it.
        It is stored encrypted on this Mac and never shown again.
      </p>
      {open ? (
        <>
          <SecretInput
            id={id}
            label="main password"
            placeholder="10 to 30 characters, with a capital, a digit and a symbol"
            value={value}
            onChange={setValue}
            extra={
              <Button size="sm" loading={busy} disabled={!value.trim()} onClick={() => save(value)}>
                Save
              </Button>
            }
          />
          {isSet && (
            <p>
              <Button size="sm" variant="ghost" onClick={() => (setEditing(false), setValue(""), setError(""))}>
                Cancel
              </Button>
            </p>
          )}
        </>
      ) : (
        <div className="ax-mp-saved" aria-label="Main password, saved">
          <span>••••••••</span>
          <small>Saved in AXIOM</small>
          <Button size="sm" variant="ghost" onClick={() => setEditing(true)}>
            Replace
          </Button>
          <Button size="sm" variant="ghost" loading={busy} onClick={() => save("")}>
            Remove
          </Button>
        </div>
      )}
      {error && (
        <p className="ax-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
