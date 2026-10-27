"""Amazon (2026-10-27): "Are you currently working as an intern at Amazon?" had no subject and the model proposed
"Yes"; the applicant has never worked there."""

from jobpilot.answers import Resolver
from jobpilot.knowledge import topic
from jobpilot.schemas import Profile


def test_currently_working_at_this_employer_is_the_prior_employment_fact(service):
    label = "Are you currently working as an intern at Amazon?"
    assert topic(label) == "prior_employment"
    r = Resolver(Profile(first_name="Alex", facts={"previously_employed_here": False}), service.db, service.config,
                 job={"ats": "custom", "url": "https://www.amazon.jobs/x", "company": "Amazon"})
    a = r.local({"id": "q", "label": label, "type": "radio", "options": ["Yes", "No"], "required": True, "value": "",
                 "valid": True})
    assert a is not None and a.value == "No"
