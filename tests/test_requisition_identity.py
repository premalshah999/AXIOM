"""One requisition, one identity: the employer's page around a Greenhouse or Ashby form, the board URL, an embed, an /apply
tail and a portal variant are the same posting (Roblox, Abnormal, Rubrik, IXL and others applied twice, 2026-10).
Synthetic employers and ids."""

import pytest
from sqlalchemy import select

from jobpilot.admission import QueueBlocked
from jobpilot.ats import canonicalize, requisition_keys
from jobpilot.db import Event, Job, Run
from jobpilot.features.discovery.policy import requisition_key

UUID_A = "11111111-2222-3333-4444-555555555555"
UUID_B = "99999999-2222-3333-4444-555555555555"


def keys(url):
    return {key for key, _ in requisition_keys(url)}


def same(*urls):
    first = keys(urls[0])
    return bool(first) and all(keys(u) & first for u in urls[1:])


def test_a_greenhouse_requisition_is_one_posting_on_the_wrapper_the_board_the_embed_and_the_eu_board():
    urls = [
        "https://careers.example.test/jobs/8262223?gh_jid=8262223",
        "https://www.example.test/company/jobs?gh_jid=8262223",
        "https://job-boards.greenhouse.io/examplecorp/jobs/8262223",
        "https://boards.greenhouse.io/examplecorp/jobs/8262223?gh_jid=8262223",
        "https://job-boards.eu.greenhouse.io/examplecorp/jobs/8262223",
        "https://job-boards.greenhouse.io/embed/job_app?for=examplecorp&token=8262223",
        "https://boards.greenhouse.io/embed/job_app?token=8262223",
        "https://app.careerpuck.test/job-board/examplecorp/job/8262223?gh_jid=8262223",
    ]
    assert same(*urls)
    assert len({canonicalize(u)[1] for u in urls}) > 1  # The gap: each was its own job.


def test_different_greenhouse_requisitions_stay_distinct():
    """IXL posts several 'Software Engineer' roles on one page and one board; only the id tells them apart."""
    ids = ["8862043002", "8862049002", "8895549002"]
    wrappers = [f"https://www.example.test/company/jobs?gh_jid={i}" for i in ids]
    boards = [f"https://job-boards.greenhouse.io/examplecorp/jobs/{i}" for i in ids]
    for n, wrapper in enumerate(wrappers):
        assert same(wrapper, boards[n])
        for other in (*wrappers[:n], *wrappers[n + 1 :], *boards[:n], *boards[n + 1 :]):
            assert not same(wrapper, other)
    assert not same("https://careers.example.test/jobs?gh_jid=123456", "https://careers.example.test/jobs?gh_jid=1234567")
    assert keys("https://careers.example.test/jobs?gh_jid=notanumber") == set()
    assert keys("https://careers.example.test/jobs") == set()


def test_an_ashby_posting_is_one_job_on_its_board_its_application_page_and_the_employers_embed():
    urls = [
        f"https://jobs.ashbyhq.com/examplecorp/{UUID_A}",
        f"https://jobs.ashbyhq.com/examplecorp/{UUID_A}/application?embed=true",
        f"https://careers.example.test/openings?ashby_jid={UUID_A}",
    ]
    assert same(*urls)
    assert not same(urls[0], f"https://jobs.ashbyhq.com/examplecorp/{UUID_B}")
    assert not same(urls[2], f"https://careers.example.test/openings?ashby_jid={UUID_B}")
    # A board whose slug is itself an id (Ashby's org ids) does not tie its postings together.
    org = "f7d8749a-72d6-407c-bf98-292ea630c73a"
    assert not same(f"https://jobs.ashbyhq.com/{org}/{UUID_A}", f"https://jobs.ashbyhq.com/{org}/{UUID_B}")
    assert same(f"https://jobs.ashbyhq.com/{org}/{UUID_A}/application", f"https://jobs.ashbyhq.com/{org}/{UUID_A}")


def test_a_lever_posting_is_one_job_with_or_without_its_apply_tail():
    assert same(f"https://jobs.lever.co/examplecorp/{UUID_A}", f"https://jobs.lever.co/examplecorp/{UUID_A}/apply")
    assert same(f"https://jobs.lever.co/examplecorp/{UUID_A}", f"https://jobs.eu.lever.co/examplecorp/{UUID_A}?lever-source=x")
    assert not same(f"https://jobs.lever.co/examplecorp/{UUID_A}", f"https://jobs.lever.co/examplecorp/{UUID_B}")


def test_a_pinpoint_posting_is_one_job_on_the_employers_domain_and_pinpoints():
    assert same(
        f"https://examplecorp.pinpointhq.com/en/postings/{UUID_A}?ats=pinpointhq",
        f"https://careers.example.test/en/postings/{UUID_A}",
    )


def test_a_workday_requisition_is_one_posting_across_portals_suffixes_and_the_apply_tail():
    jobs = "https://example.wd5.myworkdayjobs.com/en-US/external/job/Austin-TX/Analyst-I_R31111"
    site = "https://wd5.myworkdaysite.com/recruiting/example/External/job/Dallas-TX/Analyst-I_R31111-1"
    assert same(jobs, site, jobs + "/apply", jobs + "-3", site + "/apply/applyManually")
    assert not same(jobs, "https://example.wd5.myworkdayjobs.com/en-US/external/job/Austin-TX/Analyst-I_R31112")
    # The same number at another employer is another posting.
    assert not same(jobs, "https://other.wd5.myworkdayjobs.com/en-US/external/job/Austin-TX/Analyst-I_R31111")
    assert not same(jobs, "https://wd5.myworkdaysite.com/recruiting/other/External/job/Dallas-TX/Analyst-I_R31111")
    assert keys("https://wd5.myworkdayjobs.com/en-US/external/job/Austin-TX/Analyst-I_R31111") == set()  # No tenant.


def test_an_icims_requisition_is_one_posting_on_careers_talent_and_jibe_portals():
    careers = "https://careers-example.icims.com/jobs/1234/analyst/job?mobile=true"
    assert same(
        careers,
        "https://talent-example.icims.com/jobs/1234/analyst/job",
        "https://example.jibeapply.com/jobs/1234?icims=1",
        "https://employees-example.icims.com/jobs/1234/analyst/job",
    )
    assert not same(careers, "https://careers-example.icims.com/jobs/1235/analyst/job")
    assert not same(careers, "https://careers-other.icims.com/jobs/1234/analyst/job")
    # A Jibe site that is not iCIMS's numbers its own jobs.
    assert not same(careers, "https://example.jibeapply.com/jobs/1234")


def test_a_workable_posting_is_one_job_with_or_without_apply_and_in_any_case():
    assert same(
        "https://apply.workable.com/examplecorp/j/01F1C3E218",
        "https://apply.workable.com/examplecorp/j/01F1C3E218/apply",
        "https://apply.workable.com/ExampleCorp/j/01f1c3e218/apply/",
    )
    assert not same("https://apply.workable.com/examplecorp/j/01F1C3E218", "https://apply.workable.com/examplecorp/j/01F1C3E219")
    assert not same("https://apply.workable.com/examplecorp/j/01F1C3E218", "https://apply.workable.com/other/j/01F1C3E218")


def test_a_jazzhr_posting_is_named_by_its_id_not_its_title_slug():
    base = "https://examplecorp.applytojob.com/apply/AbCdEf1234"
    assert same(base + "/Software-Engineer", base + "/Software-Engineer-Emerging", base)
    assert not same(base, "https://examplecorp.applytojob.com/apply/AbCdEf1235/Software-Engineer")
    assert not same(base, "https://other.applytojob.com/apply/AbCdEf1234/Software-Engineer")


def test_a_dayforce_job_is_one_posting_whatever_the_case_or_locale():
    assert same(
        "https://jobs.dayforcehcm.com/en-US/example/candidateportal/jobs/3011",
        "https://jobs.dayforcehcm.com/en-US/example/CANDIDATEPORTAL/jobs/3011",
        "https://jobs.dayforcehcm.com/example/candidateportal/jobs/3011",
    )
    assert not same(
        "https://jobs.dayforcehcm.com/en-US/example/candidateportal/jobs/3011",
        "https://jobs.dayforcehcm.com/en-US/example/candidateportal/jobs/3012",
    )
    assert not same(
        "https://jobs.dayforcehcm.com/en-US/example/candidateportal/jobs/3011",
        "https://jobs.dayforcehcm.com/en-US/other/candidateportal/jobs/3011",
    )


def test_an_employers_own_site_numbers_its_requisitions_in_the_path():
    assert same(
        "https://www.jobs.example.test/jobs/10567489/apply",
        "https://jobs.example.test/en/jobs/10567489/software-development-engineer",
    )
    assert not same("https://jobs.example.test/jobs/10567489", "https://jobs.example.test/jobs/10567490")
    assert not same("https://jobs.example.test/jobs/10567489", "https://other.example.test/jobs/10567489")
    assert keys("https://jobs.example.test/jobs/42") == set()  # Too short to be a requisition number.


def test_urls_that_name_no_requisition_have_no_key():
    for url in ("https://example.test", "not a url", "ftp://example.test/jobs/1234567", "https://go.vansh.nyc/AB"):
        assert keys(url) == set()


# ---------- admission: every path into a run decides on the requisition ----------


def add(service, job_id, url, *, ats, status="ready", company="Example Corp"):
    with service.db.exclusive() as s:
        s.add(Job(id=job_id, identity=canonicalize(url)[1], url=url, ats=ats, status=status, company=company, title="Engineer I"))


def applied(service, job_id, state="confirmed", **fields):
    with service.db.exclusive() as s:
        s.add(Run(job_id=job_id, state=state, mode="submit", packet={}, **fields))


BOARD = "https://job-boards.greenhouse.io/examplecorp/jobs/8262223"
WRAPPER = "https://careers.example.test/jobs/8262223?gh_jid=8262223"


def assert_recorded_without_a_browser(service, job_id, original):
    with service.db.session() as s:
        job = s.get(Job, job_id)
        runs = list(s.scalars(select(Run).where(Run.job_id == job_id)))
        assert job.status == "already_applied"
        assert [r.state for r in runs] == ["already_applied"]
        assert runs[0].receipt["type"] == "duplicate_posting" and runs[0].receipt["duplicate_of"] == original
        assert s.scalar(select(Event.kind).where(Event.run_id == runs[0].id)) == "duplicate_posting"
        assert not list(s.scalars(select(Run).where(Run.state.in_(["queued", "running"]))))


async def test_the_employers_page_is_recorded_as_applied_when_its_greenhouse_posting_was(service):
    """Roblox, Abnormal, Rubrik, IXL: the board job was confirmed, then the page's own link ran."""
    add(service, "board", BOARD, ats="greenhouse", status="confirmed")
    add(service, "wrapper", WRAPPER, ats="custom")
    applied(service, "board")
    with pytest.raises(QueueBlocked) as err:
        await service.queue("wrapper", "submit", owner_pick=True)
    assert err.value.code == "duplicate_posting"
    assert_recorded_without_a_browser(service, "wrapper", "board")


async def test_the_board_job_is_recorded_as_applied_when_the_employers_page_was(service):
    add(service, "board", BOARD, ats="greenhouse")
    add(service, "wrapper", WRAPPER, ats="custom", status="confirmed")
    applied(service, "wrapper")
    with pytest.raises(QueueBlocked) as err:
        await service.queue("board", "submit")
    assert err.value.code == "duplicate_posting"
    assert_recorded_without_a_browser(service, "board", "wrapper")


@pytest.mark.parametrize(
    "ats, first, second",
    [
        ("custom", f"https://jobs.ashbyhq.com/examplecorp/{UUID_A}/application?embed=true", f"https://careers.example.test/openings?ashby_jid={UUID_A}"),
        ("workday", "https://example.wd5.myworkdayjobs.com/en-US/external/job/Austin-TX/Analyst-I_R31111", "https://wd5.myworkdaysite.com/recruiting/example/External/job/Dallas-TX/Analyst-I_R31111-1"),
        ("icims", "https://careers-example.icims.com/jobs/1234/analyst/job", "https://talent-example.icims.com/jobs/1234/analyst/job"),
        ("custom", "https://careers-example.icims.com/jobs/1234/analyst/job", "https://example.jibeapply.com/jobs/1234?icims=1"),
        ("workable", "https://apply.workable.com/examplecorp/j/01F1C3E218/apply", "https://apply.workable.com/examplecorp/j/01F1C3E218"),
        ("jazzhr", "https://examplecorp.applytojob.com/apply/AbCdEf1234/Software-Engineer", "https://examplecorp.applytojob.com/apply/AbCdEf1234/Software-Engineer-Emerging"),
        ("custom", f"https://examplecorp.pinpointhq.com/en/postings/{UUID_A}?ats=pinpointhq", f"https://careers.example.test/en/postings/{UUID_A}"),
        ("custom", "https://www.jobs.example.test/jobs/10567489/apply", "https://jobs.example.test/en/jobs/10567489/software-development-engineer"),
    ],
)
async def test_every_platform_form_of_an_applied_requisition_is_recorded_not_submitted(service, ats, first, second):
    add(service, "done", first, ats="ashby" if "ashbyhq" in first else ats if ats != "custom" else "custom", status="confirmed")
    add(service, "again", second, ats=ats)
    applied(service, "done")
    with pytest.raises(QueueBlocked) as err:
        await service.queue("again", "submit", owner_pick=True)
    assert err.value.code == "duplicate_posting"
    assert_recorded_without_a_browser(service, "again", "done")


async def test_an_employer_that_answered_already_applied_counts_as_applied(service):
    add(service, "board", BOARD, ats="greenhouse", status="already_applied")
    add(service, "wrapper", WRAPPER, ats="custom")
    applied(service, "board", "already_applied", receipt={"type": "employer_already_applied"})
    with pytest.raises(QueueBlocked) as err:
        await service.queue("wrapper", "submit", owner_pick=True)
    assert err.value.code == "duplicate_posting"


async def test_a_requisition_with_a_run_under_way_is_not_started_a_second_time(service):
    add(service, "board", BOARD, ats="greenhouse", status="queued")
    add(service, "wrapper", WRAPPER, ats="custom")
    applied(service, "board", "running")
    with pytest.raises(QueueBlocked) as err:
        await service.queue("wrapper", "submit", owner_pick=True)
    assert err.value.code == "active_run"


async def test_an_uncertain_submission_on_the_other_link_holds_this_one(service):
    add(service, "board", BOARD, ats="greenhouse", status="submission_unknown")
    add(service, "wrapper", WRAPPER, ats="custom")
    applied(service, "board", "submission_unknown")
    with pytest.raises(QueueBlocked) as err:
        await service.queue("wrapper", "submit", owner_pick=True)
    assert err.value.code == "submitted_or_uncertain"


async def test_a_different_requisition_of_the_same_employer_is_not_a_duplicate(service):
    """IXL: several 'Software Engineer' postings, one board, one page."""
    add(service, "board", BOARD, ats="greenhouse", status="confirmed")
    add(service, "other", "https://careers.example.test/jobs?gh_jid=8895549002", ats="custom")
    applied(service, "board")
    try:
        await service.queue("other", "submit", owner_pick=True)
    except QueueBlocked as err:  # Later gates may hold it; never the duplicate guard.
        assert err.code not in {"duplicate_posting", "submitted_or_uncertain"}
    except ValueError:
        pass
    with service.db.session() as s:
        assert s.get(Job, "other").status != "already_applied"


async def test_an_archived_application_guards_its_twin_on_another_platform_page(service):
    from jobpilot.ats import identity_variants
    from jobpilot.db import Setting
    from jobpilot.operations.reset import guard_key

    add(service, "board", BOARD, ats="greenhouse", status="archived")
    add(service, "wrapper", WRAPPER, ats="custom")
    with service.db.exclusive() as s:
        s.add(Setting(key=guard_key(identity_variants(BOARD)[0]), value={}))
    with pytest.raises(QueueBlocked) as err:
        await service.queue("wrapper", "submit", owner_pick=True)
    assert err.value.code == "archived_submission"


async def test_a_retry_resumed_from_a_draft_is_decided_by_the_requisition_too(service):
    """Recovery retries and scheduled retries queue with a source run; the duplicate guard comes before it."""
    add(service, "board", BOARD, ats="greenhouse", status="confirmed")
    add(service, "wrapper", WRAPPER, ats="custom", status="waiting_browser")
    applied(service, "board")
    applied(service, "wrapper", "waiting_browser")
    with service.db.session() as s:
        source = s.scalar(select(Run.id).where(Run.job_id == "wrapper"))
    with pytest.raises(QueueBlocked) as err:
        await service.queue("wrapper", "submit", retry=True, recovery_source_run=source)
    assert err.value.code == "duplicate_posting"
    with service.db.session() as s:
        assert s.get(Job, "wrapper").status == "already_applied"
        assert not list(s.scalars(select(Run).where(Run.state.in_(["queued", "running"]))))


@pytest.mark.parametrize("actor", ["autopilot", "owner"])
async def test_the_owners_resume_and_the_automatic_retry_are_decided_by_the_requisition_too(service, actor):
    from jobpilot.job_control import manage

    add(service, "board", BOARD, ats="greenhouse", status="confirmed")
    add(service, "wrapper", WRAPPER, ats="custom", status="waiting_browser")
    applied(service, "board")
    with service.db.exclusive() as s:
        s.add(Run(job_id="wrapper", state="waiting_browser", mode="submit", packet={"match_approved": True}))
    with pytest.raises(QueueBlocked) as err:
        await manage(service, "wrapper", "resume", actor=actor)
    assert err.value.code == "duplicate_posting"
    with service.db.session() as s:
        assert s.get(Job, "wrapper").status == "already_applied"


def test_apply_many_records_the_twin_and_queues_nothing_for_it(client, service, prepared):
    dispatched = []

    async def dispatch(run_id):
        dispatched.append(run_id)

    service.dispatch = dispatch
    service.db.set_setting("control", {"paused": False, "auto_submit": True})
    add(service, "board", BOARD, ats="greenhouse", status="confirmed")
    add(service, "wrapper", WRAPPER, ats="custom", status="skipped")
    applied(service, "board")
    body = client.post("/api/jobs/apply-many", json={"job_ids": ["wrapper"]}).json()
    assert body["results"][0]["outcome"] == "refused" and body["results"][0]["code"] == "duplicate_posting"
    assert not dispatched
    assert_recorded_without_a_browser(service, "wrapper", "board")


async def test_a_short_link_to_the_employers_page_finds_its_greenhouse_twin(service, monkeypatch):
    """Shield AI's shape: the feed's short link leads to the page; the board job was applied to."""
    import httpx

    from jobpilot import shortlinks

    monkeypatch.setattr(shortlinks, "_memo", {})
    monkeypatch.setattr(shortlinks, "_last", {})
    monkeypatch.setattr(shortlinks, "MIN_GAP", 0)
    monkeypatch.setattr(
        shortlinks, "TRANSPORT", httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": WRAPPER}))
    )
    add(service, "board", BOARD, ats="greenhouse", status="confirmed")
    add(service, "short", "https://go.vansh.nyc/AB", ats="custom")
    applied(service, "board")
    with pytest.raises(QueueBlocked) as err:
        await service.queue("short", "submit", owner_pick=True)
    assert err.value.code == "duplicate_posting"
    assert_recorded_without_a_browser(service, "short", "board")


async def test_the_board_job_finds_a_short_link_that_led_to_the_employers_page(service, monkeypatch):
    from jobpilot import shortlinks

    monkeypatch.setattr(shortlinks, "_memo", {})
    add(service, "board", BOARD, ats="greenhouse")
    add(service, "short", "https://go.vansh.nyc/AB", ats="custom", status="confirmed")
    applied(service, "short")
    with service.db.exclusive() as s:
        from jobpilot.db import Setting

        s.add(Setting(key=shortlinks.cache_key("https://go.vansh.nyc/AB"), value={"url": "https://go.vansh.nyc/AB", "final": WRAPPER}))
    with pytest.raises(QueueBlocked) as err:
        await service.queue("board", "submit", owner_pick=True)
    assert err.value.code == "duplicate_posting"


@pytest.mark.parametrize("text", [
    "A bachelor's degree is required. Python is required.",
    "Requirements: five years of work.",
    "Requisition Type: Regular. Job ID: see below.",
])
def test_ordinary_words_in_a_posting_are_not_a_requisition_number(text):
    first = requisition_key("https://careers.acme.test/roles/data-engineer", "Data Engineer", "Austin", text, "Acme")
    second = requisition_key("https://careers.acme.test/roles/ml-engineer", "ML Engineer", "Austin", text, "Acme")
    assert first != second


@pytest.mark.parametrize("text", ["Requisition ID: R12345", "Req #98765", "Job ID 4471", "REQ-2025-0042", "Req: AB1234"])
def test_a_stated_requisition_number_still_joins_two_links_of_one_posting(text):
    first = requisition_key("https://careers.acme.test/a", "Data Engineer", "Austin", text, "Acme")
    second = requisition_key("https://acme.test/careers/b", "Engineer, Data", "Austin, TX", text, "Acme")
    assert first == second
