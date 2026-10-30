// UI for the knowledge, resume and discovery feature services (/api/v2/...).
// Until those routers are mounted, every component here falls back to the
// existing interface; nothing in the core workflow depends on them.
import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, usePoll } from "./hooks";
import { ago, plural, stamp } from "./format";
import { Pagination, type Paging } from "./pagination";
import { Actions, Facts as KV } from "./setup-kit";
import { Chips, SearchBox } from "./worktools";
import {
  Button,
  Check,
  Empty,
  ErrorLine,
  External,
  Loading,
  Panel,
  Pill,
  Table,
  type PillTone,
} from "./ui/index";

// ---------- types (mirror jobpilot/features/*) ----------
type Page<T> = { items: T[]; total: number; page: number; size: number };
type Fact = {
  id: string;
  category: string;
  key: string;
  question: string;
  value: unknown;
  options: string[];
  layer: "confirmed" | "narrative" | "draft";
  scope: "global" | "employer";
  employer_id: string | null;
  row_id: string | null;
  provenance: { source?: string; reference?: string; at?: string };
  valid_until: string | null;
  version: number;
};
type Employer = {
  id: string;
  display: string;
  aliases: { alias: string; kind: string }[];
  memory: number;
};
type Artifact = {
  artifact_id: string;
  status:
    | "building"
    | "ready"
    | "failed"
    | "cancelled"
    | "invalid"
    | "kept_master";
  job_id: string | null;
  job_identity: string;
  employer_id: string | null;
  source_resume_id: string;
  pdf_sha: string | null;
  selection: {
    unit_id: string;
    kind: string;
    source_row: string;
    text: string;
    source_text: string;
  }[];
  validation: {
    pages?: number;
    keyword_coverage?: number | null;
    keywords_missing?: string[];
    problems?: string[];
    warnings?: string[];
    repairs?: { unit_id?: string; where?: string; problem: string }[];
    kept_master?: boolean;
    fill?: number | null;
    master_fill?: number | null;
  };
  error: { code?: string; retryable?: boolean; detail?: string };
  model_calls: number;
  created_at: string;
  finished_at: string | null;
};
type Fill = { pages: number; fill: number };
type MasterView = {
  id: string;
  status: "draft" | "approved" | "superseded";
  origin: "import" | "edited" | string;
  source_resume_id: string;
  verification: {
    coverage?: number;
    added_ratio?: number;
    missing?: string[];
    added?: string[];
    faithful?: boolean;
    source_words?: number;
    source_fill?: Fill;
    render_fill?: Fill;
  };
  created_at: string;
  approved_at: string | null;
};
type MasterStatus = {
  master: MasterView | null;
  approved_id: string | null;
  source: { id: string; name: string } | null;
  policy: {
    mode?: "master" | "tailored" | string;
    automatic_tailoring?: boolean;
    source_resume_id?: string;
  };
};
type Inventory = {
  roles: {
    row: string;
    company: string;
    title: string;
    start: string;
    end: string;
    current: boolean;
  }[];
  education: {
    row: string;
    school: string;
    degree: string;
    field_of_study: string;
    end: string;
  }[];
  projects: { row: string; title: string }[];
  units: { id: string; kind: string; source_row: string; text: string }[];
  skills: string[];
};
type Readiness = {
  fresh_jobs: number;
  posting_basis: Record<string, number>;
  classification: {
    eligible: number;
    failed: number;
    unclassified: number;
  };
  tailoring: Record<string, number>;
  ready_to_apply: number;
  bottleneck: string;
};

const unmounted = (e: Error | null) =>
  e instanceof ApiError && (e.status === 404 || e.status === 405);

// Poll a feature endpoint; stop for this session once the service proves absent.
const absent = new Set<string>();
export function useFeature<T>(
  key: string,
  load: (signal: AbortSignal) => Promise<T>,
  interval: number,
  deps: unknown[] = [],
) {
  const [, setGone] = useState(false);
  const enabled = !absent.has(key);
  const poll = usePoll<T>(load, interval, deps, enabled);
  useEffect(() => {
    if (unmounted(poll.error)) {
      absent.add(key);
      setGone(true);
    }
  }, [poll.error, key]);
  return { ...poll, unavailable: !enabled || unmounted(poll.error) };
}

function Tag({ tone, children }: { tone: string; children: React.ReactNode }) {
  return (
    <span className={"pill tone-" + tone}>
      <i aria-hidden="true" />
      {children}
    </span>
  );
}

const CATEGORY_LABEL: Record<string, string> = {
  personal: "Personal details",
  education: "Education",
  experience: "Experience",
  projects: "Projects",
  skills: "Skills",
  demographics: "Demographics",
  work_authorization: "Work authorization",
  agreements: "Agreements",
  clearance: "Clearance",
  employer_relationship: "Employer relationships",
  additional: "Additional questions",
};
const LAYER: Record<string, string> = {
  confirmed: "Confirmed fact",
  narrative: "Supported narrative",
  draft: "Draft",
};
const SOURCE: Record<string, string> = {
  profile_migration: "Profile",
  review: "Your answer to a question",
  telegram: "Telegram",
  app: "Added in the app",
  import: "Imported",
  resume: "Resume",
};

const FIELD_LABEL: Record<string, string> = {
  "kf:education_row": "Degree",
  "kf:work_row": "Role",
  "kf:skills": "Skills",
  "kf:languages": "Languages",
  // Facts the setup guide saves, worded as the question they answer.
  "kf:work_authorization_us": "Authorized to work in the US",
  "kf:requires_future_sponsorship_us": "Needs visa sponsorship now or in the future",
  "kf:current_immigration_status_us": "Current immigration status",
  "kf:us_citizen": "U.S. citizen",
  "kf:opt_end_date": "OPT end date",
  "kf:country_of_citizenship": "Country of citizenship",
  "kf:country_of_residence": "Country of residence",
  "kf:over_18": "18 or older",
  "kf:has_security_clearance": "Holds an active U.S. security clearance",
  "kf:willing_to_relocate": "Willing to relocate",
  "kf:relocation_at_own_expense": "Can relocate at own expense",
  "kf:willing_onsite": "Open to on-site or hybrid work",
  "kf:willing_to_travel": "Willing to travel",
  "kf:travel_percentage_willingness": "Travel up to (%)",
  "kf:availability": "Earliest start",
  "kf:notice_period": "Notice period (days)",
  "kf:has_relatives_at_employer": "Relatives at the employer",
  "kf:government_employment_history": "Government employment in the last 5 years",
  "kf:government_official": "Government official, or a close relative of one",
  "kf:has_non_compete": "Bound by a non-compete",
  "kf:has_employment_restrictions": "Bound by other employment restrictions",
  "kf:has_non_solicitation": "Bound by a non-solicitation agreement",
  "kf:previously_employed_here": "Worked for other employers you apply to",
  "kf:company_relationships": "Business, client or personal relationship with an employer",
  "kf:export_control_restricted_country_history": "Citizen of, or lived in, an export-control restricted country",
  "kf:has_asylee_status": "Asylee status",
  "kf:has_refugee_status": "Refugee status",
  "kf:criminal_conviction": "Criminal conviction",
  "kf:ever_fired": "Ever terminated",
  "kf:asked_to_resign": "Ever asked to resign",
  "kf:involuntarily_discharged": "Ever involuntarily discharged",
  "kf:valid_drivers_license": "Valid driver's license",
  "kf:referred_by_employee": "Referred by a current employee",
  "kf:tax_credit_screening_no": "Answers No to tax-credit (WOTC) screening",
  "kf:pronouns": "Pronouns",
};
function fieldLabel(f: Fact): string {
  if (FIELD_LABEL[f.key]) return FIELD_LABEL[f.key];
  if (f.key.startsWith("kn:evidence:")) return "Story or project note";
  if (f.key.startsWith("review:")) return "Answer to an earlier question";
  const text = f.key
    .replace(/^k[fn]:/, "")
    .replace(/^address_/, "address ")
    .replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function show(value: unknown): string {
  if (value === true) return "Yes";
  if (value === false) return "No";
  if (Array.isArray(value)) return value.map(show).join(", ");
  if (value && typeof value === "object") {
    const v = value as Record<string, unknown>;
    if ("school" in v)
      return [v.degree, v.field_of_study, v.school, v.end]
        .filter(Boolean)
        .join(" · ");
    if ("company" in v)
      return [
        v.title,
        v.company,
        v.start && `${v.start}–${v.current ? "present" : v.end || ""}`,
      ]
        .filter(Boolean)
        .join(" · ");
    return JSON.stringify(v);
  }
  return String(value ?? "");
}

// Profile › Knowledge is src/knowledge.tsx; it composes the exported pieces below.

// ---------- Statements confirmed once (e.g. a complete employment history) ----------
type Attestation = {
  id: string;
  question: string;
  statement: string;
  confirmed: {
    fact_id: string;
    version: number;
    statement: string;
    confirmed_at: string | null;
  } | null;
};

const ATTESTATION_USE: Record<string, string> = {
  employment_history_complete:
    "Lets applications answer “Have you worked for this company?” with No for employers that aren’t in your history, instead of asking you each time.",
};

export function Attestations() {
  const list = useFeature<{ items: Attestation[] }>(
    "knowledge-attestations",
    (signal) => api("/v2/knowledge/attestations", "GET", undefined, signal),
    120000,
  );
  const { pending, run } = useAction();
  const [agreed, setAgreed] = useState<Record<string, boolean>>({});
  if (list.unavailable || !list.data) return null;
  return (
    <Panel title="Confirmed once">
      <ul className="ax-rows">
        {list.data.items.map((a) => (
          <li className="ax-row" key={a.id}>
            <span className="ax-row-main">
              <strong>{a.confirmed?.statement || a.statement}</strong>
              {ATTESTATION_USE[a.id] && <small>{ATTESTATION_USE[a.id]}</small>}
            </span>
            <span className="ax-row-end">
              {a.confirmed ? (
                <>
                  <span className="ax-hint">Confirmed {a.confirmed.confirmed_at ? ago(a.confirmed.confirmed_at) : ""}</span>
                  <Button
                    size="sm"
                    disabled={pending !== null}
                    onClick={() =>
                      run(
                        "withdraw:" + a.id,
                        async () => {
                          await api(`/v2/knowledge/facts/${a.confirmed!.fact_id}/retire`, "POST", {});
                          await list.refresh();
                        },
                        "Withdrawn. Applications will ask again.",
                      )
                    }
                  >
                    Withdraw
                  </Button>
                </>
              ) : (
                <>
                  <Check
                    label="This is accurate"
                    checked={!!agreed[a.id]}
                    onChange={(on) => setAgreed((x) => ({ ...x, [a.id]: on }))}
                  />
                  <Button
                    size="sm"
                    disabled={pending !== null || !agreed[a.id]}
                    onClick={() =>
                      run(
                        "attest:" + a.id,
                        async () => {
                          await api(`/v2/knowledge/attestations/${a.id}`, "POST", { statement: a.statement });
                          await list.refresh();
                        },
                        "Confirmed.",
                      )
                    }
                  >
                    Confirm
                  </Button>
                </>
              )}
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function FactTable({ facts, employerNames }: { facts: Fact[]; employerNames?: Record<string, string> }) {
  return (
    <Table
      label="Facts"
      rows={facts}
      rowKey={(f) => f.id}
      minWidth={640}
      columns={[
        {
          key: "q",
          header: "Question or field",
          wrap: true,
          width: 260,
          cell: (f) => (
            <>
              {f.question || fieldLabel(f)}
              {f.scope === "employer" && (
                <span className="ax-hint"> · only for {employerNames?.[f.employer_id || ""] || "one employer"}</span>
              )}
            </>
          ),
        },
        { key: "v", header: "Value", wrap: true, width: 260, cell: (f) => show(f.value) },
        {
          key: "kind",
          header: "Kind",
          cell: (f) => (
            <>
              {LAYER[f.layer]}
              {f.valid_until && <span className="ax-hint"> · until {stamp(f.valid_until)}</span>}
            </>
          ),
        },
        {
          key: "src",
          header: "Source",
          cell: (f) => (
            <>
              {SOURCE[f.provenance.source || ""] || f.provenance.source || "—"}
              {f.provenance.at && <span className="ax-hint"> · {ago(f.provenance.at)}</span>}
              {f.version > 1 && <span className="ax-hint"> · v{f.version}</span>}
            </>
          ),
        },
      ]}
    />
  );
}

/** The shared pager, driven by a server's page numbers instead of a list held in memory. */
function ServerPager({
  page,
  total,
  size,
  setPage,
  label,
}: {
  page: number;
  total: number;
  size: number;
  setPage: (n: number) => void;
  label: string;
}) {
  if (total <= size) return null;
  const paging: Paging = {
    items: [],
    total,
    page,
    pages: Math.max(1, Math.ceil(total / size)),
    size,
    initialSize: size,
    setPage: setPage as Paging["setPage"],
    setSize: () => undefined,
  };
  return <Pagination paging={paging} label={label} sizes={false} />;
}

export function Facts({ summary }: { summary: Record<string, Record<string, number>> }) {
  const [category, setCategory] = useDraft("knowledge-category", "");
  const [layer, setLayer] = useDraft("knowledge-layer", "");
  const [q, setQ] = useDraft("knowledge-q", "");
  const [page, setPage] = useState(1);
  const query = new URLSearchParams({
    page: String(page),
    size: "25",
    ...(category ? { category } : {}),
    ...(layer ? { layer } : {}),
    ...(q.trim() ? { q: q.trim() } : {}),
  });
  const facts = usePoll<Page<Fact>>(
    (signal) => api("/v2/knowledge/facts?" + query, "GET", undefined, signal),
    120000,
    [query.toString()],
  );
  const total = (c: string) => Object.values(summary[c] || {}).reduce((a, b) => a + b, 0);
  return (
    <Panel title="What applications can use">
      <div className="ax-stack">
        <Chips
          label="Category"
          value={category}
          onChange={(id) => {
            setCategory(id);
            setPage(1);
          }}
          items={[
            { id: "", label: "All" },
            ...Object.keys(CATEGORY_LABEL)
              .filter((c) => total(c))
              .map((c) => ({ id: c, label: CATEGORY_LABEL[c], count: total(c) })),
          ]}
        />
        <div className="ax-toolbar">
          <SearchBox
            label="Search facts"
            placeholder="Search questions"
            value={q}
            onChange={(v) => {
              setQ(v);
              setPage(1);
            }}
          />
          <select
            className="ax-control"
            aria-label="Kind"
            value={layer}
            onChange={(e) => {
              setLayer(e.target.value);
              setPage(1);
            }}
          >
            <option value="">Every kind</option>
            <option value="confirmed">Confirmed facts</option>
            <option value="narrative">Supported narratives</option>
            <option value="draft">Drafts (never used in an application)</option>
          </select>
        </div>
        <ErrorLine error={facts.error} retry={facts.refresh} />
        {!facts.data ? (
          !facts.error && <Loading />
        ) : facts.data.items.length ? (
          <>
            <FactTable facts={facts.data.items} />
            <ServerPager page={facts.data.page} total={facts.data.total} size={facts.data.size} setPage={setPage} label="Fact pages" />
          </>
        ) : (
          <Empty title="No facts match" hint="Clear a filter to see more." />
        )}
      </div>
    </Panel>
  );
}

export function EmployerMemory() {
  const [q, setQ] = useDraft("employer-q", "");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<Employer | null>(null);
  const list = usePoll<Page<Employer>>(
    (signal) =>
      api(`/v2/knowledge/employers?page=${page}&size=25&q=${encodeURIComponent(q.trim())}`, "GET", undefined, signal),
    120000,
    [page, q],
  );
  return (
    <Panel title="Employer memory">
      <div className="ax-stack">
        <p className="ax-hint">Answers about one company are only used for that company, matched by its application site and exact names.</p>
        <div className="ax-toolbar">
          <SearchBox
            label="Search employers"
            placeholder="Search employers"
            value={q}
            onChange={(v) => {
              setQ(v);
              setPage(1);
            }}
          />
        </div>
        <ErrorLine error={list.error} retry={list.refresh} />
        {!list.data ? (
          !list.error && <Loading />
        ) : list.data.items.length ? (
          <>
            <ul className="ax-rows">
              {list.data.items.map((e) => (
                <li className="ax-row" key={e.id}>
                  <span className="ax-row-main">
                    <strong>{e.display}</strong>
                    <small>
                      {e.aliases
                        .filter((a) => a.kind === "tenant")
                        .map((a) => a.alias)
                        .join(", ") || "Matched by name"}
                    </small>
                  </span>
                  <span className="ax-row-end">
                    <span className="ax-hint">{plural(e.memory, "saved answer")}</span>
                    <Button
                      size="sm"
                      aria-expanded={open?.id === e.id}
                      disabled={!e.memory}
                      onClick={() => setOpen(open?.id === e.id ? null : e)}
                    >
                      {open?.id === e.id ? "Hide" : "Show"}
                    </Button>
                  </span>
                </li>
              ))}
            </ul>
            <ServerPager page={list.data.page} total={list.data.total} size={list.data.size} setPage={setPage} label="Employer pages" />
            {open && <EmployerFacts employer={open} />}
          </>
        ) : (
          <Empty title="No employer memory yet" hint="Company-specific answers appear here after you give one." />
        )}
      </div>
    </Panel>
  );
}

function EmployerFacts({ employer }: { employer: Employer }) {
  const facts = usePoll<Page<Fact>>(
    (signal) => api(`/v2/knowledge/facts?employer_id=${encodeURIComponent(employer.id)}&size=100`, "GET", undefined, signal),
    120000,
    [employer.id],
  );
  if (!facts.data) return facts.error ? <ErrorLine error={facts.error} /> : <Loading />;
  return (
    <div className="ax-stack">
      <h3>{employer.display}</h3>
      <FactTable facts={facts.data.items} employerNames={{ [employer.id]: employer.display }} />
    </div>
  );
}

// ---------- Setup › Resumes: tailored versions and supported material ----------
// An employer id looks like "emp:acme-corp" or "emp:careers.acme.com": show it as a name a person can scan.
function employerLabel(id: string) {
  const slug = id.replace(/^emp:/, "");
  if (slug.includes(".")) return slug;
  return slug.replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

const ARTIFACT_TONE: Record<string, [string, string]> = {
  ready: ["ok", "Ready"],
  building: ["run", "Building"],
  failed: ["fix", "Needs repair"],
  cancelled: ["idle", "Cancelled"],
  invalid: ["idle", "Out of date"],
  kept_master: ["idle", "Master kept"],
};

// One decimal below 100% so "2 words missing" never reads as a perfect match.
const pct = (n?: number | null) =>
  n == null
    ? "—"
    : (n < 1 && n > 0.99
        ? (Math.floor(n * 1000) / 10).toFixed(1)
        : Math.round(n * 100)) + "%";

function useMasterStatus() {
  return useFeature<MasterStatus>(
    "resume-master",
    (signal) => api("/v2/resumes/master", "GET", undefined, signal),
    30000,
  );
}

// ---------- Setup › Resumes: the master every tailored copy starts from ----------
const ARTIFACT_PILL: Record<string, PillTone> = {
  ready: "ok",
  building: "info",
  failed: "warn",
  cancelled: "neutral",
  invalid: "neutral",
  kept_master: "neutral",
};

function MasterResume({
  status,
  refresh,
}: {
  status: MasterStatus;
  refresh: () => Promise<unknown> | void;
}) {
  const { go } = useWorkspace();
  const { pending, run } = useAction();
  const [accept, setAccept] = useState(false);
  const { master = null, source = null, approved_id = null } = status;
  const policy = status.policy || {};
  const masterOnly = policy.mode === "master";
  const v = master?.verification || {};
  const importButton = (label: string, primary = false) => (
    <Button
      size="sm"
      variant={primary ? "primary" : "secondary"}
      loading={pending === "import"}
      disabled={!source || (pending !== null && pending !== "import")}
      onClick={() =>
        run(
          "import",
          async () => {
            const r = await api<MasterView>("/v2/resumes/master/import", "POST");
            await refresh();
            return r;
          },
          (r) =>
            r?.verification?.faithful
              ? "Imported. Compare it with the original, then approve it."
              : "Imported with differences. Review them before approving.",
        )
      }
    >
      {pending === "import" ? "Transcribing…" : label}
    </Button>
  );
  return (
    <Panel
      title="Master resume"
      actions={
        master && (
          <Pill tone={master.status === "approved" ? "ok" : v.faithful ? "warn" : "bad"} dot>
            {master.status === "approved" ? "Approved" : "Draft"}
          </Pill>
        )
      }
    >
      {masterOnly && (
        <p className="ax-hint">
          Applications are sent with {source?.name || "your uploaded resume"}, exactly as you uploaded it. Tailoring is paused.
        </p>
      )}
      {!master ? (
        <>
          <p className="ax-hint">
            Tailoring needs an editable copy. Importing transcribes {source ? source.name : "your resume PDF"} word for word;
            you compare it with the original and approve it.
          </p>
          <Actions start note={!source ? "Upload your resume above first." : undefined}>
            {importButton(source ? `Import ${source.name}` : "Import", true)}
          </Actions>
        </>
      ) : (
        <>
          <KV
            items={[
              [
                "Matches the original",
                <>
                  {pct(v.coverage)} of {v.source_words ?? "its"} words
                  {v.added_ratio ? `, ${pct(v.added_ratio)} added` : ""}
                </>,
              ],
              [
                "Page",
                <>
                  {v.render_fill
                    ? `${plural(v.render_fill.pages, "page")}, ${pct(v.render_fill.fill)} full`
                    : "Not built yet"}
                  {v.source_fill && <span className="ax-hint"> · original {pct(v.source_fill.fill)} full</span>}
                </>,
              ],
              [
                master.status === "approved" ? "Approved" : "Created",
                <>
                  {ago(master.approved_at || master.created_at)}
                  <span className="ax-hint"> · {master.origin === "edited" ? "edited by you" : "imported"}</span>
                </>,
              ],
            ]}
          />
          {(!!v.missing?.length || !!v.added?.length) && (
            <div className="ax-stack">
              {!!v.missing?.length && (
                <p>
                  <strong>Missing from this copy:</strong> {v.missing.slice(0, 12).join(", ")}
                </p>
              )}
              {!!v.added?.length && (
                <p>
                  <strong>Not in your original:</strong> {v.added.slice(0, 12).join(", ")}
                </p>
              )}
            </div>
          )}
          {master.status === "draft" && approved_id && (
            <p className="ax-hint">
              Your previously approved master stays in use until you approve this draft.
            </p>
          )}
          <Actions>
            {importButton("Import again")}
            <Button size="sm" onClick={() => go({ view: "editor", artifact: "master" })}>
              Compare and edit
            </Button>
            {master.status === "draft" && (
              <>
                {!v.faithful && <Check checked={accept} onChange={setAccept} label="These differences are intended" />}
                <Button
                  size="sm"
                  variant="primary"
                  loading={pending === "approve"}
                  disabled={(pending !== null && pending !== "approve") || (!v.faithful && !accept)}
                  onClick={() =>
                    run(
                      "approve",
                      async () => {
                        const r = await api(`/v2/resumes/master/${master.id}/approve`, "POST", {
                          accept_differences: accept,
                        });
                        await refresh();
                        return r;
                      },
                      "Approved. Tailoring starts from this master.",
                    )
                  }
                >
                  Approve
                </Button>
              </>
            )}
          </Actions>
        </>
      )}
    </Panel>
  );
}

export function TailoredResumes() {
  const { openJob, go } = useWorkspace();
  const master = useMasterStatus();
  const masterOnly = master.data?.policy?.mode === "master";
  const [status, setStatus] = useDraft("artifact-status", "");
  const [page, setPage] = useState(1);
  const list = useFeature<Page<Artifact>>(
    "resumes",
    (signal) =>
      api(
        `/v2/resumes/artifacts?page=${page}&size=25${status ? "&status=" + status : ""}`,
        "GET",
        undefined,
        signal,
      ),
    30000,
    [page, status],
  );
  if (list.unavailable) return null;
  const items = Array.isArray(list.data?.items) ? list.data!.items : [];
  return (
    <>
      {master.data && <MasterResume status={master.data} refresh={master.refresh} />}
      {!master.unavailable && master.error && !master.data && (
        <p className="ax-error" role="alert">
          {master.error.message}
        </p>
      )}
      <Panel
        title="Tailored versions"
        actions={
          <Button size="sm" onClick={() => go({ view: "editor" })}>
            Open resume editor
          </Button>
        }
      >
        {masterOnly && (
          <p className="ax-hint">Drafts for review only. None of these are attached to applications while tailoring is paused.</p>
        )}
        {list.error && !list.data && (
          <Empty
            title="Could not load tailored versions"
            hint={list.error.message}
            action={<Button size="sm" onClick={list.refresh}>Try again</Button>}
          />
        )}
        {list.data && (
          <div className="ax-stack">
            <div className="ax-toolbar">
              <select
                className="ax-control"
                aria-label="Filter by status"
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value);
                  setPage(1);
                }}
              >
                <option value="">Any status</option>
                <option value="ready">Ready</option>
                <option value="building">Building</option>
                <option value="failed">Needs repair</option>
                <option value="kept_master">Master kept</option>
              </select>
            </div>
            <Table
              label="Tailored resumes"
              rows={items}
              rowKey={(a) => a.artifact_id}
              minWidth={720}
              empty={
                <Empty
                  title="No tailored versions yet"
                  hint={
                    masterOnly
                      ? "Your master resume is used for every application."
                      : "Eligible jobs get a tailored version before an application starts."
                  }
                />
              }
              columns={[
                {
                  key: "job",
                  header: "Job",
                  cell: (a) => (
                    <>
                      {a.employer_id && (
                        <>
                          <strong title={a.artifact_id}>{employerLabel(a.employer_id)}</strong>
                          <span className="ax-hint"> · </span>
                        </>
                      )}
                      {a.job_id ? (
                        <button type="button" className="ax-link" onClick={() => openJob(a.job_id!)}>
                          Open job
                        </button>
                      ) : (
                        <span className="ax-hint">Job removed</span>
                      )}
                      {!a.employer_id && <small className="mono ax-hint"> {(a.artifact_id || "").slice(3, 11)}</small>}
                    </>
                  ),
                },
                {
                  key: "status",
                  header: "Status",
                  cell: (a) => (
                    <span
                      title={
                        a.status === "failed" && a.error?.code
                          ? a.error.code.replaceAll("_", " ") + (a.error.retryable ? " · retried automatically" : "")
                          : undefined
                      }
                    >
                      <Pill tone={ARTIFACT_PILL[a.status] || "neutral"} dot>
                        {ARTIFACT_TONE[a.status]?.[1] || a.status}
                      </Pill>
                    </span>
                  ),
                },
                {
                  key: "checks",
                  header: "Checks",
                  cell: (a) =>
                    a.status === "ready" ? (
                      <>
                        {plural(a.validation?.pages || 1, "page")}
                        {a.validation?.fill != null
                          ? `, ${pct(a.validation.fill)} full`
                          : ` · ${(a.selection || []).length} rows`}
                        {a.validation?.keyword_coverage != null && (
                          <span className="ax-hint"> · {Math.round(a.validation.keyword_coverage * 100)}% keyword overlap</span>
                        )}
                      </>
                    ) : (
                      "—"
                    ),
                },
                { key: "built", header: "Built", cell: (a) => ago(a.finished_at || a.created_at) },
                {
                  key: "act",
                  header: <span className="sr-only">Actions</span>,
                  cell: (a) =>
                    a.status === "ready" ? (
                      <span className="ax-act">
                        <Button size="sm" onClick={() => go({ view: "editor", artifact: a.artifact_id })}>
                          Edit
                        </Button>
                        <External href={`/api/v2/resumes/artifacts/${a.artifact_id}/pdf`}>PDF</External>
                      </span>
                    ) : null,
                },
              ]}
            />
            <ServerPager
              page={list.data.page ?? 1}
              total={list.data.total ?? items.length}
              size={list.data.size ?? 25}
              setPage={setPage}
              label="Tailored resume pages"
            />
          </div>
        )}
        {!list.data && !list.error && <Loading />}
      </Panel>
      <SupportedMaterial />
    </>
  );
}

function SupportedMaterial() {
  const inv = usePoll<Inventory>((signal) => api("/v2/resumes/evidence", "GET", undefined, signal), 300000);
  if (!inv.data)
    return inv.error && !unmounted(inv.error) ? (
      <p className="ax-error" role="alert">
        {inv.error.message}
      </p>
    ) : null;
  const d = inv.data;
  const roles = d.roles || [];
  const education = d.education || [];
  const projects = d.projects || [];
  const units = d.units || [];
  return (
    <details className="ax-panel ax-fold">
      <summary>
        <strong>Supported material</strong>
        <span className="ax-hint">
          {plural(roles.length, "role")}, {plural(education.length, "degree")}, {plural(projects.length, "project")},{" "}
          {plural(units.length, "bullet")}
        </span>
      </summary>
      <div className="ax-panel-body">
        <div className="ax-stack">
          <p className="ax-hint">Tailoring never adds a metric, tool or project that isn't in your master or here.</p>
          {roles.map((r) => (
            <div key={r.row}>
              <h3>
                {r.title}, {r.company}{" "}
                <small className="ax-hint">
                  {r.start}–{r.current ? "present" : r.end}
                </small>
              </h3>
              <ul className="ax-rows">
                {units
                  .filter((u) => u.source_row === r.row)
                  .map((u) => (
                    <li className="ax-row" key={u.id}>
                      <span className="ax-row-main">{u.text}</span>
                    </li>
                  ))}
              </ul>
            </div>
          ))}
          {projects.map((p) => (
            <div key={p.row}>
              <h3>{p.title}</h3>
              <ul className="ax-rows">
                {units
                  .filter((u) => u.source_row === p.row)
                  .map((u) => (
                    <li className="ax-row" key={u.id}>
                      <span className="ax-row-main">{u.text}</span>
                    </li>
                  ))}
              </ul>
            </div>
          ))}
          <KV items={education.map((e): [string, string] => [e.degree || "Degree", `${e.school}${e.end ? " · " + e.end : ""}`])} />
        </div>
      </div>
    </details>
  );
}

// ---------- Job panel: this job's tailored resume ----------
export function JobResume({
  jobId,
  eligible,
}: {
  jobId: string;
  eligible: boolean;
}) {
  const { go } = useWorkspace();
  const { pending, run } = useAction();
  const master = useMasterStatus();
  const masterOnly = master.data?.policy?.mode === "master";
  const list = useFeature<Page<Artifact>>(
    "resumes",
    (signal) =>
      api(
        `/v2/resumes/artifacts?job_id=${encodeURIComponent(jobId)}&size=1`,
        "GET",
        undefined,
        signal,
      ),
    15000,
    [jobId],
  );
  if (list.unavailable || (!list.data && !list.error)) return null;
  const a = list.data?.items?.[0];
  const [tone, label] = a
    ? ARTIFACT_TONE[a.status] || ["idle", a.status]
    : ["idle", "None yet"];
  return (
    <section className="panel-section" aria-labelledby="tailored-title">
      <h3 id="tailored-title">Resume</h3>
      {masterOnly && (
        <p className="muted sm">
          This application is sent with your master resume
          {master.data?.source ? `, ${master.data.source.name}` : ""}.
          {a ? " The draft below is for review only." : ""}
        </p>
      )}
      <ErrorLine error={list.error} retry={list.refresh} />
      {a ? (
        <>
          <p>
            <Tag tone={tone}>{label}</Tag>{" "}
            {a.status === "ready" && (
              <>
                <External
                  href={`/api/v2/resumes/artifacts/${a.artifact_id}/pdf`}
                  className="link"
                >
                  Open PDF
                </External>{" "}
                <button
                  className="btn sm"
                  onClick={() =>
                    go({
                      view: "editor",
                      artifact: a.artifact_id,
                      job: undefined,
                    })
                  }
                >
                  Review in editor
                </button>
              </>
            )}
          </p>
          {a.status === "failed" && (
            <p className="muted sm">
              {a.error.detail || a.error.code}. This is an internal repair, not
              a question for you
              {a.error.retryable ? "; it is retried automatically" : ""}.
            </p>
          )}
          {a.status === "kept_master" && (
            <p className="muted sm">
              The tailored draft did not read better than your master, so the
              master is used.
            </p>
          )}
          {a.status === "ready" && (
            <details className="fold">
              <summary>
                What it says{" "}
                <span className="muted">
                  {plural(a.selection.length, "row")}
                </span>
              </summary>
              <ul className="evidence">
                {a.selection.map((x) => (
                  <li key={x.unit_id}>
                    {x.text}
                    {x.text !== x.source_text && (
                      <small className="muted">
                        {" "}
                        (reworded from: {x.source_text})
                      </small>
                    )}
                  </li>
                ))}
              </ul>
              {!!a.validation.repairs?.length && (
                <p className="muted sm">
                  {plural(a.validation.repairs.length, "suggested change")}{" "}
                  undone (unsupported claim or page overflow); your original
                  wording was kept there.
                </p>
              )}
            </details>
          )}
        </>
      ) : (
        !masterOnly && (
          <p className="muted sm">
            {eligible
              ? "No tailored version yet. One is prepared before this application starts."
              : "Tailored versions are prepared for eligible jobs."}
          </p>
        )
      )}
      {eligible &&
        (!a || a.status === "failed" || a.status === "cancelled") && (
          <button
            className="btn sm"
            disabled={pending !== null}
            onClick={() =>
              run(
                "prepare",
                async () => {
                  const r = await api<{ status: string }>(
                    "/v2/resumes/prepare",
                    "POST",
                    { job_id: jobId },
                  );
                  await list.refresh();
                  return r;
                },
                (r) =>
                  r?.status === "ready"
                    ? "Tailored draft ready."
                    : r?.status === "use_master"
                      ? "Your master resume is used for this job."
                      : "Preparing a tailored draft.",
              )
            }
          >
            {pending
              ? "Preparing…"
              : a
                ? "Try again"
                : masterOnly
                  ? "Draft one for review"
                  : "Prepare now"}
          </button>
        )}
    </section>
  );
}
