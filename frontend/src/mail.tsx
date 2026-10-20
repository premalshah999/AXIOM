import { useEffect, useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNotify, usePoll } from "./hooks";
import type { MailStatus } from "./types";
import {
  Confirm,
  Empty,
  ErrorLine,
  Field,
  Loading,
  stamp,
  StatePill,
  words,
} from "./ui";

const LIVE = new Set(["pending", "matched", "consumed"]);
const split = (value: string) =>
  value
    .split(/[\s,]+/)
    .map((x) => x.trim())
    .filter(Boolean);

export function MailSettings() {
  const { snapshot, rows, openJob } = useWorkspace();
  const notify = useNotify();
  const mail = usePoll<MailStatus>(
    (signal) => api("/mail", "GET", undefined, signal),
    15000,
  );
  const { pending, run } = useAction();
  const [mailbox, setMailbox] = useState("");
  const [adding, setAdding] = useState(false);
  const [imap, setImap] = useState({ email: "", app_password: "" });
  const [employer, setEmployer] = useDraft("mail-employer", "");
  const [senders, setSenders] = useDraft("mail-senders", "");
  const [origins, setOrigins] = useDraft("mail-origins", "");

  // Google redirects back with ?gmail=connected|error.
  useEffect(() => {
    const url = new URL(location.href);
    const result = url.searchParams.get("gmail");
    if (!result) return;
    notify(
      result === "connected" ? "ok" : "error",
      result === "connected"
        ? "Gmail connected. Known Greenhouse and Workday verification rules work automatically."
        : "Gmail connection failed. Check the OAuth settings and try again.",
    );
    url.searchParams.delete("gmail");
    history.replaceState(null, "", url);
  }, [notify]);

  const change = (key: string, fn: () => Promise<unknown>, success?: string) =>
    run(
      key,
      async () => {
        const r = await fn();
        await mail.refresh();
        return r;
      },
      success,
    );
  const data = mail.data;
  if (!data)
    return mail.error ? (
      <ErrorLine error={mail.error} retry={mail.refresh} />
    ) : (
      <Loading />
    );
  const selected = data.mailboxes.some((m) => m.id === mailbox)
    ? mailbox
    : data.mailboxes[0]?.id || "";
  const jobFor = (runId: string) =>
    rows.find(
      (r) =>
        r.run?.id === runId ||
        snapshot.runs.some((x) => x.id === runId && x.job_id === r.job.id),
    );
  return (
    <>
      <section className="panel">
        <header className="panel-head">
          <h2>Mailbox</h2>
          <p className="muted">
            Used for one-time codes, activation and reset links, and employer
            receipts. Access is read-only; each message is checked for
            recipient, sender, time and allowed link destinations. Codes and
            links never reach this page.
          </p>
        </header>
        <ul className="list">
          {data.mailboxes.map((box) => (
            <li key={box.id}>
              <span>
                <strong>{box.email}</strong>
                <small className="muted">
                  {box.provider === "gmail_imap"
                    ? "Gmail app password"
                    : "Google sign-in"}
                  {box.error ? ` · ${box.error}` : ""}
                </small>
              </span>
              <StatePill
                state={
                  box.state === "connected" ? "confirmed" : "waiting_browser"
                }
                label={words(box.state)}
              />
              <span className="row-actions">
                <button
                  className="btn sm"
                  disabled={pending !== null}
                  onClick={() =>
                    change(
                      "check" + box.id,
                      () => api(`/mail/${box.id}/check`, "POST"),
                      "Mailbox checked.",
                    )
                  }
                >
                  Check
                </button>
                <Confirm
                  label="Disconnect"
                  confirm="Disconnect"
                  pending={pending === "dis" + box.id}
                  onConfirm={() =>
                    run(
                      "dis" + box.id,
                      async () => {
                        const r = await api<{
                          revoked?: boolean;
                          message?: string;
                        }>(`/mail/${box.id}`, "DELETE");
                        await mail.refresh();
                        return r;
                      },
                      (r) =>
                        r?.message ||
                        (r?.revoked
                          ? "Mailbox disconnected and Google access revoked."
                          : "Disconnected locally. Revoking Google access failed; remove it in your Google account."),
                    )
                  }
                />
              </span>
            </li>
          ))}
        </ul>
        {data.mailboxes.length === 0 || adding ? (
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              change(
                "imap",
                async () => {
                  await api("/mail/imap", "POST", {
                    ...imap,
                    email: imap.email || snapshot.profile.email,
                  });
                  setImap({ email: "", app_password: "" });
                  setAdding(false);
                },
                "Gmail connected. Verification codes and receipts are read automatically.",
              );
            }}
          >
            <Field
              label="Gmail address"
              hint="The address on your profile: employers send codes there."
            >
              <input
                type="email"
                value={imap.email || snapshot.profile.email}
                onChange={(e) =>
                  setImap((v) => ({ ...v, email: e.target.value }))
                }
                required
                autoComplete="email"
              />
            </Field>
            <Field
              label="App password"
              hint={
                <>
                  16 letters from{" "}
                  <a
                    href="https://myaccount.google.com/apppasswords"
                    target="_blank"
                    rel="noreferrer"
                  >
                    myaccount.google.com/apppasswords
                  </a>{" "}
                  (needs 2-Step Verification). Read-only use; stored encrypted.
                </>
              }
            >
              <input
                type="password"
                value={imap.app_password}
                onChange={(e) =>
                  setImap((v) => ({ ...v, app_password: e.target.value }))
                }
                required
                autoComplete="off"
                placeholder="xxxx xxxx xxxx xxxx"
              />
            </Field>
            <div className="form-actions start wide">
              <button className="btn primary" disabled={pending !== null}>
                {pending === "imap" ? "Checking…" : "Connect Gmail"}
              </button>
              {adding && (
                <button
                  type="button"
                  className="btn quiet"
                  onClick={() => setAdding(false)}
                >
                  Cancel
                </button>
              )}
            </div>
          </form>
        ) : (
          <div className="form-actions start">
            <button
              type="button"
              className="btn"
              onClick={() => setAdding(true)}
            >
              Connect another Gmail
            </button>
          </div>
        )}
        {data.configured && (
          <div className="form-actions start">
            <button
              className="btn"
              disabled={pending !== null}
              onClick={() =>
                run("connect", async () => {
                  const r = await api<{ authorization_url: string }>(
                    "/mail/connect",
                    "POST",
                  );
                  window.location.assign(r.authorization_url);
                })
              }
            >
              {pending === "connect"
                ? "Opening Google…"
                : "Connect Gmail with Google"}
            </button>
          </div>
        )}
      </section>

      <section className="panel">
        <header className="panel-head">
          <h2>Employer rules</h2>
          <p className="muted">
            Only needed for employers outside the built-in rules. Use the
            employer's path on shared ATS domains, exact sender domains and any
            extra verification origins.
          </p>
        </header>
        {data.mailboxes.length > 0 ? (
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              change(
                "rule",
                async () => {
                  await api("/mail-rules", "POST", {
                    mailbox_id: selected,
                    employer_origin: employer.trim(),
                    sender_domains: split(senders),
                    link_origins: split(origins),
                  });
                  setEmployer("");
                  setSenders("");
                  setOrigins("");
                },
                "Employer rule saved.",
              );
            }}
          >
            <Field label="Mailbox">
              <select
                value={selected}
                onChange={(e) => setMailbox(e.target.value)}
              >
                {data.mailboxes.map((box) => (
                  <option key={box.id} value={box.id}>
                    {box.email}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Employer URL or path prefix">
              <input
                type="url"
                required
                value={employer}
                onChange={(e) => setEmployer(e.target.value)}
                placeholder="https://careers.example.com/company"
              />
            </Field>
            <Field
              label="Trusted sender domains"
              hint="Exact domains, comma separated. No wildcards."
            >
              <input
                required
                value={senders}
                onChange={(e) => setSenders(e.target.value)}
                placeholder="notifications.example.com"
              />
            </Field>
            <Field
              label="Extra verification origins"
              hint="Optional. The employer origin is already allowed."
            >
              <input
                value={origins}
                onChange={(e) => setOrigins(e.target.value)}
                placeholder="https://login.example.com"
              />
            </Field>
            <div className="form-actions wide">
              <button className="btn" disabled={pending !== null}>
                {pending === "rule" ? "Saving…" : "Save rule"}
              </button>
            </div>
          </form>
        ) : (
          <p className="muted sm">
            Connect a Google mailbox to add custom rules. The IMAP adapters
            don't need them.
          </p>
        )}
        {data.rules.length > 0 && (
          <ul className="list">
            {data.rules.map((rule) => (
              <li key={rule.id}>
                <span>
                  <strong>
                    {rule.employer_origin}
                    {rule.employer_path === "/" ? "" : rule.employer_path}
                  </strong>
                  <small className="muted">
                    From {rule.sender_domains.join(", ")}
                    {rule.link_origins.length
                      ? ` · links to ${rule.link_origins.join(", ")}`
                      : ""}
                  </small>
                </span>
                <Confirm
                  label="Remove"
                  confirm="Remove rule"
                  pending={pending === "rm" + rule.id}
                  onConfirm={() =>
                    change(
                      "rm" + rule.id,
                      () => api(`/mail-rules/${rule.id}`, "DELETE"),
                      "Rule removed.",
                    )
                  }
                />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="panel">
        <header className="panel-head">
          <h2>Recent verifications</h2>
          <p className="muted">
            The latest 50 email challenges requested by applications.
          </p>
        </header>
        {data.challenges.length ? (
          <ul className="list">
            {data.challenges.map((c) => {
              const row = jobFor(c.run_id);
              return (
                <li key={c.id}>
                  <span>
                    {row ? (
                      <button
                        className="link"
                        onClick={() => openJob(row.job.id, c.run_id)}
                      >
                        <strong>{row.job.company}</strong> — {row.job.title}
                      </button>
                    ) : (
                      <strong>Attempt {c.run_id.slice(0, 8)}</strong>
                    )}
                    <small className="muted">
                      {c.kind === "auto" ? "Email verification" : words(c.kind)}{" "}
                      · {stamp(c.created_at)}
                      {c.reason ? ` · ${c.reason}` : ""}
                    </small>
                  </span>
                  <StatePill
                    state={
                      c.state === "verified"
                        ? "confirmed"
                        : LIVE.has(c.state)
                          ? "queued"
                          : "cancelled"
                    }
                    label={words(c.state)}
                  />
                  {LIVE.has(c.state) && (
                    <Confirm
                      label="Cancel"
                      confirm="Cancel wait"
                      pending={pending === "c" + c.id}
                      onConfirm={() =>
                        change(
                          "c" + c.id,
                          () => api(`/mail-challenges/${c.id}/cancel`, "POST"),
                          "Verification cancelled.",
                        )
                      }
                    />
                  )}
                </li>
              );
            })}
          </ul>
        ) : (
          <Empty title="No email verifications yet">
            They appear when an application asks for a code or link.
          </Empty>
        )}
      </section>
    </>
  );
}
