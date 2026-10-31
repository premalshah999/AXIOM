export type AgentModelProvider = "mimo" | "deepseek" | "haiku" | "openai" | "openrouter";
export type AgentSettingsUpdate = {
  enabled: Record<
    "answers" | "accounts" | "email" | "captcha" | "navigator" | "discovery",
    boolean
  >;
  models: Record<"answers" | "navigator", AgentModelProvider>;
};
export type AgentSettings = AgentSettingsUpdate & {
  providers: Record<AgentModelProvider, { available: boolean; model: string }>;
};

export type Job = {
  id: string;
  url: string;
  title: string;
  company: string;
  location: string;
  ats: string;
  status: string;
  score: number | null;
  reason: string;
  resume_id: string | null;
  demo: boolean;
  created_at: string;
};
export type Run = {
  id: string;
  job_id: string;
  state: string;
  mode: string;
  resume_id?: string | null;
  elapsed: number;
  cost: number;
  model_calls: number;
  reason: string;
  created_at: string;
  started_at?: string | null;
  finished_at: string | null;
  receipt: Record<string, unknown>;
  worker_slot?: number | null;
  application_pipeline?: string | null;
  controller_policy?: string | null;
};
export type Resume = {
  id: string;
  name: string;
  roles: string[];
  sha256: string;
  created_at: string;
};
export type Review = {
  id: string;
  run_id: string;
  question: string;
  options: string[];
  reason: string;
  key: string;
  created_at?: string;
  // Owner-facing form (jobpilot/owner_questions.present): the full question with its section or history row,
  // what kind of answer it takes, why it is asked in plain words, and whether a choice list takes typed text.
  display_question?: string;
  requirement?: string;
  kind?: "choice" | "upload" | "text";
  why?: string;
  allow_other?: boolean;
  /** The form takes several answers ("select all that apply"): sent one option per line. */
  multiple?: boolean;
  accept?: string;
};
export type Source = {
  id: string;
  name: string;
  kind: string;
  url: string;
  cron: string;
  enabled: boolean;
  auto_queue: boolean;
  found: number;
  error: string;
  last_run: string | null;
  next_run: string;
};
export type Address = {
  line1: string;
  line2: string;
  city: string;
  state: string;
  postal_code: string;
  country: string;
  county: string;
};
export type WorkEntry = {
  company: string;
  title: string;
  location: string;
  start: string;
  end: string;
  current: boolean;
  description: string;
};
export type EducationEntry = {
  school: string;
  degree: string;
  field_of_study: string;
  start: string;
  end: string;
  gpa: string;
};
// One learned answer: Profile.reviewed_answers items and /api/knowledge entries share this shape.
export type ReviewedAnswer = {
  id: string;
  question: string;
  answer: string;
  options: string[];
  employer: string;
  scope: "personal" | "employer";
  layer: "fact" | "narrative";
};
export type AccountState =
  | "created_locally"
  | "signing_in"
  | "authenticated"
  | "verification_pending"
  | "reset_requested"
  | "password_reset"
  | "password_unknown"
  | "credentials_rejected"
  | "exists"
  | "locked";
/** What the owner sees for an employer account (server: accounts/overview.py). */
export type EmployerAccountStatus =
  | "active"
  | "verification"
  | "password_unknown"
  | "reset_pending"
  | "locked"
  | "created"
  | "signing_in"
  | "registered"
  | "unknown";
export type AccountRow = {
  id: string;
  origin: string;
  host: string;
  tenant: string;
  employer: string;
  state: AccountState | string;
  status: EmployerAccountStatus;
  label: string;
  detail: string;
  attention: boolean;
  login_email: string;
  updated_at: string;
  confirmed_at: string | null;
  owner_set_at: string | null;
  reset_requested_at: number | null;
  can_retry_verification: boolean;
};
export type AccountSummary = {
  accounts: AccountRow[];
  password_problems: string[];
};
export type Profile = {
  name: string;
  email: string;
  phone: string;
  location: string;
  linkedin: string;
  website: string;
  github: string;
  first_name: string;
  last_name: string;
  middle_name: string;
  preferred_name: string;
  phone_country_code: string;
  phone_type: string;
  address: Address;
  work: WorkEntry[];
  education: EducationEntry[];
  skills: string[];
  languages: string[];
  salary_expectation: string;
  availability: string;
  facts: Record<string, unknown>;
  evidence: { id: string; text: string }[];
  target_roles: string[];
  job_priority: "new_grad_first" | "best_fit_first";
  include_internships?: boolean;
  target_locations: string[];
  excluded_keywords: string[];
  minimum_fit: number;
  decline_demographics: boolean;
  approved_consents: string[];
  approved_answers: Record<string, string>;
  reviewed_answers: ReviewedAnswer[];
  application_source: string;
  allow_account_creation: boolean;
  allow_application_consents: boolean;
  accept_all_application_terms: boolean;
  accept_required_marketing?: boolean;
  autonomous: boolean;
  answer_rules?: Partial<AnswerRules>;
};
export type EducationStep = "closest" | "pick3" | "other" | "ask";
export type DemographicRule = {
  decline_first: boolean;
  answer: string;
  when_missing: "closest" | "other" | "ask";
};
export type AnswerRules = {
  education: EducationStep[];
  major_fallback: string;
  source_order: string[];
  source_any_channel: boolean;
  salary_stance: string;
  travel_willingness: string;
  employment_type_sought: string;
  join_talent_communities: boolean;
  demographics: Record<string, DemographicRule>;
  guest_apply_first: boolean;
  open_agreement_links: boolean;
};
export type Limits = { job_token_cap: number; company_limit: number };
export type SchedulerHealth = {
  state?: string;
  degraded?: boolean;
  checked_at?: string;
  last_fired_at?: string;
  error?: string;
};
export type ApplicationWorkerAssignment = {
  slot: number;
  lane: "normal" | "specialist";
  ats: string;
  run_id?: string | null;
  job_id?: string | null;
  state?: string;
  phase?: string;
};
export type ApplicationWorkers = {
  normal_workers: number;
  specialist_workers: number;
  assignments: ApplicationWorkerAssignment[];
};
export type AutopilotRuntime = {
  application_workers?: ApplicationWorkers;
  state?: string;
  target?: number;
  // Every distinct job confirmed since the batch started, whoever started it.
  confirmed?: number;
  confirmed_by_batch?: number;
  confirmed_by_owner?: number;
  queued?: number;
  dispatched?: number;
  attempted_jobs?: number;
  active?: number;
  awaiting_answer?: number;
  waiting_email?: number;
  blocked?: number;
  failed?: number;
  remaining?: number;
  intake_reason?: string;
  intake_enabled?: boolean;
  inventory?: {
    discovered?: number;
    fits?: Partial<Record<"eligible" | "uncertain" | "unranked" | "below", number>>;
    fresh?: number;
    unattempted?: number;
    ready?: number;
    unranked?: number;
    sources_due?: number;
  };
  last_tick?: string | null;
  next_action?: string;
  scheduler?: SchedulerHealth;
};
export type JobControl = {
  state?: string;
  at?: string;
  previous_status?: string;
};
export type Snapshot = {
  autopilot_runtime?: AutopilotRuntime;
  job_controls?: Record<string, JobControl>;
  scheduler_health?: SchedulerHealth;
  scheduler_heartbeat?: { at?: string };
  engine_version?: string;
  autopilot?: {
    state?: string;
    confirmed?: number;
    target?: number;
    waiting_for_jobs?: boolean;
    waiting_for_daily_limit?: boolean;
    queued?: number;
    run_ids?: string[];
    started_at?: string;
    last_tick?: string | null;
    last_error?: string;
  };
  discovery_settings?: Partial<BoardSettings>;
  telegram_health?: { state?: string; checked_at?: string; error?: string };
  // Exact counts over every job's latest run (jobpilot/api/pipeline.py counts).
  counts?: Counts;
  // Only the live rows the Batch page shows: applying, waiting, questions and recent outcomes. Lists page
  // through GET /api/jobs.
  jobs: Job[];
  runs: Run[];
  resumes: Resume[];
  reviews: Review[];
  sources: Source[];
  profile: Profile;
  control: { paused: boolean; auto_submit: boolean };
  stats: {
    confirmed_today: number;
    confirmed_total?: number;
    submissions_reserved: number;
    spend_today: number;
    states: Record<string, number>;
    average_seconds: number;
    model_calls_per_run?: number;
    interventions_today?: number;
    website_receipts_today?: number;
    email_receipts_today?: number;
  };
  config: {
    workers: number;
    timeout: number;
    daily_limit: number;
    budget: number;
    timezone: string;
    model: string;
    model_provider?: string;
    demo: boolean;
    workers_enabled: boolean;
    mimo: boolean;
    telegram: boolean;
    capsolver: boolean;
    engine: string;
    multipage_timeout: number;
    account: boolean;
    account_email: string;
    imap: boolean;
    twocaptcha: boolean;
    telegram_wait: number;
  };
  ats: { id: string; name: string; tier: string; guidance: string }[];
};
export type Counts = {
  // Pipeline groups (model.ts GROUPS plus "new" and "reserved", held for the owner's approval); "all" is every
  // started or paused job.
  groups: Record<string, number>;
  // Each job's latest run by state, unless the owner's pause overrides how it reads.
  states: Record<string, number>;
  pipeline: number;
  jobs: number;
  // Jobs › Discovered: fresh listings not applied to yet (the same number its page shows).
  discovered?: number;
};
// GET /api/jobs: one page of Jobs › Pipeline.
export type JobRequest = { at?: string; code?: string; reason?: string; not_before?: string };
// A reserved employer's section (FAANG, MANGO, Big Four...) and whether the owner approved this job (reserved.py).
export type Reserved = { section: string; label: string; employer: string; approved: boolean };
export type PipelineRow = {
  job: Job;
  run: Run | null;
  attempts: number;
  control: JobControl | null;
  request?: JobRequest | null;
  reserved?: Reserved | null;
  reviews: Review[];
};
// The owner's reserved employers (setting reserved_employers), as GET/PUT /api/reserved carry them.
export type ReservedEmployer = { name: string; aliases: string[]; sites: string[] };
export type ReservedRules = {
  enabled: boolean;
  sections: { id: string; label: string; employers: ReservedEmployer[] }[];
};
// GET /api/reserved: one section's held jobs, every section's count and the saved list.
export type ReservedPage = {
  enabled: boolean;
  sections: { id: string; label: string; employers: string[]; held: number }[];
  section: string;
  rows: PipelineRow[];
  total: number;
  page: number;
  pages: number;
  size: number;
  rules: ReservedRules;
};
export type PipelinePage = {
  rows: PipelineRow[];
  total: number;
  page: number;
  size: number;
  pages: number;
  // The Pipeline chips' counts, read together with these rows.
  counts?: { groups: Record<string, number>; pipeline: number };
};
// POST /api/jobs/apply-many: one result per job.
export type ApplyOutcome = "queued" | "waiting" | "preparing" | "already" | "refused";
export type ApplyManyResult = {
  results: {
    job_id: string;
    state: string;
    outcome: ApplyOutcome;
    message: string;
    code?: string;
    run_id?: string;
    company?: string;
    title?: string;
  }[];
  summary: Partial<Record<ApplyOutcome, number>>;
};
export type FailureReport = {
  summary: string;
  likely_cause: string;
  next_steps: string[];
  source: string;
  evidence: Record<string, unknown>;
};
export type Detail = {
  failure_report?: FailureReport | null;
  run: Run;
  events: {
    id: number;
    created_at: string;
    kind: string;
    message: string;
    data: unknown;
  }[];
  answers: Record<
    string,
    { label: string; answer: string; evidence_ids: string[]; verified: boolean }
  >;
  screenshot: boolean;
};
export type RuntimeHealth = {
  application_workers?: ApplicationWorkers;
  engine_version?: string;
  scheduler?: SchedulerHealth;
  heartbeat?: { at?: string };
  outbox_pending?: number;
  workers?: number;
  models?: Partial<Record<"answers" | "navigator", {
    provider: AgentModelProvider;
    model: string;
    state: "ready" | "cooldown" | "recovery_ready" | "recovering";
    retry_at: string | null;
    last_error: string | null;
  }>>;
};

// Job board (GET /api/job-board) — BoardSettings mirrors jobpilot/jobboard.py.
export type BoardSettings = {
  enabled: boolean;
  interval_hours: 1 | 2 | 3 | 6;
  early_career_only: boolean;
  web_search: boolean;
  auto_apply: boolean;
  batch_target: number;
  minimum_auto_fit: number;
  max_age_hours: 48 | 168 | 720;
};
export type Listing = Job & {
  posted_at: string;
  date_basis: string;
  career_level: string;
  match_reason: string;
  // Why the batch has not started this job (autopilot.remember_rejection); absent once anything is done to it.
  rejection?: { reason: string; code?: string; outcome?: string; at?: string };
};
export type SourceHealth = {
  failures: number;
  last_error: string;
  last_ok: string | null;
};
export type Scan = {
  state?: string;
  started_at?: string;
  checked?: number;
  new?: number;
  matched?: number;
  unknown_date?: number;
  detail_errors?: number;
  error?: string;
};
export type Board = {
  jobs: Listing[];
  sources: (Source & { scan: Scan; health?: SourceHealth })[];
  source_count: number;
  // Boards failing their last scans (enabled ones only); dead = three or more in a row.
  source_health?: { failing: number; dead: number };
  settings: BoardSettings;
  scanning: number;
  last_requested_at: string | null;
  max_age_hours: number;
  search: { google_configured: boolean; provider: string };
  autopilot: Snapshot["autopilot"];
  // Paged requests (?page=): the filtered page, exact totals and every selectable match.
  total?: number;
  fresh_total?: number;
  platforms?: string[];
  page?: number;
  pages?: number;
  size?: number;
  selectable_ids?: string[];
  selectable_total?: number;
  // Matching jobs per fit filter, under the other filters.
  fits?: Record<"all" | "eligible" | "unranked" | "below" | "uncertain", number>;
};

export type Success = {
  job_id: string;
  run_id: string;
  title: string;
  company: string;
  url: string;
  applied_at: string;
  ats: string;
  website_confirmed: boolean;
  email_confirmed: boolean;
  existing_application: boolean;
};
export type Archived = {
  job_id: string;
  run_id?: string;
  title: string;
  company: string;
  url: string;
  ats?: string;
  state: string;
  reason: string;
  archived_at: string;
  attempts: number;
  cost?: number;
  log_bytes: number;
  owner_removed?: boolean;
  uncertain_submission: boolean;
};

// POST /api/jobs/{id}/pause|resume and DELETE /api/jobs/{id} (jobpilot/job_control.py).
export type ControlResult = {
  job_id: string;
  state: string;
  message: string;
  run_id?: string;
};
// POST /api/jobs
// GET /api/mail
export type Mailbox = {
  id: string;
  email: string;
  state: string;
  error: string;
  provider?: "gmail_imap" | "gmail_oauth" | "unknown";
};
export type MailRule = {
  id: string;
  mailbox_id: string;
  employer_origin: string;
  employer_path: string;
  sender_domains: string[];
  link_origins: string[];
};
export type MailChallenge = {
  id: string;
  run_id: string;
  kind: string;
  state: string;
  reason: string;
  created_at: string;
};
export type MailStatus = {
  configured: boolean;
  redirect_uri: string;
  mailboxes: Mailbox[];
  rules: MailRule[];
  challenges: MailChallenge[];
};
