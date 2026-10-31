import { CheckCheck, CopyCheck, Download, ExternalLink, FileCheck2, FileMinus, FileText, FilterX, Globe, LayoutDashboard, MailCheck, SearchX, ShieldCheck, UserCheck, type LucideIcon } from "lucide-react";
import { useEffect, useMemo } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useDraft, useNow, usePoll } from "./hooks";
import { confirmedToday } from "./identity/state";
import { evidenceLabel, PROOF } from "./model";
import type { JobRow } from "./model";
import { usePipelinePage } from "./pipeline";
import { Pagination, usePagination } from "./pagination";
import type { Archived, Proof, Success } from "./types";
import { ArchiveList } from "./archive";
import { useSelection } from "./bulk";
import { RowActions } from "./jobactions";
import { PipelineBulk } from "./pipelinebulk";
import { TopActions, TopLine } from "./shell/Shell";
import { ago, jobWords, platformName, stamp } from "./format";
import { Button, Empty, Listbox, RouteTabs, Table, Tooltip, type Column } from "./ui/index";
import { JobCell, LoadError, Note, SearchBox, StatusPill } from "./worktools";
import "./styles/applications.css";

// The two things a row proves: when it was sent and what proved it. Both are in the row, the receipt is one click away (the job's
// drawer opens on its status and its Proof), and the posting opens in the system browser (a real link with a target, which the
// native window hands to the Mac).

type Evidence = "all" | Exclude<Proof, "both">;
type Period = "all" | "today" | "week" | "month";
type Filters = { q: string; evidence: Evidence; period: Period; platform: string };

const PERIODS: { value: Period; label: string; days: number }[] = [
  { value: "all", label: "Any time", days: 0 },
  { value: "today", label: "Today", days: 1 },
  { value: "week", label: "Last 7 days", days: 7 },
  { value: "month", label: "Last 30 days", days: 30 },
];

// The proof menu: a confirmation page or an employer email (each also counts a job proved by both), then every other proof the
// ledger holds, in this order. One with no application is left out unless it is chosen.
const EVIDENCE: Evidence[] = ["all", "page", "email", "existing", "platform", "final_page", "dashboard", "applicant", "none"];

// What each proof means, in the words a person would use, and its small icon (both shows two).
const PROOF_HELP: Record<Proof, string> = {
  page: "The employer's own page said the application was received.",
  email: "An email from the employer acknowledged the application.",
  both: "The employer's page and an email from the employer both acknowledged it.",
  existing: "The employer says an application from you was already on file.",
  platform: "The platform answered the application as created.",
  final_page: "The last page of the application said it was received.",
  dashboard: "The employer's candidate dashboard lists the application.",
  applicant: "You checked the employer's page or email and recorded it as sent.",
  none: "No confirmation page or employer email is on file. Open it to see why it counts.",
};
const PROOF_ICON: Record<Proof, LucideIcon[]> = {
  page: [FileCheck2],
  email: [MailCheck],
  both: [FileCheck2, MailCheck],
  existing: [CopyCheck],
  platform: [Globe],
  final_page: [FileText],
  dashboard: [LayoutDashboard],
  applicant: [UserCheck],
  none: [FileMinus],
};

function hasProof(r: Success, kind: Evidence) {
  if (kind === "page") return r.website_confirmed;
  if (kind === "email") return r.email_confirmed;
  return kind === "all" || r.proof === kind;
}

// Local midnight of the first day the period covers, as epoch ms (0 for any time).
function periodStart(period: Period, now: number) {
  const days = PERIODS.find((p) => p.value === period)?.days ?? 0;
  if (!days) return 0;
  const d = new Date(now);
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - (days - 1));
  return d.getTime();
}

// A filter that skips one field, so each menu counts what choosing its options would show. The search reads what the row shows: a
// job saved without a company or title is found by its link, as the employer's name is taken from it.
function passes(r: Success, f: Filters, from: number, skip?: keyof Filters) {
  if (skip !== "evidence" && !hasProof(r, f.evidence)) return false;
  if (skip !== "platform" && f.platform !== "all" && r.ats !== f.platform) return false;
  // "Sent" counts what AXIOM sent: the employer's own "you already applied" notice is not that.
  if (skip !== "period" && from && (r.existing_application || Date.parse(r.applied_at) < from)) return false;
  const q = f.q.trim().toLowerCase();
  return skip === "q" || !q || `${r.company || r.url} ${r.title || r.url} ${r.ats}`.toLowerCase().includes(q);
}

// When it was sent, as a person says it: "Today" and 2:14 PM, "Yesterday" and 9:30 AM, "Oct 28" and 3:05 PM.
function sentAt(value: string, now: number) {
  const t = new Date(value);
  if (Number.isNaN(t.getTime())) return null;
  const midnight = new Date(now);
  midnight.setHours(0, 0, 0, 0);
  const clock = t.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (t.getTime() >= midnight.getTime()) return { day: "Today", clock };
  midnight.setDate(midnight.getDate() - 1);
  if (t.getTime() >= midnight.getTime()) return { day: "Yesterday", clock };
  const date = t.toLocaleDateString([], { month: "short", day: "numeric", year: t.getFullYear() === new Date(now).getFullYear() ? undefined : "numeric" });
  return { day: date, clock };
}

function Sent({ r, now }: { r: Success; now: number }) {
  const at = sentAt(r.applied_at, now);
  return (
    <time className="ap-sent" dateTime={r.applied_at} title={`${stamp(r.applied_at)} · ${ago(r.applied_at, now)}`}>
      {at ? (
        <>
          {at.day} <span>{at.clock}</span>
        </>
      ) : (
        "—"
      )}
    </time>
  );
}

function Proof({ r }: { r: Success }) {
  return (
    <Tooltip label={PROOF_HELP[r.proof]}>
      <span className={"ap-proof" + (r.proof === "none" ? " is-none" : "")}>
        {PROOF_ICON[r.proof].map((Icon, i) => (
          <Icon key={i} size={15} aria-hidden="true" />
        ))}
        {evidenceLabel(r)}
      </span>
    </Tooltip>
  );
}

// Only a web link opens from here; anything else a posting might hold is not a page to send the person to.
const webLink = (url: string) => (/^https?:\/\//i.test(url) ? url : "");

const TABS = ["confirmed", "unconfirmed", "archive"];

// Applications: what was sent. Confirmed is the ledger (proof on every row); Unconfirmed holds submissions with no receipt, protected
// from any retry; Archive keeps earlier failures. Discovered and the started list are under Jobs.
export function LedgerView() {
  const { route, counts } = useWorkspace();
  const tab = TABS.includes(route.tab || "") ? route.tab! : "confirmed";
  // Read here so the tabs can say how many there are before they are opened.
  const ledger = usePoll<{ applications: Success[] }>((signal) => api("/successful-applications", "GET", undefined, signal), 60000);
  const archive = usePoll<{ applications: Archived[]; log_bytes: number }>((signal) => api("/archive", "GET", undefined, signal), 60000);
  const tabs = [
    { id: "confirmed", label: "Confirmed", count: ledger.data?.applications.length },
    { id: "unconfirmed", label: "Unconfirmed", count: counts.groups.unconfirmed },
    { id: "archive", label: "Archive", count: archive.data?.applications.length },
  ];
  return (
    <div className="ax-page ax-page-fill">
      <RouteTabs view="ledger" tabs={tabs} label="Applications" current={tab} />
      {tab === "confirmed" && <Confirmed ledger={ledger} />}
      {tab === "unconfirmed" && <Unconfirmed />}
      {tab === "archive" && <ArchiveList archive={archive} />}
    </div>
  );
}

function Confirmed({ ledger }: { ledger: ReturnType<typeof usePoll<{ applications: Success[] }>> }) {
  const { route, go, counts, openJob } = useWorkspace();
  const now = useNow(60000);
  const [q, setQuery] = useDraft("ledger-query", "");
  const [evidence, setEvidence] = useDraft<Evidence>("ledger-evidence", "all");
  const [period, setPeriod] = useDraft<Period>("ledger-period", "all");
  const [platform, setPlatform] = useDraft("ledger-platform", "all");
  // A number elsewhere can open this list already searched.
  useEffect(() => {
    if (route.q) setQuery(route.q);
  }, [route.q, setQuery]);
  const all = useMemo(() => ledger.data?.applications || [], [ledger.data]);
  const filters: Filters = { q, evidence, period, platform };
  const from = periodStart(period, now);
  const shown = useMemo(() => all.filter((r) => passes(r, { q, evidence, period, platform }, from)), [all, q, evidence, period, platform, from]);
  // What each menu would show if its option were chosen (every other filter kept).
  const menus = useMemo(() => {
    const f = { q, evidence, period, platform };
    const rest = (skip: keyof Filters) => all.filter((r) => passes(r, f, from, skip));
    const byProof = rest("evidence");
    const byPlatform = rest("platform");
    const names = new Map<string, number>();
    for (const r of byPlatform) names.set(r.ats, (names.get(r.ats) || 0) + 1);
    if (platform !== "all" && !names.has(platform)) names.set(platform, 0);
    return {
      evidence: EVIDENCE.map((value) => ({
        value,
        label: value === "all" ? "All" : PROOF[value],
        count: byProof.filter((r) => hasProof(r, value)).length,
      })).filter((o) => o.count > 0 || o.value === "all" || o.value === evidence),
      period: PERIODS.map((o) => ({ ...o, count: all.filter((r) => passes(r, f, periodStart(o.value, now))).length })),
      platform: [
        { value: "all", label: "All", count: byPlatform.length },
        ...[...names].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([ats, count]) => ({ value: ats, label: platformName(ats), count })),
      ],
    };
  }, [all, q, evidence, period, platform, from, now]);
  const filtered = evidence !== "all" || period !== "all" || platform !== "all" || q.trim() !== "";
  const clear = () => {
    setQuery("");
    setEvidence("all");
    setPeriod("all");
    setPlatform("all");
  };
  const paging = usePagination(shown, [q, evidence, period, platform].join("|"), 25);
  const today = confirmedToday(all, now).length;
  const unconfirmed = counts.groups.unconfirmed || 0;

  const columns: Column<Success>[] = [
    {
      key: "job",
      header: "Job",
      width: 440,
      truncate: true,
      cell: (r) => (
        <JobCell
          {...jobWords(r)}
          onOpen={() => openJob(r.job_id, r.run_id)}
          meta={
            <>
              <Proof r={r} />
              <Sent r={r} now={now} />
            </>
          }
        />
      ),
    },
    { key: "proof", header: "Proof", hideBelow: 600, cell: (r) => <Proof r={r} /> },
    { key: "sent", header: "Sent", hideBelow: 600, cell: (r) => <Sent r={r} now={now} /> },
    { key: "platform", header: "Platform", hideBelow: 860, width: 150, truncate: true, cell: (r) => <Note>{platformName(r.ats)}</Note> },
    {
      key: "act",
      header: <span className="sr-only">Receipt and posting</span>,
      width: "1%",
      pin: true,
      cell: (r) => {
        const link = webLink(r.url);
        return (
          <span className="ap-act">
            <Button size="sm" className="ax-lead ap-receipt" onClick={() => openJob(r.job_id, r.run_id)}>
              {r.proof === "none" ? "Details" : "Receipt"}
            </Button>
            {link && (
              <a
                className="ax-btn is-sm ax-lead"
                href={link}
                target="_blank"
                rel="noreferrer noopener"
                onClick={(e) => e.stopPropagation()}
                title="Open the posting in your browser"
              >
                Posting <ExternalLink size={12} aria-hidden="true" />
                <span className="sr-only"> (opens in your browser)</span>
              </a>
            )}
          </span>
        );
      },
    },
  ];

  const exportUrl = (() => {
    const p = new URLSearchParams();
    if (evidence !== "all") p.set("evidence", evidence);
    if (platform !== "all") p.set("ats", platform);
    if (from) p.set("since", new Date(from).toISOString());
    if (q.trim()) p.set("q", q.trim());
    const query = p.toString();
    return "/api/successful-applications.csv" + (query ? "?" + query : "");
  })();

  return (
    <>
      <TopActions>
        {shown.length ? (
          // The app serves the file (as it does a resume or a log), so it saves the same way in a browser tab and in the native window.
          <a className="ax-btn" href={exportUrl} download="AXIOM-confirmed-applications.csv" title={`Download ${shown.length.toLocaleString()} applications as a spreadsheet`}>
            <Download size={15} aria-hidden="true" />
            {filtered ? `Export ${shown.length.toLocaleString()} as CSV` : "Export CSV"}
          </a>
        ) : (
          <Button disabled icon={<Download size={15} aria-hidden="true" />}>
            Export CSV
          </Button>
        )}
      </TopActions>
      <TopLine>
        {ledger.data ? (
          <span className="ap-line">
            <button type="button" className="ax-quiet-link" onClick={clear}>
              <b>{all.length.toLocaleString()}</b> confirmed
            </button>
            <i aria-hidden="true">·</i>
            <button type="button" className="ax-quiet-link" onClick={() => { clear(); setPeriod("today"); }}>
              <b>{today.toLocaleString()}</b> today
            </button>
            {unconfirmed > 0 && (
              <>
                <i aria-hidden="true">·</i>
                <button type="button" className="ax-quiet-link ap-flag" onClick={() => go({ view: "ledger", tab: "unconfirmed" })}>
                  <b>{unconfirmed.toLocaleString()}</b> unconfirmed
                </button>
              </>
            )}
          </span>
        ) : (
          !ledger.error && <span className="ap-line is-wait">Counting confirmed applications…</span>
        )}
      </TopLine>
      <div className="ax-toolbar ap-toolbar">
        <SearchBox value={q} onChange={setQuery} label="Search confirmed applications" placeholder="Company, role or platform" />
        <div className="ap-filters">
          <Listbox prefix="Proof" label="Proof" value={evidence} options={menus.evidence} onChange={setEvidence} />
          <Listbox prefix="Sent" label="Sent" value={period} options={menus.period} onChange={setPeriod} />
          <Listbox prefix="Platform" label="Platform" value={platform} options={menus.platform} onChange={setPlatform} />
          {filtered && (
            <Button variant="ghost" size="sm" icon={<FilterX size={14} aria-hidden="true" />} onClick={clear}>
              Clear
            </Button>
          )}
        </div>
      </div>
      {ledger.error && ledger.data && <LoadError error={ledger.error} retry={ledger.refresh} />}
      <Table
        fill
        label="Confirmed applications"
        columns={columns}
        rows={paging.items}
        rowKey={(r) => r.job_id}
        onRowClick={(r) => openJob(r.job_id, r.run_id)}
        loading={!ledger.data && !ledger.error}
        error={ledger.error && !ledger.data ? <LoadError error={ledger.error} retry={ledger.refresh} /> : undefined}
        footer={paging.total > 0 ? <Pagination paging={paging} label="Confirmed pages" /> : undefined}
        empty={
          all.length ? (
            <Empty
              icon={SearchX}
              title="No confirmed applications match"
              hint="Nothing here fits this search and these filters."
              action={
                <Button size="sm" onClick={clear}>
                  Clear the filters
                </Button>
              }
            />
          ) : (
            <Empty
              icon={CheckCheck}
              title="No confirmed applications yet"
              hint="An application appears here once the employer's page or an email from the employer proves it was received, with the time it was sent."
              action={
                <Button size="sm" onClick={() => go({ view: "batch" })}>
                  See what is running
                </Button>
              }
            />
          )
        }
      />
    </>
  );
}

// Every unconfirmed submission, from the server (the browser only had the newest 1,000 jobs to look through).
function Unconfirmed() {
  const { openJob, go } = useWorkspace();
  const now = useNow(30000);
  const list = usePipelinePage("unconfirmed", "", 25);
  const selection = useSelection(200);
  const when = (r: JobRow) => (
    <time dateTime={r.updated} title={stamp(r.updated)}>
      {ago(r.updated, now)}
    </time>
  );
  const columns: Column<JobRow>[] = [
    {
      key: "job",
      header: "Job",
      width: 440,
      truncate: true,
      cell: (r) => (
        <JobCell
          {...jobWords(r.job)}
          onOpen={() => openJob(r.job.id, r.run?.id)}
          meta={
            <>
              <StatusPill state={r.state} />
              {when(r)}
            </>
          }
        />
      ),
    },
    { key: "status", header: "Status", hideBelow: 600, cell: (r) => <StatusPill state={r.state} /> },
    { key: "detail", header: "Detail", hideBelow: 600, width: 300, truncate: true, cell: (r) => (r.run?.reason ? <Note>{r.run.reason}</Note> : null) },
    { key: "updated", header: "Updated", hideBelow: 600, cell: when },
    { key: "act", header: <span className="sr-only">Actions</span>, width: "1%", pin: true, cell: (r) => <RowActions row={r} promote="confirm" /> },
  ];
  return (
    <>
      <TopLine>Submitted, but no receipt was captured. They may have been sent, so they are never retried.</TopLine>
      {list.error && list.loaded && <LoadError error={list.error} retry={list.refresh} />}
      <Table
        fill
        label="Unconfirmed submissions"
        columns={columns}
        rows={list.rows}
        rowKey={(r) => r.job.id}
        onRowClick={(r) => openJob(r.job.id, r.run?.id)}
        selected={selection.chosen}
        onSelectedChange={selection.replace}
        rowLabel={(r) => r.job.company || "job"}
        loading={!list.loaded && !list.error}
        error={list.error && !list.loaded ? <LoadError error={list.error} retry={list.refresh} /> : undefined}
        busy={list.loaded && !list.current}
        footer={list.paging.total > 0 ? <Pagination paging={list.paging} label="Unconfirmed pages" /> : undefined}
        bulk={<PipelineBulk selection={selection} rows={list.rows} filter="unconfirmed" query="" total={list.paging.total} />}
        empty={
          <Empty
            icon={ShieldCheck}
            title="Nothing is waiting for a receipt"
            hint="Every submission so far has a confirmation page or an email from the employer."
            action={
              <Button size="sm" onClick={() => go({ view: "ledger" })}>
                See confirmed applications
              </Button>
            }
          />
        }
      />
    </>
  );
}
