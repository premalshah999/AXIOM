"""The platform behind a career site, read from what the page shows (2026-10-31).

Of 269 jobs read as custom career sites, 31 sat on platforms the registry could not name (Pinpoint, Comeet, Gem,
CareerPuck, ClearCompany, JobDiva, Simplify), Microsoft's and AARP's Eightfold sites showed only Eightfold stylesheets,
Two Sigma's Avature portal showed only meta tags, and PwC, Ascension and Crowe's Phenom pages returned "phenom" from a
script and never followed their real Apply link. These pin the rules with synthetic pages."""

import pytest

from jobpilot.ats import (
    AGGREGATORS,
    LABEL_ONLY,
    classify_embedded,
    custom_like,
    detect,
    detect_embedded,
    unsettled,
)
from jobpilot.pipeline_comparison import custom_site


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://acme.pinpointhq.com/postings/0f5d", "pinpoint"),
        ("https://www.comeet.com/jobs/acme/12.345", "comeet"),
        ("https://jobs.gem.com/acme/abc", "gem"),
        ("https://app.careerpuck.com/job-board/acme/job/1", "careerpuck"),
        ("https://apply.clearcompany.com/acme?jobId=1", "clearcompany"),
        ("https://www1.jobdiva.com/portal/?a=x#/jobs/1", "jobdiva"),
        ("https://app.dover.com/apply/acme/1", "dover"),
        ("https://simplify.jobs/p/0f5d", "simplify"),
        ("https://www.adzuna.com/details/1", "adzuna"),
        # Hosts that were already named stay so, and a vendor's marketing site is not a platform's job page.
        ("https://acme.teamtailor.com/jobs/1", "teamtailor"),
        ("https://acme.recruitee.com/o/engineer", "recruitee"),
        ("https://jobs.personio.de/job/1", "personio"),
        ("https://recruiting.ultipro.com/ACM1000/JobBoard/x/OpportunityDetail?opportunityId=1", "ukg"),
        ("https://www.gem.com/", "custom"),
        ("https://acme.example.test/careers", "custom"),
    ],
)
def test_hosts_name_their_platform(url, expected):
    assert detect(url).id == expected


def test_a_named_platform_with_no_driver_is_run_and_treated_as_a_custom_site():
    # Naming it changes the label and nothing else: the generic driver, the custom-site worker lane and the custom
    # site's sender rule all stay (ClearCompany's emailed sign-in link and Pinpoint's mail were matched through it).
    assert {"pinpoint", "comeet", "gem", "careerpuck", "clearcompany", "jobdiva", "simplify"} <= LABEL_ONLY
    assert AGGREGATORS == {"simplify", "adzuna"} and AGGREGATORS <= LABEL_ONLY
    assert all(custom_like(x) for x in LABEL_ONLY) and custom_like("custom")
    assert not custom_like("greenhouse") and not custom_like("workday") and not custom_like("phenom")
    assert unsettled("phenom") and unsettled("custom") and unsettled("gem") and not unsettled("ashby")
    for name in ("pinpoint", "clearcompany", "custom"):
        assert custom_site({"ats": name})
    assert not custom_site({"ats": "greenhouse"}) and not custom_site({"ats": "phenom"})


async def test_a_named_platform_keeps_the_custom_sites_own_sender_rule(service):
    from support_mail import seed

    seed(service, employer="https://apply.clearcompany.example.test")
    job = {"url": "https://apply.clearcompany.example.test/acme?jobId=1", "ats": "clearcompany"}
    rule = await service.mail.ensure_rule(job, "alex@example.test")
    assert rule["sender_domains"] == ["example.test"]
    # A label that is neither a custom-like site nor a platform with a sender family still gets none. (Ashby had none when this
    # was written; f631a23 gave every driven platform its senders, so a career-site vendor stands in.)
    assert await service.mail.ensure_rule({**job, "ats": "phenom", "url": "https://careers.example.test/acme/1"}, "alex@example.test") is None


# ----- what a page shows ------------------------------------------------------------------------------------------


def test_a_real_apply_link_beats_a_vendors_script():
    # PwC, Ascension, Crowe: Phenom's scripts are everywhere on the page, the application is on Workday.
    entries = [
        ("script", "https://cdn.phenompeople.com/CareerConnectResources/prod/phenom.js"),
        ("apply", "https://acme.wd3.myworkdayjobs.com/en-US/External/job/NY/Engineer_R1/apply"),
    ]
    assert classify_embedded(entries) == (
        "workday",
        "https://acme.wd3.myworkdayjobs.com/en-US/External/job/NY/Engineer_R1/apply",
        "apply",
    )
    assert detect_embedded(entries)[0] == "workday"


def test_a_vendor_script_alone_names_no_platform():
    assert classify_embedded([("script", "https://cdn.phenompeople.com/phenom.js"), ("meta", "use-widget=ph-job-detail-v1")]) == (
        None,
        None,
        "",
    )


def test_a_job_board_is_never_the_application_platform():
    assert classify_embedded([("apply", "https://simplify.jobs/p/0f5d")])[0] is None
    assert classify_embedded([("frame", "https://www.adzuna.com/details/1/apply")])[0] is None


def test_avature_is_read_from_the_portals_meta_tags():
    # Two Sigma's /careers/JobDetail and EA's ApplicationEEO have no locale prefix in the path, only the portal's meta.
    entries = [("meta", "avature.portal.id=106"), ("meta", "avature.portal.page=JobDetail"), ("meta", "viewport=width=device-width")]
    assert classify_embedded(entries) == ("avature", None, "meta")
    assert classify_embedded([("link", "https://templates-static-assets.avacdn.net/css/site.css")])[0] == "avature"
    assert classify_embedded([("meta", "avature_unrelated=1"), ("meta", "robots=noindex")])[0] is None


def test_eightfold_is_read_from_its_stylesheets_on_a_branded_domain():
    # Microsoft's and AARP's career sites show eightfold.ai nowhere, only its CDN and font stylesheet.
    css = "https://static.vscdn.net/fonts/css/eightfold-font-base.css"
    assert classify_embedded([("link", css)]) == ("eightfold", None, "link")
    assert classify_embedded([("link", "https://static-azure.vscdn.net/x/site.css")])[0] == "eightfold"
    assert classify_embedded([("link", "https://cdn.example.test/css/site.css")])[0] is None


def test_greenhouse_is_read_from_its_embed_markers_before_the_form_has_loaded():
    # Abnormal, Databricks and Lyft's CareerPuck render #grnhse_app and add the iframe after the load event.
    assert classify_embedded([("dom", "grnhse_app")]) == ("greenhouse", None, "dom")
    assert classify_embedded([("script", "https://boards.greenhouse.io/embed/job_board/js?for=acme")]) == ("greenhouse", None, "script")
    frame = "https://boards.greenhouse.io/embed/job_app?for=acme&token=1"
    assert classify_embedded([("dom", "grnhse_app"), ("frame", frame)]) == ("greenhouse", frame, "frame")
    # An unrelated frame is not the application.
    assert classify_embedded([("frame", "https://boards.greenhouse.io/acme/analytics-pixel")])[0] is None


def test_a_named_platform_is_a_label_never_a_place_to_navigate_to():
    # The page's own form works where it is (17 of 31 confirmed on the generic path): no navigation to Comeet's page.
    assert classify_embedded([("apply", "https://www.comeet.com/jobs/acme/12.345")]) == ("comeet", None, "apply")
    assert classify_embedded([("dom", "comeet-jsapi")]) == ("comeet", None, "dom")
    assert classify_embedded([("link", "https://d2n5ied94mazop.cloudfront.net/assets/application.css")]) == ("pinpoint", None, "link")


def test_a_real_platform_outranks_the_named_one_on_the_same_page():
    # CareerPuck's page loads its own scripts and holds Greenhouse's form: the form is what gets filled.
    entries = [("script", "https://static.careerpuck.com/app.js"), ("dom", "grnhse_app")]
    assert classify_embedded(entries)[0] == "greenhouse"


def test_the_first_frame_wins_over_scripts_and_links():
    entries = [
        ("script", "https://boards.greenhouse.io/embed/job_board/js?for=acme"),
        ("frame", "https://acme.wd1.myworkdayjobs.com/en-US/External/job/NY/Engineer_R1?embed=1"),
    ]
    assert classify_embedded(entries)[0] == "workday"
