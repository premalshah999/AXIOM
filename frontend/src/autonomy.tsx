// Saved answers (Profile › Knowledge) and employer accounts (Settings › Accounts).
import { useEffect, useId, useMemo, useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNotify, usePoll } from "./hooks";
import { ago, stamp } from "./format";
import { Pagination, usePagination } from "./pagination";
import type { AccountRow, AccountSummary, EmployerAccountStatus, ReviewedAnswer } from "./types";
import { Actions, Facts, FormGrid, SecretInput } from "./setup-kit";
import {
  Button,
  ConfirmButton,
  Drawer,
  Empty,
  ErrorLine,
  External,
  Input,
  Panel,
  Pill,
  Select,
  Table,
  Textarea,
  type PillTone,
} from "./ui/index";
import { RowMenu, SearchBox } from "./worktools";

// ---------- Saved answers ----------
export function LearnedAnswers() {
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  useEffect(() => {
    const t = window.setTimeout(() => setTerm(q.trim()), 250);
    return () => window.clearTimeout(t);
  }, [q]);
  const entries = usePoll<{ entries: ReviewedAnswer[] }>(
    (signal) => api("/knowledge?q=" + encodeURIComponent(term), "GET", undefined, signal),
    120000,
    [term],
  );
  const { pending, run } = useAction();
  const [question, setQuestion] = useDraft("kb-question", "");
  const [answer, setAnswer] = useDraft("kb-answer", "");
  const [scope, setScope] = useDraft<ReviewedAnswer["scope"]>("kb-scope", "personal");
  const [employer, setEmployer] = useDraft("kb-employer", "");
  const rows = entries.data?.entries || [];
  const paging = usePagination(rows, term, 25);
  return (
    <div className="ax-sections">
      <Panel title="Add an answer">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            run(
              "add",
              async () => {
                await api("/knowledge", "POST", {
                  question: question.trim(),
                  answer: answer.trim(),
                  options: [],
                  scope,
                  employer: scope === "employer" ? employer.trim() : "",
                });
                setQuestion("");
                setAnswer("");
                await entries.refresh();
              },
              "Answer saved.",
            );
          }}
        >
          <FormGrid>
            <Input
              label="Question"
              required
              minLength={2}
              maxLength={2000}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Do you have experience with Kafka?"
            />
            <Textarea label="Answer" required rows={2} maxLength={8000} value={answer} onChange={(e) => setAnswer(e.target.value)} />
            <Select label="Applies to" value={scope} onChange={(e) => setScope(e.target.value as ReviewedAnswer["scope"])}>
              <option value="personal">Every employer</option>
              <option value="employer">One employer</option>
            </Select>
            {scope === "employer" && (
              <Input label="Employer" required value={employer} onChange={(e) => setEmployer(e.target.value)} />
            )}
          </FormGrid>
          <Actions>
            <Button type="submit" loading={pending === "add"} disabled={pending !== null && pending !== "add"}>
              Save answer
            </Button>
          </Actions>
        </form>
      </Panel>
      <Panel title="Saved answers" actions={<SearchBox label="Search saved answers" placeholder="Search" value={q} onChange={setQ} />} flush>
        <ErrorLine error={entries.error} retry={entries.refresh} />
        <Table
          label="Saved answers"
          rows={paging.items}
          rowKey={(e) => e.id}
          loading={!entries.data && !entries.error}
          minWidth={640}
          empty={<Empty title={term ? "No saved answers match" : "No saved answers yet"} hint={term ? undefined : "Answers you give to genuine questions are saved here and reused."} />}
          columns={[
            { key: "q", header: "Question", wrap: true, width: 260, cell: (e) => e.question },
            { key: "a", header: "Answer", wrap: true, width: 260, cell: (e) => e.answer },
            {
              key: "scope",
              header: "Applies to",
              cell: (e) => (
                <>
                  {e.scope === "personal" ? "Every employer" : e.employer || "One employer"}
                  <span className="ax-hint"> · {e.layer === "narrative" ? "narrative" : "fact"}</span>
                </>
              ),
            },
            {
              key: "act",
              header: <span className="sr-only">Actions</span>,
              cell: (e) => (
                <ConfirmButton
                  label="Forget"
                  confirm="Forget answer"
                  variant="secondary"
                  pending={pending === e.id}
                  onConfirm={() =>
                    run(
                      e.id,
                      async () => {
                        await api("/knowledge/" + encodeURIComponent(e.id), "DELETE");
                        await entries.refresh();
                      },
                      "Answer forgotten. It won't be reused.",
                    )
                  }
                />
              ),
            },
          ]}
        />
        <Pagination paging={paging} label="Answer pages" />
      </Panel>
    </div>
  );
}

// ---------- Accounts ----------
// What the owner sees for each recorded engine state comes from the server (accounts/overview.py); the tone here only
// decides whether it earns a dot.
const ACCOUNT_TONE: Record<EmployerAccountStatus, PillTone> = {
  active: "ok",
  verification: "warn",
  password_unknown: "warn",
  reset_pending: "info",
  locked: "bad",
  created: "info",
  signing_in: "info",
  registered: "neutral",
  unknown: "neutral",
};
const ACCOUNT_FILTERS: { id: string; label: string; match: (a: AccountRow) => boolean }[] = [
  { id: "all", label: "All accounts", match: () => true },
  { id: "attention", label: "Needs you", match: (a) => a.attention },
  { id: "active", label: "Active", match: (a) => a.status === "active" },
  { id: "password_unknown", label: "Password unknown", match: (a) => a.status === "password_unknown" },
  { id: "verification", label: "Needs email verification", match: (a) => a.status === "verification" },
  { id: "reset_pending", label: "Reset pending", match: (a) => a.status === "reset_pending" },
  { id: "locked", label: "Locked", match: (a) => a.status === "locked" },
  {
    id: "other",
    label: "Created or not signed in yet",
    match: (a) => ["created", "signing_in", "registered", "unknown"].includes(a.status),
  },
];

export function AccountSettings() {
  const { snapshot } = useWorkspace();
  const summary = usePoll<AccountSummary>((signal) => api("/accounts", "GET", undefined, signal), 60000);
  const { pending, run } = useAction();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [resetting, setResetting] = useState<AccountRow | null>(null);
  const q = query.trim().toLowerCase();
  const all = useMemo(() => (Array.isArray(summary.data?.accounts) ? summary.data!.accounts : []), [summary.data]);
  const counts = useMemo(() => Object.fromEntries(ACCOUNT_FILTERS.map((f) => [f.id, all.filter(f.match).length])), [all]);
  const chosen = ACCOUNT_FILTERS.find((f) => f.id === filter) || ACCOUNT_FILTERS[0];
  const accounts = useMemo(
    () =>
      all.filter(
        (a) => chosen.match(a) && (!q || `${a.host} ${a.employer} ${a.login_email} ${a.label}`.toLowerCase().includes(q)),
      ),
    [all, chosen, q],
  );
  const paging = usePagination(accounts, q + filter, 25);
  const cfg = snapshot.config || ({} as typeof snapshot.config);
  const attention = counts.attention || 0;
  const problems = summary.data?.password_problems || [];
  return (
    <div className="ax-sections">
      <Panel title="How sign-in works">
        <Facts
          items={[
            ["Login email", cfg.account_email || "Profile email"],
            ["Shared password", cfg.account ? "Set on the server" : "Generated and encrypted"],
            ["Gmail IMAP", cfg.imap ? "Configured" : <Pill tone="warn" dot>Not configured</Pill>],
            ["Captcha service", cfg.twocaptcha ? "Configured" : "Not configured"],
            [
              "Account creation",
              snapshot.profile?.allow_account_creation ? (
                "Allowed"
              ) : (
                <Pill tone="warn" dot>Off (Profile › Knowledge)</Pill>
              ),
            ],
          ]}
        />
        {problems.length > 0 && (
          <div className="ax-notice is-warn">
            <span>Some portals may reject the shared password: {problems.join("; ")}.</span>
          </div>
        )}
        <Actions start note={!cfg.imap ? "Add the Gmail address and app password in Connections." : undefined}>
          <Button
            size="sm"
            loading={pending === "imap"}
            disabled={!cfg.imap || (pending !== null && pending !== "imap")}
            onClick={() => run("imap", () => api("/inbox/check", "POST"), "Gmail IMAP sign-in works.")}
          >
            Test Gmail connection
          </Button>
        </Actions>
      </Panel>

      <Panel title={`Employer accounts${summary.data ? " · " + all.length.toLocaleString() : ""}`}>
        <div className="ax-stack">
          {attention > 0 && (
            <div className="ax-notice is-warn">
              <span>
                {attention} {attention === 1 ? "account needs" : "accounts need"} you.
              </span>
            </div>
          )}
          <div className="ax-toolbar">
            <select className="ax-control" aria-label="Show accounts" value={filter} onChange={(e) => setFilter(e.target.value)}>
              {ACCOUNT_FILTERS.map((f) => (
                <option key={f.id} value={f.id} disabled={f.id !== "all" && !counts[f.id] && filter !== f.id}>
                  {f.label} ({counts[f.id] || 0})
                </option>
              ))}
            </select>
            <SearchBox label="Search employer accounts" placeholder="Search" value={query} onChange={setQuery} />
          </div>
          <Table
            label="Employer accounts"
            rows={paging.items}
            rowKey={(a) => a.id}
            loading={!summary.data && !summary.error}
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
                hint={q || filter !== "all" ? undefined : "They appear after the first application that needs one."}
              />
            }
            minWidth={600}
            columns={[
              {
                key: "site",
                header: "Employer site",
                truncate: true,
                width: 230,
                cell: (a) => (a.origin ? <External href={a.origin}>{a.host}</External> : a.host),
              },
              {
                key: "status",
                header: "Status",
                cell: (a) => (
                  <span title={a.detail}>
                    <Pill tone={ACCOUNT_TONE[a.status] || "neutral"} dot>
                      {a.label}
                    </Pill>
                  </span>
                ),
              },
              {
                key: "login",
                header: "Login",
                truncate: true,
                hideBelow: 600,
                width: 240,
                cell: (a) => {
                  const text = [a.employer && "for " + a.employer, a.login_email].filter(Boolean).join(" · ");
                  return <span title={text}>{text || "—"}</span>;
                },
              },
              {
                key: "updated",
                header: "Updated",
                hideBelow: 860,
                cell: (a) => <span title={stamp(a.updated_at)}>{ago(a.updated_at)}</span>,
              },
              {
                key: "actions",
                header: <span className="sr-only">Actions</span>,
                pin: true,
                cell: (a) => (
                  <RowMenu
                    label={`Actions for ${a.host}`}
                    busy={pending !== null}
                    actions={[
                      ...(a.can_retry_verification
                        ? [
                            {
                              id: "verify",
                              label: "Retry verification",
                              onSelect: () =>
                                run(
                                  "verify" + a.id,
                                  async () => {
                                    await api(`/accounts/${encodeURIComponent(a.id)}/verify`, "POST");
                                    await summary.refresh();
                                  },
                                  "Verification restarted. The paused application asks the employer for a new email.",
                                ),
                            },
                          ]
                        : []),
                      { id: "reset", label: "I reset it myself", onSelect: () => setResetting(a) },
                      {
                        id: "forget",
                        label: "Forget",
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
                ),
              },
            ]}
          />
          <Pagination paging={paging} label="Account pages" />
        </div>
      </Panel>

      <SessionImport />
      <Drawer
        open={!!resetting}
        onClose={() => setResetting(null)}
        title={resetting ? `Reset password · ${resetting.host}` : "Reset password"}
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
    </div>
  );
}

/** "I reset it myself": the password the owner set on the employer's site, sealed on the server. */
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
  const [key, setKey] = useState(0);
  return (
    <details className="ax-panel ax-fold">
      <summary>
        <strong>Import an employer session</strong>
        <span className="ax-hint">for SMS or passkey sign-ins</span>
      </summary>
      <div className="ax-panel-body">
        <form
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
                setKey((k) => k + 1);
                return r;
              },
              (r) => r && `Session saved for ${r.host}.`,
            );
          }}
        >
          <FormGrid>
            <Input label="Employer URL" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} />
            <Input
              key={key}
              label="Storage-state JSON"
              hint={
                <>
                  From <code>jobpilot login https://employer.example</code> on your own machine, or a Playwright
                  storage-state file. Only cookies for that exact host are kept.
                </>
              }
              type="file"
              accept="application/json"
              required
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </FormGrid>
          <Actions>
            <Button type="submit" loading={pending === "session"} disabled={!file}>
              Import session
            </Button>
          </Actions>
        </form>
      </div>
    </details>
  );
}
