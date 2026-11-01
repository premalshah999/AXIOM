"""Detection runs again when the page changes (2026-10-31).

It ran once, before the first click: Roblox's Greenhouse frame exists only on the form step, Abnormal's, Databricks' and
Lyft's CareerPuck frames were added after the load event, Two Sigma's Avature portal and AARP's Eightfold site never
showed a platform URL, and PwC's Phenom pages returned "phenom" from a script and kept that label without ever following
their Apply link. These pin each step with a synthetic page."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import Error as PlaywrightError

import jobpilot.browser as browser
from jobpilot.adapters.base import Adapter
from jobpilot.browser import BrowserEngine

FRAME = "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=77"
SHORT = "https://go.example.test/AbC"


def engine(url=SHORT, ats="custom", **extra):
    e = BrowserEngine.__new__(BrowserEngine)
    e.job = {"id": "j", "url": url, "ats": ats}
    e.emitted = []
    e.emit = lambda kind, message, data=None: e.emitted.append((kind, data))
    e.armed = False
    e.__dict__.update(extra)
    return e


def page_showing(*looks, url="https://careers.example.test/jobs/1"):
    """A page whose successive looks (EMBED_JS results) are `looks`; goto moves it."""
    page = SimpleNamespace(url=url, wait_for_load_state=AsyncMock())
    page.evaluate = AsyncMock(side_effect=list(looks) + [looks[-1] if looks else []] * 20)

    async def goto(target, **kwargs):
        page.url = target

    page.goto = AsyncMock(side_effect=goto)
    return page


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(BrowserEngine, "embed_poll", 0)


# ----- the first look ---------------------------------------------------------------------------------------------


async def test_a_greenhouse_holder_waits_for_the_frame_added_after_the_load_event():
    e = engine()
    holder = [["dom", "grnhse_app"], ["script", "https://boards.greenhouse.io/embed/job_board/js?for=acme"]]
    page = page_showing(holder, holder, [*holder, ["frame", FRAME]])
    e.page = page
    assert await e.detect_ats(page, "custom") == "greenhouse"
    page.goto.assert_awaited_once()
    assert page.url == FRAME and e.job == {"id": "j", "url": FRAME, "ats": "greenhouse"}


async def test_a_holder_whose_frame_never_comes_keeps_the_pages_label_without_moving():
    e = engine()
    holder = [["dom", "grnhse_app"]]
    page = page_showing(holder)
    e.page = page
    assert await e.detect_ats(page, "custom") == "greenhouse"
    page.goto.assert_not_awaited()


async def test_a_phenom_script_is_not_an_application_platform():
    e = engine()
    page = page_showing([["script", "https://cdn.phenompeople.com/phenom.js"], ["meta", "use-widget=ph-job-detail-v1"]])
    e.page = page
    assert await e.detect_ats(page, "custom") == "custom"
    assert e.job["ats"] == "custom"


async def test_phenoms_real_apply_link_is_followed():
    e = engine()
    apply = "https://acme.wd3.myworkdayjobs.com/en-US/External/job/NY/Engineer_R1/apply"
    page = page_showing([["script", "https://cdn.phenompeople.com/phenom.js"], ["apply", apply]])
    e.page = page
    assert await e.detect_ats(page, "custom") == "workday"
    assert e.job["ats"] == "workday" and e.job["url"].endswith("/Engineer_R1")


async def test_an_avature_portal_without_a_locale_path_is_read_from_its_meta():
    # Two Sigma: go.vansh.nyc -> careers.twosigma.com/careers/JobDetail/...
    portal = "https://careers.example.test/careers/JobDetail/Engineer/1"
    e = engine()
    page = page_showing([["meta", "avature.portal.id=106"], ["meta", "avature.portal.page=JobDetail"]], url=portal)
    e.page = page
    assert await e.detect_ats(page, "custom") == "avature"
    assert e.job == {"id": "j", "url": portal, "ats": "avature"}  # the portal, not the short link, owns accounts and mail
    assert ("handoff", {"found": "avature", "via": "meta"}) in e.emitted


async def test_an_eightfold_site_on_its_own_domain_is_read_from_its_stylesheet():
    # AARP: apply.aarp.org shows eightfold.ai nowhere; its code mail was awaited from aarp.org, not eightfold.ai.
    e = engine("https://apply.example.test/careers/job/1")
    page = page_showing([["link", "https://static.vscdn.net/fonts/css/eightfold-font-base.css"]], url=e.job["url"])
    e.page = page
    assert await e.detect_ats(page, "custom") == "eightfold"
    assert e.job["ats"] == "eightfold"


async def test_a_named_platform_with_a_label_only_is_found_without_moving():
    e = engine("https://careers.example.test/jobs/1")
    page = page_showing([["dom", "comeet-jsapi"]])
    e.page = page
    assert await e.detect_ats(page, "custom") == "comeet"
    page.goto.assert_not_awaited()
    assert e.job["url"] == "https://careers.example.test/jobs/1"


async def test_a_careerpuck_job_still_looks_for_the_greenhouse_form_it_holds():
    e = engine("https://app.example.test/job-board/acme/job/1", ats="careerpuck")
    page = page_showing([["dom", "grnhse_app"], ["frame", FRAME]], url="https://app.careerpuck.com/job-board/acme/job/1")
    e.page = page
    assert await e.detect_ats(page, "careerpuck") == "greenhouse"


async def test_a_job_with_a_real_platform_label_is_not_looked_at_again():
    e = engine(ats="ashby")
    page = page_showing([["dom", "grnhse_app"]])
    e.page = page
    assert await e.detect_ats(page, "ashby") == "ashby"
    page.evaluate.assert_not_awaited()


# ----- after the run has started ----------------------------------------------------------------------------------


async def test_a_frame_that_appears_on_the_form_step_is_followed():
    # Roblox: the Greenhouse iframe exists only after Apply.
    e = engine("https://careers.example.test/jobs/1")
    e.form = SimpleNamespace(ledger={}, uploaded=False)
    page = page_showing([["frame", FRAME], ["dom", "grnhse_iframe"]])
    e.page = page
    assert await e.follow_embedded("custom") == "greenhouse"
    page.goto.assert_awaited_once()
    assert e.job["ats"] == "greenhouse" and e.job["url"] == FRAME


async def test_work_already_in_the_employers_frame_is_not_thrown_away():
    e = engine("https://careers.example.test/jobs/1")
    e.form = SimpleNamespace(ledger={"f1": {"value": "x"}}, uploaded=False)
    page = page_showing([["frame", FRAME]])
    e.page = page
    assert await e.follow_embedded("custom") == "greenhouse"
    page.goto.assert_not_awaited()
    assert e.job["ats"] == "greenhouse"
    assert any(kind == "handoff_skipped" for kind, _ in e.emitted)


async def test_a_failed_move_says_so_and_stops_trying_after_three():
    e = engine("https://careers.example.test/jobs/1")
    e.form = SimpleNamespace(ledger={}, uploaded=False)
    page = page_showing([["frame", FRAME]])
    page.goto = AsyncMock(side_effect=PlaywrightError("net::ERR_ABORTED"))
    e.page = page
    for _ in range(5):
        assert await e.follow_embedded("custom") is None
    assert page.goto.await_count == 3
    assert [kind for kind, _ in e.emitted].count("handoff_skipped") == 3


async def test_a_redirect_or_new_tab_already_on_a_platform_is_followed_by_its_address():
    e = engine("https://careers.example.test/jobs/1")
    e.form = SimpleNamespace(ledger={}, uploaded=False)
    page = page_showing([], url="https://job-boards.greenhouse.io/acme/jobs/77")
    e.page = page
    assert await e.follow_embedded("custom") == "greenhouse"
    page.evaluate.assert_not_awaited()


@pytest.mark.parametrize("label", ["workday", "greenhouse", "ashby"])
async def test_a_run_on_a_real_platform_is_never_moved(label):
    e = engine(ats=label)
    page = page_showing([["frame", FRAME]])
    e.page = page
    assert await e.follow_embedded(label) is None
    page.evaluate.assert_not_awaited()


async def test_nothing_moves_after_the_commit():
    e = engine(armed=True)
    page = page_showing([["frame", FRAME]])
    e.page = page
    assert await e.follow_embedded("custom") is None


async def test_a_named_platform_does_not_flip_to_another_named_platform():
    e = engine("https://acme.pinpointhq.com/postings/1", ats="pinpoint")
    e.form = SimpleNamespace(ledger={}, uploaded=False)
    page = page_showing([["dom", "comeet-jsapi"]], url=e.job["url"])
    e.page = page
    assert await e.follow_embedded("pinpoint") is None


async def test_the_adapter_hands_the_run_over_each_step():
    # The hook sits before the page is classified, so the generic driver never works a frame it should hand over.
    obs = {"url": "https://careers.example.test/jobs/1", "text": "", "headings": [], "errors": [], "fields": [],
           "controls": [], "busy": False, "dialog": False}
    adapter = object.__new__(Adapter)
    follow = AsyncMock(return_value="greenhouse")
    adapter.e = SimpleNamespace(
        result=None, form=SimpleNamespace(settle=AsyncMock(), resume_gate=AsyncMock(return_value=False), scan=AsyncMock(return_value=obs)),
        refresh_expired_entry=AsyncMock(return_value=False), follow_embedded=follow,
    )
    adapter.id, adapter.job, adapter.committed = "custom", {"url": obs["url"], "id": "j"}, False
    adapter.reset_mail_continuation = lambda: False
    adapter.dismiss_overlays = AsyncMock()
    adapter.captcha_open = AsyncMock(return_value=False)
    adapter.classify_page = lambda *a: pytest.fail("classified a page it should have handed over")
    assert await adapter._drive() == {"handoff": "greenhouse"}
    follow.assert_awaited_once_with("custom")
