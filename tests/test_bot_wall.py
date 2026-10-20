"""SmartRecruiters 2026-10-18: DataDome's "Verification Required" slider walled every tenant for this browser, and twelve
jobs re-ran two to seven times each. The wall now pauses the platform, other platforms continue, and the stopped run
is not one of the job's starts."""

from types import SimpleNamespace

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.application_budget import nothing_sent
from jobpilot.application_pacing import ats_limit
from jobpilot.bot_wall import COOLDOWN_SECONDS, datadome_kind, pause_platform
from jobpilot.browser import BrowserEngine
from jobpilot.db import Job

SLIDER = "https://geo.captcha-delivery.com/captcha/?initialCid=AHrlqA&cid=x&referer=https%3A%2F%2Fjobs.smartrecruiters.com"
DEVICE_CHECK = "https://geo.captcha-delivery.com/interstitial/?initialCid=AHrlqA&cid=x"
HARD_BLOCK = "https://geo.captcha-delivery.com/captcha/?initialCid=AHrlqA&t=bv&cid=x"
FORM = "https://jobs.smartrecruiters.com/oneclick-ui/company/AbbVie/publication/abc"


def test_datadome_frames_are_named():
    assert datadome_kind([FORM, SLIDER]) == "captcha"
    assert datadome_kind([FORM, DEVICE_CHECK]) == "interstitial"
    assert datadome_kind([HARD_BLOCK]) == "blocked"
    assert datadome_kind([FORM, "https://www.google.com/recaptcha/api2/anchor"]) is None
    assert datadome_kind(["https://captcha-delivery.com.example.net/captcha/"]) is None


def test_the_wall_pauses_the_platform_and_costs_no_start(service):
    result = pause_platform(service.db, "smartrecruiters", "captcha")
    assert result["site_cooldown_seconds"] == COOLDOWN_SECONDS and result["recovery_cause"] == "site_temporary"
    assert nothing_sent(result["state"], result["reason"])
    with service.db.exclusive() as s:
        s.add(Job(id="abbvie", identity="abbvie", url=FORM, ats="smartrecruiters", company="AbbVie"))
        s.add(Job(id="ford", identity="ford", url="https://efds.fa.em5.oraclecloud.com/x", ats="oracle", company="Ford"))
    with service.db.session() as s:
        with pytest.raises(QueueBlocked) as blocked:
            service.check_cooldown(s, s.get(Job, "abbvie"))
        assert blocked.value.outcome == "deferred" and blocked.value.code == "platform_bot_check"
        assert "bot check" in str(blocked.value)
        service.check_cooldown(s, s.get(Job, "ford"))  # Other platforms continue.


def test_smartrecruiters_runs_two_at_a_time(service):
    with service.db.session() as s:
        assert ats_limit(s, "smartrecruiters") == 2
        assert ats_limit(s, "successfactors") == 1
        assert ats_limit(s, "icims") == 1


def frame(url):
    return SimpleNamespace(url=url)


def engine(service, urls, armed=False):
    e = object.__new__(BrowserEngine)
    e.db, e.armed, e.job = service.db, armed, {"ats": "smartrecruiters"}
    e.page = SimpleNamespace(frames=[frame(u) for u in urls])
    e.emitted = []
    e.emit = lambda kind, message, data=None: e.emitted.append(kind)
    return e


async def test_the_navigator_stops_on_the_wall_before_submission(service):
    e = engine(service, [FORM, SLIDER])
    result = await e.platform_wall()
    assert result["state"] == "waiting_browser" and e.emitted == ["bot_wall"]
    assert service.db.get_setting("ats_outage:smartrecruiters", {}).get("code") == "platform_bot_check"
    # After the submit click the outcome is unknown; receipt reconciliation owns it.
    assert await engine(service, [FORM, SLIDER], armed=True).platform_wall() is None
    assert await engine(service, [FORM]).platform_wall() is None


def test_a_tab_that_never_opened_costs_no_start():
    # Michelin and HeyGen 2026-10-19: the worker Chrome could not attach a tab; nothing reached the employer.
    from jobpilot.repair_agent import eligible

    reason = ("Chrome worker tab initialization failed at attach_target (TimeoutError). "
              "No employer form was opened.")
    assert nothing_sent("timed_out", reason)
    assert not eligible("timed_out", reason)
