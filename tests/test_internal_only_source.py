"""Bank of America (Workday) 2026-10-19: "How Did You Hear About Us?" offered only "Displaced (US)" and "LOA - Job Search
(US)", internal channels, but the navigator's target carried no options, so the run asked the owner three times. The
form's own observed catalog decides: an internal-only posting closes."""

from types import SimpleNamespace

from jobpilot.answers import UNOFFERED_SOURCE
from jobpilot.browser import BrowserEngine


def engine(form_options):
    e = object.__new__(BrowserEngine)
    e.form = SimpleNamespace(fields={"f1": {"id": "f1", "label": "How Did You Hear About Us?", "options": form_options}},
                             pending=[])
    e.service = SimpleNamespace(review_kind=lambda q: "missing_fact")
    return e


def verdict(e):
    target = {"id": "f1", "label": "How Did You Hear About Us?", "type": "dropdown"}
    feedback = {"failure_category": "unsupported", "detail": "Answer validation rejected: " + UNOFFERED_SOURCE}
    return e.answer_verdict(SimpleNamespace(action="choose"), target, feedback, {})


def test_an_internal_only_catalog_closes_the_job_even_when_the_target_lacks_options():
    result = verdict(engine(["Select One", "Displaced (US)", "LOA - Job Search (US)"]))
    assert result["state"] == "closed" and "Internal-only" in result["reason"]


def test_a_public_catalog_without_the_saved_source_still_asks():
    result = verdict(engine(["Select One", "Indeed", "Company Website"]))
    assert result["state"] != "closed"


def test_the_pending_question_supplies_the_catalog_when_the_field_record_has_none():
    # Bank of America 2026-10-27: the field record carried no options; the refused pending question held them.
    e = engine([])
    e.form.pending = [{"question": "How Did You Hear About Us?", "options": ["Displaced (US)", "LOA - Job Search (US)"]}]
    result = verdict(e)
    assert result["state"] == "closed" and "Internal-only" in result["reason"]


def test_an_other_non_referral_choice_is_a_way_in_for_an_external_applicant():
    # Thrivent (Workday 2026-10-14): "Employee/Advisor Referral" and "Other/Non-Referral" were closed as internal-only
    # because "refer" matched both; the second names the plain external applicant.
    result = verdict(engine(["Employee/Advisor Referral", "Other/Non-Referral"]))
    assert result["state"] != "closed"
    assert verdict(engine(["Select One", "Employee Referral", "Other"]))["state"] != "closed"


def test_a_source_list_closes_the_posting_only_when_every_choice_is_internal():
    # The rule used the "cannot be claimed truthfully" pattern, which also reads agencies, recruiters, referrals and
    # campus events as internal. GE Vernova (2026-10-15..28, four jobs) offered "Agency / Search Firm", "Current Employee"
    # and "GE Vernova Recruiter", GDIT (2026-10-24) only "Employee Referral": each is a way in for an outside applicant,
    # so the application waits for the owner instead of being closed as internal-only.
    from jobpilot.answers.sources import internal_channels_only

    assert not internal_channels_only(["Agency / Search Firm", "Current Employee", "GE Vernova Recruiter"])
    assert not internal_channels_only(["Employee Referral"])
    assert not internal_channels_only(["Select One", "Campus Event", "Career Fair", "University Career Center"])
    assert not internal_channels_only(["Employee Referral", "Staffing Agency", "Alumni Network"])
    assert not internal_channels_only(["Select One", "Displaced (US)", "LOA - Job Search (US)", "Company Website"])
    for result in (verdict(engine(["Agency / Search Firm", "Current Employee", "GE Vernova Recruiter"])),
                   verdict(engine(["Employee Referral"]))):
        assert result["state"] == "waiting_browser" and "owner review" in result["reason"]


def test_internal_channels_alone_still_close_the_posting():
    from jobpilot.answers.sources import internal_channels_only

    assert internal_channels_only(["Select One", "Displaced (US)", "LOA - Job Search (US)"])
    assert internal_channels_only(["Employee"])  # Snap (2026-10-15)
    assert internal_channels_only(["-- Select --", "Current Employee", "Former Employee", "Internal Transfer"])
    assert not internal_channels_only([])
    assert not internal_channels_only(["Select One"])


def test_a_non_referral_choice_is_a_truthful_source_and_a_referral_is_not():
    from jobpilot.answer_rules import AnswerRules
    from jobpilot.answers.sources import source_choice

    rules = AnswerRules(source_order=["linkedin"], source_any_channel=True)
    assert source_choice(["Employee/Advisor Referral", "Other/Non-Referral"], rules) == "Other/Non-Referral"
    assert source_choice(["Employee Referral", "Non Referral"], rules) == "Non Referral"
    assert source_choice(["Employee Referral"], rules) is None
    assert source_choice(["Agency / Search Firm", "Current Employee", "Example Recruiter"], rules) is None


def test_a_profile_accepting_any_channel_closes_a_posting_with_no_true_source():
    # Owner 2026-10-31 on GE Vernova: "leave and delete those jobs".
    from jobpilot.answer_rules import AnswerRules

    e = engine(["Agency / Search Firm", "Current Employee", "Example Recruiter"])
    e.form.resolver = SimpleNamespace(answer_rules=AnswerRules(source_any_channel=True))
    result = verdict(e)
    assert result["state"] == "closed" and "true for this applicant" in result["reason"]

    e = engine(["Agency / Search Firm", "Current Employee", "Example Recruiter"])
    e.form.resolver = SimpleNamespace(answer_rules=AnswerRules(source_any_channel=False))
    assert verdict(e)["state"] == "waiting_browser"


def test_other_is_the_last_truthful_source():
    from jobpilot.answer_rules import AnswerRules
    from jobpilot.answers.sources import source_choice

    rules = AnswerRules(source_any_channel=True)
    assert source_choice(["Employee Referral", "Other"], rules) == "Other"
    assert source_choice(["Other", "Indeed Listing"], AnswerRules(source_any_channel=True)) == "Indeed Listing"
    assert source_choice(["Employee Referral", "Other"], AnswerRules(source_any_channel=False)) is None
