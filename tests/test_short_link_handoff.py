"""Applied Materials' and Booz Allen's short links (2026-10-26) redirect straight to their Workday tenants. The run saw
Workday but kept the short link as the employer, and Workday refused the tenant: "Workday account origin does not
match this employer"."""

from types import SimpleNamespace

from jobpilot.browser import BrowserEngine

POSTING = "https://amat.wd1.myworkdayjobs.com/External/job/Chandler-AZ/Software-Engineer_R2627911"


async def test_a_custom_link_that_lands_on_workday_is_the_employers_handoff():
    engine = BrowserEngine.__new__(BrowserEngine)
    engine.job = {"id": "j", "url": "https://go.example.test/Bi", "ats": "custom"}
    engine.page = SimpleNamespace(url=POSTING + "/apply")
    engine.emit = lambda *a, **k: None
    assert await engine.detect_ats(engine.page, "custom") == "workday"
    assert engine.handoff_origin == "https://amat.wd1.myworkdayjobs.com"
    assert engine.job["url"] == POSTING and engine.job["ats"] == "workday"


async def test_a_platform_job_is_not_rewritten():
    engine = BrowserEngine.__new__(BrowserEngine)
    engine.job = {"id": "j", "url": POSTING, "ats": "workday"}
    engine.page = SimpleNamespace(url=POSTING)
    assert await engine.detect_ats(engine.page, "workday") == "workday"
    assert not hasattr(engine, "handoff_origin")


async def test_embedded_workday_link_adopts_the_observed_employer_tenant(monkeypatch):
    from unittest.mock import AsyncMock
    import jobpilot.browser as browser

    engine = BrowserEngine.__new__(BrowserEngine)
    engine.job = {'id': 'j', 'url': 'https://careers.example.test/engineer', 'ats': 'custom'}
    original = engine.job
    page = SimpleNamespace(url=engine.job['url'], wait_for_load_state=AsyncMock(), evaluate=AsyncMock(return_value=[]))
    async def goto(url, **kwargs):
        page.url = POSTING
    page.goto = AsyncMock(side_effect=goto)
    engine.page = page
    engine.emit = lambda *a, **k: None
    monkeypatch.setattr(browser, 'detect_embedded', lambda _: ('workday', POSTING))
    assert await engine.detect_ats(page, 'custom') == 'workday'
    assert engine.job is original
    assert engine.job['url'] == POSTING
    assert engine.handoff_origin == 'https://amat.wd1.myworkdayjobs.com'
