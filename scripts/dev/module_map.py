#!/usr/bin/env python3
"""The target home of every module in `jobpilot/`, and what the boundaries cost today.

docs/ARCHITECTURE.md section 6 is generated from this file, so the map cannot silently leave a module out:

    python scripts/dev/module_map.py --check             # exit 1 if a module has no target, or a target no module
    python scripts/dev/module_map.py --write             # rewrite the generated blocks in docs/ARCHITECTURE.md
    python scripts/dev/module_map.py --crossings         # imports the target forbids, after the planned symbol moves
    python scripts/dev/module_map.py --tests             # which owner's test folder each file in tests/ belongs to

Standard library only, and it never imports the package: it reads source with `ast`, so function-level imports count
(1,135 of the 2,537 import statements are inside functions; a regex over top-level lines misses them).

A target is written `<owner>:<path>`. The owner is a service (`control`, `applier`, `discovery`, `mail`), a library
(`core`, `domain`, `contracts`, `ledger`, `llm`, `knowledge`) or the launcher (`desktop`). The path is relative to that
package's source root: `applier:forms/session.py` is `services/applier/src/axiom_applier/forms/session.py`, `core:db.py` is
`libs/core/src/axiom_core/db.py` and `desktop:supervisor.py` is `desktop/axiom_desktop/supervisor.py`. `split` marks a module
whose contents go to several homes (ARCHITECTURE.md 6.2).
"""

from __future__ import annotations

import argparse
import ast
import collections
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "jobpilot"
DOC = ROOT / "docs" / "ARCHITECTURE.md"

SERVICES = ("control", "applier", "discovery", "mail")
LIBRARIES = ("core", "domain", "contracts", "ledger", "llm", "knowledge")
OWNERS = (*SERVICES, *LIBRARIES, "desktop")

# Which service may import which library. Anything else that crosses an owner is a call, an event or a read grant.
# ARCHITECTURE.md 5.2 turns this into import-linter contracts.
LIBRARY_RULES = {
    "control": set(LIBRARIES),
    "applier": set(LIBRARIES),
    "discovery": {"core", "domain", "contracts", "llm", "knowledge"},
    "mail": {"core", "domain", "contracts", "knowledge"},
    # Libraries may only depend downwards.
    "core": set(),
    "domain": {"core"},
    "contracts": {"core", "domain"},
    "llm": {"core", "domain"},
    "knowledge": {"core", "domain", "llm"},
    "ledger": {"core", "domain", "contracts", "knowledge", "llm"},
    "desktop": set(),  # The supervisor is standard library only, and must stay small enough to outlive what it supervises.
}

# Names that leave their module for a lower layer. An import of one of these is not a boundary problem: it is a move.
# {module: {name: new owner}}. The move itself is phase (b) of the migration.
_MATCHING = "norm _alias_group best_option placeholder_option parse_date date_text checkbox_value foreign_fallback ALIASES US_STATES"
_BOARD_TYPES = "FITS FOREIGN LOCKED fit_of us_location GERMAN_ROLE BoardSettings settings"
SYMBOL_MOVES = {
    "widgets.py": dict.fromkeys(_MATCHING.split(), "domain"),
    "forms.py": dict.fromkeys(["EMPLOYER_CONFIRMATION_RE"], "domain"),
    "browser.py": dict.fromkeys(["posting_gone"], "domain"),
    "accounts/__init__.py": dict.fromkeys(["registrable", "AccountStore"], "domain"),
    "adapters/icims.py": dict.fromkeys(["ICIMS"], "domain"),
    "discovery.py": dict.fromkeys(["application_entry", "title_admits"], "domain"),
    "features/discovery/__init__.py": dict.fromkeys(["gate", "policy_for"], "domain"),
    "features/discovery/policy.py": dict.fromkeys(["SENIOR", "DiscoveryPolicy", "unfetched"], "domain"),
    "features/discovery/feeds.py": dict.fromkeys(["INTERN", "NEW_GRAD"], "domain"),
    "jobboard.py": dict.fromkeys(_BOARD_TYPES.split(), "domain"),
    "execution_services.py": dict.fromkeys(["enabled", "master_policy", "packet_metadata", "authoritative_packet"], "knowledge"),
    "repair_agent.py": dict.fromkeys(["MASKED_VALUE", "YES_NO", "CONFIRMATION"], "domain"),
    "failure_reports.py": dict.fromkeys(["clean"], "domain"),
    "telegram.py": dict.fromkeys(["configured"], "core"),
    "telegram_workers.py": dict.fromkeys(["any_configured"], "core"),
    "owner_questions.py": dict.fromkeys(["document_for", "generic", "row_for", "context_from", "*"], "domain"),
    "owner_apply.py": dict.fromkeys(["picked", "PICK"], "ledger"),
    "service/queueing.py": dict.fromkeys(["fit_blocker"], "ledger"),
    "operations/superseded.py": dict.fromkeys(["close_superseded_waits"], "ledger"),
    "operations/reset.py": dict.fromkeys(["guard_key"], "domain"),
    "recovery.py": dict.fromkeys(["schedule_recovery", "open_blocked_site_circuit"], "ledger"),
    "knowledge.py": dict.fromkeys(["relevant_fact", "topic", "GOVERNMENT", "IMMIGRATION_VISA", "NDA_INSTRUCTION"], "domain"),
    "features/resumes/__init__.py": dict.fromkeys(["variant_rows", "current_artifact", "preparation_state"], "knowledge"),
    "features/resumes/claims.py": dict.fromkeys(["numbers"], "domain"),
    "features/knowledge/migration.py": dict.fromkeys(["row_id"], "domain"),
    "admission.py": dict.fromkeys(["employer_partition", "QueueBlocked", "SHARED_LINK_HOSTS"], "domain"),
    "tiers.py": dict.fromkeys(["apply_model", "call_limit", "effort_for", "provider_for", "tier_of", "token_floor"], "llm"),
    "receipts.py": dict.fromkeys(["unfinished_receipt"], "domain"),
    "mail/__init__.py": dict.fromkeys(
        ["origin", "platform_link_allowed", "tracker_allowed", "employer_label", "tenant_tokens", "LIVE", "RuleInput"], "domain"
    ),
    "diagnostics.py": dict.fromkeys(["current_help_bytes"], "ledger"),
    "workers.py": dict.fromkeys(["enqueue_source"], "discovery"),
    "schemas.py": dict.fromkeys(["job_title"], "core"),
}

# Modules whose importing code goes elsewhere when they split, so the import moves with it. {module: owners it may reach}.
IMPORTER_MOVES = {
    "workers.py": {"discovery", "applier", "mail"},  # Each workflow moves to the service that executes it.
    "api/lifespan.py": {"applier", "mail", "discovery"},  # Each service starts its own loops.
    "outbox.py": {"control"},  # deliver() is control's; enqueue and claim are the shared bus.
    "pipeline_comparison.py": {"applier"},  # engine_for
    "sensitive_ids.py": {"applier", "ledger"},  # stop_if_sensitive
    "provider_health.py": {"ledger"},  # dispatch_gate
    # Facades and one-time code that end rather than move.
    "service/__init__.py": {"control", "applier", "mail", "ledger"},
    "answers/__init__.py": {"applier"},
    "features/__init__.py": {"control", "discovery", "knowledge"},
    "features/_store.py": {"discovery", "knowledge", "ledger"},
    "features/knowledge/__init__.py": {"control"},
    "service/migrations.py": {"applier", "domain"},
}

# fmt: off
MAP = {
    # ---- Package roots and entry points ------------------------------------------------------------------------------
    "__init__.py":                       ("core:__init__.py", "Version only"),
    "cli.py":                            ("control:cli.py", "split: serve/token/doctor stay; probe, login, browser, twin-browser go to applier:cli.py"),
    "verify.py":                         ("control:verify.py", "`axiom verify`, the deployment acceptance check"),
    "demo.py":                           ("control:demo/seed.py", "Synthetic application for `verify` and the first-run tour"),
    "dbos_sqlite.py":                    ("core:dbos_sqlite.py", ""),

    # ---- Foundations -------------------------------------------------------------------------------------------------
    "config.py":                         ("core:config.py", "split: one Settings per service over one shared base"),
    "db.py":                             ("core:db.py", "split: Database stays; each ORM class moves to its owner's tables.py (section 3)"),
    "network.py":                        ("core:network.py", "SSRF-safe fetch"),
    "shortlinks.py":                     ("domain:shortlinks.py", "Resolved once, at admission and at feed import"),
    "agent_settings.py":                 ("core:agent_settings.py", "Helper switches every process reads; mail needs them without the LLM clients"),
    "runtime.py":                        ("core:runtime.py", ""),
    "memory.py":                         ("core:memory.py", "Heap trim; every process runs it"),
    "connections.py":                    ("core:connections.py", "Provider keys entered in the dashboard overlay config at start"),
    "features/__init__.py":              ("core:_retired.py", "Removed: `features` is not a domain"),
    "features/_store.py":                ("core:feature_store.py", "FeatureBase, digest, norm"),
    "schemas.py":                        ("domain:schemas.py", "split: Profile and history types stay; request models go to contracts:<service>.py"),
    "ats.py":                            ("domain:ats.py", "Pure URL to platform; imported by 17 modules"),
    "email_alias.py":                    ("domain:email_alias.py", "Pure address arithmetic"),
    "mail_errors.py":                    ("contracts:mail_errors.py", "MailPending and friends cross the mail boundary"),
    "browser_contracts.py":              ("applier:engine/contracts.py", ""),

    # ---- Run ledger: the only code that writes runs, reviews, budgets ------------------------------------------------
    "service/states.py":                 ("ledger:states.py", ""),
    "service/__init__.py":               ("ledger:_retired_service.py", "split: `Service` ends. control:orchestrator/app.py and applier:worker/app.py assemble what each process needs"),
    "service/completion.py":             ("ledger:completion.py", "split: policy functions become hooks (section 7, phase b)"),
    "admission.py":                      ("ledger:admission.py", "QueueBlocked, capacity, employer_partition"),
    "application_budget.py":             ("ledger:budget.py", "Allowance, nothing_sent"),
    "application_pacing.py":             ("ledger:pacing.py", ""),
    "application_slots.py":              ("ledger:slots.py", ""),
    "pipeline_comparison.py":            ("ledger:pools.py", "split: engine_for moves to applier:worker/engine_choice.py (it imports BrowserEngine)"),
    "tiers.py":                          ("ledger:tiers.py", "split: escalate_due moves to control:orchestrator/escalation.py"),
    "specialist.py":                     ("ledger:specialist.py", ""),
    "job_control.py":                    ("ledger:job_control.py", "split: manage() moves to control:owner/job_control.py; watch() to applier:worker/watch.py"),
    "reserved.py":                       ("ledger:reserved.py", "require_approval runs inside the reservation"),
    "run_profile.py":                    ("ledger:run_profile.py", ""),
    "run_trace.py":                      ("ledger:run_trace.py", ""),
    "outbox.py":                         ("core:outbox.py", "split: enqueue, claim, retry are the shared bus; deliver() moves to control:orchestrator/delivery.py"),
    "archive.py":                        ("control:orchestrator/archive.py", ""),
    "controller_policy.py":              ("ledger:controller_policy.py", "Validated at admission, read by the engine"),
    "host_health.py":                    ("ledger:host_gate.py", "split: HostLoad sampling moves to applier:worker/host_load.py; the published state and Gate stay"),
    "service/migrations.py":             ("control:orchestrator/migrations/legacy.py", "Runs at start today; becomes Alembic revisions"),

    # ---- control: orchestrator (admission, autopilot, recovery, repair) -----------------------------------------------
    "service/queueing.py":               ("control:orchestrator/admission.py", "_queue becomes an ordered list of guards"),
    "service/reviews.py":                ("control:orchestrator/reviews.py", ""),
    "autopilot.py":                      ("control:orchestrator/autopilot.py", "Reads admission_candidates instead of scanning the board"),
    "recovery.py":                       ("control:orchestrator/recovery.py", "split: schedule_recovery runs inside ledger.complete and moves to ledger:recovery.py"),
    "repair_agent.py":                   ("control:orchestrator/repair.py", "Constants the resolver borrows move to domain:answers/masks.py"),
    "failure_reports.py":                ("control:orchestrator/failure_reports.py", ""),
    "execution_services.py":             ("control:orchestrator/preparation.py", "split: authoritative_packet moves to knowledge:packet.py"),
    "retention.py":                      ("control:orchestrator/retention.py", "DBOS retention is a singleton job"),
    "workers.py":                        ("control:orchestrator/schedule.py", "split: each DBOS workflow moves to the service that executes it (section 2)"),
    "operations/__init__.py":            ("control:ops/__init__.py", ""),
    "operations/closed_postings.py":     ("control:ops/closed_postings.py", ""),
    "operations/reset.py":               ("control:ops/reset.py", ""),
    "operations/superseded.py":          ("control:ops/superseded.py", "close_superseded_waits runs inside completion and moves to the ledger"),

    # ---- control: the owner ------------------------------------------------------------------------------------------
    "owner_account.py":                  ("control:owner/account.py", ""),
    "owner_apply.py":                    ("control:owner/apply.py", "`picked` moves to ledger:admission.py"),
    "owner_questions.py":                ("control:owner/questions.py", "split: document_for, generic, row_for are read at fill time and move to domain:answers/owner_view.py"),
    "profile_photo.py":                  ("knowledge:profile_photo.py", ""),

    # ---- control: gateway --------------------------------------------------------------------------------------------
    "api/__init__.py":                   ("control:gateway/__init__.py", ""),
    "api/app.py":                        ("control:gateway/app.py", ""),
    "api/lifespan.py":                   ("control:gateway/lifespan.py", "One per process afterwards; each service starts only its own loops"),
    "api/middleware.py":                 ("control:gateway/middleware.py", ""),
    "api/pipeline.py":                   ("control:gateway/pipeline.py", ""),
    "api/sessions.py":                   ("control:gateway/sessions.py", ""),
    "api/snapshot.py":                   ("control:gateway/snapshot.py", "Assembled from each owner's status API"),
    "api/routes/__init__.py":            ("control:gateway/routes/__init__.py", ""),
    "api/routes/accounts.py":            ("control:gateway/routes/accounts.py", "Proxies to applier /v1/accounts"),
    "api/routes/archive.py":             ("control:gateway/routes/archive.py", ""),
    "api/routes/demo.py":                ("control:gateway/routes/demo.py", ""),
    "api/routes/frontend.py":            ("control:gateway/routes/frontend.py", ""),
    "api/routes/health.py":              ("control:gateway/routes/health.py", ""),
    "api/routes/job_board.py":           ("control:gateway/routes/job_board.py", "Proxies to discovery /v1"),
    "api/routes/jobs.py":                ("control:gateway/routes/jobs.py", ""),
    "api/routes/knowledge.py":           ("control:gateway/routes/knowledge.py", ""),
    "api/routes/mail.py":                ("control:gateway/routes/mail.py", "Proxies to mail /v1"),
    "api/routes/owner.py":               ("control:gateway/routes/owner.py", ""),
    "api/routes/profile.py":             ("control:gateway/routes/profile.py", ""),
    "api/routes/queue.py":               ("control:gateway/routes/queue.py", ""),
    "api/routes/reserved.py":            ("control:gateway/routes/reserved.py", ""),
    "api/routes/resumes.py":             ("control:gateway/routes/resumes.py", ""),
    "api/routes/runs.py":                ("control:gateway/routes/runs.py", ""),
    "api/routes/session.py":             ("control:gateway/routes/session.py", ""),
    "api/routes/settings.py":            ("control:gateway/routes/settings.py", ""),
    "api/routes/sources.py":             ("control:gateway/routes/sources.py", "Proxies to discovery /v1"),
    "api/routes/state.py":               ("control:gateway/routes/state.py", ""),

    # ---- control: Telegram is a second front end, not a service --------------------------------------------------------
    "telegram.py":                       ("control:notify/telegram/__init__.py", "split: 6.2"),
    "telegram_workers.py":               ("control:notify/telegram/worker_bots.py", ""),

    # ---- control: resumes (owner-edited documents; tailoring queue) -----------------------------------------------------
    "features/resumes/__init__.py":      ("control:resumes/__init__.py", ""),
    "features/resumes/claims.py":        ("control:resumes/claims.py", ""),
    "features/resumes/compiler.py":      ("control:resumes/compiler.py", ""),
    "features/resumes/editor.py":        ("control:resumes/editor.py", ""),
    "features/resumes/evidence.py":      ("domain:resume_evidence.py", "profile_digest is read by admission and ranking"),
    "features/resumes/latex.py":         ("control:resumes/latex.py", ""),
    "features/resumes/master.py":        ("control:resumes/master.py", ""),
    "features/resumes/pipeline.py":      ("control:resumes/pipeline.py", ""),
    "features/resumes/router.py":        ("control:gateway/routes/resumes_v2.py", ""),
    "features/resumes/select.py":        ("control:resumes/select.py", ""),
    "features/resumes/tables.py":        ("knowledge:resume_tables.py", "rs_artifacts is pinned by admission and read by ranking and the engine"),
    "features/resumes/tailor2.py":       ("control:resumes/tailor.py", ""),
    "features/resumes/validate.py":      ("control:resumes/validate.py", ""),

    # ---- knowledge library: the one read path, plus the store control writes through -----------------------------------
    "knowledge.py":                      ("knowledge:legacy_answers.py", "Retired once kn_facts is the one store (API.md D4)"),
    "answer_rules.py":                   ("domain:answer_rules.py", "The owner-editable rule types; schemas.Profile embeds them"),
    "eligibility.py":                    ("knowledge:eligibility.py", "Facts versus hiring restrictions; ranking and admission both ask"),
    "sensitive_ids.py":                  ("domain:sensitive_ids.py", "split: stop_if_sensitive (it touches the page) moves to applier:engine/sensitive.py"),
    "features/knowledge/__init__.py":    ("knowledge:__init__.py", ""),
    "features/knowledge/attestations.py":("knowledge:attestations.py", ""),
    "features/knowledge/employers.py":   ("knowledge:employers.py", "resolve() has write side effects; see section 3"),
    "features/knowledge/migration.py":   ("knowledge:migration.py", "Moves from read paths to profile save (API.md I4)"),
    "features/knowledge/models.py":      ("domain:knowledge_types.py", ""),
    "features/knowledge/questionnaires.py": ("knowledge:questionnaires.py", ""),
    "features/knowledge/questions.py":   ("domain:question_identity.py", ""),
    "features/knowledge/retrieval.py":   ("knowledge:retrieval.py", "Served from a per-run snapshot in the applier"),
    "features/knowledge/router.py":      ("control:gateway/routes/knowledge_v2.py", ""),
    "features/knowledge/store.py":       ("knowledge:store.py", ""),
    "features/knowledge/tables.py":      ("knowledge:tables.py", ""),
    "features/knowledge/writing.py":     ("knowledge:writing.py", "Role briefs and cover letter text"),

    # ---- domain vocabulary: what a question means, with no I/O ----------------------------------------------------------
    "answer_context.py":                 ("domain:answers/propositions.py", ""),
    "answers/__init__.py":               ("domain:answers/__init__.py", "Re-exports end; callers name the module"),
    "answers/consent.py":                ("domain:answers/consent.py", ""),
    "answers/contact.py":                ("domain:answers/contact.py", ""),
    "answers/demographics.py":           ("domain:answers/demographics.py", ""),
    "answers/eligibility.py":            ("domain:answers/eligibility.py", ""),
    "answers/experience.py":             ("domain:answers/experience.py", "Years-of-experience questions, from the profile"),
    "answers/history.py":                ("domain:answers/history.py", ""),
    "answers/intents.py":                ("domain:answers/intents.py", ""),
    "answers/sources.py":                ("domain:answers/sources.py", ""),
    "answers/text.py":                   ("domain:answers/text.py", ""),
    "answers/third_party.py":            ("domain:answers/third_party.py", ""),

    # ---- applier: answers at fill time -----------------------------------------------------------------------------------
    "answers/resolver.py":               ("applier:answers/resolver.py", ""),
    "answers/consent_rules.py":          ("applier:answers/consent_rules.py", ""),
    "answers/demographic_rules.py":      ("applier:answers/demographic_rules.py", ""),
    "answers/history_rules.py":          ("applier:answers/history_rules.py", ""),
    "answers/intent_rules.py":           ("applier:answers/intent_rules.py", ""),
    "answers/local_rules.py":            ("applier:answers/local_rules.py", "_local becomes an ordered list of rules"),
    "answers/model_batch.py":            ("applier:answers/model_batch.py", ""),
    "answers/people_rules.py":           ("applier:answers/people_rules.py", ""),
    "answers/policy_rules.py":           ("applier:answers/policy_rules.py", ""),
    "answers/validate.py":               ("applier:answers/validate.py", ""),
    "answer_quality.py":                 ("applier:answers/quality.py", ""),
    "evidence_equivalence.py":           ("applier:answers/equivalence.py", ""),
    "inference_agent.py":                ("applier:answers/inference.py", ""),
    "letters.py":                        ("applier:answers/letters.py", ""),

    # ---- applier: the worker process ---------------------------------------------------------------------------------------
    "service/execution.py":              ("applier:worker/execution.py", "split: reserve_submission and release_submission move to ledger:reservation.py"),
    "operations/close_tabs.py":          ("applier:worker/close_tabs.py", ""),

    # ---- applier: engine -----------------------------------------------------------------------------------------------------
    "browser.py":                        ("applier:engine/__init__.py", "split: 6.2"),
    "bot_wall.py":                       ("applier:engine/bot_wall.py", ""),
    "desktop_cdp.py":                    ("applier:engine/tab.py", ""),
    "diagnostics.py":                    ("applier:engine/diagnostics.py", ""),
    "playwright_fast.py":                ("applier:engine/playwright_fast.py", ""),
    "posting_read.py":                   ("applier:engine/posting_read.py", ""),
    "privacy.py":                        ("applier:engine/privacy.py", ""),
    "continuous_agent.py":               ("applier:engine/flows/agent_evidence.py", "The cleanup branch removes drive() and leaves about 80 lines of shared evidence helpers"),
    "control_agent.py":                  ("applier:engine/flows/control_agent.py", ""),

    # ---- applier: forms and widgets ---------------------------------------------------------------------------------------
    "forms.py":                          ("applier:forms/session.py", "split: 6.2"),
    "history_editor_guard.py":           ("applier:forms/history_editor.py", ""),
    "phone_fields.py":                   ("applier:forms/phone_fields.py", ""),
    "widgets.py":                        ("applier:widgets/__init__.py", "split: 6.2"),
    "widgets_ats.py":                    ("applier:widgets/ats_combo.py", ""),

    # ---- applier: adapters ----------------------------------------------------------------------------------------------------
    "adapters/__init__.py":              ("applier:adapters/__init__.py", ""),
    "adapters/base.py":                  ("applier:adapters/base/__init__.py", "split: 6.2"),
    "adapters/icims.py":                 ("applier:adapters/icims/adapter.py", ""),
    "icims_protocol.py":                 ("applier:adapters/icims/protocol.py", ""),
    "adapters/oracle.py":                ("applier:adapters/oracle.py", ""),
    "adapters/others.py":                ("applier:adapters/others.py", ""),
    "adapters/workday.py":               ("applier:adapters/workday/adapter.py", ""),
    "workday.py":                        ("applier:adapters/workday/auth.py", ""),
    "taleo_protocol.py":                 ("applier:adapters/taleo_protocol.py", ""),

    # ---- applier: employer accounts and the email step ------------------------------------------------------------------------
    "account_identity.py":               ("applier:accounts/identity.py", ""),
    "accounts/__init__.py":              ("applier:accounts/__init__.py", ""),
    "accounts/credentials.py":           ("applier:accounts/credentials.py", ""),
    "accounts/dayforce.py":              ("applier:accounts/dayforce.py", ""),
    "accounts/errors.py":                ("applier:accounts/errors.py", ""),
    "accounts/flow.py":                  ("applier:accounts/flow.py", ""),
    "accounts/flow_dayforce.py":         ("applier:accounts/flow_dayforce.py", ""),
    "accounts/flow_gates.py":            ("applier:accounts/flow_gates.py", ""),
    "accounts/flow_handle.py":           ("applier:accounts/flow_handle.py", ""),
    "accounts/overview.py":              ("applier:accounts/overview.py", "Served at applier /v1/accounts"),
    "accounts/pages.py":                 ("applier:accounts/pages.py", ""),
    "accounts/store.py":                 ("applier:accounts/store.py", ""),
    "accounts/tenants.py":               ("domain:account_tenants.py", "Mail attributes codes with it too"),
    "email_browser.py":                  ("applier:mailflow/email_browser.py", ""),
    "inbox.py":                          ("applier:mailflow/inbox.py", "Becomes the in-process, then HTTP, client of the mail service"),

    # ---- applier: CAPTCHA --------------------------------------------------------------------------------------------------------
    "captcha.py":                        ("applier:captcha/solver.py", ""),
    "captcha_client.py":                 ("applier:captcha/client.py", ""),
    "captcha_inpage.py":                 ("applier:captcha/inpage.py", ""),
    "captcha_observation.py":            ("applier:captcha/observation.py", ""),
    "captcha_same_ip.py":                ("applier:captcha/same_ip.py", ""),
    "captcha_twin.py":                   ("applier:captcha/twin.py", ""),
    "captcha_visual.py":                 ("applier:captcha/visual.py", ""),

    # ---- llm library ----------------------------------------------------------------------------------------------------------------
    "models.py":                         ("llm:client.py", "Writes runs.cost today; the meter goes through a ledger callback"),
    "model_admission.py":                ("llm:admission.py", ""),
    "provider_health.py":                ("llm:provider_health.py", "dispatch_gate moves to ledger:host_gate.py"),
    "jev.py":                            ("llm:jev.py", ""),
    "prompt_layout.py":                  ("llm:prompt_layout.py", ""),
    "vision.py":                         ("llm:vision.py", ""),

    # ---- discovery --------------------------------------------------------------------------------------------------------------------
    "discovery.py":                      ("discovery:ranking/__init__.py", "split: add_job to ingest/jobs.py; rank and rank_batch to ranking/; per-ATS scrapers to sources/; application_entry to domain:ats.py"),
    "jobboard.py":                       ("discovery:board/__init__.py", "split: settings and sources to board/; scrape_source to sources/; board_snapshot to board/snapshot.py"),
    "jobsearch.py":                      ("discovery:sources/web_search.py", ""),
    "features/discovery/__init__.py":    ("discovery:__init__.py", ""),
    "features/discovery/classify.py":    ("discovery:ranking/classify.py", ""),
    "features/discovery/companies.py":   ("discovery:sources/companies.py", ""),
    "features/discovery/degree.py":      ("domain:posting/degree.py", ""),
    "features/discovery/feeds.py":       ("discovery:sources/feeds.py", ""),
    "features/discovery/graduation.py":  ("domain:posting/graduation.py", ""),
    "features/discovery/policy.py":      ("discovery:policy.py", "SENIOR and friends move to domain:posting/seniority.py; gate() is read at admission"),
    "features/discovery/postings.py":    ("discovery:sources/postings.py", ""),
    "features/discovery/readiness.py":   ("discovery:readiness.py", "Publishes admission candidates instead of being called by the tick"),
    "features/discovery/roles.py":       ("domain:posting/roles.py", "Pure target-role matching; admission and ranking both ask"),
    "features/discovery/router.py":      ("discovery:api/v1.py", ""),
    "features/discovery/tables.py":      ("discovery:tables.py", ""),

    # ---- mail ---------------------------------------------------------------------------------------------------------------------------
    "mail/__init__.py":                  ("mail:__init__.py", "Re-exports end; origin, tenant_tokens, employer_label move to domain:mail_identity.py"),
    "mail/challenges.py":                ("mail:challenges.py", ""),
    "mail/constants.py":                 ("mail:constants.py", ""),
    "mail/extraction.py":                ("mail:extraction.py", ""),
    "mail/helpers.py":                   ("domain:mail_identity.py", "Pure helpers both sides of the boundary need"),
    "mail/mailboxes.py":                 ("mail:mailboxes.py", ""),
    "mail/models.py":                    ("contracts:mail.py", ""),
    "mail/polling.py":                   ("mail:polling.py", "Writes runs today; emits challenge.matched and receipt.matched instead"),
    "mail/rules.py":                     ("mail:rules.py", ""),
    "mail/senders.py":                   ("domain:mail_senders.py", "Pure sender families; the applier reads them when it arms a challenge"),
    "mail/service.py":                   ("mail:service.py", ""),
    "mail/transport.py":                 ("mail:transport.py", ""),
    "mail/vault.py":                     ("core:vault.py", "Employer accounts seal with the same key"),
    "gmail_imap.py":                     ("mail:imap.py", ""),
    "receipts.py":                       ("mail:receipts.py", "Writes runs today; emits receipt.matched instead"),

    # ---- desktop: the launcher. Standard library only -------------------------------------------------------------------------------------
    "native/__init__.py":                ("desktop:__init__.py", ""),
    "native/__main__.py":                ("desktop:__main__.py", ""),
    "native/cli.py":                     ("desktop:cli.py", ""),
    "native/env.py":                     ("desktop:env.py", ""),
    "native/migrate.py":                 ("desktop:migrate.py", "Docker to native; deleted after the cut-over"),
    "native/pg.py":                      ("desktop:pg.py", ""),
    "native/release.py":                 ("desktop:release.py", "Snapshots services/, libs/ and web/ instead of jobpilot/"),
    "native/services.py":                ("desktop:services.py", ""),
    "native/supervisor.py":              ("desktop:supervisor.py", ""),
}
# fmt: on


def modules():
    """Every module under jobpilot/, as a path relative to it."""
    return sorted(str(p.relative_to(PACKAGE)) for p in PACKAGE.rglob("*.py"))


def dotted(rel):
    parts = list(pathlib.PurePosixPath(rel).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(["jobpilot", *parts])


def owner_of(rel):
    return MAP[rel][0].split(":", 1)[0]


def target_path(rel):
    owner, path = MAP[rel][0].split(":", 1)
    return f"{owner}:{path}"


def import_edges():
    """{(importer rel, imported rel): {names}} from every import statement, at any depth."""
    rels = modules()
    by_dotted = {dotted(r): r for r in rels}
    edges = collections.defaultdict(set)
    for rel in rels:
        module = dotted(rel)
        package = module if rel.endswith("__init__.py") else module.rsplit(".", 1)[0]
        tree = ast.parse((PACKAGE / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package.split(".")
                    base = base[: len(base) - (node.level - 1)]
                    prefix = ".".join(base + ([node.module] if node.module else []))
                else:
                    prefix = node.module or ""
                if not prefix.startswith("jobpilot"):
                    continue
                for alias in node.names:
                    candidate = f"{prefix}.{alias.name}"
                    if candidate in by_dotted:
                        edges[(rel, by_dotted[candidate])].add("*")
                    else:
                        walk = prefix
                        while walk and walk not in by_dotted:
                            walk = walk.rsplit(".", 1)[0] if "." in walk else ""
                        if walk:
                            edges[(rel, by_dotted[walk])].add(alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    walk = alias.name
                    if not walk.startswith("jobpilot"):
                        continue
                    while walk and walk not in by_dotted:
                        walk = walk.rsplit(".", 1)[0] if "." in walk else ""
                    if walk:
                        edges[(rel, by_dotted[walk])].add("*")
    return {k: v for k, v in edges.items() if k[0] != k[1]}


def allowed(source_owner, target_owner):
    if source_owner == target_owner:
        return True
    return target_owner in LIBRARY_RULES.get(source_owner, set())


def crossings(moves=True):
    """Imports the target design forbids: a call, an event or a published table has to replace each one.

    With `moves`, a name in SYMBOL_MOVES counts at its new owner and code in IMPORTER_MOVES counts where it is going.
    Without, every import is taken at its module's mapped owner: the size of the job, not what is left after it."""
    out = collections.defaultdict(list)
    for (source, target), names in import_edges().items():
        if source not in MAP or target not in MAP:
            continue
        a, b = owner_of(source), owner_of(target)
        if allowed(a, b):
            continue
        if not moves:
            out[(a, b)].append((source, target, sorted(names)))
            continue
        if b in IMPORTER_MOVES.get(source, ()):
            continue
        moved = SYMBOL_MOVES.get(target, {})
        left = sorted(n for n in names if not (n in moved and allowed(a, moved[n])))
        if left:
            out[(a, b)].append((source, target, left))
    return out


def line_count(rel):
    with open(PACKAGE / rel, encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def check():
    present = set(modules())
    missing = sorted(present - set(MAP))
    stale = sorted(set(MAP) - present)
    for rel in missing:
        print(f"no target for jobpilot/{rel}")
    for rel in stale:
        print(f"target for a module that no longer exists: jobpilot/{rel}")
    bad = [rel for rel in MAP if owner_of(rel) not in OWNERS]
    for rel in bad:
        print(f"unknown owner in {rel}: {MAP[rel][0]}")
    if missing or bad:
        return 1
    # A deleted module is progress, not an error: the cleanup branch removes modules while this map stands.
    print(f"{len(present)} modules, {len(stale)} deleted since the map was written, all mapped")
    return 0


def kind_of(owner):
    return "service" if owner in SERVICES else "library" if owner in LIBRARIES else "launcher"


def summary_block():
    owners = collections.Counter()
    lines = collections.Counter()
    present = set(modules())
    for rel in present:
        owner = owner_of(rel)
        owners[owner] += 1
        lines[owner] += line_count(rel)
    rows = ["| Owner | Kind | Modules | Lines |", "|---|---|--:|--:|"]
    for owner in OWNERS:
        rows.append(f"| `{owner}` | {kind_of(owner)} | {owners[owner]} | {lines[owner]:,} |")
    rows.append(f"| **Total** | | **{sum(owners.values())}** | **{sum(lines.values()):,}** |")
    return "\n".join(rows)


def table_block():
    present = set(modules())
    edges = import_edges()
    fan_in = collections.Counter(target for (_, target) in edges)
    rows = []
    for owner in OWNERS:
        members = sorted((rel for rel in present if owner_of(rel) == owner), key=lambda r: MAP[r][0])
        if not members:
            continue
        rows.append(f"\n<details><summary><code>{owner}</code> ({kind_of(owner)}): {len(members)} modules</summary>\n")
        rows.append("| Today (`jobpilot/`) | Lines | Used by | Target | Note |")
        rows.append("|---|--:|--:|---|---|")
        for rel in members:
            target, note = MAP[rel]
            path = target.split(":", 1)[1]
            rows.append(f"| `{rel}` | {line_count(rel):,} | {fan_in[rel]} | `{path}` | {note} |")
        rows.append("\n</details>")
    return "\n".join(rows).strip()


def crossings_block():
    before, after = crossings(moves=False), crossings()
    rows = ["| From | To | Imports today | After the symbol moves | What is left, by name |", "|---|---|--:|--:|---|"]
    for key in sorted(before, key=lambda k: (-len(before[k]), k)):
        names = sorted({n for _, _, found in after.get(key, []) for n in found})
        shown = ", ".join(f"`{n}`" for n in names[:9]) + (f", and {len(names) - 9} more" if len(names) > 9 else "")
        rows.append(f"| `{key[0]}` | `{key[1]}` | {len(before[key])} | {len(after.get(key, []))} | {shown} |")
    rows.append(f"| **Total** | | **{sum(map(len, before.values()))}** | **{sum(map(len, after.values()))}** | |")
    return "\n".join(rows)


def test_owners():
    """{test file: owner}. A test that imports one owner lives with it; one that imports several is a contract or e2e test."""
    by_dotted = {dotted(r): r for r in modules()}
    out = {}
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owners = collections.Counter()
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("jobpilot"):
                names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names if a.name.startswith("jobpilot")]
            for name in names:
                if name in by_dotted and by_dotted[name] in MAP:
                    owners[owner_of(by_dotted[name])] += 1
        services = [o for o in owners if o in SERVICES]
        if not owners:
            out[path.name] = "e2e"
        elif len(services) > 1:
            out[path.name] = "e2e"
        elif services:
            out[path.name] = services[0]
        else:
            out[path.name] = owners.most_common(1)[0][0]
    return out


def replace_block(text, name, body):
    begin, end = f"<!-- BEGIN GENERATED {name} -->", f"<!-- END GENERATED {name} -->"
    if begin not in text or end not in text:
        raise SystemExit(f"{DOC} has no generated block {name}")
    head = text.split(begin, 1)[0]
    tail = text.split(end, 1)[1]
    return f"{head}{begin}\n{body}\n{end}{tail}"


def write():
    text = DOC.read_text(encoding="utf-8")
    text = replace_block(text, "MODULE-SUMMARY", summary_block())
    text = replace_block(text, "MODULE-MAP", table_block())
    text = replace_block(text, "CROSSINGS", crossings_block())
    DOC.write_text(text, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--crossings", action="store_true")
    parser.add_argument("--tests", action="store_true")
    args = parser.parse_args()
    status = check()
    if status:
        return status
    if args.write:
        write()
    if args.crossings:
        for (a, b), items in sorted(crossings().items(), key=lambda kv: (-len(kv[1]), kv[0])):
            print(f"\n{a} -> {b}: {len(items)}")
            for source, target, names in sorted(items):
                print(f"  {source} -> {target}: {', '.join(names)}")
    if args.tests:
        counts = collections.Counter(test_owners().values())
        for owner, count in counts.most_common():
            print(f"{owner}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
