"""Production regressions of 2026-10-25.

The pre-submit answer audit held Oracle, iCIMS and SuccessFactors applications on values it misjudged: a picker's
"Other - Field of Study Not Listed" saved as a global answer was typed into free-text Majors, a remembered address
"Type: Home" was read as the phone type, a numbered "Link 1" holding the applicant's site was "corrected" to LinkedIn,
and availability "Immediately" was compared against work-history start dates. Oracle identity codes mailed from an
employer's own domain in four other wordings expired unread, and a receipt sweep's mailbox reads starved live code
searches until their windows closed.
"""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from jobpilot import answer_quality, phone_fields
from jobpilot.answers.resolver import Resolver
from jobpilot.features.knowledge.retrieval import clear_cache, retrieve_answer_context
from jobpilot.schemas import Answer, EducationEntry, Profile, ReviewedAnswer
from jobpilot.widgets import foreign_fallback

FALLBACK = "Other - Field of Study Not Listed"


def profile_with_fallback_review():
    return Profile(
        first_name="Alex",
        last_name="Rivera",
        education=[EducationEntry(school="State University", degree="Master of Science", field_of_study="Statistics")],
        reviewed_answers=[
            ReviewedAnswer(id="r1", question="Major", answer=FALLBACK, options=[FALLBACK], scope="personal")
        ],
    )


def test_a_pickers_fallback_choice_answers_only_a_picker_that_offers_it():
    assert foreign_fallback(FALLBACK, [FALLBACK], [])
    assert foreign_fallback(FALLBACK, [FALLBACK], ["Statistics", "Biology"])
    assert not foreign_fallback(FALLBACK, [FALLBACK], ["Statistics", FALLBACK])
    # A typed answer saved without a catalog, or a real field of study, is unaffected.
    assert not foreign_fallback(FALLBACK, [], [])
    assert not foreign_fallback("Statistics", ["Statistics"], [])
    assert foreign_fallback("None of the above", ["None of the above"], [])


def test_retrieval_keeps_a_catalog_fallback_out_of_a_free_text_question(service):
    clear_cache()
    service.db.set_setting("profile", profile_with_fallback_review().model_dump())
    assert retrieve_answer_context(service.db, "Major").exact_answer is None
    clear_cache()
    offered = retrieve_answer_context(service.db, "Major", [FALLBACK, "Biology"])
    assert offered.exact_option == FALLBACK


def test_a_reviewed_fallback_is_not_typed_into_a_free_text_major(service):
    profile = profile_with_fallback_review()
    resolver = Resolver(profile, service.db, service.config, job={"ats": "oracle", "url": "https://example.test/j"})
    answer = resolver.local({"id": "m", "label": "Major", "type": "text", "options": []})
    assert answer is None or answer.value != FALLBACK
    picked = resolver.local({"id": "p", "label": "Major", "type": "select", "options": [FALLBACK, "Biology"]})
    assert picked is not None and picked.value == FALLBACK


@pytest.mark.parametrize(
    "field,expected",
    [
        ({"label": "Type", "type": "select", "section": "Phones (1) * required."}, True),
        ({"label": "Type", "type": "select", "group": "Telephone numbers"}, True),
        ({"label": "Type", "type": "select", "section": "Addresses (1) * required."}, False),
        ({"label": "Type", "type": "select"}, False),
        ({"label": "Type", "type": "text", "section": "Phones (1)"}, False),
        ({"label": "Phone Type", "type": "select"}, True),
    ],
)
def test_a_bare_type_selector_is_the_phone_type_only_inside_a_phone_section(field, expected):
    assert phone_fields.is_phone_type(field) is expected


def test_the_phone_group_fact_precedes_a_remembered_address_type():
    remembered = Answer(field_id="t", disposition="answer", value="Home", evidence_ids=["knowledge:review"])
    resolver = SimpleNamespace(
        profile=Profile(first_name="Alex", phone_type="Mobile"),
        question_context=lambda field: None,
        structured_answer=lambda field, packet: remembered,
        local=lambda field: None,
    )
    form = SimpleNamespace(resolver=resolver, workday=False)
    phone = {"id": "t", "label": "Type", "type": "select", "options": ["Home", "Mobile"], "section": "Phones (1)"}
    assert answer_quality.supported_local(form, phone).value == "Mobile"
    address = {"id": "a", "label": "Type", "type": "select", "options": ["Home", "Other"], "section": "Addresses (1)"}
    assert answer_quality.supported_local(form, address).value == "Home"


def gate(monkeypatch, known, profile=None):
    monkeypatch.setattr(answer_quality, "supported_local", lambda form, field: known)
    resolver = SimpleNamespace(profile=profile or Profile(first_name="Alex"))
    return SimpleNamespace(resolver=resolver, optional_picker_omissions=set(), field_key=lambda f: f["id"])


def test_a_numbered_link_slot_accepts_any_of_the_applicants_links(monkeypatch):
    profile = Profile(first_name="Alex", linkedin="https://www.linkedin.com/in/alex", website="https://alex.example")
    linkedin = Answer(field_id="l", disposition="answer", value=profile.linkedin, evidence_ids=["profile:linkedin"])
    form = gate(monkeypatch, linkedin, profile)
    field = {"id": "l", "label": "Link 1", "type": "url", "required": False, "value": "https://alex.example/"}
    assert answer_quality.local_corrections(form, [field]) == {}
    # Someone else's link in the slot, or the applicant's site in a field that names LinkedIn, is still corrected.
    stranger = {**field, "value": "https://elsewhere.example"}
    assert answer_quality.local_corrections(form, [stranger])["l"][1] == "confirmed_fact_mismatch"
    named = {**field, "label": "LinkedIn Profile"}
    assert answer_quality.local_corrections(form, [named])["l"][1] == "confirmed_fact_mismatch"


def test_a_date_is_not_disproved_by_a_fact_that_is_no_date(monkeypatch):
    immediately = Answer(field_id="d", disposition="answer", value="Immediately", evidence_ids=["fact:availability"])
    field = {"id": "d", "label": "Start Date", "type": "text", "required": True, "value": "10/23/2026"}
    assert answer_quality.local_corrections(gate(monkeypatch, immediately), [field]) == {}
    today = Answer(field_id="d", disposition="answer", value="10/25/2026", evidence_ids=["fact:availability"])
    assert answer_quality.local_corrections(gate(monkeypatch, today), [field])["d"][1] == "confirmed_fact_mismatch"


def test_a_history_rows_own_fact_is_not_replaced_by_an_unrelated_fact(monkeypatch):
    field = {
        "id": "w",
        "label": "Start Date",
        "type": "text",
        "required": True,
        "value": "12/01/2025",
        "evidence_ids": ["profile:work.0.start"],
    }
    availability = Answer(field_id="w", disposition="answer", value="10/25/2026", evidence_ids=["fact:availability"])
    assert answer_quality.local_corrections(gate(monkeypatch, availability), [field]) == {}
    other_row = Answer(field_id="w", disposition="answer", value="06/01/2024", evidence_ids=["profile:work.1.start"])
    assert answer_quality.local_corrections(gate(monkeypatch, other_row), [field])["w"][1] == "confirmed_fact_mismatch"


@pytest.mark.parametrize(
    "body",
    [
        "Hello Alex, Your one-time verification code to confirm your identity is 482913 Note: it expires soon.",
        "Please confirm your identity using this one-time pass code: 482913",
        "We need you to confirm your identity for your job application for using this code: 482913.",
        "Confirm your identity using this code: 482913. The code expires in 10 minutes.",
        "You must confirm your identity using the one-time pass code : 482913",
    ],
)
def test_oracle_identity_codes_from_an_employers_own_domain_are_read_in_every_wording(body):
    from jobpilot.mail import extract_message
    from support_mail import message

    now = time.time()
    msg = message(body, received=now)
    headers = msg["payload"]["headers"]
    headers[0]["value"] = "Acme Careers <noreply@acmehiring.example>"
    headers[2]["value"] = "mx.google.com; dkim=pass; dmarc=pass header.from=acmehiring.example"
    rule = {"sender_domains": ["oraclecloud.com", "oracle.com"], "link_origins": []}
    window = {
        "id": "ch", "recipient": "alex@example.test", "since": now - 30, "expires": now + 30, "kind": "code",
        "ats": "oracle", "employer_name": "Acme", "tenant_tokens": ["acme"], "employer_senders": True,
        "open_windows": [], "oracle_pod": "", "oracle_peers": [],
    }
    assert extract_message(msg, rule, window) == {"kind": "code", "value": "482913"}


def test_an_employer_domain_mail_that_is_not_the_identity_template_is_refused():
    from jobpilot.mail import extract_message
    from support_mail import message

    now = time.time()
    msg = message("Confirm your identity by opening your profile. Reference code: 482913", received=now)
    headers = msg["payload"]["headers"]
    headers[0]["value"] = "Acme Careers <noreply@acmehiring.example>"
    headers[2]["value"] = "mx.google.com; dkim=pass; dmarc=pass header.from=acmehiring.example"
    rule = {"sender_domains": ["oraclecloud.com", "oracle.com"], "link_origins": []}
    window = {
        "id": "ch", "recipient": "alex@example.test", "since": now - 30, "expires": now + 30, "kind": "code",
        "ats": "oracle", "employer_name": "Acme", "tenant_tokens": ["acme"], "employer_senders": True,
        "open_windows": [], "oracle_pod": "", "oracle_peers": [],
    }
    diagnostics = {}
    assert extract_message(msg, rule, window, diagnostics=diagnostics) is None
    assert diagnostics["rejection"] == "sender"


async def test_receipt_reads_wait_for_a_live_code_search_and_use_their_own_connections(service, monkeypatch):
    from test_receipt_cursor import make_box

    box_id = make_box(service)
    release, entered, calls = threading.Event(), threading.Event(), []

    def fake_request(address, password, path, params=None, lane="live"):
        calls.append((lane, path))
        if lane == "live":
            entered.set()
            release.wait(5)
        return {"messages": []}

    monkeypatch.setattr("jobpilot.gmail_imap.request", fake_request)
    live = asyncio.create_task(service.mail.request(box_id, "/messages", {"q": "code"}))
    await asyncio.to_thread(entered.wait, 5)
    background = asyncio.create_task(service.mail.request(box_id, "/messages", {"q": "receipt"}, background=True))
    await asyncio.sleep(0.3)
    assert calls == [("live", "/messages")]  # The receipt read waits while the code search is in flight.
    release.set()
    await asyncio.gather(live, background)
    assert calls == [("live", "/messages"), ("background", "/messages")]
    assert service.mail.live_requests[box_id] == 0


def code_page_adapter(service, armed):
    from collections import Counter
    from unittest.mock import AsyncMock, MagicMock

    from jobpilot.adapters.base import Adapter

    class Probe(Adapter):
        mail_rule_known = True

    value = Probe.__new__(Probe)
    url = "https://careers.example.test/apply/email"
    resend = {"id": "r", "label": "Resend code", "disabled": False}
    obs = {"fields": [{"id": "c", "type": "text", "label": "Verification code", "maxlength": 6}],
           "controls": [resend], "text": "Enter the code we sent", "url": url}
    form = SimpleNamespace(scan=AsyncMock(return_value=obs), frames={"c": SimpleNamespace(url=url)},
                           locator=lambda _id: SimpleNamespace(fill=AsyncMock()), page=SimpleNamespace(url=url),
                           settle=AsyncMock())
    value.inbox = SimpleNamespace(
        configured=True,
        live=lambda challenge: bool(challenge) and challenge["id"] == "armed",
        live_request=lambda run_id: {"id": "armed", "rule": {}} if armed else None,
    )
    value.e = SimpleNamespace(form=form, landing_url=url, emit=lambda *a, **k: None, run_id="run-1")
    value.service, value.config, value.id = service, service.config, "oracle"
    value.job = {"id": "j", "url": url, "ats": "oracle"}
    value.challenge, value.attempts = None, Counter()
    value.arm_mail = AsyncMock(return_value=True)
    value.click = AsyncMock()
    value.emit = MagicMock()
    value.wait_email = AsyncMock(return_value=None)
    value.release_mail = MagicMock()
    value.mail_problem = lambda what: "no code"
    value.defer = lambda reason, *a, **k: {"reason": reason}
    return value, obs, resend


async def test_a_code_page_uses_the_request_its_send_click_armed_instead_of_resending(service):
    from jobpilot.adapters.base import Adapter

    value, obs, resend = code_page_adapter(service, armed=True)
    clicks_before_wait = []
    value.wait_email.side_effect = lambda kind: clicks_before_wait.append(value.click.await_count)
    await Adapter.on_email_code(value, obs)
    assert value.challenge["id"] == "armed"
    assert value.attempts["entry_resend"] == 0
    assert clicks_before_wait[0] == 0  # It waited for the armed request's code before asking for another.


async def test_a_reopened_code_page_without_a_live_request_asks_for_a_fresh_code(service):
    from jobpilot.adapters.base import Adapter

    value, obs, resend = code_page_adapter(service, armed=False)
    await Adapter.on_email_code(value, obs)
    assert value.attempts["entry_resend"] == 1
    assert value.click.await_args_list[0].args[0] is resend


@pytest.mark.parametrize(
    "label,options,expected",
    [
        ("Please enter your relevant employment above using the + Add Another Employment link.", ["Thank you"], "Thank you"),
        ("Attach your transcript in the Documents section.", ["OK"], "OK"),
        ("Do you have a current professional license?", ["Thank you"], None),
        ("Please enter your relevant employment above.", ["Yes", "No"], None),
        ("Which office do you prefer?", ["Remote"], None),
    ],
)
def test_an_instruction_whose_only_option_acknowledges_it_takes_that_option(service, label, options, expected):
    profile = Profile(first_name="Alex", last_name="Rivera", accept_all_application_terms=True)
    resolver = Resolver(profile, service.db, service.config, job={"ats": "greenhouse", "url": "https://example.test/j"})
    answer = resolver.local({"id": "i", "label": label, "type": "select", "options": options, "required": True})
    got = answer.value if answer is not None and answer.disposition == "answer" else None
    if expected is None:
        assert got not in {"Thank you", "OK"}
    else:
        assert got == expected


def test_a_month_number_picks_that_month_in_a_menu_of_month_names():
    from jobpilot.widgets import best_option

    names = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December"]
    assert best_option(names, "8") == "August"
    assert best_option(names, "08") == "August"
    assert best_option([n[:3] for n in names], "9") == "Sep"
    # Numeric menus and menus that are not months keep their own matching.
    assert best_option(["1", "2", "8", "10"], "8") == "8"
    assert best_option(["Small", "Medium", "Large"], "8") is None
    assert best_option(names, "13") is None


@pytest.mark.parametrize(
    "observed,expected,same",
    [
        ("College Town, ST", "College Town", True),
        ("Springfield", "Springfield, Country", True),
        ("Springfield, ST", "Rivertown", False),
        ("", "Rivertown", False),
    ],
)
def test_a_city_with_or_without_its_region_is_the_same_city(observed, expected, same):
    field = {"id": "c", "label": "City", "type": "text", "options": []}
    assert answer_quality.equivalent(field, observed, expected) is same


SLASH_DEGREES = ["High School/GED", "AA/AS", "BA/BS", "MA/MS", "MBA", "PhD", "JD"]


@pytest.mark.parametrize("degree,expected", [("Master of Science", "MA/MS"), ("BE", "BA/BS"), ("MS", "MA/MS")])
def test_a_degree_takes_its_level_in_a_menu_of_combined_types_never_mba(service, degree, expected):
    profile = Profile(first_name="Alex", education=[EducationEntry(school="State University", degree=degree)])
    resolver = Resolver(profile, service.db, service.config, job={"ats": "workday", "url": "https://example.test/j"})
    answer = resolver.local({"id": "d", "label": "Degree", "type": "dropdown", "options": SLASH_DEGREES,
                             "required": True, "group": "Education 1", "section": "Education"})
    assert answer is not None and answer.value == expected


def test_degree_levels_read_bare_abbreviations():
    from jobpilot.answers.history import degree_level

    assert [degree_level(v) for v in ("MS", "MSc", "MA/MS", "BS", "BA/BS", "MBA")] == [
        "master", "master", "master", "bachelor", "bachelor", None]
