"""ADP, 2026-10-29 (ExampleSoft stands for the employer): "Candidate Source" listed "<Employer> Website", "Job Board" and
"Social Media". The saved profile pre-filled "Social Media", whose required follow-up "Social Media Source" offered only
Facebook/Instagram/Meta, so the run looped on "Value not accepted" for 30 decisions. The owner's list says LinkedIn, then
the company website, ... social media last, and the same form offered "Job Board", whose follow-up offers LinkedIn."""

import pytest

from jobpilot.answer_rules import AnswerRules, employer_names, source_patterns
from jobpilot.answers import Resolver, source_choice
from jobpilot.answers.sources import follow_up_channel, same_channel
from jobpilot.knowledge import topic
from jobpilot.schemas import Profile

LIST = {
    "source_order": [
        "linkedin", "company website", "ziprecruiter", "indeed", "internet search", "job board", "glassdoor",
        "social media",
    ],
    "source_any_channel": True,
}
PARENT = [
    "-", "ExampleSoft Website", "Current Employee Referral", "Friend/Family Referral", "Job Board",
    "ExampleSoft Billboard", "ExampleSoft TV Commercial", "ExampleSoft Email", "Social Media", "Career Fair",
    "College/University",
]
JOB_BOARDS = ["-", "Indeed", "Glassdoor", "LinkedIn", "Job Service", "Handshake", "Other"]
SOCIAL = ["-", "Facebook/Instagram/Meta"]


def question(label, options, type="combobox", **extra):
    return {"id": "q", "label": label, "type": type, "required": True, "options": options, "value": "", "valid": True, **extra}


def resolver(service, employer="ExampleSoft Inc", rules=LIST, source="LinkedIn"):
    return Resolver(Profile(application_source=source, answer_rules=rules), service.db, service.config, employer=employer)


@pytest.mark.parametrize(
    "option",
    ["ExampleSoft Website", "ExampleSoft web site", "ExampleSoft Careers Page", "ExampleSoft's Career Site", "examplesoft.com"],
)
def test_the_employers_own_site_is_the_company_website_channel(option):
    assert source_patterns(["company website"], "ExampleSoft Inc", [option])[0].search(option)


@pytest.mark.parametrize("option", ["ExampleSoft Billboard", "ExampleSoft Employee Referral", "Other Corp Website", "Social Media"])
def test_other_options_are_not_the_employers_site(option):
    assert not source_patterns(["company website"], "ExampleSoft Inc", [option])[0].search(option)


def test_the_employer_comes_from_the_job_or_else_from_the_brand_the_options_share():
    assert "ExampleSoft" in employer_names("ExampleSoft Inc")
    # A job saved without a company is named by its URL, which names no employer.
    assert employer_names("https://myjobs.example.test/examplesoft/cx/job-details?reqId=1") == set()
    assert employer_names("", PARENT) == {"ExampleSoft"}
    # A channel word is not a brand, and a brand needs other options that lead with it.
    assert employer_names("", ["Job Site", "Job Board", "Job Posting"]) == set()
    assert employer_names("", ["Acme Website", "Social Media"]) == set()


def test_the_best_offered_channel_is_the_employers_site_ahead_of_job_board_and_social_media():
    rules = AnswerRules(**LIST)
    assert source_choice(PARENT, rules, "ExampleSoft Inc") == "ExampleSoft Website"
    # With no usable company the shared brand finds it.
    assert source_choice(PARENT, rules, "") == "ExampleSoft Website"


def test_the_parent_source_menu_is_answered_with_the_policys_best_channel(service):
    r = resolver(service)
    for kind in ("select", "combobox", "dropdown"):
        answer = r.local(question("Candidate Source", PARENT, kind))
        assert answer.disposition == "answer" and answer.value == "ExampleSoft Website"
    # No company-website option: job board outranks social media.
    without_site = [o for o in PARENT if o != "ExampleSoft Website"]
    assert r.local(question("Candidate Source", without_site)).value == "Job Board"
    assert r.policy_choice(question("Candidate Source", without_site), without_site) == "Job Board"


def test_a_profile_without_a_list_keeps_the_general_category_for_its_saved_source(service):
    r = resolver(service, rules={})
    assert r.local(question("Candidate Source", PARENT)).value == "Job Board"


def test_a_follow_up_with_no_truthful_option_sends_the_parent_back_to_the_next_channel(service):
    r = resolver(service, rules={**LIST, "source_order": ["social media", "company website"]})
    assert r.local(question("Candidate Source", PARENT)).value == "Social Media"
    follow_up = r.local(question("Social Media Source", SOCIAL))
    # Facebook/Instagram/Meta is not the applicant's channel, and the any-channel fallback never names a platform.
    assert follow_up.disposition == "review" and follow_up.blocker_type == "control_failure" and follow_up.retryable
    assert "Social Media Source" in follow_up.reason and "truthful" in follow_up.reason
    assert r.dead_sources == {"social media"}
    # The parent menu is answered again without that channel: the next one in the owner's list.
    assert r.local(question("Candidate Source", PARENT)).value == "ExampleSoft Website"
    assert r.policy_choice(question("Candidate Source", PARENT), PARENT) == "ExampleSoft Website"


def test_the_job_board_follow_up_offers_linkedin(service):
    r = resolver(service)
    answer = r.local(question("Job Board Source", JOB_BOARDS))
    assert answer.disposition == "answer" and answer.value == "LinkedIn"
    assert r.dead_sources == set()
    # Without LinkedIn, a channel the owner's list names is still the applicant's.
    answer = r.local(question("Job Board Source", ["-", "Glassdoor", "Handshake", "Indeed"]))
    assert answer.value == "Indeed"
    # Nothing the owner uses: the channel is dead, whatever the any-channel setting.
    answer = r.local(question("Job Board Source", ["-", "Handshake", "Job Service"]))
    assert answer.disposition == "review" and r.dead_sources == {"job board"}


def test_a_follow_up_not_yet_read_is_not_a_dead_end(service):
    r = resolver(service)
    assert r.local(question("Social Media Source", ["-"])).value == "LinkedIn"  # unread: search by the saved source
    assert r.local(question("Social Media Source", [], options_partial=True)).value == "LinkedIn"
    assert r.dead_sources == set()


def test_pre_filled_channel_is_replaced_by_the_policys_best(service):
    # fill_current writes the resolver's answer when it differs from the page's value.
    r = resolver(service)
    prefilled = question("Candidate Source", PARENT, value="Social Media")
    assert r.local(prefilled).value != prefilled["value"]


@pytest.mark.parametrize(
    "label,channel",
    [
        ("Social Media Source", "social media"),
        ("Job Board Source *", "job board"),
        ("Job Boards Source", "job boards"),
        ("Candidate Source", ""),
        ("Funding Source", ""),
        ("Source of income", ""),
    ],
)
def test_follow_up_channel(label, channel):
    assert follow_up_channel(label) == channel


@pytest.mark.parametrize(
    "label,is_source",
    [
        ("Candidate Source", True),
        ("Social Media Source", True),
        ("Job Board Source", True),
        ("How did you hear about us?", True),
        ("Funding Source", False),
        ("Source of funds", False),
        ("Data source", False),
    ],
)
def test_qualified_source_labels_are_the_source_topic(label, is_source):
    assert (topic(label) == "source") is is_source


def test_the_channel_that_failed_is_found_under_its_plural_too():
    assert same_channel("Job Boards", "job board") and same_channel("Social Media", "social media")
    assert not same_channel("Social Media Ads", "social media")
