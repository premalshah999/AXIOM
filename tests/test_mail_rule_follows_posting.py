"""A job entered through a short link has its mail rule on the posting the link opens (2026-10-29).

Interdigital 4c9405de came in through go.vansh.nyc/HD: the rule was keyed on go.vansh.nyc, its link origin was go.vansh.nyc,
and the Workday reset it asked for (a link on interdigital.wd5.myworkdayjobs.com) was refused on every poll as a foreign
origin. EA's reset used the short-link host's sender list, which was another employer's. Synthetic data only."""

import time

from jobpilot import shortlinks
from jobpilot.db import Setting
from jobpilot.mail import RuleInput, extract_message
from support_mail import seed

SHORT = "https://go.vansh.nyc/HD"
POSTING = "https://acme.wd5.myworkdayjobs.com/AcmeCareers/job/Remote/Engineer_R1"


def remember(service, final=POSTING):
    with service.db.exclusive() as s:
        s.add(Setting(key=shortlinks.cache_key(SHORT), value={"url": SHORT, "final": final, "at": "2026-10-29T00:00:00+00:00"}))


async def test_the_rule_is_the_postings_not_the_short_links(service):
    box, _ = seed(service)
    remember(service)
    rule = await service.mail.ensure_rule({"url": SHORT, "ats": "workday", "id": "j"}, "alex@example.test")
    assert rule["employer_origin"] == "https://acme.wd5.myworkdayjobs.com"
    assert rule["link_origins"] == ["https://acme.wd5.myworkdayjobs.com"]
    assert "myworkday.com" in rule["sender_domains"] and "vansh.nyc" not in rule["sender_domains"]
    assert service.mail.rule_for(SHORT)["id"] == rule["id"]  # the short link finds the posting's rule
    assert service.mail.rule_for(POSTING)["id"] == rule["id"]


async def test_the_reset_link_on_the_postings_origin_is_accepted(service):
    from test_mail_sender_gates import challenge, msg

    seed(service)
    remember(service)
    rule = await service.mail.ensure_rule({"url": SHORT, "ats": "workday", "id": "j"}, "alex@example.test")
    link = "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers/passwordreset/abc123def456"
    mail = msg("acme@myworkday.com", f"Reset your password: {link}", "Reset your password for your candidate account")
    assert extract_message(mail, rule, challenge("password_reset", ["acme"], ats="workday")) == {"kind": "link", "value": link}
    other = msg("acme@myworkday.com", "Reset your password: https://other.wd5.myworkdayjobs.com/x/passwordreset/abc123def456",
                "Reset your password for your candidate account")
    assert extract_message(other, rule, challenge("password_reset", ["acme"], ats="workday")) is None


async def test_an_unresolved_short_link_keeps_its_own_origin(service):
    seed(service)
    assert service.mail.posting_url(SHORT) == SHORT  # nothing saved: no network, no guess
    with service.db.exclusive() as s:
        s.add(Setting(key=shortlinks.cache_key(SHORT), value={"url": SHORT, "final": None, "retry_at": "2999-01-01T00:00:00+00:00"}))
    assert service.mail.posting_url(SHORT) == SHORT


async def test_an_ordinary_url_is_never_rewritten(service):
    seed(service)
    assert service.mail.posting_url(POSTING) == POSTING and service.mail.posting_url("") == ""
