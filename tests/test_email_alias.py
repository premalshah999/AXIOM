"""iCIMS (0 of 36 by 2026-10-18): the applicant's address was bound to an iCIMS Central identity the app never verified.
A Gmail plus-address is a new address to iCIMS and reaches the same inbox, so a profile can apply on chosen platforms
with one."""

from jobpilot.email_alias import ats_address, base_address

ALIASES = {"icims": "icims"}


def test_a_listed_platform_gets_a_plus_address_on_gmail_only():
    assert ats_address("alex.doe.applicant@gmail.com", "icims", ALIASES) == "alex.doe.applicant+icims@gmail.com"
    assert ats_address("alex.doe.applicant@gmail.com", "workday", ALIASES) == "alex.doe.applicant@gmail.com"
    assert ats_address("alex@example.edu", "icims", ALIASES) == "alex@example.edu"  # plus-addressing is not assumed elsewhere
    assert ats_address("alex+jobs@gmail.com", "icims", ALIASES) == "alex+jobs@gmail.com"


def test_a_plus_address_maps_back_to_its_mailbox():
    assert base_address("Alex.Doe.Applicant+icims@gmail.com") == "alex.doe.applicant@gmail.com"
    assert base_address("alex+tag@example.edu") == "alex+tag@example.edu"


def test_a_domain_alias_for_platforms_that_fold_plus_addresses():
    # ADP (Avalon 2026-10-19): "Another user account already exists with the provided email address" for local+adp@.
    aliases = {"adp": "@googlemail.com"}
    assert ats_address("alex.doe.applicant@gmail.com", "adp", aliases) == "alex.doe.applicant@googlemail.com"
    assert ats_address("alex@example.edu", "adp", aliases) == "alex@example.edu"
    assert ats_address("alex@googlemail.com", "adp", aliases) == "alex@googlemail.com"
    assert base_address("Alex.Doe.Applicant@GoogleMail.com") == "alex.doe.applicant@gmail.com"


def test_the_run_packet_applies_with_the_alias(service):
    from jobpilot.schemas import Profile

    profile = Profile.model_validate({"email": "alex.doe.applicant@gmail.com", "ats_email_aliases": ALIASES})
    assert ats_address(profile.email, "icims", profile.ats_email_aliases).endswith("+icims@gmail.com")


def test_the_account_login_follows_the_platform_alias(service, monkeypatch):
    # ACCOUNT_EMAIL is the base address in production; an iCIMS run's packet carries the alias (2026-10-18).
    from jobpilot.accounts import Accounts
    from jobpilot.schemas import Profile

    monkeypatch.setattr(service.config, "account_email", "applicant@gmail.com")
    aliased = Accounts(service, Profile(email="applicant+icims@gmail.com"), "https://careers-acme.icims.com/jobs/1")
    assert aliased.email == "applicant+icims@gmail.com"
    plain = Accounts(service, Profile(email="applicant@gmail.com"), "https://acme.wd5.myworkdayjobs.com/x")
    assert plain.email == "applicant@gmail.com"
    other = Accounts(service, Profile(email="someone.else@example.com"), "https://acme.wd5.myworkdayjobs.com/x")
    assert other.email == "applicant@gmail.com"  # Only a plus-address of the configured login replaces it.


def test_a_knowledge_email_answer_takes_the_runs_platform_address(service):
    # REA icims.md risk 1: the Email field was answered from knowledge:m:personal:email (the base mailbox).
    from types import SimpleNamespace

    from jobpilot.answers import Resolver
    from jobpilot.schemas import Profile

    item = SimpleNamespace(kind="fact", match="exact", fact_id="m:personal:email")
    packet = SimpleNamespace(conflicts=[], exact_answer="applicant@gmail.com", exact_option=None, items=[item])
    field = {"id": "e", "label": "Email", "type": "email", "required": True, "options": [], "value": ""}
    aliased = Resolver(Profile(email="applicant+icims@gmail.com"), service.db, service.config)
    assert aliased.structured_answer(field, packet).value == "applicant+icims@gmail.com"
    plain = Resolver(Profile(email="applicant@gmail.com"), service.db, service.config)
    assert plain.structured_answer(field, packet).value == "applicant@gmail.com"


def test_every_answer_source_takes_the_runs_platform_address(service):
    # ADP (Avalon) 2026-10-19: saved confirmed answers held the base mailbox; resolve() is where every source meets.
    from jobpilot.answers import Resolver
    from jobpilot.schemas import Profile

    aliased = Resolver(Profile(email="applicant+adp@gmail.com"), service.db, service.config)
    assert aliased.platform_address("applicant@gmail.com") == "applicant+adp@gmail.com"
    assert aliased.platform_address("Applicant@Gmail.com") == "applicant+adp@gmail.com"
    assert aliased.platform_address("someone@gmail.com") == "someone@gmail.com"
    assert aliased.platform_address("Reach me at applicant@gmail.com") == "Reach me at applicant@gmail.com"
    plain = Resolver(Profile(email="applicant@gmail.com"), service.db, service.config)
    assert plain.platform_address("applicant@gmail.com") == "applicant@gmail.com"


def test_one_employer_can_get_its_own_alias():
    from jobpilot.email_alias import EMPLOYER_ALIAS_TAG, employer_alias_key

    key = employer_alias_key("https://Careers-Acme.icims.com/jobs/1/engineer/job?mode=apply")
    assert key == "email_alias:https://careers-acme.icims.com"
    assert employer_alias_key("") == ""
    tagged = ats_address("alex.doe.applicant@gmail.com", "icims", {"icims": EMPLOYER_ALIAS_TAG})
    assert tagged == "alex.doe.applicant+apply@gmail.com" and base_address(tagged) == "alex.doe.applicant@gmail.com"


def test_an_unusable_icims_identity_is_abandoned_once_for_that_employer():
    """HERE, Berry, American Systems, Publicis (2026-10-29): iCIMS Central accepted the address, refused the shared
    password, and no reset ever reached the mailbox. The next run applies there with a plus-address."""
    from collections import Counter
    from types import SimpleNamespace

    from jobpilot.adapters.base import Adapter

    settings = {}
    db = SimpleNamespace(get_setting=lambda k, d=None: settings.get(k, d), set_setting=settings.__setitem__)

    def adapter(ats="icims", email="alex.doe.applicant@gmail.com", url="https://careers-acme.icims.com/jobs/1/engineer/job"):
        value = Adapter.__new__(Adapter)
        value.service, value.attempts = SimpleNamespace(db=db), Counter()
        value.job = {"ats": ats, "url": url}
        value.accounts = SimpleNamespace(email=email)
        value.config = SimpleNamespace(browser_cdp_url="")
        value.emit = lambda *a, **k: None
        value.reset_mail_silent = lambda record: True  # the mailbox check has its own tests (test_fresh_identity.py)
        return value

    proven = {"state": "password_unknown", "reset_requested_at": 1.0, "reset_submitted_at": "2026-10-29T00:00:00"}
    assert adapter().fresh_identity({**proven, "confirmed_at": "2026-10-20T00:00:00"}) is None  # a real account is kept
    assert adapter(ats="workday", url="https://acme.wd5.myworkdayjobs.com/x/job/1").fresh_identity(proven) is None
    assert adapter(email="alex+jobs@gmail.com").fresh_identity(proven) is None
    assert adapter(email="alex@example.edu").fresh_identity(proven) is None
    assert adapter().fresh_identity({"state": "password_unknown"}) is None  # no reset the employer confirmed
    assert not settings
    result = adapter().fresh_identity(proven)
    assert result["state"] == "waiting_browser" and not result["reviews"]
    assert settings["email_alias:https://careers-acme.icims.com"]["tag"] == "apply"
    assert adapter().fresh_identity(proven) is None  # once per employer
    # An iCIMS career site filed under another ATS name is the same platform.
    assert adapter(ats="custom", url="https://careers-other.icims.com/jobs/2/job").fresh_identity(proven)
    assert "email_alias:https://careers-other.icims.com" in settings
