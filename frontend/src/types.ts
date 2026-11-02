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
  /** What the answer takes. `cannot_automate`: nothing the owner could type or send fits (a video of them); `input_kind` then keeps the shape. */
  kind?: "choice" | "upload" | "text" | "cannot_automate";
  input_kind?: "choice" | "upload" | "text";
  /** The server's plain reason when no answer from the profile or the owner's words completes this field ("" when one does). */
  cannot_automate?: string;
  why?: string;
  allow_other?: boolean;
  /** The form takes several answers ("select all that apply"): sent one option per line. */
  multiple?: boolean;
  /** The answer belongs to one employer ("Why this role?"): never one answer for two employers, however alike the words. */
  employer_only?: boolean;
  accept?: string;
  /** Where on the form it sits, and the row of the work history it belongs to. */
  context?: {
    type?: string;
    section?: string;
    group?: string;
    caption?: string;
    hint?: string;
    /** The other boxes beside a tick box: "None of the above" means nothing without them. */
    peers?: string;
    /** The list empties until something is typed: the form itself searches. */
    search_only?: boolean;
    /** What was read of the list may not be all of it. */
    options_partial?: boolean;
    history_kind?: string;
    history_identity?: { company?: string; title?: string; school?: string };
  } | null;
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
type AccountState =
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
  /** Whose password the record holds: yours (typed here), the main one, a random one only this employer has, or none. */
  password_kind: "yours" | "main" | "own" | "";
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
  admit_by_title?: boolean;
  accept_experience_years?: number;
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
  history_day?: "" | "first";
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
type ApplicationWorkerAssignment = {
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
  // Custom career-site applications run on their own slot (a third lane); absent on older servers.
  custom_workers?: number;
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
type JobControl = {
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
    apply_mode?: "review" | "auto";
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
  // CONFIRMED: jobs with a settled confirmed run. The Applications ledger's count and the Jobs "Confirmed" chip.
  confirmed?: number;
  // The five words (jobpilot/vocabulary.py): each opens exactly that many rows in Jobs; Applied equals `confirmed`.
  words?: Record<"applied" | "applying" | "needs" | "failed" | "not", number>;
  // Jobs › Discovered: fresh listings not applied to yet (the same number its page shows).
  discovered?: number;
};
// GET /api/jobs: one page of Jobs › Pipeline.
export type JobRequest = { at?: string; code?: string; reason?: string; not_before?: string };
// A reserved employer's section (FAANG, MANGO, Big Four...) and whether the owner approved this job (reserved.py).
export type Reserved = { section: string; label: string; employer: string; approved: boolean };
export type PipelineRow = {
  job: Job;
  // A settled confirmed run exists (the ledger): the row reads Confirmed whatever its latest attempt says.
  confirmed?: boolean;
  // The row's word (applied, applying, needs, failed, not) and the one sentence saying what is happening (vocabulary.py).
  word?: "applied" | "applying" | "needs" | "failed" | "not";
  sentence?: string;
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
type FailureReport = {
  summary: string;
  likely_cause: string;
  next_steps: string[];
};
export type Detail = {
  failure_report?: FailureReport | null;
  run: Run;
  /** What happened, as a person follows it (vocabulary.activity). `events` is the engine's own diary, which no screen shows. */
  activity: { id: number; created_at: string; kind: string; message: string }[];
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
  /** How many jobs a review batch gathers (jobpilot/review_batch.py). */
  review_size: number;
  minimum_auto_fit: number;
  max_age_hours: 48 | 168 | 720;
  // Public board APIs list only live postings; null follows max_age_hours (60 days when that is 30 days).
  live_board_age_hours: 48 | 168 | 720 | 1080 | 1440 | 2160 | null;
};
export type Listing = Job & {
  posted_at: string;
  date_basis: string;
  career_level: string;
  match_reason: string;
  // Why the batch has not started this job (autopilot.remember_rejection); absent once anything is done to it.
  rejection?: { reason: string; code?: string; outcome?: string; at?: string };
  // A Ready job the batch will not start: the plain reason and what happens next (jobpilot/startable.py).
  hold?: Hold;
};
export type Hold = { code: string; reason: string; next: string; label?: string };
// GET /api/startable: Ready jobs that can start, and the rest by reason; `idle` says why runners wait when none can.
export type Startable = {
  at: string;
  ready: number;
  startable: number;
  waiting: number;
  held: { code: string; count: number; phrase: string }[];
  idle: string | null;
  // Employers stopped as a whole (jobpilot/loop_guard.py), one item each however many jobs wait behind them.
  employers?: { key: string; kind: "limit" | "loop" | "site"; company: string; reason: string; until: string; jobs: number }[];
};
type SourceHealth = {
  failures: number;
  last_error: string;
  last_ok: string | null;
};
type Scan = {
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
  fits?: Record<"all" | "eligible" | "unranked" | "below" | "uncertain" | "ready" | "held", number>;
  // Why jobs are held, with counts (Ready and Held read the startable reading).
  holds?: Startable["held"];
};

export type Proof = "page" | "both" | "email" | "existing" | "platform" | "final_page" | "dashboard" | "applicant" | "none";
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
  /** What proved it (the server decides from the receipt): see PROOF in model.ts. */
  proof: Proof;
};
/** One platform's (or everything's) success over the window: GET /api/success, defined as the 90% push's score (jobpilot/api/success.py). */
export type Replies = { connected: boolean; complete: boolean; replies: number; job_ids: string[] };

export type SuccessRow = {
  tried: number;
  closed: number;
  already_applied: number;
  owner_only: number;
  eligible: number;
  confirmed: number;
  /** Confirmed over eligible, to one decimal and never rounded up; null with nothing eligible. */
  final_pct: number | null;
  first_try_pct: number | null;
  meets_target: boolean;
};
export type SuccessReport = {
  days: number;
  target: number;
  overall: SuccessRow;
  platforms: (SuccessRow & { ats: string; volume: boolean })[];
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
  checked_at?: string | null;
  created_at?: string | null;
  // When a request to the mailbox last worked and last failed, and whether AXIOM is still trying (agent8/fix-mail). The page
  // reads them when they are there and falls back to `error` and `checked_at` when they are not.
  last_ok_at?: string | null;
  last_error_at?: string | null;
  retrying?: boolean;
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

// GET /api/over-limit: Ready jobs held only because their employer reached the user's limit (jobpilot/startable.py), best fit first.
export type OverLimitRow = {
  id: string;
  company: string;
  title: string;
  location: string;
  ats: string;
  url: string;
  score: number | null;
  created_at: string;
  hold: { code: string; reason: string; count: number; limit: number };
};
export type OverLimitPage = { rows: OverLimitRow[]; total: number; count: number; page: number; pages: number; size: number; at: string };

// GET /api/queue: what is running, then what starts next in the dispatcher's own order (jobpilot/queue_order.py).
export type QueueRunning = {
  job_id: string;
  run_id: string;
  title: string;
  company: string;
  url: string;
  ats: string;
  location: string;
  state: string;
  since: string;
  phase: string;
  /** For a queued run: the gate it is waiting at, in words. */
  gate?: string;
};
export type QueueItem = {
  id: string;
  position: number;
  title: string;
  company: string;
  url: string;
  ats: string;
  location: string;
  score: number | null;
  career_level: string;
  posted_at: string;
  kind: "front" | "request" | "auto" | "retry";
  wait: string;
};
// GET /api/problems: what went wrong recently (jobpilot/problems.py).
export type Problem = {
  run_id: string;
  job_id: string;
  company: string;
  title: string;
  url: string;
  ats: string;
  location: string;
  state: string;
  at: string;
  what: string;
  next: { kind: "retry" | "you" | "stopped" | "done"; sentence: string; at: string };
  attempt: number;
  attempts: number;
  screenshot: boolean;
};
export type ProblemPage = { rows: Problem[]; total: number; page: number; pages: number; size: number; days: number };
export type QueueRetry = {
  job_id: string;
  run_id: string;
  title: string;
  company: string;
  url: string;
  ats: string;
  location: string;
  state: string;
  sentence: string;
  reason: string;
  since: string;
  tries: number;
};
export type QueuePage = {
  at: string;
  planning?: boolean;
  paused: boolean;
  auto_submit: boolean;
  auto_apply: boolean;
  /** The dispatcher starts Ready jobs by itself. Off, only what was asked for starts. */
  dispatching?: boolean;
  stopped: string | null;
  workers: { normal: number; specialist: number; custom: number; free: number };
  decides: string;
  exact: number;
  running: QueueRunning[];
  next: QueueItem[];
  total: number;
  page: number;
  pages: number;
  size: number;
  retrying: QueueRetry[] | null;
  held: Startable["held"];
  held_total: number;
};
export type HeldRow = { id: string; title: string; company: string; url: string; ats: string; location: string; score: number | null; hold: Hold };
export type HeldPage = { rows: HeldRow[]; total: number; page: number; pages: number; size: number; held: Startable["held"]; selectable?: number };

// GET /api/board: one page of the Job board, and each filter's choices with exact counts (jobpilot/board_view.py).
export type BoardRow = {
  id: string;
  title: string;
  company: string;
  location: string;
  url: string;
  ats: string;
  posted_at: string;
  dated: boolean;
  source: string;
  score: number | null;
  state: "queue" | "approval" | "held" | "below" | "checking" | "ready";
  line: string;
  position: number | null;
  can_add: boolean;
  level: string;
  approval: "" | "over" | "reserved";
  /** The reserved employer this row's company is on the user's list as ("" when it is not). */
  reserved: string;
  /** Grouped by company: how many more of this company's rows the page hides after this one. */
  more: number;
};
export type BoardFacet = { value: string; label: string; count: number };
// GET /api/review-batch (jobpilot/review_batch.py): the open batch, and what the empty screen needs to say.
export type ReviewRow = { id: string; title: string; company: string; location: string; url: string; ats: string; posted_at: string; dated: boolean; source: string; score: number; line: string; level: string };
export type ReviewBatch = {
  mode: "review" | "auto";
  planning?: boolean;
  id: string;
  size: number;
  rows: ReviewRow[];
  more: number;
  checking: number;
  searching: boolean;
};

export type BoardPage = {
  planning?: boolean;
  rows: BoardRow[];
  total: number;
  /** What Select all takes: every row these filters show that can be queued or moved up the line, on any page. */
  selectable: number;
  page: number;
  pages: number;
  size: number;
  all: number;
  hidden: { blank: number; stale: number; closed: number; applied: number; twin: number };
  searched_at: string | null;
  scanning: number;
  /** Boards of the latest search that have been read (or failed), out of all of them. */
  scan_read?: number;
  scan_total?: number;
  enabled: boolean;
  window: number;
  grouped?: boolean;
  per_company?: number;
  /** How many company-site jobs the Company sites section holds (jobpilot/company_sites.py); they are never among `rows` of the main list. */
  sites?: number;
  section?: "main" | "sites";
  facets: Record<"fit" | "role" | "level" | "company" | "place" | "platform" | "posted", BoardFacet[]>;
};
// GET /api/jobs/{id}/posting: the job panel's posting.
export type JobPosting = { text: string; link: string; posted_at: string; dated: boolean; source: string; level: string; listed: boolean | null; last_seen: string | null };
export type QueuePlace = { position: number; total: number; wait: string };
