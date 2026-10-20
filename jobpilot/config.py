import os
import secrets
import warnings
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Application owners share one browser and event loop. At most this many run at once, ATS workers and specialists together.
MAX_WORKERS = 32
MAX_NORMAL_WORKERS = 30
MAX_SPECIALIST_WORKERS = 4


@dataclass(frozen=True)
class Topology:
    """How many browser owners run, split into ATS workers and specialists.

    Slots are numbered ATS workers 1..N, then specialists N+1..N+S (six and two by default: 1-6 and 7-8).
    """

    normal: int = 6
    specialist: int = 2

    @property
    def total(self) -> int:
        return self.normal + self.specialist

    def slot_ids(self, pool: str) -> list[int]:
        if pool == "specialist":
            return list(range(self.normal + 1, self.total + 1))
        return list(range(1, self.normal + 1))


def legacy_topology(total: int) -> Topology:
    """The layout the old single WORKERS number implied: slots 1-6 are ATS workers and 7-8 specialists, and a larger
    fleet keeps two specialists as its last slots. WORKERS=6 therefore meant six ATS workers and no specialist."""
    total = max(0, min(MAX_WORKERS, int(total)))
    return Topology(min(total, 6), max(0, total - 6)) if total <= 8 else Topology(total - 2, 2)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)
    data_dir: Path = Path(".data")
    database_url: str = ""
    dbos_database_url: str = ""
    app_token: str = ""
    secure_cookies: bool = False
    base_url: str = "http://127.0.0.1:8080"
    mimo_api_key: str = ""
    mimo_base_url: str = "https://api.xiaomimimo.com/v1"
    mimo_model: str = "mimo-v2.6-pro"
    mimo_input_price: float = Field(default=0.435, ge=0)
    mimo_output_price: float = Field(default=0.87, ge=0)
    # MiMo enables deep thinking by default. Form answers need fast JSON, not reasoning traces.
    # "disabled", "enabled", or empty to send nothing (for other OpenAI-compatible providers).
    mimo_thinking: str = "disabled"
    llm_provider: Literal["mimo", "deepseek", "haiku"] = "deepseek"
    haiku_api_key: str = Field(
        default="", repr=False, validation_alias=AliasChoices("haiku_api_key", "anthropic_api_key")
    )
    haiku_base_url: str = "https://api.anthropic.com"
    haiku_model: str = "claude-haiku-5-5"
    haiku_input_price: float = Field(default=0.1, ge=0)
    haiku_output_price: float = Field(default=0.5, ge=0)
    haiku_thinking: str = "disabled"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_thinking: str = "enabled"
    deepseek_reasoning_effort: str = "low"
    # Peak, cache-miss rates: conservative even when requests receive off-peak discounts.
    deepseek_input_price: float = Field(default=0.3, ge=0)
    deepseek_output_price: float = Field(default=1.2, ge=0)
    # Cache-hit input tokens bill at a fraction of the miss rate; most of an application's
    # prompt is a repeated prefix (2026-10-13: dashboard $4.90 for 46M tokens).
    deepseek_cache_price: float = Field(default=0.03, ge=0)
    # TypeSafe Jev answers bounded choices (pick one offered option) before DeepSeek; output is free.
    typesafe_api_key: str = Field(default="", repr=False)
    typesafe_base_url: str = "https://api.typesafe.ai"
    jev_model: str = "jev-1.13.0"
    jev_input_price: float = Field(default=0.042, ge=0)
    jev_min_confidence: float = Field(default=0.8, ge=0.5, le=0.99)
    model_request_concurrency: int = Field(default=7, ge=1, le=7)
    model_background_concurrency: int = Field(default=2, ge=1, le=6)
    browser_recovery_episodes: int = Field(default=2, ge=0, le=4)
    browser_recovery_actions: int = Field(default=6, ge=1, le=12)
    # Supported DeepSeek Flash can share its configured account for masked image observations.
    browser_vision_recovery: bool = True
    answer_quality_enabled: bool = True
    autopilot_refill_seconds: int = Field(default=15, ge=5, le=60)
    vision_api_key: str = ""
    vision_base_url: str = ""
    vision_model: str = ""
    vision_input_price: float = Field(default=0, ge=0)
    vision_output_price: float = Field(default=0, ge=0)
    telegram_bot_token: str = ""
    # Optional distinct BotFather bots for application owners. Main remains global.
    telegram_worker_1_bot_token: str = ""
    telegram_worker_2_bot_token: str = ""
    telegram_worker_3_bot_token: str = ""
    telegram_worker_4_bot_token: str = ""
    telegram_worker_5_bot_token: str = ""
    telegram_worker_6_bot_token: str = ""
    telegram_worker_7_bot_token: str = ""
    telegram_worker_8_bot_token: str = ""
    telegram_user_id: str = ""
    telegram_enabled: bool = True
    # Seconds a worker keeps its page open waiting for a Telegram answer before deferring the job.
    telegram_wait_seconds: int = Field(default=0, ge=0, le=900)
    # Optional Google results provider; free web search and public job catalogs work without it.
    serper_api_key: str = ""
    search_queries_per_sweep: int = Field(default=12, ge=1, le=40)
    # Official 2Captcha SDK for hCaptcha, reCAPTCHA and Turnstile tokens.
    twocaptcha_api_key: str = ""
    captcha_timeout: int = Field(default=300, ge=10, le=300)
    captcha_max_attempts: int = Field(default=2, ge=1, le=5)
    # Owner-authorized 2026-10-14: hCaptcha image challenges are answered in the worker's own
    # browser (2Captcha coordinates for the puzzle image only), so the token belongs to this IP.
    captcha_same_ip: bool = True
    captcha_image_rounds: int = Field(default=3, ge=1, le=8)
    # Optional password for NEW employer accounts; otherwise one is generated and encrypted once.
    # ACCOUNT_PASSWORD is accepted as an older name for the same setting.
    application_password: str = Field(
        default="", validation_alias=AliasChoices("application_password", "account_password")
    )
    # Applicant-provided working iCIMS login; never replace passwords on other ATSs.
    icims_login: str = Field(default="", repr=False)
    # Optional login email override; defaults to the profile email.
    account_email: str = ""
    # Recover an employer account through its reset email when the saved password is rejected.
    account_password_reset: bool = True
    google_client_id: str = ""
    google_client_secret: str = ""
    gmail_address: str = ""
    gmail_app_password: str = ""
    mail_encryption_key: str = ""
    mail_poll_seconds: float = Field(default=3, ge=0.1, le=60)
    mail_wait_seconds: int = Field(default=90, ge=5, le=300)
    # Persisted waits release their browser slot; delivery need not happen in
    # the short synchronous polling window. Never renew this limit on retry.
    mail_durable_wait_seconds: int = Field(default=1200, ge=300, le=1800)
    # Worker topology (docs/INFRA.md). ATS workers take every new application and run that platform's own flow. Specialists
    # take difficult or already-failed ones (the SPECIALIST_* rules below) and get a somewhat larger budget.
    normal_workers: int = Field(default=6, ge=1, le=MAX_NORMAL_WORKERS)
    specialist_workers: int = Field(default=2, ge=0, le=MAX_SPECIALIST_WORKERS)
    # Deprecated single knob for the total. While set it keeps the old implied layout (see legacy_topology).
    workers: int = Field(default=0, ge=0, le=MAX_WORKERS)
    # Wall-clock time, model calls and the per-job token cap are this many times larger on a specialist.
    specialist_budget_multiplier: float = Field(default=1.5, ge=1, le=3)
    # Failures on ATS workers before a job moves to a specialist.
    specialist_after_failures: int = Field(default=2, ge=1, le=5)
    # Specialist attempts per job before it is left for the owner.
    specialist_attempts: int = Field(default=2, ge=1, le=5)
    # Platforms where one failure is enough (comma separated): their hard steps rarely yield to a plain retry.
    specialist_hard_ats: str = "successfactors,eightfold,smartrecruiters,dayforce"
    # A stalled controller (no progress on an unchanged form) also needs only one failure.
    specialist_on_stall: bool = True
    retain_paused_browser: bool = False
    recovery_max_attempts: int = Field(default=2, ge=0, le=3)
    recovery_delay_seconds: int = Field(default=120, ge=30, le=1800)
    discovery_workers: int = Field(default=3, ge=1, le=12)
    # Single-page forms (Greenhouse, Lever, Ashby...).
    application_timeout: int = Field(default=180, ge=30, le=900)
    # Multi-page wizards with accounts and email verification (Workday, Oracle, iCIMS, Taleo...).
    multipage_timeout: int = Field(default=480, ge=60, le=1200)
    daily_application_limit: int = Field(default=100, ge=1, le=500)
    daily_budget_usd: float = Field(default=5, gt=0)
    max_model_calls: int = Field(default=18, ge=1, le=80)
    # IANA zone for the daily limit, the daily budget, schedules and "today" on forms. scripts/setup.sh writes the computer's.
    timezone: str = "UTC"
    # Dashboard snapshot: polls older than this are answered from the last result while it refreshes in
    # the background (0 always builds it in the request). A write through the API makes it stale.
    snapshot_cache_seconds: float = Field(default=5, ge=0, le=60)
    chromium_path: str = ""
    # Desktop Chrome for Docker on Mac (`python -m jobpilot.cli browser`), e.g. http://host.docker.internal:9224
    browser_cdp_url: str = ""
    # Seconds between sweeps that close leftover application tabs on the desktop Chrome (BROWSER_CDP_URL). 0 disables.
    tab_cleanup_seconds: int = Field(default=15, ge=0, le=300)
    # Owner decision 2026-10-19: a second local Chrome that Playwright never attaches to mints hCaptcha tokens
    # (`python -m jobpilot.cli twin-browser`), e.g. http://host.docker.internal:9225. Empty disables it.
    browser_twin_cdp_url: str = ""
    # Host suffixes whose hCaptcha goes to the twin first. iCIMS routes a distrusted browser to its login-only page.
    captcha_twin_hosts: str = "icims.com"
    headless: bool = True
    # Skip images, media and fonts on employer pages. Captcha providers are never blocked.
    block_assets: bool = True
    # Save a DOM snapshot, screenshot and decision per wizard step under DATA_DIR/runs/<id>/steps.
    trace_steps: bool = True
    failure_reports_enabled: bool = True
    repair_agent_enabled: bool = True
    # "adapters": deterministic ATS drivers for multi-page portals, the model only for answers,
    # falling back to the guided flow. "guided": main's guided flow for every site.
    engine: str = "adapters"
    enable_workers: bool = True
    enable_demo: bool = True
    allow_private_urls: bool = False  # Tests only. Never enabled by the production image.

    @model_validator(mode="after")
    def resolve_topology(self):
        if "workers" in self.model_fields_set and self.workers:
            if self.model_fields_set & {"normal_workers", "specialist_workers"}:
                raise ValueError("Set NORMAL_WORKERS and SPECIALIST_WORKERS, or the deprecated WORKERS, not both")
            layout = legacy_topology(self.workers)
            warnings.warn(
                f"WORKERS={self.workers} is deprecated and means {layout.normal} ATS workers + {layout.specialist} "
                "specialists. Set NORMAL_WORKERS and SPECIALIST_WORKERS instead.",
                UserWarning,
                stacklevel=2,
            )
            self.normal_workers, self.specialist_workers = layout.normal, layout.specialist
        if self.normal_workers + self.specialist_workers > MAX_WORKERS:
            raise ValueError(f"NORMAL_WORKERS + SPECIALIST_WORKERS may not exceed {MAX_WORKERS}")
        self.workers = self.normal_workers + self.specialist_workers
        return self

    @property
    def topology(self) -> Topology:
        return Topology(self.normal_workers, self.specialist_workers)

    @property
    def llm_api_key(self):
        return getattr(self, self.llm_provider + "_api_key")

    @property
    def llm_base_url(self):
        return getattr(self, self.llm_provider + "_base_url")

    @property
    def llm_model(self):
        return getattr(self, self.llm_provider + "_model")

    @property
    def llm_thinking(self):
        return getattr(self, self.llm_provider + "_thinking")

    @property
    def llm_input_price(self):
        return getattr(self, self.llm_provider + "_input_price")

    @property
    def llm_output_price(self):
        return getattr(self, self.llm_provider + "_output_price")

    @property
    def llm_cache_price(self):
        return getattr(self, self.llm_provider + "_cache_price", self.llm_input_price)

    def prepare(self):
        self.data_dir = self.data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("resumes", "runs", "sessions"):
            (self.data_dir / name).mkdir(exist_ok=True, mode=0o700)
        if not self.app_token:
            token_file = self.data_dir / "access-token"
            if not token_file.exists():
                fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(secrets.token_urlsafe(32))
            self.app_token = token_file.read_text().strip()
        if not self.database_url:
            self.database_url = f"sqlite:///{self.data_dir / 'application.db'}"
        if not self.dbos_database_url:
            self.dbos_database_url = f"sqlite:///{self.data_dir / 'workflows.db'}"
        os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
        os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "warning")
        return self


def password_problems(password: str) -> list[str]:
    """The strictest common ATS policy (Workday, Taleo, iCIMS, SuccessFactors)."""
    problems = []
    if len(password) < 10:
        problems.append("use at least 10 characters")
    if len(password) > 30:
        problems.append("use at most 30 characters (some Taleo and Oracle tenants reject longer)")
    if not any(c.isupper() for c in password):
        problems.append("add an uppercase letter")
    if not any(c.islower() for c in password):
        problems.append("add a lowercase letter")
    if not any(c.isdigit() for c in password):
        problems.append("add a digit")
    if not any(not c.isalnum() for c in password):
        problems.append("add a symbol")
    return problems


@lru_cache
def settings() -> Settings:
    return Settings().prepare()
