// Settings › Mailbox: the Gmail AXIOM reads employers' codes from, what is true about it right now, the senders it trusts, and the
// verifications it has waited for.
import { useEffect, useId, useState } from "react";
import { Mail, Plus } from "lucide-react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, useNotify, usePoll } from "./hooks";
import { Pagination, usePagination } from "./pagination";
import { Card } from "./connections";
import { SecretInput } from "./setup-kit";
import type { MailChallenge, MailRule, MailStatus, Mailbox } from "./types";
import { ago, day, stamp, plainReason } from "./format";
import { SearchBox } from "./worktools";
import "./styles/settings.css";
import {
  Button,
  ConfirmButton,
  Dialog,
  Empty,
  External,
  Input,
  Monogram,
  Pill,
  Select,
  Skeleton,
  Table,
  type PillTone,
} from "./ui/index";

const LIVE = new Set(["pending", "matched", "consumed"]);
const split = (value: string) =>
  value
    .split(/[\s,]+/)
    .map((x) => x.trim())
    .filter(Boolean);

export type MailHealth = { tone: "ok" | "warn" | "bad"; label: string; detail: string; when: string };

// What the server's own sentences mean for a person.
const EXPLAIN: [RegExp, string][] = [
  [/app password/i, "Gmail rejected the app password."],
  [/expired or was revoked/i, "Google access expired or was revoked."],
  [/interrupted/i, "The connection to Gmail was interrupted."],
  [/operation failed/i, "The last request to Gmail failed."],
];
const explain = (error: string) => EXPLAIN.find(([re]) => re.test(error || ""))?.[1] || error || "";
const at = (v?: string | null) => (v ? Date.parse(v) : NaN);

/**
 * What is true about a mailbox. The server keeps `state` ("connected" while the login is accepted) apart from `error` (the last
 * request's failure, cleared by the next one that works), so "connected" beside a failure is a state of its own: the login is
 * fine and requests are failing. Where the server says when each last happened (`last_ok_at`, `last_error_at`) the later one
 * wins, and a failure that something later superseded is not shown. Without those it is `error` and `checked_at`, the time a
 * request last worked.
 */
export function mailHealth(b: Mailbox): MailHealth {
  const ok = at(b.last_ok_at);
  const bad = at(b.last_error_at);
  const worked = b.last_ok_at ?? b.checked_at ?? null;
  const when = worked ? ago(worked) : "";
  // For a login the server refused, checked_at is the last time it tried, not the last time it worked.
  if (b.state === "reconnect_required")
    return {
      tone: "bad",
      label: "Needs reconnecting",
      detail: `${explain(b.error) || "Gmail rejected the saved login."} Connect the mailbox again.`,
      when: b.last_ok_at ? ago(b.last_ok_at) : "",
    };
  if (b.state && b.state !== "connected")
    return { tone: "warn", label: b.state.replaceAll("_", " ").replace(/^./, (m) => m.toUpperCase()), detail: explain(b.error), when };
  const failing = Number.isNaN(bad) ? !!b.error : Number.isNaN(ok) || bad > ok;
  if (failing)
    return {
      tone: "warn",
      label: "Trouble reaching Gmail",
      detail: `${explain(b.error) || "The last request failed."} ${b.retrying === false ? "AXIOM is not retrying: check the connection." : "AXIOM tries again on its own."}`,
      when,
    };
  return { tone: "ok", label: "Connected", detail: "AXIOM can read this mailbox.", when };
}

const KIND: Record<string, string> = { auto: "Code or link", code: "Code", link: "Link", password_reset: "Password reset" };
const STATE: Record<string, { label: string; tone: PillTone }> = {
  verified: { label: "Verified", tone: "ok" },
  pending: { label: "Waiting for the email", tone: "info" },
  matched: { label: "Email found", tone: "info" },
  consumed: { label: "Used", tone: "info" },
  expired: { label: "No email came", tone: "neutral" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  failed: { label: "Failed", tone: "neutral" },
};

export function MailSettings() {
  const { snapshot, rows, openJob } = useWorkspace();
  const notify = useNotify();
  const mail = usePoll<MailStatus>((signal) => api("/mail", "GET", undefined, signal), 15000);
  const { pending, run } = useAction();
  const [connecting, setConnecting] = useState(false);
  const [ruling, setRuling] = useState(false);
  const [ruleQuery, setRuleQuery] = useState("");

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
  const rulePaging = usePagination(shownRules, q, 10);
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
  const jobFor = (runId: string) =>
    (rows || []).find(
      (r) => r.run?.id === runId || (snapshot.runs || []).some((x) => x.id === runId && x.job_id === r.job.id),
    );
  const profileEmail = snapshot.profile?.email || "";

  if (!data)
    return mail.error ? (
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
      <Skeleton rows={3} mono={false} />
    );
  return (
    <>
      <section className="st-group" aria-labelledby="st-mailbox">
        <div className="st-head">
          <div>
            <h2 id="st-mailbox">Mailbox</h2>
            <p>Where AXIOM reads the verification codes and receipts employers send.</p>
          </div>
          {mailboxes.length > 0 && (
            <Button size="sm" icon={<Plus size={15} aria-hidden="true" />} onClick={() => setConnecting(true)}>
              Connect another Gmail
            </Button>
          )}
        </div>
        {mailboxes.length ? (
          <div className="st-cards">
            {mailboxes.map((box) => {
              const health = mailHealth(box);
              return (
                <Card
                  key={box.id}
                  icon={Mail}
                  name={box.email}
                  sub={`${box.provider === "gmail_imap" ? "Gmail, app password" : "Gmail, Google sign-in"}${box.created_at ? ` · added ${day(box.created_at)}` : ""}`}
                  state={
                    <Pill tone={health.tone} dot>
                      {health.label}
                    </Pill>
                  }
                  wide={mailboxes.length === 1}
                >
                  <div className="st-state">
                    <i className={"ax-lamp " + (health.tone === "ok" ? "is-ok" : "is-ask")} aria-hidden="true" />
                    <div>
                      <strong>{health.detail}</strong>
                      {health.when && <span>{health.tone === "ok" ? "Last checked" : "Last worked"} {health.when}.</span>}
                    </div>
                  </div>
                  <div className="st-card-foot">
                    <Button
                      size="sm"
                      loading={pending === "check" + box.id}
                      disabled={pending !== null && pending !== "check" + box.id}
                      onClick={() => change("check" + box.id, () => api(`/mail/${box.id}/check`, "POST"), "Mailbox checked.")}
                    >
                      Check now
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
                  </div>
                </Card>
              );
            })}
          </div>
        ) : (
          <div className="ax-panel">
            <Empty
              icon={Mail}
              title="No mailbox connected"
              hint="Without one AXIOM cannot read the codes employers send, so most applications stop at email verification."
              action={
                <Button variant="primary" onClick={() => setConnecting(true)}>
                  Connect Gmail
                </Button>
              }
            />
          </div>
        )}
      </section>

      <section className="st-group" aria-labelledby="st-rules">
        <div className="st-head">
          <div>
            <h2 id="st-rules">Trusted senders{rules.length ? ` · ${rules.length.toLocaleString()}` : ""}</h2>
            <p>Which addresses AXIOM trusts when an employer emails a code or a link. Most are learned on their own; add one only for an employer it keeps missing.</p>
          </div>
          <Button size="sm" icon={<Plus size={15} aria-hidden="true" />} disabled={!mailboxes.length} onClick={() => setRuling(true)}>
            Add a rule
          </Button>
        </div>
        {rules.length > 0 && (
          <div className="st-tools">
            <SearchBox label="Search trusted senders" placeholder="Search employers or senders" value={ruleQuery} onChange={setRuleQuery} />
          </div>
        )}
        <Table
          label="Trusted senders"
          rows={rulePaging.items}
          rowKey={(r) => r.id}
          minWidth={340}
          footer={rules.length > 10 ? <Pagination paging={rulePaging} label="Rule pages" sizes={false} /> : undefined}
          empty={
            <Empty
              title={q ? "No rules match" : "No rules yet"}
              hint={q ? "Try a shorter search." : "Rules appear as AXIOM learns which address an employer's emails come from."}
            />
          }
          columns={[
            {
              key: "employer",
              header: "Employer",
              truncate: true,
              width: 320,
              cell: (r) => {
                const text = `${r.employer_origin}${r.employer_path === "/" ? "" : r.employer_path || ""}`;
                return (
                  <>
                    <span title={text}>{text.replace(/^https?:\/\//, "")}</span>
                    <small className="st-sub">{(r.sender_domains || []).join(", ")}</small>
                  </>
                );
              },
            },
            {
              key: "from",
              header: "Trusted senders",
              truncate: true,
              width: 260,
              hideBelow: 600,
              cell: (r) => (r.sender_domains || []).join(", ") || "—",
            },
            {
              key: "links",
              header: "Also trusts links to",
              truncate: true,
              width: 220,
              hideBelow: 860,
              cell: (r) => (r.link_origins || []).join(", ").replaceAll("https://", "") || "—",
            },
            {
              key: "act",
              header: <span className="sr-only">Remove</span>,
              pin: true,
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
      </section>

      <section className="st-group" aria-labelledby="st-verifications">
        <div className="st-head">
          <div>
            <h2 id="st-verifications">Recent verifications</h2>
            <p>Each time an application waited for an email from an employer.</p>
          </div>
        </div>
        <Table
          label="Recent verifications"
          rows={challengePaging.items}
          rowKey={(c) => c.id}
          minWidth={340}
          footer={challenges.length > 10 ? <Pagination paging={challengePaging} label="Verification pages" sizes={false} /> : undefined}
          empty={<Empty title="No email verifications yet" hint="They appear when an application asks for a code or a link." />}
          columns={[
            {
              key: "who",
              header: "Application",
              truncate: true,
              width: 300,
              cell: (c) => {
                const row = jobFor(c.run_id);
                const name = row?.job.company || row?.job.title || "";
                return name ? (
                  <button type="button" className="ax-link st-who2" onClick={() => openJob(row!.job.id, c.run_id)}>
                    <Monogram name={name} employer={!!row?.job.company} />
                    <span>
                      <strong>{name}</strong>
                    </span>
                  </button>
                ) : (
                  <span className="ax-note" title="An application that is no longer in the lists">
                    Earlier application <span className="mono">{(c.run_id || "").slice(0, 8)}</span>
                  </span>
                );
              },
            },
            { key: "kind", header: "Waiting for", hideBelow: 860, cell: (c) => KIND[c.kind] || c.kind.replaceAll("_", " ") },
            {
              key: "state",
              header: "Result",
              cell: (c) => {
                const s = STATE[c.state] || { label: c.state.replaceAll("_", " "), tone: "neutral" as PillTone };
                return (
                  <span title={plainReason(c.reason) || undefined}>
                    <Pill tone={s.tone} dot>
                      {s.label}
                    </Pill>
                  </span>
                );
              },
            },
            { key: "when", header: "When", hideBelow: 600, cell: (c) => <time title={stamp(c.created_at)}>{ago(c.created_at)}</time> },
            {
              key: "act",
              header: <span className="sr-only">Actions</span>,
              pin: true,
              cell: (c) =>
                LIVE.has(c.state) ? (
                  <ConfirmButton
                    label="Cancel wait"
                    confirm="Cancel wait"
                    variant="secondary"
                    pending={pending === "c" + c.id}
                    onConfirm={() => change("c" + c.id, () => api(`/mail-challenges/${c.id}/cancel`, "POST"), "Verification cancelled.")}
                  />
                ) : null,
            },
          ]}
        />
      </section>

      <ConnectGmail
        open={connecting}
        onClose={() => setConnecting(false)}
        profileEmail={profileEmail}
        oauth={data.configured}
        pending={pending}
        onConnect={(email, app_password) =>
          change(
            "imap",
            async () => {
              await api("/mail/imap", "POST", { email: email || profileEmail, app_password });
              setConnecting(false);
            },
            "Gmail connected. Verification codes and receipts are read automatically.",
          )
        }
        onOauth={() =>
          run("connect", async () => {
            const r = await api<{ authorization_url: string }>("/mail/connect", "POST");
            window.location.assign(r.authorization_url);
          })
        }
      />
      <AddRule
        open={ruling}
        onClose={() => setRuling(false)}
        mailboxes={mailboxes}
        pending={pending === "rule"}
        onSave={async (body) => {
          let saved = false;
          await change(
            "rule",
            async () => {
              await api("/mail-rules", "POST", body);
              saved = true;
              setRuling(false);
            },
            "Rule saved.",
          );
          return saved;
        }}
      />
    </>
  );
}

function ConnectGmail({
  open,
  onClose,
  profileEmail,
  oauth,
  pending,
  onConnect,
  onOauth,
}: {
  open: boolean;
  onClose: () => void;
  profileEmail: string;
  oauth: boolean;
  pending: string | null;
  onConnect: (email: string, password: string) => unknown;
  onOauth: () => unknown;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const id = useId();
  // A closed dialog keeps nothing: the password never outlives it.
  useEffect(() => {
    if (!open) {
      setEmail("");
      setPassword("");
    }
  }, [open]);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Connect Gmail"
      sub="AXIOM reads the codes and receipts employers send you. It never sends from this mailbox."
      footer={
        <>
          {oauth && (
            <Button variant="ghost" loading={pending === "connect"} onClick={() => void onOauth()}>
              Connect with Google instead
            </Button>
          )}
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            type="submit"
            form={id}
            loading={pending === "imap"}
            disabled={!password.trim() || (pending !== null && pending !== "imap")}
          >
            Connect Gmail
          </Button>
        </>
      }
    >
      <form
        id={id}
        className="st-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (password.trim()) void onConnect(email, password);
        }}
      >
        <Input
          label="Gmail address"
          hint="Empty: the address on your profile, where employers send codes."
          type="email"
          autoComplete="email"
          placeholder={profileEmail || "you@gmail.com"}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          data-autofocus
        />
        <div className="ax-field">
          <label className="ax-label" htmlFor={id + "-pw"}>
            App password
          </label>
          <SecretInput
            id={id + "-pw"}
            label="app password"
            describedBy={id + "-msg"}
            placeholder="xxxx xxxx xxxx xxxx"
            value={password}
            onChange={setPassword}
          />
          <p className="ax-hint" id={id + "-msg"}>
            16 letters from <External href="https://myaccount.google.com/apppasswords">myaccount.google.com/apppasswords</External>{" "}
            (needs 2-Step Verification). Stored encrypted.
          </p>
        </div>
      </form>
    </Dialog>
  );
}

function AddRule({
  open,
  onClose,
  mailboxes,
  pending,
  onSave,
}: {
  open: boolean;
  onClose: () => void;
  mailboxes: Mailbox[];
  pending: boolean;
  onSave: (body: { mailbox_id: string; employer_origin: string; sender_domains: string[]; link_origins: string[] }) => Promise<boolean>;
}) {
  const [mailbox, setMailbox] = useState("");
  const [employer, setEmployer] = useDraft("mail-employer", "");
  const [senders, setSenders] = useDraft("mail-senders", "");
  const [origins, setOrigins] = useDraft("mail-origins", "");
  const id = useId();
  const selected = mailboxes.some((m) => m.id === mailbox) ? mailbox : mailboxes[0]?.id || "";
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Add a trusted sender"
      sub="For an employer AXIOM keeps missing emails from. On a shared hiring site, use the employer's own path."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" form={id} loading={pending}>
            Save rule
          </Button>
        </>
      }
    >
      <form
        id={id}
        className="st-form"
        onSubmit={async (e) => {
          e.preventDefault();
          const saved = await onSave({
            mailbox_id: selected,
            employer_origin: employer.trim(),
            sender_domains: split(senders),
            link_origins: split(origins),
          });
          if (!saved) return;
          setEmployer("");
          setSenders("");
          setOrigins("");
        }}
      >
        {mailboxes.length > 1 && (
          <Select label="Mailbox" value={selected} onChange={(e) => setMailbox(e.target.value)}>
            {mailboxes.map((box) => (
              <option key={box.id} value={box.id}>
                {box.email}
              </option>
            ))}
          </Select>
        )}
        <Input
          label="Employer URL or path"
          type="url"
          required
          value={employer}
          onChange={(e) => setEmployer(e.target.value)}
          placeholder="https://careers.example.com/company"
          data-autofocus
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
          label="Also trust links to"
          hint="Optional. The employer's own address is already allowed."
          value={origins}
          onChange={(e) => setOrigins(e.target.value)}
          placeholder="https://login.example.com"
        />
      </form>
    </Dialog>
  );
}
