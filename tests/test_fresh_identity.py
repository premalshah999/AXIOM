"""An employer identity that refuses the login and mails no reset is replaced on any platform (2026-10-30).

SuccessFactors, Farmers (21 runs) and Wipro (20): Create answered "Account Already Exists", the saved login "Invalid email
address or password", the reset "If an account matches the information you entered ..." and no mail ever came from the
employer. iCIMS already moved to a per-employer plus-address (HERE, Berry, American Systems, Publicis, 2026-10-29); this
extends it to every adapter, with the silence read from the mailbox instead of assumed."""

import time
from collections import Counter
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from jobpilot.adapters.base import Adapter, create_allowed, create_stamp
from jobpilot.db import Event, Job, MailChallenge, Run
from jobpilot.email_alias import EMPLOYER_ALIAS_TAG, alias_refusal, applying_email, employer_alias_key

BASE = "alex.doe.applicant@gmail.com"
SF_URL = "https://jobs.examplesoft.test/job/Springfield-Analyst/100"
ASKED = time.time() - 4000
PROVEN = {
    "state": "password_unknown",
    "reset_requested_at": ASKED,
    "reset_submitted_at": "2026-10-30T19:53:24+00:00",
    "absent_create_at": "2026-10-30T19:52:21+00:00",
    "confirmed_at": "2026-10-30T19:52:39+00:00",  # "Account Already Exists" is proof the account exists, not a sign-in
}


def adapter(db, ats="successfactors", email=BASE, url=SF_URL, service=None, silent=True):
    value = Adapter.__new__(Adapter)
    value.service, value.attempts = service or SimpleNamespace(db=db), Counter()
    value.job = {"id": "job1", "ats": ats, "url": url}
    value.accounts = SimpleNamespace(email=email)
    value.config = SimpleNamespace(browser_cdp_url="")
    value.emit = lambda *a, **k: None
    if silent is not None:
        value.reset_mail_silent = lambda record: silent
    return value


@pytest.fixture
def settings():
    store = {}
    db = SimpleNamespace(get_setting=lambda k, d=None: store.get(k, d), set_setting=store.__setitem__)
    return db, store


def test_a_successfactors_identity_that_exists_but_cannot_be_reset_moves_to_an_alias(settings):
    db, store = settings
    result = adapter(db).fresh_identity(dict(PROVEN))
    assert result["state"] == "waiting_browser" and result["blocker_type"] == "account" and not result["reviews"]
    assert store[employer_alias_key(SF_URL)]["tag"] == EMPLOYER_ALIAS_TAG
    assert adapter(db).fresh_identity(dict(PROVEN)) is None  # one replacement per employer, never a third identity


@pytest.mark.parametrize(
    "change",
    [
        {"reset_submitted_at": None},  # the employer never confirmed a reset request: its silence says nothing
        {"absent_create_at": None, "confirmed_at": None},  # nothing shows the employer holds an account (non-iCIMS)
        {"absent_create_at": None},  # an account that signed in before is kept unless its own Create refused as existing
    ],
)
def test_the_evidence_must_be_the_employers_not_assumed(settings, change):
    db, store = settings
    assert adapter(db).fresh_identity({**PROVEN, **change}) is None and not store


def test_no_alias_where_none_can_exist_or_the_mailbox_was_not_silent(settings):
    db, store = settings
    assert adapter(db, silent=False).fresh_identity(dict(PROVEN)) is None  # a reset mail was seen, or none is on record
    assert adapter(db, email="alex@example.edu").fresh_identity(dict(PROVEN)) is None  # only Gmail delivers a plus-address
    assert adapter(db, email="alex+jobs@gmail.com").fresh_identity(dict(PROVEN)) is None  # already a tagged address
    assert not store


def test_an_icims_identity_keeps_its_earlier_rule_plus_the_mailbox_check(settings):
    db, store = settings
    icims = "https://careers-acme.icims.com/jobs/1/engineer/job"
    record = {k: v for k, v in PROVEN.items() if k not in {"absent_create_at", "confirmed_at"}}
    assert adapter(db, ats="icims", url=icims).fresh_identity(record)["state"] == "waiting_browser"
    assert store["email_alias:https://careers-acme.icims.com"]["tag"] == "apply"
    # A once-working Central login is still kept; so is one whose reset was never confirmed.
    assert adapter(db, ats="icims", url=icims.replace("acme", "other")).fresh_identity({**record, "confirmed_at": "x"}) is None
    assert adapter(db, ats="icims", url=icims.replace("acme", "third")).fresh_identity({**record, "reset_submitted_at": None}) is None


def seed_resets(service, challenge_state="expired", poll=None, since=ASKED - 30, extra_challenge=None):
    """Job1's run asked for a reset (window opened at `since`); optionally the poll's findings."""
    when = datetime.now(UTC).isoformat()
    with service.db.exclusive() as s:
        s.add(Job(id="job1", identity="job1", url=SF_URL, ats="successfactors", company="ExampleSoft", title="Analyst",
                  status="waiting_browser"))
        s.add(Run(id="run1", job_id="job1", mode="submit", state="waiting_browser", packet={}))
        s.add(MailChallenge(id="c1", run_id="run1", rule_id="r", mailbox_id="m", recipient="alex@example.test",
                            kind="password_reset", state=challenge_state, since=since, expires=since + 1200, created_at=when))
        if extra_challenge:
            s.add(extra_challenge)
        if poll is not None:
            s.add(Event(run_id="run1", kind="mail_poll", message="Checked the verification mailbox", data=poll,
                        created_at=datetime.now(UTC).isoformat()))


def silent(service):
    return Adapter.reset_mail_silent(adapter(None, service=service, silent=None), dict(PROVEN))


QUIET = {"candidate_count": 0, "matching_count": 0, "rejection_counts": {}, "outside_senders": []}


def test_the_mailbox_shows_the_reset_was_never_answered(service):
    seed_resets(service, poll=QUIET)
    assert silent(service)


@pytest.mark.parametrize(
    "poll",
    [
        {**QUIET, "candidate_count": 1},  # a message from the employer's sender reached the window
        {**QUIET, "outside_senders": ["mail.examplesoft.test"]},  # reset wording from a sender the rule does not list
        {**QUIET, "matching_count": 1},
    ],
)
def test_any_mail_in_the_window_means_the_employer_does_answer(service, poll):
    seed_resets(service, poll=poll)
    assert not silent(service)


@pytest.mark.parametrize("state", ["matched", "consumed", "verified", "failed", "pending"])
def test_a_reset_that_was_claimed_or_is_still_open_is_not_silence(service, state):
    seed_resets(service, challenge_state=state, poll=QUIET)
    assert not silent(service)


def test_a_reset_with_no_window_on_record_proves_nothing(service):
    seed_resets(service, poll=QUIET, since=ASKED - 90000)  # the newest window is a day older than the recorded request
    assert not silent(service)
    assert not Adapter.reset_mail_silent(adapter(None, service=service, silent=None), {**PROVEN, "reset_requested_at": 0})


def test_the_alias_reaches_the_address_the_run_applies_with_on_any_platform():
    setting = {"tag": EMPLOYER_ALIAS_TAG}
    assert applying_email(BASE, "successfactors", {}, setting) == "alex.doe.applicant+apply@gmail.com"
    assert applying_email(BASE, "custom", {"icims": "icims"}, setting) == "alex.doe.applicant+apply@gmail.com"
    assert applying_email(BASE, "successfactors", {}, None) == BASE  # an employer without one
    assert applying_email("alex@example.edu", "successfactors", {}, setting) == "alex@example.edu"
    assert applying_email(BASE, "icims", {"icims": "icims"}, None) == "alex.doe.applicant+icims@gmail.com"


def test_the_alias_is_the_account_the_flow_signs_up_and_every_answer_source_sees(service):
    from jobpilot.accounts import Accounts
    from jobpilot.accounts.store import AccountStore
    from jobpilot.answers import Resolver
    from jobpilot.schemas import Profile

    alias = applying_email(BASE, "successfactors", {}, {"tag": EMPLOYER_ALIAS_TAG})
    profile = Profile(name="Alex Doe", email=alias)
    accounts = Accounts(service, profile, SF_URL)
    assert accounts.email == alias  # the employer's account record is keyed by the alias, not the dead identity
    assert AccountStore(service, BASE, SF_URL).key(SF_URL) != accounts.store.key(SF_URL)
    # A knowledge fact or saved answer holding the base mailbox is typed as the address the run applies with.
    assert Resolver(profile, None, None).platform_address(BASE) == alias
    assert Resolver(profile, None, None).platform_address("someone.else@example.test") == "someone.else@example.test"


def test_the_replacement_account_has_its_own_two_registrations_a_day():
    def after(*runs):
        record = {}
        for run in runs:
            record = {"create_sent": create_stamp(record, run)}
        return record

    assert not create_allowed(after("run1", "run2"), "run3")  # the dead identity's two Creates today
    assert create_allowed({}, "run3")  # the alias is a new record: its cap starts at zero
    assert create_allowed(after("run4"), "run5") and not create_allowed(after("run4", "run5"), "run6")  # and holds at two


def test_a_sign_up_form_that_refuses_the_plus_address_is_reported_once_and_the_alias_dropped(settings):
    db, store = settings
    alias = applying_email(BASE, "successfactors", {}, {"tag": EMPLOYER_ALIAS_TAG})
    key = employer_alias_key(SF_URL)
    store[key] = {"tag": EMPLOYER_ALIAS_TAG, "at": "2026-10-30T20:00:00+00:00"}
    # Not the address: a refused sign-in says "Invalid email address or password", and the base address was never aliased.
    assert not alias_refusal(db, SF_URL, alias, ["Invalid email address or password. Please re-enter your login info."])
    assert not alias_refusal(db, SF_URL, BASE, ["Please enter a valid email address"])
    assert not alias_refusal(db, SF_URL, alias, [])
    assert alias_refusal(db, SF_URL, alias, ["Please enter a valid email address"])
    assert store[key]["refused_at"] and "valid email" in store[key]["refusal"]
    assert not alias_refusal(db, SF_URL, alias, ["Please enter a valid email address"])  # already recorded
    # Later runs apply with the profile's own address again; no further identity is created.
    assert applying_email(BASE, "successfactors", {}, store[key]) == BASE
    assert adapter(db).fresh_identity(dict(PROVEN)) is None


async def test_a_reset_not_answered_for_six_hours_is_replaced_by_an_identity_not_asked_again(settings):
    """recover() asked for a reset again every six hours (RESET_RETRY_SECONDS) although the mailbox had shown none."""
    db, store = settings
    value = adapter(db)
    value.auth_url = lambda obs: SF_URL
    value.config = SimpleNamespace(account_password_reset=True)
    old = datetime.fromtimestamp(time.time() - 7 * 3600, UTC).isoformat()
    value.accounts = SimpleNamespace(email=BASE, record=lambda url: {**PROVEN, "password_unknown_at": old, "reset_requested_at": time.time() - 7 * 3600})
    result = await value.recover({"controls": [], "fields": [], "text": ""}, "Invalid email address or password")
    assert result["state"] == "waiting_browser" and "new address" in result["reason"]
    assert store[employer_alias_key(SF_URL)]["tag"] == EMPLOYER_ALIAS_TAG
