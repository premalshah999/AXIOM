// UI for the knowledge, resume and discovery feature services (/api/v2/...).
// Until those routers are mounted, every component here falls back to the
// existing interface; nothing in the core workflow depends on them.
import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import { useWorkspace } from "./context";
import { useAction, useDraft, usePoll } from "./hooks";
import { ago, day, plural, stamp } from "./format";
import { Pagination, type Paging } from "./pagination";
import { TailorChoice, type TailorState } from "./tailoring";
import { Chips, SearchBox } from "./worktools";
import {
  Button,
  Check,
  Empty,
  ErrorLine,
  Loading,
  Panel,
  Pill,
  Table,
  type PillTone,
} from "./ui/index";

// ---------- types (mirror jobpilot/features/*) ----------
export type Page<T> = { items: T[]; total: number; page: number; size: number };
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
export type Artifact = {
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
  /** The job it was made for, and whether an application went out with it. */
  job?: { company: string; title: string; location: string } | null;
  sent?: { state: "confirmed" | "unknown"; at: string | null } | null;
};
type Fill = { pages: number; fill: number };
export type MasterView = {
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
export type MasterStatus = {
  master: MasterView | null;
  approved_id: string | null;
  source: { id: string; name: string } | null;
  policy: {
    mode?: "master" | "tailored" | string;
    automatic_tailoring?: boolean;
    /** The resume every application sends while the mode is "master". */
    resume_id?: string;
    source_resume_id?: string;
    changed_at?: string;
  };
};
export type Inventory = {
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
export const unmounted = (e: Error | null) =>
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

// ---------- Resume facts shared by the Resumes page (resumes.tsx) and a job's drawer ----------
// An employer id looks like "emp:acme-corp" or "emp:careers.acme.com": show it as a name a person can scan.
export function employerLabel(id: string) {
  const slug = id.replace(/^emp:/, "");
  if (slug.includes(".")) return slug;
  return slug.replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export const ARTIFACT_WORD: Record<string, string> = {
  ready: "Ready",
  building: "Building",
  failed: "Needs repair",
  cancelled: "Cancelled",
  invalid: "Out of date",
  kept_master: "Master kept",
};

export const ARTIFACT_PILL: Record<string, PillTone> = {
  ready: "ok",
  building: "info",
  failed: "warn",
  cancelled: "neutral",
  invalid: "neutral",
  kept_master: "neutral",
};

export function useMasterStatus() {
  return useFeature<MasterStatus>(
    "resume-master",
    (signal) => api("/v2/resumes/master", "GET", undefined, signal),
    30000,
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
  const [tailored, setTailored] = useState<TailorState | null>(null);
  if (list.unavailable || (!list.data && !list.error)) return null;
  const a = tailored ? undefined : list.data?.items?.[0];
  return (
    <Panel
      title="Resume"
      actions={
        a && (
          <Pill tone={ARTIFACT_PILL[a.status] || "neutral"} dot>
            {ARTIFACT_WORD[a.status] || a.status}
          </Pill>
        )
      }
    >
      <div className="dr-stack">
        <TailorChoice jobId={jobId} onChange={setTailored} />
        {masterOnly && !tailored && (
          <p className="ax-note">
            {a?.sent?.state === "confirmed" ? (
              <>This application went out on {day(a.sent.at)} with the tailored version below.</>
            ) : (
              <>
                This application is sent with your uploaded resume
                {master.data?.source ? `, ${master.data.source.name}` : ""}.
                {a ? " The draft below is for review only." : ""}
              </>
            )}
          </p>
        )}
        {list.error && (
          <p className="ax-error" role="alert">
            {list.error.message}{" "}
            <button type="button" className="ax-link" onClick={() => list.refresh()}>
              Try again
            </button>
          </p>
        )}
        {a ? (
          <>
            {a.status === "ready" && (
              <div className="ax-toolbar">
                <Button size="sm" onClick={() => go({ view: "resumes", artifact: a.artifact_id, job: undefined })}>
                  Preview
                </Button>
                <Button
                  size="sm"
                  onClick={() =>
                    go({
                      view: "editor",
                      artifact: a.artifact_id,
                      job: undefined,
                    })
                  }
                >
                  Review in editor
                </Button>
              </div>
            )}
            {a.status === "failed" && (
              <p className="ax-note">
                {a.error.detail || a.error.code}. This is an internal repair, not
                a question for you
                {a.error.retryable ? "; it is retried automatically" : ""}.
              </p>
            )}
            {a.status === "kept_master" && (
              <p className="ax-note">
                The tailored draft did not read better than your master, so the
                master is used.
              </p>
            )}
            {a.status === "ready" && (
              <details className="ax-fold">
                <summary>
                  What it says <span className="ax-note">{plural(a.selection.length, "row")}</span>
                </summary>
                <ul className="ax-rows">
                  {a.selection.map((x) => (
                    <li key={x.unit_id} className="ax-row">
                      <span className="ax-row-main">
                        <span>{x.text}</span>
                        {x.text !== x.source_text && <small>Reworded from: {x.source_text}</small>}
                      </span>
                    </li>
                  ))}
                </ul>
                {!!a.validation.repairs?.length && (
                  <p className="ax-note">
                    {plural(a.validation.repairs.length, "suggested change")}{" "}
                    undone (unsupported claim or page overflow); your original
                    wording was kept there.
                  </p>
                )}
              </details>
            )}
          </>
        ) : (
          !masterOnly && !tailored && (
            <p className="ax-note">
              {eligible
                ? "No tailored version yet. One is prepared before this application starts."
                : "Tailored versions are prepared for eligible jobs."}
            </p>
          )
        )}
        {eligible && !masterOnly && !tailored && (!a || a.status === "failed" || a.status === "cancelled") && (
          <div>
            <Button
              size="sm"
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
                  : "Prepare now"}
            </Button>
          </div>
        )}
      </div>
    </Panel>
  );
}
