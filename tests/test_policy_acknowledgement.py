"""MassMutual 2026-10-14: an at-will acknowledgement follows the accept-all-terms policy, not the owner."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Answer, Profile

AT_WILL = (
    "13. I understand that employment with the Company is at will, for no fixed period of time. If hired, "
    "I will be free to resign at any time. Likewise, the Company will have the right to terminate my "
    "employment at any time with or without any reason or notice, except as specifically prohibited by law. "
    "No oral representation to the contrary has been made to me and I understand that no employee or "
    "representative of the company is authorized to make any such representation now or in the future."
)


def check(service, label, value, accept=True):
    r = Resolver(Profile(accept_all_application_terms=accept), service.db, service.config)
    field = {
        "id": "q",
        "label": label,
        "type": "select",
        "required": True,
        "options": ["Yes, I agree", "No, I do not consent"],
        "value": "",
        "valid": True,
    }
    facts, evidence, learned = r.answer_sources()
    proposal = Answer(
        field_id="q", value=value, disposition="answer", evidence_ids=["fact:application_consent_policy"]
    )
    return r._validate_answer(field, proposal, facts, evidence, learned)


def test_at_will_acknowledgement_is_accepted(service):
    assert check(service, AT_WILL, "Yes, I agree").disposition == "answer"


def test_refusal_or_no_policy_is_not_auto_accepted():
    from jobpilot.answers import policy_acknowledgement

    policy = {"fact:application_consent_policy": "accept all"}
    no = Answer(field_id="q", value="No, I do not consent", disposition="answer")
    yes = Answer(field_id="q", value="Yes, I agree", disposition="answer")
    assert policy_acknowledgement(AT_WILL, yes, policy, set())
    assert not policy_acknowledgement(AT_WILL, no, policy, set())
    assert not policy_acknowledgement(AT_WILL, yes, {}, set())


def test_acknowledgement_embedding_a_disclosure_still_needs_its_fact(service):
    label = "I certify that I have never been convicted of a felony."
    assert check(service, label, "Yes, I agree").disposition != "answer"


def test_scoped_batch_facts_still_honour_the_consent_policy(service):
    """AVEVA: the batch path's retrieval-scoped facts omitted the consent policy fact."""
    label = (
        "Yes, I agree that AVEVA may keep my data on file to be considered for other roles and may contact me "
        "at a later date. I can withdraw this consent at any time"
    )
    r = Resolver(Profile(accept_all_application_terms=True), service.db, service.config)
    field = {
        "id": "q",
        "label": label,
        "type": "dropdown",
        "required": True,
        "options": ["Yes", "No"],
        "value": "",
        "valid": True,
    }
    proposal = Answer(field_id="q", value="Yes", disposition="answer", evidence_ids=["fact:other"])
    assert r._validate_answer(field, proposal, {"fact:other": "x"}, {}, {}).disposition == "answer"


def test_citing_the_consent_policy_is_valid_with_scoped_facts(service):
    """AVEVA: the model cited fact:application_consent_policy, absent from retrieval-scoped facts."""
    label = (
        "Yes, I agree that AVEVA may keep my data on file to be considered for other roles and may contact me "
        "at a later date. I can withdraw this consent at any time"
    )
    r = Resolver(Profile(accept_all_application_terms=True), service.db, service.config)
    field = {
        "id": "q",
        "label": label,
        "type": "dropdown",
        "required": True,
        "options": ["Yes", "No"],
        "value": "",
        "valid": True,
    }
    proposal = Answer(
        field_id="q", value="Yes", disposition="answer", evidence_ids=["fact:application_consent_policy"]
    )
    assert r._validate_answer(field, proposal, {"fact:other": "x"}, {}, {}).disposition == "answer"
