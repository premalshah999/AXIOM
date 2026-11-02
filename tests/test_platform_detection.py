"""A job's platform, read from what its page and address prove (2026-11-01).

Of the jobs that ran as career sites in the last three weeks, 70 whose saved pages showed Greenhouse, iCIMS, Workday,
SuccessFactors, Oracle or Ashby kept the custom label until a run had found the platform: first-try success 33% (the
platforms' own drivers: 60-68%), 4.2 runs per job (1.5), all of them in the single custom-site lane. The label is now
also written when a job is added (the page `enrich` fetches, the address's own platform parameter) and once at startup
from the hand-offs earlier runs recorded. These use synthetic pages and addresses."""

from jobpilot.admission import QueueBlocked
from jobpilot.ats import canonicalize
from jobpilot.discovery import add_job
from jobpilot.schemas import JobInput

import pytest

from jobpilot.ats import classify_embedded, entries_from_html, marketing_host, url_platform
from jobpilot.db import Event, Job, Run
from jobpilot.platform_label import label_job_from_page, label_jobs_from_history, record_observed_platform

SITE = "https://careers.example.test/jobs/4455"


def seed(service, ats="custom", url=SITE, job_id="job1", status="new", identity=None):
    with service.db.session() as s:
        s.add(Job(id=job_id, identity=identity or job_id, url=url, ats=ats, title="Engineer", company="Acme", status=status))


def row(service, job_id="job1"):
    with service.db.session() as s:
        j = s.get(Job, job_id)
        return j.ats, j.url, j.identity


# ----- what the page proves -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "entries",
    [
        # A platform's marketing site, a "Powered by" badge or a tracker: the employer uses the vendor, the application
        # is not made there.
        [("apply", "https://www.workable.com/")],
        [("apply", "https://www.icims.com/products")],
        [("script", "https://js.icims.com/analytics/pixel.js")],
        [("script", "https://boards.greenhouse.io/static/tracking-beacon.js")],
        [("script", "https://cdn.ashbyhq.com/consent/cookie-banner.js")],
        [("frame", "https://boards.greenhouse.io/acme/newsletter-signup")],
        [("form", "https://acme.icims.com/newsletter/subscribe")],
        [("form", "https://www.lever.co/contact-sales")],
        [("jsonld", "https://www.smartrecruiters.com/")],
    ],
)
def test_a_platforms_footer_tracker_or_marketing_page_is_not_proof(entries):
    assert classify_embedded(entries) == (None, None, "")


def test_a_platforms_own_address_is_proof_but_its_marketing_host_is_not():
    assert marketing_host("https://www.workable.com/") and marketing_host("https://greenhouse.io/")
    assert not marketing_host("https://apply.workable.com/acme/j/ABC123")
    assert not marketing_host("https://acme.icims.com/jobs/12/x/job")
    assert not marketing_host("https://www.comeet.com/jobs/acme/12.345")  # a posting on the vendor's own host
    assert not marketing_host("https://jobs.gem.com/acme/x") and not marketing_host("https://careers.oracle.com/jobs")


def test_a_form_that_posts_to_a_platform_names_it_but_is_no_page_to_open():
    entries = [("form", "https://boards.greenhouse.io/acme/jobs/4455/apply")]
    assert classify_embedded(entries) == ("greenhouse", None, "form")
    # An Apply link to a real page still wins, because it gives the run the application to open.
    link = "https://acme.wd3.myworkdayjobs.com/en-US/External/job/NY/Engineer_R1/apply"
    assert classify_embedded(entries + [("apply", link)]) == ("workday", link, "apply")


def test_the_postings_structured_data_names_the_platform_that_takes_the_application():
    url = "https://jobs.lever.co/acme/0f5d2c1e-1111-2222-3333-444455556666"
    assert classify_embedded([("jsonld", SITE), ("jsonld", url)]) == ("lever", url, "jsonld")
    assert classify_embedded([("jsonld", SITE)]) == (None, None, "")


def test_the_page_markup_is_read_without_a_browser():
    html = """<html><head>
    <script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting","title":"Engineer",
      "url":"https://careers.example.test/jobs/4455",
      "potentialAction":{"@type":"ApplyAction","target":"https://jobs.ashbyhq.com/acme/0f5d2c1e-1111-2222-3333-444455556666/application"}}</script>
    <script src="//www.googletagmanager.com/gtag/js"></script></head>
    <body><a href="/apply/4455">Start your application</a><a href="/privacy">Privacy</a>
    <form action="/search"></form><iframe src="https://boards.greenhouse.io/embed/job_app?for=acme&token=4455"></iframe>
    </body></html>"""
    entries = entries_from_html(html, SITE)
    kinds = {}
    for kind, value in entries:
        kinds.setdefault(kind, []).append(value)
    assert kinds["frame"] == ["https://boards.greenhouse.io/embed/job_app?for=acme&token=4455"]
    assert kinds["apply"] == ["https://careers.example.test/apply/4455"]
    assert kinds["form"] == ["https://careers.example.test/search"]
    assert "https://jobs.ashbyhq.com/acme/0f5d2c1e-1111-2222-3333-444455556666/application" in kinds["jsonld"]
    assert classify_embedded(entries)[0] == "greenhouse"  # the frame, the form itself, first


def test_markup_without_an_application_shows_no_platform():
    html = '<script src="https://www.googletagmanager.com/gtag/js"></script><a href="https://www.icims.com/">Powered by iCIMS</a>'
    assert classify_embedded(entries_from_html(html, SITE)) == (None, None, "")


# ----- what the address proves --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://careers.example.test/jobs/27974?icims=1", "icims"),
        ("https://careers.example.test/jobs/8168881?gh_jid=8168881", "greenhouse"),
        ("https://www.example.test/open-roles?ashby_jid=0f5d2c1e-1111-2222-3333-444455556666", "ashby"),
        # Not a platform's parameter: any other value, or no job id behind it.
        ("https://careers.example.test/jobs/27974?icims=0", None),
        ("https://careers.example.test/jobs/27974?gh_jid=abc", None),
        ("https://careers.example.test/jobs?icims=1", None),
        ("https://careers.example.test/jobs/27974", None),
        # Portal paths only one platform uses: Eightfold's position page, Avature's unprefixed JobDetail page.
        ("https://apply.careers.example.test/careers/job/1970393557045536", "eightfold"),
        ("https://jobs.example.test/careers/job/893397927110/", "eightfold"),
        ("https://careers.example.test/careers/JobDetail/New-York-Engineer/14014", "avature"),
        ("https://www.example.test/careers/job/8036615003", None),  # Ten digits: a Greenhouse-style requisition.
        ("https://www.example.test/careers/job/1970393557045536/apply", None),
        ("https://careers.example.test/careers/JobDetail/campus", None),
        # A platform's own address is read by its host, never by this.
        ("https://job-boards.greenhouse.io/acme/jobs/4455?gh_jid=4455", None),
    ],
)
def test_an_employer_address_names_a_platform_only_by_the_platforms_own_parameter(url, expected):
    assert url_platform(url) == expected


# ----- writing the label --------------------------------------------------------------------------------------------

GH_PAGE = '<body><iframe src="https://boards.greenhouse.io/embed/job_app?for=acme&token=4455"></iframe></body>'


def test_a_posting_page_that_shows_its_platform_relabels_the_job_and_nothing_else(service):
    seed(service, identity="idhash")
    assert label_job_from_page(service.db, "job1", GH_PAGE, SITE) == "greenhouse"
    # Address and identity are the career site's still; the label is an observation, so the next run starts as the
    # career site (no account or mail rule moves with the label).
    assert row(service) == ("greenhouse", SITE, "idhash")
    assert service.db.get_setting("observed_ats:job1", {})["was"] == "custom"


def test_a_page_with_only_a_tracker_leaves_the_job_a_career_site(service):
    seed(service)
    page = '<script src="https://boards.greenhouse.io/static/tracking-beacon.js"></script><a href="https://www.icims.com">Apply</a>'
    assert label_job_from_page(service.db, "job1", page, SITE) is None
    assert row(service)[0] == "custom"
    assert service.db.get_setting("observed_ats:job1", {}) == {}


def test_a_redirect_to_the_platform_is_the_pages_answer(service):
    seed(service, url="https://go.example.test/Ab")
    landed = "https://acme.wd3.myworkdayjobs.com/en-US/External/job/NY/Engineer_R1"
    assert label_job_from_page(service.db, "job1", "<html></html>", landed) == "workday"
    assert row(service)[:2] == ("workday", "https://go.example.test/Ab")


def test_the_label_is_never_taken_from_a_vendor_or_a_board(service):
    seed(service)
    page = '<script src="https://cdn.phenompeople.com/phenom.js"></script><a href="https://simplify.jobs/p/1">Apply now</a>'
    assert label_job_from_page(service.db, "job1", page, SITE) is None


def test_a_job_with_a_run_in_flight_is_not_relabelled_from_outside_the_run(service):
    seed(service, status="running")
    assert label_job_from_page(service.db, "job1", GH_PAGE, SITE) is None
    assert row(service)[0] == "custom"
    # Its own run may, as before.
    assert record_observed_platform(service.db, "job1", "greenhouse") is True


def test_a_label_from_the_address_is_not_replaced_by_a_page(service):
    seed(service, "eightfold", "https://acme.eightfold.ai/careers/job/1")
    assert label_job_from_page(service.db, "job1", GH_PAGE, "https://acme.eightfold.ai/careers/job/1") is None
    assert row(service)[0] == "eightfold"


def test_the_label_only_moves_up(service):
    seed(service, "pinpoint", "https://acme.pinpointhq.com/postings/1")
    assert label_job_from_page(service.db, "job1", GH_PAGE, "https://acme.pinpointhq.com/postings/1") == "greenhouse"
    assert label_job_from_page(service.db, "job1", '<div id="comeet-jsapi"></div>', SITE) is None
    assert row(service)[0] == "greenhouse"


# ----- the page enrich fetches, and the startup pass ----------------------------------------------------------------


async def test_reading_a_posting_labels_the_job_from_the_page_it_fetched(service, config, monkeypatch):
    from jobpilot import discovery

    seed(service)

    async def fetch(url, **_):
        return f"<html><title>Engineer</title>{GH_PAGE}</html>", url

    monkeypatch.setattr(discovery, "fetch", fetch)
    await discovery.enrich(service.db, config, "job1")
    assert row(service)[0] == "greenhouse"


def test_startup_labels_career_sites_from_earlier_handoffs_and_their_own_address(service):
    seed(service, job_id="a", url="https://careers.a.example.test/x", identity="ia")
    seed(service, job_id="b", url="https://careers.b.example.test/jobs/27974?icims=1", identity="ib")
    seed(service, job_id="c", url="https://careers.c.example.test/y", identity="ic")
    seed(service, job_id="d", url="https://careers.d.example.test/z", identity="id", status="running")
    seed(service, job_id="e", url="https://acme.eightfold.ai/careers/job/1", ats="eightfold", identity="ie")
    with service.db.session() as s:
        for run_id, job_id, message in (
            ("ra1", "a", "Career site uses ashby; opening its application directly"),
            ("ra2", "a", "Career site uses icims; opening its application directly"),
            ("rc1", "c", "The career site is a eightfold portal"),  # no hand-off to a platform's page
            ("rd1", "d", "Career site uses icims; opening its application directly"),
            ("re1", "e", "Application moved to workday"),
        ):
            s.add(Run(id=run_id, job_id=job_id, state="confirmed"))
            s.add(Event(run_id=run_id, kind="handoff", message=message, created_at=f"2026-10-2{run_id[-1]}T00:00:00"))
    assert label_jobs_from_history(service.db) == 2
    assert row(service, "a")[0] == "icims"  # the latest hand-off
    assert row(service, "b")[0] == "icims"  # the address
    assert row(service, "c")[0] == "custom"
    assert row(service, "d")[0] == "custom"  # busy
    assert row(service, "e")[0] == "eightfold"  # labelled by its address
    # Identity, address and runs are untouched; running it again changes nothing.
    assert row(service, "a")[1:] == ("https://careers.a.example.test/x", "ia")
    assert label_jobs_from_history(service.db) == 0
    with service.db.session() as s:
        assert s.get(Run, "ra1").state == "confirmed"


def test_a_relabelled_job_leaves_the_custom_lane_and_its_run_still_starts_as_the_career_site(service):
    from jobpilot.browser import BrowserEngine
    from jobpilot.pipeline_comparison import custom_site

    seed(service)
    assert custom_site({"ats": row(service)[0]})
    label_job_from_page(service.db, "job1", GH_PAGE, SITE)
    assert not custom_site({"ats": row(service)[0]})
    e = BrowserEngine.__new__(BrowserEngine)
    e.db = service.db
    assert e.starting_label({"id": "job1", "url": SITE, "ats": "greenhouse"}) == "custom"


async def test_a_relabelled_job_still_blocks_a_second_application_to_its_address(service):
    """The job moved from "custom" to the platform it runs on, and its stored identity is an older one, so neither the label
    nor the identity column finds it. Its address and its confirmed run still must."""
    url = "https://careers.example.test/roles/data-engineer"
    with service.db.session() as session:
        session.add(Job(id="earlier", url=url, identity="older-hash", ats="greenhouse", company="Example", title="Data Engineer",
                        status="confirmed", location="", description="x", demo=False))
        session.add(Run(id="sent", job_id="earlier", state="confirmed", mode="submit", packet={}))
        session.commit()
    row, created = add_job(service.db, JobInput(url=url), origin="manual")
    assert row["id"] == "earlier" and not created
    with service.db.session() as session:
        session.add(Job(id="later", url=url + "?utm_source=x", identity=canonicalize(url)[1], ats="custom", company="Example",
                        title="Data Engineer", status="ready", location="", description="x", demo=False))
        session.commit()
    with pytest.raises(QueueBlocked) as blocked:
        await service.queue("later", "submit")
    assert blocked.value.code == "duplicate_posting"


def test_a_short_link_takes_the_platform_of_the_posting_it_leads_to(service):
    from jobpilot.admission import SHARED_LINK_HOSTS
    from jobpilot.db import Setting
    from jobpilot.shortlinks import cache_key

    short_host = sorted(SHARED_LINK_HOSTS)[0]
    leads = {
        f"https://{short_host}/a": "https://job-boards.greenhouse.io/examplecorp/jobs/11",
        f"https://{short_host}/b": "https://careers.example.test/roles/data-engineer",
    }
    with service.db.session() as session:
        for job_id, (short, final) in zip(("one", "two"), leads.items()):
            session.add(Job(id=job_id, url=short, identity=job_id, ats="custom", company="Example", title="Engineer",
                            status="ready", location="", description="x", demo=False))
            session.add(Setting(key=cache_key(short), value={"url": short, "final": final}))
        session.commit()
    assert label_jobs_from_history(service.db) == 1
    with service.db.session() as session:
        assert session.get(Job, "one").ats == "greenhouse"
        assert session.get(Job, "two").ats == "custom"  # An employer's own page proves nothing by its address.


def test_a_career_site_proven_on_one_platform_speaks_for_its_other_jobs(service):
    site = "https://careers.example.test/jobs/"
    mixed = "https://jobs.example.test/roles/"
    rows = {"a": site + "11?icims=1", "b": site + "22", "c": mixed + "1?icims=1", "d": mixed + "2?gh_jid=2", "e": mixed + "3"}
    with service.db.session() as session:
        for job_id, url in rows.items():
            session.add(Job(id=job_id, url=url, identity=job_id, ats="custom", company="Example", title="Engineer",
                            status="ready", location="", description="x", demo=False))
        session.commit()
    label_jobs_from_history(service.db)
    with service.db.session() as session:
        assert session.get(Job, "b").ats == "icims"
        assert session.get(Job, "e").ats == "custom"  # This host shows two platforms.
