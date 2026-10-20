"""Qualcomm (Eightfold) 2026-10-14: careers.qualcomm.com is Qualcomm's own login origin."""

from types import SimpleNamespace

from jobpilot.email_browser import EmailBrowser


def browser(company="Qualcomm", url="https://qualcomm.eightfold.ai/careers/job/1"):
    b = object.__new__(EmailBrowser)
    b.engine = SimpleNamespace(job={"url": url, "company": company})
    return b


def test_employer_named_https_origin_is_owned():
    assert browser().employer_owned("https://careers.qualcomm.com")


def test_other_or_insecure_origins_are_not_owned():
    assert not browser().employer_owned("https://careers.qualcomm-jobs.example.com")
    assert not browser().employer_owned("http://careers.qualcomm.com")
    assert not browser().employer_owned("https://starbucks.eightfold.ai")


def test_emailed_code_may_be_entered_on_the_employers_own_origin():
    b = browser()
    b.engine.job["ats"] = "eightfold"
    b.rule = {"link_origins": ["https://qualcomm.eightfold.ai"]}
    obs = {"text": "Enter the verification code"}
    assert b.code_origin_allowed("https://careers.qualcomm.com/careers/apply?pid=1", obs)
    assert not b.code_origin_allowed("https://careers.example.com/careers/apply?pid=1", obs)
    assert b.rule["link_origins"] == ["https://qualcomm.eightfold.ai"]


async def test_eightfold_reopens_apply_route_after_verification(monkeypatch):
    import asyncio

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    visits, events = [], []

    class Page:
        url = "https://starbucks.eightfold.ai/careers?domain=starbucks.com"

        async def goto(self, url, **kw):
            visits.append(url)

    b = browser("Starbucks", "https://starbucks.eightfold.ai/careers/job/481078371803")
    b.engine.page = Page()
    b.engine.emit = lambda *a: events.append(a[0])
    await b.reopen_eightfold_apply()
    assert visits == ["https://starbucks.eightfold.ai/careers/apply?pid=481078371803"]
    assert events == ["browser_recovery"]
    Page.url = "https://starbucks.eightfold.ai/careers/apply?pid=481078371803"
    await b.reopen_eightfold_apply()
    assert len(visits) == 1
