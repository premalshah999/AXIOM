"""Found applying by hand (2026-10-29): career sites parse the uploaded resume and pre-fill the form, and an employer's
saved candidate profile pre-fills answers from past applications. The pre-filled Email held the address printed on the
resume (not the application mailbox), the LinkedIn box held the GitHub URL, the Major box held the degree name. A
pre-filled value that contradicts the profile, or is the wrong kind for its box, is not accepted."""

from types import SimpleNamespace

import pytest

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession, degree_as_major, implausible_prefill, wrong_site_link
from jobpilot.schemas import EducationEntry, Profile

PROFILE = Profile(
    email="alexample.apply@gmail.com",
    phone="(555) 555-0100",
    linkedin="https://www.linkedin.com/in/alex-example",
    github="https://github.com/alexexample",
    education=[EducationEntry(school="Example University", degree="Master of Science", field_of_study="Data Science")],
)


def box(label, value, type="text", **extra):
    return {"id": "q", "label": label, "type": type, "required": False, "options": [], "value": value, "valid": True, **extra}


def form():
    session = object.__new__(FormSession)
    session.resolver = SimpleNamespace(profile=PROFILE)
    return session


@pytest.mark.parametrize(
    "label,value,wrong",
    [
        ("LinkedIn", "https://github.com/alexexample", True),
        ("LinkedIn Profile URL", "https://github.com/alexexample", True),
        ("GitHub", "https://www.linkedin.com/in/alex-example", True),
        ("LinkedIn", "https://www.linkedin.com/in/alex-example", False),
        ("LinkedIn", "https://uk.linkedin.com/in/alex-example", False),
        ("GitHub URL", "https://github.com/alexexample", False),
        # A box shared by several sites takes any of the applicant's links.
        ("LinkedIn or GitHub", "https://github.com/alexexample", False),
        ("Website", "https://github.com/alexexample", False),
        ("Portfolio / LinkedIn", "https://example.test/work", False),
        # A bare handle is not another site's address.
        ("LinkedIn username", "alex-example", False),
        ("LinkedIn", "", False),
    ],
)
def test_a_link_box_named_for_one_site_holds_that_site(label, value, wrong):
    assert wrong_site_link(box(label, value, "url")) is wrong
    if wrong:
        assert form().prefill_conflict(box(label, value, "url"))


@pytest.mark.parametrize(
    "label,value,degree",
    [
        ("Major", "Master of Science", True),
        ("Field of Study", "Bachelor of Technology", True),
        ("Major *", "BE", True),
        ("Major", "B.Tech", True),
        ("Major", "M.S.", True),
        ("Major", "MBA", True),
        ("Area of Study", "Master's in Computer Science", True),
        ("Major", "Data Science", False),
        ("Major", "Computer Science", False),
        ("Major", "Biomedical Engineering", False),
        ("Degree", "Master of Science", False),  # The degree box holds a degree.
        ("Major", "", False),
    ],
)
def test_a_major_is_a_subject_and_not_a_degree_name(label, value, degree):
    assert degree_as_major(box(label, value)) is degree
    assert implausible_prefill(box(label, value)) is degree


def test_the_resume_address_in_an_email_box_contradicts_the_profile():
    assert form().prefill_conflict(box("Email Address", "printed.on.resume@example.test"))
    assert form().prefill_conflict(box("Confirm Email", "printed.on.resume@example.test"))
    assert form().prefill_conflict(box("Contact", "printed.on.resume@example.test", "email"))


@pytest.mark.parametrize(
    "value",
    [
        "alexample.apply@gmail.com",
        "Alexample.Apply@gmail.com",
        "alexample.apply+icims@gmail.com",  # a platform plus-address of the same mailbox
        "alexample.apply@googlemail.com",
        "alex.ample.apply@gmail.com",  # Gmail ignores dots
    ],
)
def test_the_same_mailbox_under_another_spelling_is_not_a_contradiction(value):
    assert not form().prefill_conflict(box("Email Address", value))


@pytest.mark.parametrize("label", ["Reference Email", "Supervisor's email", "Manager Email", "Emergency Contact Email"])
def test_another_partys_email_box_is_never_the_applicants(label):
    assert not form().prefill_conflict(box(label, "someone.else@example.test", "email"))


def test_checkboxes_passwords_and_empty_boxes_never_conflict():
    assert not form().prefill_conflict(box("Email Address", "", "email"))
    assert not form().prefill_conflict(box("Password", "hunter2", "password"))
    assert not form().prefill_conflict(box("Email me updates", "true", "checkbox"))


def test_a_url_box_named_for_one_site_is_not_confirmed_by_another_saved_link(service):
    # The saved GitHub link "confirmed" the GitHub URL that a resume parse put in the LinkedIn box.
    resolver = Resolver(PROFILE, service.db, service.config)
    answer = resolver.local(box("LinkedIn", "https://github.com/alexexample", "url"))
    assert answer.value == PROFILE.linkedin and answer.evidence_ids == ["profile:linkedin"]
    answer = resolver.local(box("GitHub", "https://www.linkedin.com/in/alex-example", "url"))
    assert answer.value == PROFILE.github
    # A generic link box still takes the saved link its value matches.
    answer = resolver.local(box("Website", "https://github.com/alexexample", "url"))
    assert answer.value == PROFILE.github


def test_an_email_typed_box_with_an_unusual_label_is_the_profiles_address(service):
    resolver = Resolver(PROFILE, service.db, service.config)
    assert resolver.local(box("Contact email", "printed.on.resume@example.test", "email")).value == PROFILE.email
    assert resolver.local(box("Reference Email", "someone.else@example.test", "email")) is None
    assert resolver.local(box("Supervisor email", "someone.else@example.test", "email")) is None
