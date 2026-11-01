"""Startup migrations of legacy rows."""

from sqlalchemy import or_, select

from ..db import Job


class MigrationsMixin:
    def relabel_custom_ats(self):
        """Jobs saved as a custom career site before their site was recognized (SuccessFactors
        Career Site Builder on employer domains, 2026-10-15) take the ATS their URL is known to use."""
        from ..ats import detect

        with self.db.session() as s:
            for job in s.scalars(select(Job).where(Job.ats == "custom")):
                if (ats := detect(job.url).id) != "custom":
                    job.ats = ats

    def label_platforms_from_history(self):
        """Career-site jobs take the platform an earlier run's hand-off or the address's own parameter names, as a run's
        observation does (2026-11-01: 70 jobs whose pages showed a platform kept the custom label and its single lane)."""
        from ..platform_label import label_jobs_from_history

        label_jobs_from_history(self.db)

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

    def tidy_board_data(self):
        """The board's data, tidied through the app's own code on every start (jobpilot.board_data): employers spelled one way,
        no career-site host as an employer, readable places, closed / expired / duplicate-link listings not live, and the
        thousands of `new` jobs that no screen could ever show gone. A job with any run behind it is never deleted."""
        import logging

        from ..board_data import backfill

        try:
            report = backfill(self.db)
        except Exception:
            logging.getLogger("jobpilot.board_data").warning("Board data tidy failed", exc_info=True)
            return {}
        if any(report.values()):
            logging.getLogger("jobpilot.board_data").info("Board data tidied: %s", report)
        return report
