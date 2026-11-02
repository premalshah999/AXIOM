"""What a new install can and cannot match when the sender is one nobody listed (2026-11-01).

Every platform's real link and reset format (tests/mail_formats.py), from an empty database, with the sender replaced by a mailer
whose domain names neither the employer nor the platform. A real socket speaks Gmail's IMAP (tests/fake_imap.py). The mail
links to the employer's own origin on its platform's real host. It is matched where that host belongs to one employer (a
tenant's own host or the employer's own career site) and left alone where many employers share it: a link to a shared host
says nothing about whose mail it is, so those wait for the sender to be named by the employer's name or the platform."""

import time

import pytest

from jobpilot.mail.polling import employer_only_host
from mail_formats import ME, PLATFORMS, deliver
from test_mail_fresh_install import WINDOW, imap, link_for, onboard, start_run  # noqa: F401  (imap is a fixture)

UNFAMILIAR = "Careers Desk <no-reply@mailer.hr-services.example>"
CASES = [(p, k) for p in PLATFORMS for k in ("link", "reset")]


@pytest.mark.parametrize("platform,kind", CASES)
async def test_a_link_from_an_unfamiliar_sender_is_matched_only_on_a_host_one_employer_owns(client, service, imap, platform, kind):
    service.config.mail_poll_seconds = 0.1
    spec = PLATFORMS[platform]
    onboard(client)
    job, run = start_run(service, spec["url"], spec["ats"], spec["company"])
    challenge = await service.inbox.arm(run["id"], job, ME, WINDOW[kind])
    assert challenge, "no window opened: this employer has no sender rule"
    time.sleep(1.1)
    link = link_for(spec, kind)
    deliver(imap, platform, kind, link=link, from_=UNFAMILIAR)
    if employer_only_host(spec["ats"], spec["url"]) and platform != "icims":
        token = await service.inbox.wait(challenge, max_wait_seconds=4)
        assert token and token["value"] == link
    else:
        with pytest.raises(Exception):
            await service.inbox.wait(challenge, max_wait_seconds=3)
