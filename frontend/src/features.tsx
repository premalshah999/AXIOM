// UI for the knowledge, resume and discovery feature services (/api/v2/...).
// Until those routers are mounted, every component here falls back to the
// existing interface; nothing in the core workflow depends on them.
import { useEffect, useState } from "react";
import { Search } from "lucide-react";
import { api, ApiError } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, usePoll } from "./hooks";
import { Actions, Facts as KV, Lede } from "./setup-kit";
import { Button, Empty as AxEmpty, Panel, Pill, Table, type PillTone } from "./ui/index";
import {
  ago,
  Empty,
  ErrorLine,
  External,
  KeyValues,
  Loading,
  plural,
  stamp,
} from "./ui";

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
const LAYER: Record<string, { label: string; tone: string }> = {
  confirmed: { label: "Confirmed fact", tone: "ok" },
  narrative: { label: "Supported narrative", tone: "run" },
  draft: { label: "Draft", tone: "idle" },
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

// ---------- Batch: discovery → ranking → tailoring → ready ----------
export function PipelineReadiness() {
  const { go } = useWorkspace();
  const status = useFeature<Readiness>(
    "discovery",
    (signal) => api("/v2/discovery/status", "GET", undefined, signal),
    30000,
  );
  if (status.unavailable || !status.data) return null; // Unmounted service or first load: the batch view stands alone.
  const s = status.data;
  const building = s.tailoring.building || 0;
  return (
    <section className="block pipeline" aria-labelledby="pipeline-title">
      <header className="block-head">
        <h2 id="pipeline-title">Pipeline</h2>
        <button
          className="link"
          onClick={() => go({ view: "setup", tab: "resumes" })}
        >
          Tailored resumes
        </button>
      </header>
      <ol className="flow">
        <li>
          <b>{s.fresh_jobs}</b> fresh listings
          {s.posting_basis.inferred ? (
            <small> · {s.posting_basis.inferred} with inferred dates</small>
          ) : null}
        </li>
        <li>
          <b>{s.classification.eligible}</b> eligible
          {s.classification.unclassified ? (
            <small> · {s.classification.unclassified} not ranked</small>
          ) : null}
        </li>
        <li>
          <b>{s.tailoring.ready || 0}</b> resumes ready
          {building ? <small> · {building} building</small> : null}
          {s.tailoring.failed ? (
            <small> · {s.tailoring.failed} need repair</small>
          ) : null}
        </li>
        <li>
          <b>{s.ready_to_apply}</b> ready to apply
        </li>
      </ol>
      <p className="block-note">{s.bottleneck}</p>
    </section>
  );
}

// Setup › Knowledge is src/knowledge.tsx; it composes the exported pieces below.

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
    <section className="panel" aria-labelledby="attest-title">
      <header className="panel-head">
        <h2 id="attest-title">Confirmed once</h2>
        <p className="muted">
          Broad statements you confirm in your own words. Withdraw one at any
          time and applications go back to asking.
        </p>
      </header>
      {list.data.items.map((a) => (
        <div key={a.id} className="stack">
          <blockquote className="statement">
            {a.confirmed?.statement || a.statement}
          </blockquote>
          <p className="muted sm">{ATTESTATION_USE[a.id]}</p>
          {a.confirmed ? (
            <p className="row-actions">
              <Tag tone="ok">Confirmed</Tag>
              <span className="muted sm">
                {a.confirmed.confirmed_at ? ago(a.confirmed.confirmed_at) : ""}
              </span>
              <button
                className="btn sm"
                disabled={pending !== null}
                onClick={() =>
                  run(
                    "withdraw:" + a.id,
                    async () => {
                      await api(
                        `/v2/knowledge/facts/${a.confirmed!.fact_id}/retire`,
                        "POST",
                        {},
                      );
                      await list.refresh();
                    },
                    "Withdrawn. Applications will ask again.",
                  )
                }
              >
                Withdraw
              </button>
            </p>
          ) : (
            <p className="row-actions">
              <label className="check">
                <input
                  type="checkbox"
                  checked={!!agreed[a.id]}
                  onChange={(e) =>
                    setAgreed((x) => ({ ...x, [a.id]: e.target.checked }))
                  }
                />
                <span>This is accurate</span>
              </label>
              <button
                className="btn sm primary"
                disabled={pending !== null || !agreed[a.id]}
                onClick={() =>
                  run(
                    "attest:" + a.id,
                    async () => {
                      await api(`/v2/knowledge/attestations/${a.id}`, "POST", {
                        statement: a.statement,
                      });
                      await list.refresh();
                    },
                    "Confirmed.",
                  )
                }
              >
                Confirm
              </button>
            </p>
          )}
        </div>
      ))}
    </section>
  );
}

function FactTable({
  facts,
  employerNames,
}: {
  facts: Fact[];
  employerNames?: Record<string, string>;
}) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Question or field</th>
            <th scope="col">Value</th>
            <th scope="col">Kind</th>
            <th scope="col">Source</th>
          </tr>
        </thead>
        <tbody>
          {facts.map((f) => (
            <tr key={f.id}>
              <td className="wrap">
                {f.question || fieldLabel(f)}
                {f.scope === "employer" && (
                  <small className="muted">
                    {" "}
                    · only for{" "}
                    {employerNames?.[f.employer_id || ""] || "one employer"}
                  </small>
                )}
              </td>
              <td className="wrap">{show(f.value)}</td>
              <td data-label="Kind">
                <Tag tone={LAYER[f.layer].tone}>{LAYER[f.layer].label}</Tag>
                {f.valid_until && (
                  <small className="muted">
                    {" "}
                    · until {stamp(f.valid_until)}
                  </small>
                )}
              </td>
              <td data-label="Source">
                {SOURCE[f.provenance.source || ""] ||
                  f.provenance.source ||
                  "—"}
                {f.provenance.at && (
                  <small className="muted"> · {ago(f.provenance.at)}</small>
                )}
                {f.version > 1 && (
                  <small className="muted"> · v{f.version}</small>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

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
  const pages = Math.max(1, Math.ceil(total / size));
  if (total <= size) return null;
  const nums = [...new Set([1, page - 1, page, page + 1, pages])]
    .filter((n) => n >= 1 && n <= pages)
    .sort((a, b) => a - b);
  return (
    <nav className="pager" aria-label={label}>
      <span className="pager-range">
        {(page - 1) * size + 1}–{Math.min(page * size, total)} of {total}
      </span>
      <div className="pager-pages">
        <button
          className="pager-btn"
          disabled={page === 1}
          onClick={() => setPage(page - 1)}
          aria-label="Previous page"
        >
          ‹
        </button>
        {nums.map((n, i) => (
          <span key={n} className="pager-item">
            {i > 0 && n - nums[i - 1] > 1 && (
              <span className="pager-gap">…</span>
            )}
            <button
              className="pager-btn"
              aria-label={`Page ${n}`}
              aria-current={n === page ? "page" : undefined}
              onClick={() => setPage(n)}
            >
              {n}
            </button>
          </span>
        ))}
        <button
          className="pager-btn"
          disabled={page === pages}
          onClick={() => setPage(page + 1)}
          aria-label="Next page"
        >
          ›
        </button>
      </div>
    </nav>
  );
}

export function Facts({
  summary,
}: {
  summary: Record<string, Record<string, number>>;
}) {
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
  const total = (c: string) =>
    Object.values(summary[c] || {}).reduce((a, b) => a + b, 0);
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>What applications can use</h2>
        <p className="muted">
          Confirmed facts answer questions directly. Supported narratives inform
          written answers. Drafts are never used in an application. Employer
          answers stay with that employer.
        </p>
      </header>
      <div className="chips" role="group" aria-label="Category">
        <button
          className="chip"
          aria-pressed={!category}
          onClick={() => {
            setCategory("");
            setPage(1);
          }}
        >
          All
        </button>
        {Object.keys(CATEGORY_LABEL)
          .filter((c) => total(c))
          .map((c) => (
            <button
              key={c}
              className="chip"
              aria-pressed={category === c}
              onClick={() => {
                setCategory(c);
                setPage(1);
              }}
            >
              {CATEGORY_LABEL[c]} <span className="chip-n">{total(c)}</span>
            </button>
          ))}
      </div>
      <div className="toolbar">
        <label className="search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="Search facts"
            placeholder="Search questions"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
        </label>
        <select
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
          <option value="draft">Drafts</option>
        </select>
      </div>
      <ErrorLine error={facts.error} retry={facts.refresh} />
      {!facts.data ? (
        !facts.error && <Loading />
      ) : facts.data.items.length ? (
        <>
          <FactTable facts={facts.data.items} />
          <ServerPager
            page={facts.data.page}
            total={facts.data.total}
            size={facts.data.size}
            setPage={setPage}
            label="Fact pages"
          />
        </>
      ) : (
        <Empty title="No facts match">Clear a filter to see more.</Empty>
      )}
    </section>
  );
}

export function EmployerMemory() {
  const [q, setQ] = useDraft("employer-q", "");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<Employer | null>(null);
  const list = usePoll<Page<Employer>>(
    (signal) =>
      api(
        `/v2/knowledge/employers?page=${page}&size=25&q=${encodeURIComponent(q.trim())}`,
        "GET",
        undefined,
        signal,
      ),
    120000,
    [page, q],
  );
  return (
    <section className="panel">
      <header className="panel-head">
        <h2>Employer memory</h2>
        <p className="muted">
          Answers about one company (prior employment, relatives, why this role)
          are only used for that company. Employers are matched by their
          application site and exact names, never by partial text.
        </p>
      </header>
      <div className="toolbar">
        <label className="search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="Search employers"
            placeholder="Search employers"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
        </label>
      </div>
      <ErrorLine error={list.error} retry={list.refresh} />
      {!list.data ? (
        !list.error && <Loading />
      ) : list.data.items.length ? (
        <>
          <ul className="list">
            {list.data.items.map((e) => (
              <li key={e.id}>
                <span>
                  <strong>{e.display}</strong>
                  <small className="muted">
                    {e.aliases
                      .filter((a) => a.kind === "tenant")
                      .map((a) => a.alias)
                      .join(", ") || "Matched by name"}
                  </small>
                </span>
                <span className="row-actions">
                  <span className="muted sm">
                    {plural(e.memory, "saved answer")}
                  </span>
                  <button
                    className="btn sm"
                    aria-expanded={open?.id === e.id}
                    disabled={!e.memory}
                    onClick={() => setOpen(open?.id === e.id ? null : e)}
                  >
                    {open?.id === e.id ? "Hide" : "Show"}
                  </button>
                </span>
              </li>
            ))}
          </ul>
          <ServerPager
            page={list.data.page}
            total={list.data.total}
            size={list.data.size}
            setPage={setPage}
            label="Employer pages"
          />
          {open && <EmployerFacts employer={open} />}
        </>
      ) : (
        <Empty title="No employer memory yet">
          Company-specific answers appear here after you give one.
        </Empty>
      )}
    </section>
  );
}

function EmployerFacts({ employer }: { employer: Employer }) {
  const facts = usePoll<Page<Fact>>(
    (signal) =>
      api(
        `/v2/knowledge/facts?employer_id=${encodeURIComponent(employer.id)}&size=100`,
        "GET",
        undefined,
        signal,
      ),
    120000,
    [employer.id],
  );
  if (!facts.data)
    return facts.error ? <ErrorLine error={facts.error} /> : <Loading />;
  return (
    <div className="stack">
      <h3>{employer.display}</h3>
      <FactTable
        facts={facts.data.items}
        employerNames={{ [employer.id]: employer.display }}
      />
    </div>
  );
}

// ---------- Setup › Resumes: tailored versions and supported material ----------
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
      <Lede>
        {masterOnly
          ? `Applications are sent with ${source?.name || "your uploaded resume"}, exactly as you uploaded it. Tailoring is paused.`
          : "A job gets a tailored copy only when it reads at least as well as this master. Otherwise the master is sent."}
      </Lede>
      {!master ? (
        <>
          <Lede>
            Tailoring needs an editable copy of your resume. Importing transcribes {source ? source.name : "your resume PDF"}{" "}
            word for word. You compare it with the original, match the spacing, and approve it. Experience and education are
            then never reworded.
          </Lede>
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
            <div className="ax-notice" style={{ marginTop: "var(--space-3)", display: "block" }}>
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
            <p className="ax-hint" style={{ marginTop: "var(--space-3)" }}>
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
                {!v.faithful && (
                  <label className="check">
                    <input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} />
                    <span>These differences are intended</span>
                  </label>
                )}
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
        <Lede>
          {masterOnly
            ? "Drafts for review only. None of these are attached to applications while tailoring is paused."
            : "One per job. Projects and skills are chosen for the posting; experience, education and your contact line are copied from the master unchanged. A version is used only if it reads at least as well as the master."}
        </Lede>
        {list.error && !list.data && (
          <AxEmpty
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
                style={{ width: "auto" }}
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
                <AxEmpty
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
                      {a.job_id ? (
                        <button className="link" onClick={() => openJob(a.job_id!)}>
                          Open job
                        </button>
                      ) : (
                        <span className="ax-hint">Job removed</span>
                      )}
                      <small className="ax-mono ax-hint"> {(a.artifact_id || "").slice(3, 11)}</small>
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
                      <span className="ax-row-end" style={{ flexWrap: "nowrap" }}>
                        <Button size="sm" onClick={() => go({ view: "editor", artifact: a.artifact_id })}>
                          Edit
                        </Button>
                        <External href={`/api/v2/resumes/artifacts/${a.artifact_id}/pdf`} className="link">
                          PDF
                        </External>
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
        {!list.data && !list.error && <p className="ax-hint" role="status">Loading…</p>}
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
          · {plural(roles.length, "role")}, {plural(education.length, "degree")}, {plural(projects.length, "project")},{" "}
          {plural(units.length, "bullet")}
        </span>
      </summary>
      <div className="ax-panel-body">
        <Lede>
          Projects and skills a tailored copy may draw on beyond your master. Tailoring never adds a metric, tool or
          project that isn't in your master or here. Edit it in Profile.
        </Lede>
        <div className="ax-stack">
          {roles.map((r) => (
            <div key={r.row}>
              <h3 style={{ fontSize: "var(--text-ui)", fontWeight: 600 }}>
                {r.title}, {r.company}{" "}
                <small className="ax-hint">
                  {r.start}–{r.current ? "present" : r.end}
                </small>
              </h3>
              <ul className="evidence">
                {units
                  .filter((u) => u.source_row === r.row)
                  .map((u) => (
                    <li key={u.id}>{u.text}</li>
                  ))}
              </ul>
            </div>
          ))}
          {projects.map((p) => (
            <div key={p.row}>
              <h3 style={{ fontSize: "var(--text-ui)", fontWeight: 600 }}>{p.title}</h3>
              <ul className="evidence">
                {units
                  .filter((u) => u.source_row === p.row)
                  .map((u) => (
                    <li key={u.id}>{u.text}</li>
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
  const masterOnly = master.data?.policy.mode === "master";
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
  const a = list.data?.items[0];
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

// ---------- Jobs › Discovered: internet-wide new-grad sweep ----------
type Sweep = {
  state: "never" | "running" | "done" | "error";
  started_at?: string;
  finished_at?: string;
  found?: number;
  feeds?: number;
  boards?: number;
  only_from_boards?: number;
  window_days?: number;
  ingested?: {
    created: number;
    existing: number;
    duplicate: number;
    protected: number;
  };
};

export function NewGradSweep({ onFinished }: { onFinished?: () => void }) {
  const { pending, run } = useAction();
  const sweep = useFeature<Sweep>(
    "discovery-feeds",
    (signal) => api("/v2/discovery/feeds", "GET", undefined, signal),
    10000,
  );
  const [wasRunning, setWasRunning] = useState(false);
  const s = sweep.data;
  useEffect(() => {
    if (s?.state === "running") setWasRunning(true);
    else if (wasRunning && s) {
      setWasRunning(false);
      onFinished?.();
    }
  }, [s?.state]); // eslint-disable-line react-hooks/exhaustive-deps
  if (sweep.unavailable || !s) return null;
  const running = s.state === "running";
  const added = s.ingested?.created ?? 0;
  return (
    <div className="sweep">
      <p>
        {running ? (
          <>
            <span className="spinner" aria-hidden="true" /> Searching new-grad
            lists and employer job boards across the internet…
          </>
        ) : s.state === "done" ? (
          <>
            Last internet sweep {ago(s.finished_at)}:{" "}
            <strong>{s.found} new-grad roles</strong> from {s.feeds} curated
            lists and {s.boards?.toLocaleString()} employer boards
            {added ? `, ${added} new to your board` : ""}.
          </>
        ) : s.state === "error" ? (
          <>
            The last internet sweep could not finish. Your board is unchanged.
          </>
        ) : (
          <>
            Search curated new-grad lists and employer job boards across the
            internet.
          </>
        )}
      </p>
      <button
        className="btn sm"
        disabled={running || pending !== null}
        onClick={() =>
          run(
            "sweep",
            async () => {
              await api("/v2/discovery/feeds/refresh", "POST");
              await sweep.refresh();
            },
            "Sweep started. New roles appear here as soon as it finishes.",
          )
        }
      >
        {running ? "Sweeping…" : "Sweep now"}
      </button>
    </div>
  );
}
