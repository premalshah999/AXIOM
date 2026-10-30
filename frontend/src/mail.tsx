import { useEffect, useId, useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNotify, usePoll } from "./hooks";
import { Pagination, usePagination } from "./pagination";
import { Actions, FormGrid, SecretInput } from "./setup-kit";
import type { MailChallenge, MailRule, MailStatus } from "./types";
import { stamp, words } from "./format";
import { SearchBox } from "./worktools";
import { Button, ConfirmButton, Empty, External, Input, Loading, Panel, Pill, Select, Table, type PillTone } from "./ui/index";

const LIVE = new Set(["pending", "matched", "consumed"]);
const split = (value: string) =>
  value
    .split(/[\s,]+/)
    .map((x) => x.trim())
    .filter(Boolean);

const challengeTone = (state: string): PillTone => (state === "verified" ? "ok" : LIVE.has(state) ? "info" : "neutral");

export function MailSettings() {
  const { snapshot, rows, openJob } = useWorkspace();
  const notify = useNotify();
  const mail = usePoll<MailStatus>((signal) => api("/mail", "GET", undefined, signal), 15000);
  const { pending, run } = useAction();
  const [mailbox, setMailbox] = useState("");
  const [adding, setAdding] = useState(false);
  const [imap, setImap] = useState({ email: "", app_password: "" });
  const [employer, setEmployer] = useDraft("mail-employer", "");
  const [senders, setSenders] = useDraft("mail-senders", "");
  const [origins, setOrigins] = useDraft("mail-origins", "");
  const [ruleQuery, setRuleQuery] = useState("");
  const passwordId = useId();

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

  const data = mail.data;
  const mailboxes = data?.mailboxes || [];
  const rules: MailRule[] = data?.rules || [];
  const challenges: MailChallenge[] = data?.challenges || [];
  const q = ruleQuery.trim().toLowerCase();
  const shownRules = rules.filter(
    (r) =>
      !q ||
      `${r.employer_origin}${r.employer_path || ""} ${(r.sender_domains || []).join(" ")} ${(r.link_origins || []).join(" ")}`
        .toLowerCase()
        .includes(q),
  );
  const rulePaging = usePagination(shownRules, q, 25);
  const challengePaging = usePagination(challenges, "", 10);

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
  if (!data)
    return (
      <Panel title="Mailbox">
        {mail.error ? (
          <Empty
            title="Could not load the mailbox"
            hint={mail.error.message}
            action={
              <Button size="sm" onClick={mail.refresh}>
                Try again
              </Button>
            }
          />
        ) : (
          <Loading />
        )}
      </Panel>
    );
  const selected = mailboxes.some((m) => m.id === mailbox) ? mailbox : mailboxes[0]?.id || "";
  const profileEmail = snapshot.profile?.email || "";
  const jobFor = (runId: string) =>
    (rows || []).find(
      (r) => r.run?.id === runId || (snapshot.runs || []).some((x) => x.id === runId && x.job_id === r.job.id),
    );
  return (
    <>
      <Panel title="Mailbox">
        {mailboxes.length > 0 && (
          <ul className="ax-rows">
            {mailboxes.map((box) => (
              <li className="ax-row" key={box.id}>
                <span className="ax-row-main">
                  <strong>{box.email}</strong>
                  <small>
                    {box.provider === "gmail_imap" ? "Gmail app password" : "Google sign-in"}
                    {box.error ? ` · ${box.error}` : ""}
                  </small>
                </span>
                <span className="ax-row-end">
                  <Pill tone={box.state === "connected" ? "ok" : "warn"} dot>
                    {words(box.state || "unknown")}
                  </Pill>
                  <Button
                    size="sm"
                    loading={pending === "check" + box.id}
                    disabled={pending !== null && pending !== "check" + box.id}
                    onClick={() =>
                      change("check" + box.id, () => api(`/mail/${box.id}/check`, "POST"), "Mailbox checked.")
                    }
                  >
                    Check
                  </Button>
                  <ConfirmButton
                    label="Disconnect"
                    confirm="Disconnect"
                    note="Applications can no longer read codes from it."
                    pending={pending === "dis" + box.id}
                    onConfirm={() =>
                      run(
                        "dis" + box.id,
                        async () => {
                          const r = await api<{ revoked?: boolean; message?: string }>(`/mail/${box.id}`, "DELETE");
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
        )}
        {mailboxes.length === 0 || adding ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              change(
                "imap",
                async () => {
                  await api("/mail/imap", "POST", { ...imap, email: imap.email || profileEmail });
                  setImap({ email: "", app_password: "" });
                  setAdding(false);
                },
                "Gmail connected. Verification codes and receipts are read automatically.",
              );
            }}
          >
            <FormGrid>
              <Input
                label="Gmail address"
                hint="The address on your profile: employers send codes there."
                type="email"
                value={imap.email || profileEmail}
                onChange={(e) => setImap((v) => ({ ...v, email: e.target.value }))}
                required
                autoComplete="email"
              />
              <div className="ax-field">
                <label className="ax-label" htmlFor={passwordId}>
                  App password
                </label>
                <SecretInput
                  id={passwordId}
                  label="app password"
                  describedBy={passwordId + "-msg"}
                  placeholder="xxxx xxxx xxxx xxxx"
                  value={imap.app_password}
                  onChange={(v) => setImap((s) => ({ ...s, app_password: v }))}
                />
                <p className="ax-hint" id={passwordId + "-msg"}>
                  16 letters from <External href="https://myaccount.google.com/apppasswords">myaccount.google.com/apppasswords</External>{" "}
                  (needs 2-Step Verification). Read-only; stored encrypted.
                </p>
              </div>
            </FormGrid>
            <Actions>
              {adding && (
                <Button variant="ghost" onClick={() => setAdding(false)}>
                  Cancel
                </Button>
              )}
              <Button
                type="submit"
                variant="primary"
                loading={pending === "imap"}
                disabled={!imap.app_password.trim() || (pending !== null && pending !== "imap")}
              >
                Connect Gmail
              </Button>
            </Actions>
          </form>
        ) : (
          <Actions start>
            <Button onClick={() => setAdding(true)}>Connect another Gmail</Button>
            {data.configured && (
              <Button
                loading={pending === "connect"}
                disabled={pending !== null && pending !== "connect"}
                onClick={() =>
                  run("connect", async () => {
                    const r = await api<{ authorization_url: string }>("/mail/connect", "POST");
                    window.location.assign(r.authorization_url);
                  })
                }
              >
                Connect Gmail with Google
              </Button>
            )}
          </Actions>
        )}
        {(mailboxes.length === 0 || adding) && data.configured && (
          <Actions start>
            <Button
              loading={pending === "connect"}
              disabled={pending !== null && pending !== "connect"}
              onClick={() =>
                run("connect", async () => {
                  const r = await api<{ authorization_url: string }>("/mail/connect", "POST");
                  window.location.assign(r.authorization_url);
                })
              }
            >
              Connect Gmail with Google
            </Button>
          </Actions>
        )}
      </Panel>

      <Panel title={`Employer rules · ${rules.length}`}>
        <p className="ax-hint">Only for employers outside the built-in rules. Use the employer's path on shared ATS domains.</p>
        {mailboxes.length > 0 ? (
          <form
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
            <FormGrid>
              <Select label="Mailbox" value={selected} onChange={(e) => setMailbox(e.target.value)}>
                {mailboxes.map((box) => (
                  <option key={box.id} value={box.id}>
                    {box.email}
                  </option>
                ))}
              </Select>
              <Input
                label="Employer URL or path prefix"
                type="url"
                required
                value={employer}
                onChange={(e) => setEmployer(e.target.value)}
                placeholder="https://careers.example.com/company"
              />
              <Input
                label="Trusted sender domains"
                hint="Exact domains, comma separated. No wildcards."
                required
                value={senders}
                onChange={(e) => setSenders(e.target.value)}
                placeholder="notifications.example.com"
              />
              <Input
                label="Extra verification origins"
                hint="Optional. The employer origin is already allowed."
                value={origins}
                onChange={(e) => setOrigins(e.target.value)}
                placeholder="https://login.example.com"
              />
            </FormGrid>
            <Actions>
              <Button
                type="submit"
                variant="primary"
                loading={pending === "rule"}
                disabled={pending !== null && pending !== "rule"}
              >
                Save rule
              </Button>
            </Actions>
          </form>
        ) : (
          <p className="ax-hint">Connect a Google mailbox to add custom rules. The IMAP adapters don't need them.</p>
        )}
        {rules.length > 0 && (
          <div className="ax-stack">
            <div className="ax-toolbar">
              <SearchBox label="Search rules" placeholder="Search rules" value={ruleQuery} onChange={setRuleQuery} />
            </div>
            <Table
              label="Employer rules"
              rows={rulePaging.items}
              rowKey={(r) => r.id}
              minWidth={640}
              empty={<Empty title="No rules match" hint="Try a shorter search." />}
              columns={[
                {
                  key: "employer",
                  header: "Employer",
                  truncate: true,
                  width: 320,
                  cell: (r) => (
                    <span title={`${r.employer_origin}${r.employer_path === "/" ? "" : r.employer_path || ""}`}>
                      {r.employer_origin}
                      {r.employer_path === "/" ? "" : r.employer_path}
                    </span>
                  ),
                },
                {
                  key: "from",
                  header: "Trusted senders",
                  truncate: true,
                  width: 260,
                  cell: (r) => (r.sender_domains || []).join(", ") || "—",
                },
                {
                  key: "links",
                  header: "Extra link origins",
                  truncate: true,
                  width: 220,
                  cell: (r) => (r.link_origins || []).join(", ") || "—",
                },
                {
                  key: "act",
                  header: <span className="sr-only">Remove</span>,
                  cell: (r) => (
                    <ConfirmButton
                      label="Remove"
                      variant="secondary"
                      confirm="Remove rule"
                      pending={pending === "rm" + r.id}
                      onConfirm={() => change("rm" + r.id, () => api(`/mail-rules/${r.id}`, "DELETE"), "Rule removed.")}
                    />
                  ),
                },
              ]}
            />
            <Pagination paging={rulePaging} label="Rule pages" />
          </div>
        )}
      </Panel>

      <Panel title="Recent verifications">
        {challenges.length ? (
          <>
            <ul className="ax-rows">
              {challengePaging.items.map((c) => {
                const row = jobFor(c.run_id);
                return (
                  <li className="ax-row" key={c.id}>
                    <span className="ax-row-main">
                      {row && (row.job.company || row.job.title) ? (
                        <button type="button" className="ax-link" onClick={() => openJob(row.job.id, c.run_id)}>
                          <strong>{row.job.company || row.job.title}</strong>
                          {row.job.company && row.job.title ? ` — ${row.job.title}` : ""}
                        </button>
                      ) : (
                        <strong>Attempt {(c.run_id || "").slice(0, 8)}</strong>
                      )}
                      <small>
                        {c.kind === "auto" ? "Email verification" : words(c.kind || "")} · {stamp(c.created_at)}
                        {c.reason ? ` · ${c.reason}` : ""}
                      </small>
                    </span>
                    <span className="ax-row-end">
                      <Pill tone={challengeTone(c.state)}>{words(c.state || "unknown")}</Pill>
                      {LIVE.has(c.state) && (
                        <ConfirmButton
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
                    </span>
                  </li>
                );
              })}
            </ul>
            <Pagination paging={challengePaging} label="Verification pages" sizes={false} />
          </>
        ) : (
          <Empty title="No email verifications yet" hint="They appear when an application asks for a code or link." />
        )}
      </Panel>
    </>
  );
}
