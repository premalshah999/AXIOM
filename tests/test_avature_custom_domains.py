"""Avature on employers' own domains (2026-10-28): EA, Lenovo, MetLife and Bain read as custom career sites, so their
reset mail from <tenant>.avature.net was never trusted and seven resets "did not arrive". Avature is recognised by its
portal paths on any domain, and a saved rule gains the platform's senders when a run finds it."""

import pytest

from jobpilot.ats import detect


@pytest.mark.parametrize("url", [
    "https://jobs.lenovo.com/en_US/careers/JobDetail?jobId=82510&src=LinkedIn",
    "https://jobs.ea.com/en_US/careers/JobDetail/Software-Engineer-I/216357",
    "https://jobs.lenovo.com/en_US/careers/Register?jobId=82510",
    "https://jobs.ea.com/en_US/careers/Login?jobId=1",
    "https://www.metlifecareers.com/en_US/ml/ApplicationMethods?formValues=&jobId=204",
    "https://careers.bain.com/jobs/FolderDetail?folderId=108806",
    "https://intuit.avature.net/careers/Login",
])
def test_avature_portal_paths_are_avature_on_any_domain(url):
    assert detect(url).id == "avature"


@pytest.mark.parametrize("url", [
    "https://jobs.intuit.com/job/mountain-view/software-engineer-1/27595/102012325536",
    "https://careers.example.test/jobs/12345/software-engineer",
    "https://job-boards.greenhouse.io/acme/jobs/1",
    "https://example.test/en_US/blog/JobDetailsExplained",
])
def test_other_career_sites_are_not_avature(url):
    assert detect(url).id != "avature"


async def test_a_saved_own_site_rule_gains_avatures_senders_through_the_runs_lookup(service):
    from support_mail import seed

    seed(service, employer="https://jobs.acme-corp.example.test")
    url = "https://jobs.acme-corp.example.test/en_US/careers/Login?jobId=1"
    own = await service.mail.ensure_rule({"url": url, "ats": "custom"}, "alex@example.test")
    assert own["sender_domains"] == ["example.test"]
    # The run found Avature behind the site: its reset lookup (Inbox.rule) upgrades the saved rule.
    from jobpilot.inbox import Inbox

    rule = await Inbox(service).rule({"url": url, "ats": "avature"}, "alex@example.test")
    assert rule["id"] == own["id"] and "avature.net" in rule["sender_domains"]
