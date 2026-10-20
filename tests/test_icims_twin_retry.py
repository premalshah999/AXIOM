"""A twin-minted token is sometimes still scored low and iCIMS answers the email step with its returning-candidate
Central login (1 of 6 on 2026-10-19). Central cannot create accounts, so for an employer with no known account the
email step is retried from the job page once per run, with the solver's memory of the old widget cleared."""

import time
from collections import Counter
from types import SimpleNamespace

from jobpilot.adapters.icims import ICIMS

JOB = "https://campus-americas.icims.com/jobs/25999/machine-learning-engineer/job"
CENTRAL = {"url": "https://login.icims.com/u/login/identifier?state=fixture", "fields": [], "controls": []}


def adapter(*, delivered=True, known=False):
    visits = []

    class Page:
        async def goto(self, url, **kwargs):
            visits.append(url)

    async def settle(timeout):
        return None

    captcha = SimpleNamespace(
        twin_delivered_at=time.monotonic() if delivered else None,
        twin_tried={("old",)},
        token_delivered_at={("old",): 1.0},
    )
    a = ICIMS.__new__(ICIMS)
    a.e = SimpleNamespace(captcha=captcha, form=SimpleNamespace(page=Page(), settle=settle), emit=lambda *x: None)
    a.job, a.attempts = {"url": JOB}, Counter()
    a.accounts = SimpleNamespace(store=SimpleNamespace(known=lambda url: known))
    a.auth_url = lambda obs: obs["url"]
    return a, visits, captcha


async def test_central_after_a_twin_token_retries_the_email_step_once():
    a, visits, captcha = adapter()
    assert await a.retry_email_step(CENTRAL)
    assert visits == [ICIMS.prepare_url(JOB)] and "in_iframe=1" in visits[0]
    assert not captcha.twin_tried and not captcha.token_delivered_at and captcha.twin_delivered_at is None
    captcha.twin_delivered_at = time.monotonic()
    assert not await a.retry_email_step(CENTRAL)
    assert len(visits) == 1


async def test_no_retry_for_a_known_account_or_without_a_twin_token():
    for kwargs in ({"known": True}, {"delivered": False}):
        a, visits, _ = adapter(**kwargs)
        assert not await a.retry_email_step(CENTRAL)
        assert not visits
