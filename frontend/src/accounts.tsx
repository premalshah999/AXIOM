// Settings › Employer accounts: the logins AXIOM made on employers' career sites. The ones that need you come first.
import { useId, useMemo, useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useNotify, usePoll } from "./hooks";
import { ago, stamp } from "./format";
import { Pagination, usePagination } from "./pagination";
import type { AccountRow, AccountSummary, EmployerAccountStatus } from "./types";
import { Actions, Facts, FormGrid, SecretInput } from "./setup-kit";
import { MainPassword } from "./mainpassword";
import { Chips, RowMenu, SearchBox } from "./worktools";
import "./styles/settings.css";
import { Button, Drawer, Dropzone, Empty, External, Field, Input, Monogram, Pill, Table, Tooltip, type PillTone } from "./ui/index";

// What the owner reads for each recorded engine state comes from the server (accounts/overview.py); the tone here only decides what
// kind of lamp it gets.
const TONE: Record<EmployerAccountStatus, PillTone> = {
  active: "ok",
  verification: "warn",
  password_unknown: "warn",
  reset_pending: "warn",
  locked: "bad",
  created: "info",
  signing_in: "info",
  registered: "neutral",
  unknown: "neutral",
};
// Whose password a row holds, in a few words: "its own" is the random one an employer got after it refused the main password.
const KIND: Record<string, string> = { main: "Main password", own: "Its own password", yours: "Password you saved" };
const QUIET = new Set<string>(["created", "signing_in", "registered", "unknown"]);
const FILTERS = [
  { id: "all", label: "All", match: () => true },
  { id: "attention", label: "Needs you", match: (a: AccountRow) => a.attention },
  { id: "active", label: "Working", match: (a: AccountRow) => a.status === "active" },
  { id: "other", label: "Not signed in yet", match: (a: AccountRow) => QUIET.has(a.status) },
];

// The one thing to do about a row, in plain words. A row that needs nothing has none.
function nextStep(a: AccountRow) {
  if (a.can_retry_verification) return "Retry verification";
  if (a.status === "reset_pending" || a.status === "password_unknown" || a.status === "locked") return "Set password";
  return "";
}

export function AccountSettings() {
  const summary = usePoll<AccountSummary>((signal) => api("/accounts", "GET", undefined, signal), 60000);
  const { pending, run } = useAction();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [resetting, setResetting] = useState<AccountRow | null>(null);
  const [showing, setShowing] = useState<AccountRow | null>(null);
  const q = query.trim().toLowerCase();
  const all = useMemo(() => {
    const list = Array.isArray(summary.data?.accounts) ? summary.data!.accounts : [];
    // What needs you first, then the most recently touched.
    return list
      .map((a, i) => ({ a, i }))
      .sort((x, y) => Number(y.a.attention) - Number(x.a.attention) || x.i - y.i)
      .map((x) => x.a);
  }, [summary.data]);
  const counts = useMemo(() => Object.fromEntries(FILTERS.map((f) => [f.id, all.filter(f.match).length])), [all]);
  const chosen = FILTERS.find((f) => f.id === filter) || FILTERS[0];
  const accounts = useMemo(
    () => all.filter((a) => chosen.match(a) && (!q || `${a.host} ${a.employer} ${a.login_email} ${a.label}`.toLowerCase().includes(q))),
    [all, chosen, q],
  );
  const paging = usePagination(accounts, q + filter, 10);
  const attention = counts.attention || 0;
  const act = (a: AccountRow) => (a.can_retry_verification ? retry(a) : setResetting(a));
  const retry = (a: AccountRow) =>
    run(
      "verify" + a.id,
      async () => {
        await api(`/accounts/${encodeURIComponent(a.id)}/verify`, "POST");
        await summary.refresh();
      },
      "Verification restarted. The paused application asks the employer for a new email.",
    );
  return (
    <>
      <section className="st-group" aria-labelledby="st-accounts">
        <div className="st-head">
          <div>
            <h2 id="st-accounts">Employer accounts{summary.data ? ` · ${all.length.toLocaleString()}` : ""}</h2>
            <p>
              {summary.data
                ? attention
                  ? `${attention} ${attention === 1 ? "needs" : "need"} you, and ${attention === 1 ? "is" : "are"} listed first. Everything else works on its own.`
                  : "AXIOM creates these when an application needs one. None needs you right now."
                : "AXIOM creates these when an application needs one."}
            </p>
          </div>
        </div>
        <div className="st-tools">
          <SearchBox label="Search employer accounts" placeholder="Search employer or email" value={query} onChange={setQuery} />
          <Chips
            label="Show accounts"
            value={filter}
            onChange={setFilter}
            items={FILTERS.map((f) => ({ id: f.id, label: f.label, count: summary.data ? counts[f.id] || 0 : undefined }))}
          />
        </div>
        <Table
          label="Employer accounts"
          rows={paging.items}
          rowKey={(a) => a.id}
          loading={!summary.data && !summary.error}
          footer={accounts.length > 10 ? <Pagination paging={paging} label="Account pages" sizes={false} /> : undefined}
          error={
            summary.error && !summary.data ? (
              <Empty
                title="Could not load accounts"
                hint={summary.error.message}
                action={
                  <Button size="sm" onClick={summary.refresh}>
                    Try again
                  </Button>
                }
              />
            ) : undefined
          }
          empty={
            <Empty
              title={q || filter !== "all" ? "No accounts match" : "No employer accounts yet"}
              hint={q || filter !== "all" ? "Try another filter or a shorter search." : "They appear after the first application that needs one."}
              action={
                q || filter !== "all" ? (
                  <Button
                    size="sm"
                    onClick={() => {
                      setQuery("");
                      setFilter("all");
                    }}
                  >
                    Show all accounts
                  </Button>
                ) : undefined
              }
            />
          }
          minWidth={340}
          columns={[
            {
              key: "who",
              header: "Employer",
              truncate: true,
              width: 300,
              cell: (a) => (
                <span className="st-who2" title={[a.host, a.login_email].filter(Boolean).join(" · ")}>
                  <Monogram name={a.employer || a.host} employer={!!a.employer} />
                  <span>
                    <strong>{a.employer || a.host}</strong>
                    <small>
                      {a.host}
                      {a.login_email ? ` · ${a.login_email}` : ""}
                    </small>
                  </span>
                </span>
              ),
            },
            {
              key: "status",
              header: "Status",
              cell: (a) => (
                <Tooltip label={a.detail}>
                  <Pill tone={TONE[a.status] || "neutral"} dot>
                    {a.label}
                  </Pill>
                </Tooltip>
              ),
            },
            {
              key: "login",
              header: "Signs in as",
              truncate: true,
              hideBelow: 860,
              width: 220,
              cell: (a) => (
                <span className="st-who2" title={a.login_email}>
                  <span>
                    <strong>{a.login_email || "—"}</strong>
                    {KIND[a.password_kind] && <small>{KIND[a.password_kind]}</small>}
                  </span>
                </span>
              ),
            },
            {
              key: "updated",
              header: "Updated",
              hideBelow: 600,
              cell: (a) => <time title={stamp(a.updated_at)}>{ago(a.updated_at)}</time>,
            },
            {
              key: "actions",
              header: <span className="sr-only">Actions</span>,
              pin: true,
              cell: (a) => {
                const step = nextStep(a);
                return (
                  <span className="ax-act">
                    {step && (
                      <Button
                        size="sm"
                        className="ax-lead st-lead"
                        loading={pending === "verify" + a.id}
                        disabled={pending !== null && pending !== "verify" + a.id}
                        onClick={() => act(a)}
                      >
                        {step}
                      </Button>
                    )}
                    <RowMenu
                      label={`Actions for ${a.host}`}
                      busy={pending !== null}
                      actions={[
                        ...(a.can_retry_verification ? [{ id: "verify", label: "Retry verification", onSelect: () => retry(a) }] : []),
                        ...(a.password_kind ? [{ id: "show", label: "Show or copy the password", onSelect: () => setShowing(a) }] : []),
                        { id: "reset", label: "Set the password myself", onSelect: () => setResetting(a) },
                        ...(a.origin ? [{ id: "open", label: "Open the site", onSelect: () => window.open(a.origin, "_blank", "noopener") }] : []),
                        {
                          id: "forget",
                          label: "Forget this account",
                          danger: true,
                          confirm: "Forget this account? The next application signs in again or creates it.",
                          confirmLabel: "Forget account",
                          onSelect: () =>
                            run(
                              "forget" + a.id,
                              async () => {
                                await api("/accounts/" + encodeURIComponent(a.id), "DELETE");
                                await summary.refresh();
                              },
                              "Account record forgotten.",
                            ),
                        },
                      ]}
                    />
                  </span>
                );
              },
            },
          ]}
        />
      </section>

      <HowItSignsIn summary={summary.data} />
      <SessionImport />
      <Drawer open={!!showing} onClose={() => setShowing(null)} title="Password" sub={showing ? showing.employer || showing.host : undefined} width={480}>
        {showing && <ShowPassword account={showing} close={() => setShowing(null)} />}
      </Drawer>
      <Drawer
        open={!!resetting}
        onClose={() => setResetting(null)}
        title="Set the password"
        sub={resetting ? resetting.employer || resetting.host : undefined}
        width={480}
      >
        {resetting && (
          <EmployerPassword
            account={resetting}
            close={() => setResetting(null)}
            saved={async () => {
              setResetting(null);
              await summary.refresh();
            }}
          />
        )}
      </Drawer>
    </>
  );
}

/** How AXIOM signs in and what it relies on: kept out of the way, because it only matters when something is wrong. */
function HowItSignsIn({ summary }: { summary: AccountSummary | null }) {
  const { snapshot, refresh } = useWorkspace();
  const { pending, run } = useAction();
  const cfg = snapshot.config || ({} as typeof snapshot.config);
  const problems = summary?.password_problems || [];
  return (
    <details className="ax-fold st-adv">
      <summary>
        <strong>How AXIOM signs in</strong>
        <span>The email, the password and what it relies on</span>
      </summary>
      <div className="st-adv-body">
        <Facts
          items={[
            ["Signs in as", cfg.account_email || "The email on your profile"],
            ["Password", cfg.account ? "Your main password, tried first" : "One password AXIOM made, encrypted on this Mac"],
            ["Gmail", cfg.imap ? "Connected" : <Pill tone="warn" dot>Not connected</Pill>],
            ["Captcha solving", cfg.twocaptcha ? "On" : "Off"],
            [
              "Creating accounts",
              snapshot.profile?.allow_account_creation ? "Allowed" : <Pill tone="warn" dot>Off, in Profile › Knowledge</Pill>,
            ],
          ]}
        />
        <MainPassword onChange={refresh} />
        {problems.length > 0 && (
          <div className="ax-notice is-warn">
            <span>Some portals may reject the shared password: {problems.join("; ")}.</span>
          </div>
        )}
        <Actions start note={!cfg.imap ? "Connect your Gmail under Settings › Mailbox." : undefined}>
          <Button
            size="sm"
            loading={pending === "imap"}
            disabled={!cfg.imap || (pending !== null && pending !== "imap")}
            onClick={() => run("imap", () => api("/inbox/check", "POST"), "Gmail sign-in works.")}
          >
            Test the Gmail connection
          </Button>
        </Actions>
      </div>
    </details>
  );
}

const WHOSE: Record<string, string> = {
  main: "This is your main password. AXIOM tried it first on this employer and it was accepted (or it is the one AXIOM made).",
  own: "This employer refused your main password, so AXIOM made this one for it. No other employer has it.",
  yours: "You saved this password here. The next application signs in with it.",
};

/** One employer's login, shown only when asked for: fetched on the press, hidden again when the drawer closes. */
function ShowPassword({ account: a, close }: { account: AccountRow; close: () => void }) {
  const notify = useNotify();
  const [login, setLogin] = useState<{ email: string; password: string } | null>(null);
  const [shown, setShown] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const reveal = async () => {
    setBusy(true);
    setError("");
    try {
      setLogin(await api<{ email: string; password: string }>(`/accounts/${encodeURIComponent(a.id)}/reveal`, "POST", {}));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="ax-stack">
      <p className="ax-hint">{WHOSE[a.password_kind] || "AXIOM keeps this employer's login."}</p>
      {!login ? (
        <Actions start>
          <Button loading={busy} onClick={reveal}>
            Show the password
          </Button>
        </Actions>
      ) : (
        <>
          <Facts
            items={[
              ["Sign-in email", login.email],
              ["Password", <code key="p">{shown ? login.password : "•".repeat(Math.min(login.password.length, 16))}</code>],
            ]}
          />
          <Actions start>
            <Button size="sm" variant="ghost" aria-pressed={shown} onClick={() => setShown((v) => !v)}>
              {shown ? "Hide" : "Show"}
            </Button>
            <Button
              size="sm"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(login.password);
                  notify("ok", "Password copied.");
                } catch {
                  setError("This browser would not copy it. Press Show and copy it by hand.");
                }
              }}
            >
              Copy password
            </Button>
          </Actions>
        </>
      )}
      {error && (
        <p className="ax-error" role="alert">
          {error}
        </p>
      )}
      <Actions>
        <Button variant="ghost" onClick={close}>
          Close
        </Button>
      </Actions>
    </div>
  );
}

/** "Set the password myself": the password the owner set on the employer's site, sealed on the server. */
function EmployerPassword({
  account: a,
  close,
  saved,
}: {
  account: AccountRow;
  close: () => void;
  saved: () => Promise<void>;
}) {
  const notify = useNotify();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const id = useId();
  return (
    <form
      className="ax-stack"
      noValidate
      onSubmit={async (e) => {
        e.preventDefault();
        if (!password.trim()) return setError(`Enter the new password you set on ${a.host}.`);
        if (confirm !== password) return setError("The two passwords don't match.");
        setBusy(true);
        setError("");
        try {
          await api(`/accounts/${encodeURIComponent(a.id)}/password`, "PUT", { password });
          notify("ok", `Password saved. The next application to ${a.host} signs in with it.`);
          await saved();
        } catch (err) {
          setError((err as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <p className="ax-hint">
        Reset the password on {a.origin ? <External href={a.origin}>{a.host}</External> : a.host} yourself
        {a.login_email ? <> (sign-in email {a.login_email})</> : null}, then enter it here. It is encrypted on the server
        and never shown again.
      </p>
      <div className="ax-field">
        <label className="ax-label" htmlFor={id + "-p"}>
          New password
        </label>
        <SecretInput id={id + "-p"} label="password" value={password} onChange={setPassword} />
      </div>
      <div className="ax-field">
        <label className="ax-label" htmlFor={id + "-c"}>
          Confirm password
        </label>
        <SecretInput id={id + "-c"} label="confirmation" value={confirm} onChange={setConfirm} />
        {error && (
          <p className="ax-error" role="alert">
            {error}
          </p>
        )}
      </div>
      <Actions>
        <Button variant="ghost" onClick={close}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={busy}>
          Save password
        </Button>
      </Actions>
    </form>
  );
}

function SessionImport() {
  const { pending, run } = useAction();
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  return (
    <details className="ax-fold st-adv">
      <summary>
        <strong>Import a session</strong>
        <span>For employers that sign in with a text message or a passkey</span>
      </summary>
      <div className="st-adv-body">
        <form
          className="st-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (!file) return;
            const f = new FormData();
            f.append("url", url);
            f.append("file", file);
            run(
              "session",
              async () => {
                const r = await api<{ host: string }>("/sessions", "POST", f);
                setFile(null);
                return r;
              },
              (r) => r && `Session saved for ${r.host}.`,
            );
          }}
        >
          <FormGrid>
            <Input label="Employer URL" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} />
            <Field
              label="Session file"
              hint={
                <>
                  From <code>jobpilot login https://employer.example</code> on your own machine, or a Playwright storage-state
                  file. Only cookies for that exact host are kept.
                </>
              }
            >
              <Dropzone label="Session file" title="Drop the file, or click to choose" accept="application/json,.json" file={file} onFile={setFile} />
            </Field>
          </FormGrid>
          <Actions start>
            <Button type="submit" loading={pending === "session"} disabled={!file || !url.trim()}>
              Import session
            </Button>
          </Actions>
        </form>
      </div>
    </details>
  );
}
