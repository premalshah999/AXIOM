"""A career site that hands off to its platform changes the run's job identity; the verifier follows (2026-11-01).

Databricks 10eda520 and OneImaging 9e113532 / 94ceb954 ran on Greenhouse while the verifier still held the career site's
custom rule (no Greenhouse sender). Synthetic data only."""

from types import SimpleNamespace

from jobpilot.email_browser import EmailBrowser
from support_mail import seed

CAREERS = "https://careers.example.test/jobs/42"
BOARD = "https://boards.greenhouse.io/acme/jobs/42"


def engine_for(service, job):
    return SimpleNamespace(
        service=service,
        job=job,
        run_record={"packet": {"profile": {"email": "alex@example.test"}}},
        form=SimpleNamespace(resolver=SimpleNamespace(profile=SimpleNamespace(email="alex@example.test"))),
        run_id="r1",
    )


async def test_handoff_rebinds_the_verifier_to_the_platforms_tenant_rule(service):
    seed(service, "https://careers.example.test")
    await service.mail.ensure_rule({"url": CAREERS, "ats": "custom", "id": "j"}, "alex@example.test")
    job = {"id": "j", "url": CAREERS, "ats": "custom", "demo": False}
    verifier = EmailBrowser(engine_for(service, job))
    assert verifier.rule["employer_origin"] == "https://careers.example.test"
    job.update(url=BOARD, ats="greenhouse")  # what adopt_handoff does
    verifier.rebind()  # adopt_handoff calls this
    await verifier.bind_rule()
    assert verifier.rule["employer_origin"].startswith("https://boards.greenhouse.io")
    assert any("greenhouse" in d for d in verifier.rule["sender_domains"])
    assert verifier.rule["link_origins"] == ["https://boards.greenhouse.io"]


async def test_an_open_request_keeps_its_rule_through_a_handoff(service):
    seed(service, "https://careers.example.test")
    rule = await service.mail.ensure_rule({"url": CAREERS, "ats": "custom", "id": "j"}, "alex@example.test")
    job = {"id": "j", "url": CAREERS, "ats": "custom", "demo": False}
    verifier = EmailBrowser(engine_for(service, job))
    verifier.challenge_id = "open"
    job.update(url=BOARD, ats="greenhouse")
    await verifier.bind_rule()
    assert verifier.rule["id"] == rule["id"]
