"""Each registration (Create) mails the employer's passcode. An unconfirmed account pressed Create 6, 4 and 4 times on
three days (20+ passcode emails, 2026-10-27..29); reset requests had a cooldown (38efd0c), registrations had none."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from jobpilot.accounts import AccountFlow
from jobpilot.adapters.base import (
    CREATE_SENT_LIMIT,
    CREATE_SENT_WINDOW,
    create_allowed,
    create_limit_result,
    create_stamp,
)
from jobpilot.application_budget import nothing_sent

URL = "https://career5.successfactors.eu/career?company=ExampleCorp&lang=en_US"


def sent(*ages, run="other"):
    # Each entry another run's, unless a test names the run: one run's repeats count once.
    return {"create_sent": [[time.time() - age, f"{run}-{n}" if run == "other" else run] for n, age in enumerate(ages)]}


def test_two_registrations_from_other_runs_in_a_day_stop_a_third():
    assert create_allowed({}, "now") and create_allowed(sent(600), "now")
    assert not create_allowed(sent(600, 7200), "now")
    assert CREATE_SENT_LIMIT == 2


def test_old_registrations_a_confirmed_account_and_the_same_run_do_not_count():
    assert create_allowed(sent(600, CREATE_SENT_WINDOW + 60), "now")
    assert create_allowed({**sent(600, 7200), "confirmed_at": "2026-10-29T00:00:00+00:00"}, "now")
    # A retry inside one run (password rules, a form that came back) is the same registration attempt.
    assert create_allowed(sent(60, 120, run="now"), "now")


def test_the_stamp_keeps_the_newest_four_inside_the_window():
    record = sent(5, 10, 20, 30, CREATE_SENT_WINDOW + 100)
    stamp = create_stamp(record, "now")
    assert len(stamp) == 4 and stamp[-1][1] == "now" and all(time.time() - t < CREATE_SENT_WINDOW for t, _ in stamp)


def test_a_refused_registration_sent_nothing_so_it_is_not_a_spent_start():
    result = create_limit_result()
    assert result["state"] == "waiting_browser" and not result["recoverable"]
    assert nothing_sent(result["state"], result["reason"])


class Handle:
    def __init__(self):
        self.value = "x"

    async def fill(self, value, **kwargs):
        self.value = value

    async def evaluate(self, *args):
        return True

    async def press_sequentially(self, *args, **kwargs):
        pass


class Locator:
    async def evaluate(self, *args):
        return False  # not an embedded application form

    async def element_handle(self):
        return Handle()


class Form:
    def __init__(self):
        frame = SimpleNamespace(url=URL)
        self.obs = {
            "fields": [
                {"id": "email", "type": "text", "label": "Email Address:"},
                {"id": "pw0", "type": "password", "label": "Choose Password"},
                {"id": "pw1", "type": "password", "label": "Retype Password"},
            ],
            "controls": [{"id": "create", "label": "Create Account"}],
            "errors": [], "text": "", "headings": [], "dialogs": [],
        }
        self.frames = {item["id"]: frame for item in [*self.obs["fields"], *self.obs["controls"]]}
        self.resolver = SimpleNamespace(
            profile=SimpleNamespace(allow_account_creation=True, accept_all_application_terms=False, email="alex@example.test")
        )
        self.page = SimpleNamespace(url=URL)
        self.clicked = []
        self.fill_current = AsyncMock(return_value={"pending": [], "ok": True, "problems": []})

    async def scan(self):
        return self.obs

    def locator(self, _id):
        return Locator()

    async def click(self, control_id, auth_control=False):
        self.clicked.append(control_id)

    async def settle(self, timeout=0):
        pass


def flow_for(service, run_id="run-now"):
    form = Form()
    engine = SimpleNamespace(
        service=service, form=form, job={"url": URL}, run_id=run_id, emit=lambda *a, **k: None, result=None,
        email_verification=SimpleNamespace(prepare_submission=AsyncMock()), page=form.page,
        browser_challenge=AsyncMock(return_value=None),
    )
    return AccountFlow(engine), engine, form


async def test_a_third_registration_in_a_day_is_not_sent(service):
    flow, engine, form = flow_for(service)
    flow.store.save(URL, flow.store.shared(), "created_locally", **sent(600, 7200))
    assert await flow.handle() is True
    assert form.clicked == [] and engine.result["state"] == "waiting_browser"
    assert nothing_sent(engine.result["state"], engine.result["reason"])
    assert len(flow.store.record(URL)["create_sent"]) == 2  # nothing was stamped for the refused one


async def test_a_registration_within_the_limit_is_stamped_before_it_is_sent(service):
    flow, engine, form = flow_for(service)
    flow.store.save(URL, flow.store.shared(), "created_locally", **sent(600))
    try:
        await flow.handle()
    except (AttributeError, TypeError, KeyError):
        pass  # the fake page ends inside the account action, past the point that matters here
    assert form.clicked == ["create"]
    stamps = flow.store.record(URL)["create_sent"]
    assert len(stamps) == 2 and stamps[-1][1] == "run-now"


async def test_a_press_that_sends_nothing_is_told_from_one_that_sends(page):
    """Atlas Copco (SuccessFactors, 2026-11-01): an inert Submit was counted as a registration sent, and a day's wait followed."""
    from jobpilot.adapters.base import SiteWrites

    async def answer(route):
        await route.fulfill(content_type="text/html", body="""<button id=inert>Submit</button>
        <button id=sends onclick="fetch('/register',{method:'POST'})">Submit</button>""" if route.request.method == "GET" else "ok")

    await page.route("https://careers.example.test/**", answer)
    await page.goto("https://careers.example.test/join")
    with SiteWrites(page) as writes:
        await page.click("#inert")
        await page.wait_for_timeout(100)
    assert not writes.seen
    with SiteWrites(page) as writes:
        await page.click("#sends")
        await page.wait_for_timeout(100)
    assert writes.seen


def press_sees(url, page="https://careers.example.test/join", method="POST", kind="xhr"):
    from jobpilot.adapters.base import SiteWrites

    writes = SiteWrites(SimpleNamespace(url=page))
    writes.note(SimpleNamespace(url=url, method=method, resource_type=kind))
    return writes.seen


def test_a_registration_posted_to_the_platform_behind_the_page_is_sent():
    """Sign-up forms post to the platform's host, or sit in its frame. Read as "the page sent nothing", the stamp came off and
    each run mailed another passcode with no daily limit (the limit's own safe side is "counts as sent")."""
    assert press_sees("https://careers-example.platform.test/register")
    assert press_sees("https://careers.example.test/register")
    assert press_sees("https://other.example.test/join", kind="document")


def test_a_pixel_on_another_host_and_a_read_are_not_a_registration():
    assert not press_sees("https://px.ads.test/collect")
    assert not press_sees("https://www.google-analytics.com/g/collect")
    assert not press_sees("https://careers-example.platform.test/register", method="GET")


async def test_a_busy_mail_sender_does_not_spend_a_registration(service):
    """The stamp was written before the mail lease: two busy-sender attempts used up the day's allowance unsent."""
    flow, engine, form = flow_for(service)
    flow.store.save(URL, flow.store.shared(), "created_locally", **sent(600))
    engine.email_verification.prepare_submission = AsyncMock(side_effect=ValueError("Another application is verifying with this sender"))
    try:
        await flow.handle()
    except ValueError:
        pass
    assert form.clicked == []
    assert len(flow.store.record(URL)["create_sent"]) == 1  # only the other run's


async def test_an_inert_create_press_does_not_spend_a_registration(service):
    flow, engine, form = flow_for(service)
    form.page = engine.page = SimpleNamespace(url=URL, on=lambda *a: None, remove_listener=lambda *a: None)  # nothing is posted
    flow.store.save(URL, flow.store.shared(), "created_locally", **sent(600))
    try:
        await flow.handle()
    except (AttributeError, TypeError, KeyError):
        pass
    assert form.clicked == ["create"]
    assert len(flow.store.record(URL)["create_sent"]) == 1


async def test_a_create_press_that_posts_is_still_counted(service):
    flow, engine, form = flow_for(service)
    handlers = []
    form.page = engine.page = SimpleNamespace(url=URL, on=lambda name, fn: handlers.append(fn), remove_listener=lambda *a: None)

    async def click(control_id, auth_control=False):
        form.clicked.append(control_id)
        for fn in handlers:
            fn(SimpleNamespace(url="https://careers.example.test/register", method="POST", resource_type="xhr"))

    form.click = click
    flow.store.save(URL, flow.store.shared(), "created_locally", **sent(600))
    try:
        await flow.handle()
    except (AttributeError, TypeError, KeyError):
        pass
    stamps = flow.store.record(URL)["create_sent"]
    assert len(stamps) == 2 and stamps[-1][1] == "run-now"
