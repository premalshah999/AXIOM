"""Dell's Oracle job link (iawmqy.fa.ocs.oraclecloud.com) redirects to enterpriseplatform.dell.com, where the code box
is. Six runs refused to type their code there ("Code field on an unexpected website") and Oracle locked verification
(2026-10-23). The host the job link itself landed on is the employer's; any other host is still refused."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jobpilot.adapters.base import Adapter

JOB = "https://iawmqy.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/careers/job/299245"
LANDED = "https://enterpriseplatform.dell.com/hcmUI/CandidateExperience/en/sites/careers/job/299245"


class Probe(Adapter):
    mail_rule_known = True
    inbox = SimpleNamespace(configured=True, live=lambda _c: True)


def adapter(service, code_frame):
    value = Probe.__new__(Probe)
    fill = AsyncMock()
    obs = {"fields": [{"id": "c", "type": "text", "label": "Verification code", "maxlength": 6}], "controls": [],
           "text": "Enter the code we sent", "url": code_frame}
    form = SimpleNamespace(scan=AsyncMock(return_value=obs), frames={"c": SimpleNamespace(url=code_frame)},
                           locator=lambda _id: SimpleNamespace(fill=fill), page=SimpleNamespace(url=code_frame))
    value.e = SimpleNamespace(form=form, landing_url=LANDED, emit=lambda *a, **k: None)
    value.service, value.config, value.id = service, service.config, "oracle"
    value.job = {"id": "dell", "url": JOB, "ats": "oracle"}
    value.challenge = None
    value.attempts = __import__("collections").Counter()
    value.wait_email = AsyncMock(return_value={"kind": "code", "value": "123456"})
    value.finish_mail = MagicMock()
    value.defer = lambda reason, *a, **k: {"reason": reason}
    value.accounts = SimpleNamespace(password_allowed=lambda *a: False)
    return value, fill


async def test_the_code_goes_into_the_host_the_job_link_landed_on(service):
    value, fill = adapter(service, LANDED + "/apply/email")
    try:
        await Adapter.on_email_code(value, await value.e.form.scan())
    except Exception:
        pass  # Past the host check; the rest of the flow is not modelled here.
    fill.assert_awaited_with("123456", timeout=4000)


async def test_a_code_box_on_any_other_host_is_refused(service):
    value, fill = adapter(service, "https://evil.example.test/verify")
    result = await Adapter.on_email_code(value, await value.e.form.scan())
    assert "unexpected website" in result["reason"]
    fill.assert_not_awaited()


async def test_the_landing_host_over_plain_http_is_refused(service):
    value, fill = adapter(service, LANDED.replace("https://", "http://") + "/apply/email")
    result = await Adapter.on_email_code(value, await value.e.form.scan())
    assert "unexpected website" in result["reason"]
    fill.assert_not_awaited()
