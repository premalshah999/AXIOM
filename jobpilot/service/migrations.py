"""Startup migrations of legacy rows."""

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
