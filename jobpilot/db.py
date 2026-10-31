import asyncio
import threading
import time
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    select,
)
from sqlalchemy.engine import make_url
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.types import TypeDecorator

from .schemas import job_title


def now():
    return datetime.now(UTC).isoformat()


def uid():
    return uuid4().hex


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class Resume(Base):
    __tablename__ = "resumes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String)
    filename: Mapped[str] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String, unique=True)
    roles: Mapped[list] = mapped_column(JSON, default=list)
    text: Mapped[str] = mapped_column(Text, default="")
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String, default=now)


class JobTitleText(TypeDecorator):
    """A job title as read: without the "Requisition Title" field label that 26 stored titles carry (schemas.job_title).
    Every read of the column (entities, the dashboard's column selects, the cover letter's job) is clean; the stored
    text is replaced when the title is next written. Reads only: a LIKE pattern bound through here would be altered."""

    impl = String
    cache_ok = True

    def process_result_value(self, value, dialect):
        return job_title(value)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    identity: Mapped[str] = mapped_column(String, unique=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(JobTitleText, default="")
    company: Mapped[str] = mapped_column(String, default="")
    location: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    ats: Mapped[str] = mapped_column(String, default="custom")
    status: Mapped[str] = mapped_column(String, default="new")
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    resume_id: Mapped[str | None] = mapped_column(String, nullable=True)
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String, default=now)


class JobListing(Base):
    """Discovery evidence, separate from immutable application/receipt history."""

    __tablename__ = "job_listings"
    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(String, index=True)
    posted_at: Mapped[str] = mapped_column(String, index=True)
    date_basis: Mapped[str] = mapped_column(String)
    location: Mapped[str] = mapped_column(String)
    country: Mapped[str] = mapped_column(String, default="US")
    career_level: Mapped[str] = mapped_column(String)
    match_reason: Mapped[str] = mapped_column(String)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen: Mapped[str] = mapped_column(String, default=now)
    last_seen: Mapped[str] = mapped_column(String, default=now, index=True)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(String, index=True)
    state: Mapped[str] = mapped_column(String, default="queued", index=True)
    mode: Mapped[str] = mapped_column(String, default="dry_run")
    resume_id: Mapped[str | None] = mapped_column(String, nullable=True)
    packet: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)
    elapsed: Mapped[float] = mapped_column(Float, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(Text, default="")
    receipt: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, default=now)


class ProfileSnapshot(Base):
    """A profile exactly as some run started with it (run_profile.py)."""

    __tablename__ = "profile_snapshots"
    version: Mapped[str] = mapped_column(String, primary_key=True)
    profile: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, default=now)


class Outbox(Base):
    __tablename__ = "application_outbox"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    run_id: Mapped[str] = mapped_column(String, default="", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[float] = mapped_column(Float, default=0)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    error: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[str] = mapped_column(String, default=now)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(String, index=True)
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON, default=list)
    key: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now)
    # The field facts the owner needs to answer: type (an upload is a file, not text), section or history row,
    # hint, accepted file types, whether the options were only partly read (owner_questions.context_from).
    context: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)


# create_all() never adds a column to an existing table. Nullable columns a model gained since a table was created.
ADDED_COLUMNS = {"reviews": {"context": "JSON"}}


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    url: Mapped[str] = mapped_column(Text)
    cron: Mapped[str] = mapped_column(String)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_queue: Mapped[bool] = mapped_column(Boolean, default=False)
    next_run: Mapped[str] = mapped_column(String, default=now)
    last_run: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    found: Mapped[int] = mapped_column(Integer, default=0)


class Budget(Base):
    __tablename__ = "budgets"
    day: Mapped[str] = mapped_column(String, primary_key=True)
    spent: Mapped[float] = mapped_column(Float, default=0)
    submissions: Mapped[int] = mapped_column(Integer, default=0)


class Mailbox(Base):
    __tablename__ = "mailboxes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String, unique=True)
    credentials: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String, default="connected")
    error: Mapped[str] = mapped_column(String, default="")
    checked_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=now)


class MailRule(Base):
    __tablename__ = "mail_rules"
    __table_args__ = (UniqueConstraint("employer_origin", "employer_path"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    mailbox_id: Mapped[str] = mapped_column(String)
    employer_origin: Mapped[str] = mapped_column(String)
    employer_path: Mapped[str] = mapped_column(String, default="/")
    sender_domains: Mapped[list] = mapped_column(JSON)
    link_origins: Mapped[list] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class MailChallenge(Base):
    __tablename__ = "mail_challenges"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(String, index=True)
    rule_id: Mapped[str] = mapped_column(String)
    mailbox_id: Mapped[str] = mapped_column(String)
    recipient: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String, default="pending", index=True)
    since: Mapped[float] = mapped_column(Float)
    expires: Mapped[float] = mapped_column(Float)
    payload: Mapped[str] = mapped_column(Text, default="")
    message_id: Mapped[str] = mapped_column(String, default="")
    reason: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[str] = mapped_column(String, default=now)


class MailConsumption(Base):
    __tablename__ = "mail_consumption"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    challenge_id: Mapped[str] = mapped_column(String)


class MailOAuth(Base):
    __tablename__ = "mail_oauth"
    state_hash: Mapped[str] = mapped_column(String, primary_key=True)
    browser_hash: Mapped[str] = mapped_column(String)
    verifier: Mapped[str] = mapped_column(Text)
    expires: Mapped[float] = mapped_column(Float)


def record(obj):
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


class bytewise(FunctionElement):
    """A text column compared byte by byte: COLLATE "C" on PostgreSQL, unchanged elsewhere (SQLite already compares
    bytes). Ids are hex and times are ISO 8601, so byte order is their real order. Ranking every run by job and time
    under the database's en_US collation spent 26 of a 33 ms count query sorting (2026-10-22); bytewise, 3 ms."""

    inherit_cache = True
    type = String()


@compiles(bytewise)
def _bytewise(element, compiler, **kw):
    return compiler.process(element.clauses, **kw)


@compiles(bytewise, "postgresql")
def _bytewise_postgresql(element, compiler, **kw):
    return compiler.process(element.clauses, **kw) + ' COLLATE "C"'


def latest_runs(*where):
    """Each job's newest run (ties broken by id) among runs matching `where`, as a subquery of light columns.

    Never the packet: the dashboard and the batch read the newest run of every job, and whole rows carry ~320 KB
    packets (2026-10-22).
    """
    rank = (
        func.row_number()
        .over(
            partition_by=bytewise(Run.job_id),
            order_by=(bytewise(Run.created_at).desc(), bytewise(Run.id).desc()),
        )
        .label("rank")
    )
    ranked = select(Run.id, Run.job_id, Run.state, Run.created_at, Run.started_at, Run.finished_at, rank)
    ranked = ranked.where(*where).subquery("ranked")
    return select(ranked).where(ranked.c.rank == 1).subquery("latest")


# Connections for one app process: eight application workers, the discovery, matching, classification and tailoring
# queues, the scheduler, the mail poller and API requests. SQLAlchemy's default (5 + 10 overflow) ran out under load.
POOL_SIZE, POOL_OVERFLOW = 10, 20
# Set by Settings.prepare from DB_AUDIT, so a local installation can measure its own hot paths before moving.
AUDIT = {"enabled": False}
# Connections to these hosts must come through DATABASE_MODE=supabase. A bare DATABASE_URL would create every table in `public`,
# which Supabase publishes to anyone holding the anon key.
HOSTED_DATABASES = (".supabase.co", ".supabase.com")
# Events the application reads back to decide whether it already submitted or must wait (recovery, receipts, the iCIMS
# repost guard). Over a network they are on the server before the caller carries on; every other kind is batched
# (storage/remote_db.EventWriter), so a crash can lose at most the last half second of telemetry and never one of these.
DURABLE_EVENTS = frozenset({"submitting", "verification", "form_post", "model_timeout_retry"})


def remote_options(url):
    """`url` without this application's own parameters (a schema and a pool, from storage.choice), and those."""
    if url.startswith("sqlite"):
        return url, {}
    parsed = make_url(url)
    found = {k: parsed.query[k] for k in ("axiom_schema", "axiom_pool", "axiom_overflow") if k in parsed.query}
    if (parsed.host or "").endswith(HOSTED_DATABASES) and not found:
        raise RuntimeError("Use DATABASE_MODE=supabase and SUPABASE_DB_URL for a Supabase database, not DATABASE_URL.")
    if not found:
        return url, {}
    return parsed.difference_update_query(found).render_as_string(hide_password=False), found


def engine_options(url, remote=None):
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False, "timeout": 30}, "pool_pre_ping": True}
    if remote:
        from .storage.remote_db import RECYCLE_SECONDS

        # Pings are the pool's checkout hook (storage.remote_db): one round trip each is too dear here.
        return {"pool_size": int(remote.get("axiom_pool", POOL_SIZE)), "max_overflow": int(remote.get("axiom_overflow", POOL_OVERFLOW)),
                "pool_timeout": 30, "pool_recycle": RECYCLE_SECONDS, "isolation_level": "AUTOCOMMIT"}
    return {"pool_pre_ping": True, "pool_size": POOL_SIZE, "max_overflow": POOL_OVERFLOW, "pool_timeout": 30}


class Database:
    def __init__(self, url):
        url, options = remote_options(url)
        self.remote = bool(options)
        self.schema = options.get("axiom_schema")
        self.engine = create_engine(url, **engine_options(url, options))
        self.audit = self.settings_cache = self.events = None
        self._exclusive_lock = nullcontext()
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def setup(connection, _):
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA busy_timeout=30000")

        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        if self.remote or AUDIT["enabled"]:
            from .storage.remote_db import StatementAudit

            self.audit = StatementAudit(self.engine)
        if self.remote:
            from .storage.remote_db import EventWriter, SettingCache, install, install_session_hooks

            install(self.engine, self.schema)
            self.settings_cache, self.events = SettingCache(), EventWriter(self.engine, Event)
            install_session_hooks(self, self.Session, Setting, Event)
            self._exclusive_lock = threading.Lock()
        Base.metadata.create_all(self.engine)
        self.add_missing_columns()
        self.add_settings_prefix_index()
        if self.remote:
            self.require_private()
        with self.session() as s:
            if not s.get(Setting, "control"):
                s.add(Setting(key="control", value={"paused": False, "auto_submit": False}))

    def add_missing_columns(self):
        """Add ADDED_COLUMNS an existing table lacks; idempotent, and safe when two processes start together."""
        from sqlalchemy import inspect

        for table, columns in ADDED_COLUMNS.items():
            for name, kind in columns.items():
                if name in {c["name"] for c in inspect(self.engine).get_columns(table)}:
                    continue
                try:
                    with self.engine.begin() as connection:
                        connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
                except Exception:
                    if name not in {c["name"] for c in inspect(self.engine).get_columns(table)}:
                        raise

    def add_settings_prefix_index(self):
        """Let a prefix read use an index: the primary key cannot serve one under en_US collation, so each scanned all
        62,000 settings (2.9 scans/s, 179,000 rows/s, 6 ms each, 2026-10-30). Query with
        `Setting.key.startswith(prefix, autoescape=True)`: in a plain LIKE every `_` is a wildcard, so "job_control:%" has
        the fixed prefix "job" and the planner keeps scanning. Built once, here, before any worker starts: 0.2 s for 62,000
        rows, and only writers wait, of which there are none yet. Not CONCURRENTLY: that cannot run in a transaction and a
        failed build would leave an invalid index that IF NOT EXISTS then keeps."""
        if self.engine.dialect.name != "postgresql":
            return
        with self.engine.begin() as connection:
            # Asked first: CREATE INDEX IF NOT EXISTS takes the table's SHARE lock even when the index exists, and every
            # CLI command that opens the database would wait for writers.
            if connection.exec_driver_sql("SELECT to_regclass('ix_settings_key_prefix')").scalar() is None:
                connection.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_settings_key_prefix ON settings (key text_pattern_ops)"
                )

    @contextmanager
    def session(self):
        with self.Session.begin() as session:
            yield session

    def require_private(self):
        """Refuse to run with anything of ours reachable through the database host's public API (Supabase's)."""
        from .storage.remote_db import exposure_problems

        if found := exposure_problems(self.engine, self.schema, list(Base.metadata.tables)):
            raise RuntimeError("Refusing to start: " + " ".join(found))

    @contextmanager
    def exclusive(self):
        """Short business transactions; never hold this while doing network work."""
        # Remote: one process owns the database, so waiters queue here without taking a pooled connection each. The advisory
        # lock below still serialises other processes (a command run beside the app) and is what guarantees the exclusion.
        with self._exclusive_lock:
            started = time.perf_counter()
            with self.Session() as s:
                if self.engine.dialect.name == "sqlite":
                    s.connection().exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    # One per-app lock for budget/cap reservations and job transitions. These commits are the ones that must
                    # survive a crash ("submitting" before the click), so they wait for the disk even where plain commits do
                    # not (jobpilot/native/pg.py), and on a server whose own setting is off. Under Compose every commit waits
                    # already, and this changes nothing.
                    s.connection().exec_driver_sql(
                        "SELECT pg_advisory_xact_lock(72319001), set_config('synchronous_commit', 'on', true)"
                    )
                try:
                    yield s
                    s.commit()
                except BaseException:
                    s.rollback()
                    raise
            if self.audit:
                self.audit.hold(time.perf_counter() - started)

    async def off_loop(self, call, *args, **kwargs):
        """Run blocking database work where it cannot stall the browsers. Only a remote database needs it: locally a statement
        is a fraction of a millisecond and the call stays inline, exactly as it has always run."""
        if self.remote:
            return await asyncio.to_thread(call, *args, **kwargs)
        return call(*args, **kwargs)

    def event(self, run_id, kind, message, data=None):
        if self.events:
            self.events.add(run_id, kind, message, data, now())  # Stamped now: the row is written a moment later.
            if kind in DURABLE_EVENTS:
                self.events.flush()  # In order behind everything already waiting, and on the server when this returns.
            return
        with self.session() as s:
            s.add(Event(run_id=run_id, kind=kind, message=message, data=data or {}))

    def flush_events(self):
        if self.events:
            self.events.flush()

    def get_setting(self, key, default=None):
        if self.settings_cache:
            from .storage.remote_db import read_setting

            cached = self.settings_cache.get(key)
            if cached is not self.settings_cache.MISSING:
                return default if cached is None else cached
            generation = self.settings_cache.begin_read()
            value = read_setting(self.engine, Setting, key)
            self.settings_cache.store(key, value, generation)
            return default if value is None else value
        with self.session() as s:
            row = s.get(Setting, key)
            return row.value if row else default

    def set_setting(self, key, value):
        if self.settings_cache:
            from .storage.remote_db import single_statement_upsert

            # One round trip without the global lock, held for four otherwise and taken for every setting written.
            # Last write wins on the row, as it does for every get_setting then set_setting in the application.
            single_statement_upsert(self.engine, Setting, key, value)
            self.settings_cache.invalidate([key])
            return
        with self.exclusive() as s:
            row = s.get(Setting, key)
            if row:
                row.value = value
            else:
                s.add(Setting(key=key, value=value))

    def list(self, model, limit=500):
        with self.session() as s:
            return [record(x) for x in s.scalars(select(model).limit(limit))]
