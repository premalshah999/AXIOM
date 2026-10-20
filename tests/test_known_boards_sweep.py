"""The scheduled sweep also covers employer boards this database already knows (2026-10-17).

The 2-hourly sweep visited only boards named in the curated feeds and the static catalog, so Workday tenants imported
by hand or by earlier sweeps were never revisited: 45 such tenants held 58 fresh postings that night, found only by a
manual script. Known boards of the focused ATSs now join the sweep.
"""

from jobpilot.db import Job
from jobpilot.features.discovery import feeds


def add(service, n, ats, url, company):
    with service.db.exclusive() as s:
        s.add(Job(identity=f"kb-{n}", url=url, company=company, title="Software Engineer", ats=ats))


def test_known_boards_come_from_jobs_of_focused_ats(service):
    service.db.set_setting("ats_focus", {"exclude": ["greenhouse"]})
    add(service, 1, "workday", "https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/Austin-TX/SWE_R1", "Acme")
    add(service, 2, "workday", "https://acme.wd5.myworkdayjobs.com/Careers/job/Austin-TX/SWE_R2", "Acme")
    add(service, 3, "smartrecruiters", "https://jobs.smartrecruiters.com/Beta/744000000000001", "Beta")
    add(service, 4, "greenhouse", "https://boards.greenhouse.io/gamma/jobs/1", "Gamma")
    boards = feeds.known_boards(service.db)
    assert ("workday", "acme.wd5.myworkdayjobs.com|acme|Careers", "Acme") in boards
    assert ("smartrecruiters", "Beta", "Beta") in boards
    assert not any(kind == "greenhouse" for kind, _, _ in boards)
    assert len([b for b in boards if b[0] == "workday"]) == 1


async def test_collect_sweeps_the_extra_boards(monkeypatch):
    swept = []

    async def fake_sweep(client, kind, ident, company, now, queries=feeds.WORKDAY_QUERIES):
        swept.append((kind, ident))
        return []

    monkeypatch.setattr(feeds, "sweep_board", fake_sweep)
    monkeypatch.setattr(feeds, "catalog_boards", lambda: set())
    await feeds.collect(7, feeds=[], extra_boards={("workday", "acme.wd5.myworkdayjobs.com|acme|Careers", "Acme")})
    assert swept == [("workday", "acme.wd5.myworkdayjobs.com|acme|Careers")]
