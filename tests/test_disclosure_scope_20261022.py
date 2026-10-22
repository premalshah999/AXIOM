"""Two answer-scope refusals on 2026-10-22. BNY's "regulatory charges ... license revoked ... sanction arising out of any
banking, securities or investment activities" was filed as export control, so the applicant's no-FINRA-licence and
no-conviction facts were refused. CDW's ITAR notice ended "By typing my name below, I acknowledge that I have read the
following statement" and went to the owner as an export-control disclosure."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.knowledge import relevant_fact, topic
from jobpilot.schemas import Answer, Profile

BNY = (
    "The following question is in relation ONLY to financial regulatory agencies, banking securities or investment "
    "activities: Have you ever, or at this time are any administrative or regulatory charges pending against you; been "
    "fined, or a had a license revoked; subject to order, judgement, decree or sanction arising out of any banking, "
    "securities or investment activities?"
)
CDW = (
    "U.S. export control laws, including the International Traffic in Arms Regulations (ITAR), require CDW to perform an "
    "export compliance assessment for certain positions at CDW that involve access to export-controlled items. If your "
    "role requires it , you will be asked to provide information and/or documentation after hire regarding your status "
    "as a U.S. Person as defined under ITAR (U.S. citizen, U.S. national, U.S. permanent resident (green card holder), "
    "refugee, or asylee). B y typing my name below, I acknowledge that I have read the following statement:"
)


def test_a_financial_regulatory_question_is_scoped_to_licence_and_conviction_facts():
    assert topic(BNY) == "financial_regulatory"
    assert relevant_fact(BNY, "has_finra_licenses") and relevant_fact(BNY, "criminal_conviction")
    assert not relevant_fact(BNY, "citizenship")
    for question in ("Are you a national of a country subject to U.S. sanctions?", "Are you on any OFAC sanctions list?"):
        assert topic(question) == "export_control"


@pytest.mark.parametrize(
    "label,signed",
    [
        (CDW, True),
        ("By typing my name below, I certify that I am authorized to work in the United States.", False),
    ],
)
def test_a_signature_that_only_acknowledges_reading_a_notice_is_signed(service, label, signed):
    profile = Profile(first_name="Alex", last_name="Rivera", name="Alex Rivera", accept_all_application_terms=True)
    resolver = Resolver(profile, service.db, service.config, job={"ats": "workday", "url": "https://example.test/j"})
    field = {"id": "f", "label": label, "type": "text", "options": []}
    assert resolver.signature_ok(field, Answer(field_id="f", value="Alex Rivera", disposition="answer")) is signed
