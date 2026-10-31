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
