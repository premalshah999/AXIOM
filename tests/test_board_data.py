"""The job board's data: employers spelled one way, readable places, live only while open, one entry per posting
(owner 2026-11-01: blank titles and employers, career-site hosts as employers, "2 Locations", closed postings listed live)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from jobpilot import board_data as b
from jobpilot.db import Job, JobListing, Run, Setting

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def test_a_career_site_host_is_no_employer_but_a_brand_with_a_dot_is():
    assert b.junk_company("career-example.icims.com") and b.junk_company("careers.example.com") and b.junk_company("")
    assert b.junk_company("Inc.") and b.junk_company("N/A")
    assert not b.junk_company("Checkout.com") and not b.junk_company("incident.io") and not b.junk_company("ExampleSoft, Inc.")


def test_the_career_site_label_and_entity_codes_leave_the_employer_name():
    assert b.clean_company("Example Careers") == "Example"
    assert b.clean_company("ExampleSoft - Career's Page") == "ExampleSoft"
    assert b.clean_company("Example Early Careers") == "Example"
    assert b.clean_company("Job Listings at Example Corp") == "Example Corp"
    assert b.clean_company("00002 Example Bank, N.A.") == "Example Bank, N.A."
    assert b.clean_company("2020 Companies") == "2020 Companies" and b.clean_company("1800 Contacts") == "1800 Contacts"
    assert b.clean_company("  Example  ") == "Example"


def test_spellings_of_one_name_and_an_acronym_are_recognized_but_a_different_suffix_is_not_the_same_by_itself():
    assert b.compact("C3.ai") == b.compact("C3 AI")
    assert b.compact("JPMorgan Chase") == b.compact("JP Morgan Chase")
    assert b.acronym_variant("Example Hopkins APL", "Example Hopkins Applied Physics Laboratory")
    assert not b.acronym_variant("Example Hopkins APL", "Example Hopkins Applied Chemistry Lab")
    assert b.compact("Brain Co.") != b.compact("Brain Corp")  # Two employers: only evidence merges legal-suffix variants.


def test_places_a_person_can_read():
    assert b.clean_location("2 Locations") == "" and b.clean_location("Multiple Locations") == ""
    assert b.clean_location("Austin, TX +2") == "Austin, TX (+2 more)"
    assert b.clean_location("Austin, TX; Denver, CO +1") == "Austin, TX; Denver, CO (+1 more)"
    assert b.clean_location("  Remote - USA ") == "Remote - USA"


def test_an_internship_whose_named_term_ended_is_expired():
    assert b.expired_internship("Software Engineer Intern, Summer 2026", NOW)
    assert b.expired_internship("2025 Data Science Internship", NOW)
    assert not b.expired_internship("Software Engineering Co-op - Spring 2027", NOW)
    assert not b.expired_internship("Software Engineer", NOW)
    assert not b.expired_internship("Software Engineer Intern", NOW)  # No term named: not judged.
    assert not b.expired_internship("Fall 2026 Engineer", NOW)  # Not an internship.


def test_a_page_label_leaves_the_title():
    assert b.clean_title("Data Engineer | Careers | ExampleSoft", "ExampleSoft") == "Data Engineer"
    assert b.clean_title("Engineer - Backend") == "Engineer - Backend"


def test_one_workday_requisition_under_two_links_is_a_twin_but_two_requisitions_are_not():
    base = "https://ex.wd1.myworkdayjobs.com"
    rows = [
        {"url": f"{base}/en-US/site/job/Austin-TX/Engineer_R100"},
        {"url": f"{base}/site/job/Austin-TX/Engineer_R100-1"},
        {"url": f"{base}/site/job/Austin-TX/Engineer_R200"},
        {"url": f"{base}/site/job/Austin-TX/Analyst_2026-02149"},
        {"url": f"{base}/site/job/Austin-TX/Analyst_2026-02147"},
    ]
    groups = b.twin_groups(rows)
    assert [sorted(r["url"] for r in g) for g in groups.values()] == [sorted(r["url"] for r in rows[:2])]


def add(service, key, company="ExampleSoft", title="Engineer", status="new", url=None, location="Austin, TX", listed=True, runs=0, origin="source"):
    with service.db.exclusive() as s:
        job = Job(identity=key, url=url or f"https://jobs.example.test/{key}", company=company, title=title, status=status,
                  location=location, created_at=(NOW - timedelta(days=3)).isoformat())
        s.add(job)
        s.flush()
        s.add(Setting(key="job_origin:" + job.id, value={"kind": origin}))
        if listed:
            s.add(JobListing(job_id=job.id, source_id="s", posted_at=NOW.isoformat(), date_basis="x", location=location,
                             career_level="", match_reason="", active=True))
        for _ in range(runs):
            s.add(Run(job_id=job.id, state="confirmed", packet={}))
        return job.id


def test_backfill_clears_each_class_and_never_touches_a_job_with_runs(service):
    stub = add(service, "stub", listed=False)
    manual = add(service, "manual", listed=False, origin="manual")
    sent = add(service, "sent", listed=False, runs=1, status="new")
    closed = add(service, "closed", status="closed")
    host = add(service, "host", company="career-example.icims.com", url="https://career-example.icims.com/jobs/1/x/job")
    peer = add(service, "peer", company="ExampleSoft Holdings", url="https://career-example.icims.com/jobs/2/y/job")
    case_a = add(service, "casea", company="Zoox")
    case_b = add(service, "caseb", company="zoox")
    place = add(service, "place", location="2 Locations")
    dup_a = add(service, "dupa", url="https://ex.wd1.myworkdayjobs.com/en-US/site/job/Austin-TX/Engineer_R100", status="skipped")
    dup_b = add(service, "dupb", url="https://ex.wd1.myworkdayjobs.com/site/job/Austin-TX/Engineer_R100", status="skipped")
    old_term = add(service, "term", title="Software Engineer Intern, Summer 2026")

    report = b.backfill(service.db)

    with service.db.session() as s:
        jobs = {j.id: j for j in s.scalars(select(Job))}
        live = {e.job_id: e.active for e in s.scalars(select(JobListing))}
    assert stub not in jobs and manual in jobs and sent in jobs  # Unshown stubs go; hand-added and attempted jobs stay.
    assert live[closed] is False
    assert jobs[host].company == "ExampleSoft Holdings" and jobs[peer].company == "ExampleSoft Holdings"
    assert {jobs[case_a].company, jobs[case_b].company} == {"Zoox"} or jobs[case_a].company == jobs[case_b].company
    assert jobs[place].location == "" and live[place] is True
    assert sorted((live[dup_a], live[dup_b])) == [False, True]  # One entry per posting; both jobs remain.
    assert dup_a in jobs and dup_b in jobs
    assert live[old_term] is False
    assert report["stubs_removed"] == 1
    assert b.backfill(service.db) == {k: 0 for k in report}  # A second pass changes nothing.


def test_a_job_with_runs_keeps_its_employer_identity_when_the_new_name_would_change_it(service):
    sent = add(service, "sentacr", company="example hopkins apl", runs=1, url="https://careers.example.test/a")
    peer = add(service, "peeracr", company="Example Hopkins Applied Physics Laboratory", url="https://careers.example.test/b")
    b.backfill(service.db)
    with service.db.session() as s:
        assert s.get(Job, sent).company == "example hopkins apl"  # Receipts and caps were counted under this name.
        assert s.get(Job, peer).company == "Example Hopkins Applied Physics Laboratory"


def test_a_scan_does_not_reopen_a_closed_jobs_listing(service):
    closed = add(service, "reopen", status="closed")
    b.retire_listings(service.db)
    with service.db.session() as s:
        assert s.get(JobListing, closed).active is False
