"""Startup migrations of legacy rows."""

import json

from sqlalchemy import or_, select

from ..db import Job, Review, Run, Setting, now
from ..knowledge import learned_answer


class MigrationsMixin:
    def relabel_custom_ats(self):
        """Jobs saved as a custom career site before their site was recognized (SuccessFactors
        Career Site Builder on employer domains, 2026-10-15) take the ATS their URL is known to use."""
        from ..ats import detect

        with self.db.session() as s:
            for job in s.scalars(select(Job).where(Job.ats == "custom")):
                if (ats := detect(job.url).id) != "custom":
                    job.ats = ats

    def employer_names_from_titles(self):
        """Jobs and sources saved with a portal page title as their employer ("Job Listings at PepsiCo") take the
        employer's name (2026-10-23: 669 iCIMS jobs; receipts and employer caps never matched them)."""
        from ..db import Source
        from ..schemas import PORTAL_TITLE, employer_from_title

        def titled(column):
            return or_(*(column.ilike(f"%{prefix} at %") for prefix in ("listings", "jobs", "careers", "opportunities",
                                                                          "openings", "positions")))

        with self.db.session() as s:
            for job in s.scalars(select(Job).where(titled(Job.company))):
                if PORTAL_TITLE.match(job.company):
                    job.company = employer_from_title(job.company)
            for source in s.scalars(select(Source).where(titled(Source.name))):
                if PORTAL_TITLE.match(source.name):
                    source.name = employer_from_title(source.name)

    def migrate_reviewed_answers(self):
        """Give existing applicant reviews provenance without changing their answers.

        Runs once per database: afterwards the reviewed answers are the store, and answers you
        corrected or forgot must not come back from old review rows."""
        with self.db.exclusive() as s:
            item = s.get(Setting, "profile")
            if not item or s.get(Setting, "migrated:reviewed_answers"):
                return
            value = dict(item.value)
            learned = {a["id"]: a for a in value.get("reviewed_answers", [])}
            forgotten = set(value.get("forgotten_answers", []))
            for review, job in s.execute(
                select(Review, Job)
                .join(Run, Review.run_id == Run.id)
                .join(Job, Run.job_id == Job.id)
                .where(Review.answer.is_not(None))
            ):
                # Only migrate actual answers which are still in the approved answer store.
                if (
                    review.id not in learned
                    and review.id not in forgotten
                    and review.key not in {"manual", "session"}
                    and value.get("approved_answers", {}).get(review.key) == review.answer
                ):
                    learned[review.id] = learned_answer(
                        review.id, review.question, review.answer, review.options, job.company or job.url
                    )
            value["reviewed_answers"] = list(learned.values())
            item.value = value
            s.add(Setting(key="migrated:reviewed_answers", value={"at": now()}))

    def audit_legacy_confirmation_evidence(self):
        """Keep duplicate guards while reconciling acknowledgements from the old detector."""
        with self.db.exclusive() as session:
            if session.get(Setting, "migrated:confirmation_evidence_v2"):
                return
            pending = 0
            for row in session.scalars(
                select(Run)
                .join(Job, Job.id == Run.job_id)
                .where(Run.state == "confirmed", Job.demo.is_(False))
            ):
                receipt = dict(row.receipt)
                email = receipt.get("email", {})
                trusted_email = (
                    isinstance(email, dict) and email.get("message_id") and email.get("sender_trusted", True)
                )
                current_dom = (
                    receipt.get("evidence_version") == 2
                    and receipt.get("evidence_selector")
                    and receipt.get("status_region")
                )
                if trusted_email or current_dom or receipt.get("type") == "user_reconciled":
                    receipt["evidence_reconciliation"] = "verified"
                else:
                    receipt["evidence_reconciliation"] = "pending"
                    pending += 1
                row.receipt = receipt
            session.add(
                Setting(key="migrated:confirmation_evidence_v2", value={"at": now(), "pending": pending})
            )

    def migrate_legacy(self):
        """One-way, idempotent import of data written by the earlier adapter branch.

        Its `knowledge` table becomes reviewed answers with provenance; its `accounts` table
        becomes encrypted employer account records, but only where the password it recorded is
        provably the configured one (fingerprint match). Legacy tables are left untouched."""
        from sqlalchemy import inspect, text

        from ..knowledge import save

        tables = set(inspect(self.db.engine).get_table_names())
        if "knowledge" in tables and not self.db.get_setting("migrated:knowledge"):
            with self.db.engine.connect() as c:
                rows = list(c.execute(text("SELECT * FROM knowledge")).mappings())
            for row in rows:
                if not row.get("question") or not row.get("answer"):
                    continue
                scope = row.get("scope") or "global"
                employer = "" if scope == "global" else scope.removeprefix("employer:")
                options = row.get("options") or []
                if isinstance(options, str):
                    options = json.loads(options or "[]")
                try:
                    save(
                        self.db,
                        row["question"],
                        row["answer"],
                        options,
                        employer,
                        "employer" if employer else None,
                        review_id="legacy-" + str(row["id"])[:24],
                    )
                except (ValueError, KeyError, TypeError):
                    continue
            self.db.set_setting("migrated:knowledge", {"rows": len(rows), "at": now()})
        if "accounts" in tables and not self.db.get_setting("migrated:accounts"):
            import hashlib as _hashlib
            import hmac

            from ..accounts import AccountStore

            password = self.config.application_password
            fingerprint = (
                hmac.new(self.config.app_token.encode(), password.encode(), _hashlib.sha256).hexdigest()[:16]
                if password and self.config.app_token
                else ""
            )
            with self.db.engine.connect() as c:
                rows = list(c.execute(text("SELECT * FROM accounts")).mappings())
            moved = 0
            for row in rows:
                host = (row.get("realm") or "").split(":")[1] if ":" in (row.get("realm") or "") else ""
                if (
                    not host
                    or not row.get("email")
                    or not fingerprint
                    or row.get("password_hash") != fingerprint
                ):
                    continue
                store = AccountStore(self, row["email"])
                url = "https://" + host
                if store.get(url):
                    continue  # Never overwrite a record main already keeps for this employer.
                state = {
                    "active": "authenticated",
                    "verified": "authenticated",
                    "reset": "password_reset",
                }.get(row.get("state"), "created_locally")
                store.save(url, {"email": row["email"].lower(), "password": password}, state, migrated=True)
                moved += 1
            self.db.set_setting("migrated:accounts", {"rows": len(rows), "moved": moved, "at": now()})
