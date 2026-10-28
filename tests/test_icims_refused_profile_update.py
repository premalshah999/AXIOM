"""A signed-in iCIMS profile editor (Log Out, Update Profile, and the profile's own Login/Password boxes) answered
Update Profile with "An unexpected server error occurred". With the error on the page it was no longer read as the
signed-in editor: its two password boxes made it account creation, and the run ended "Could not sign in (Signing in
did not succeed)" (Emory, 2026-10-26..27). A refused save on a signed-in session stays a form."""

from jobpilot.adapters.base import Step
from support_icims import adapter

PROFILE = "https://careers-acme.icims.com/jobs/42/data-analyst/candidate?csrf=1&hashed=1"


def editor(errors=(), save="Update Profile"):
    fields = [
        {"id": "login", "type": "text", "label": "Login", "required": True, "icims_name": "PersonProfileFields.Login"},
        {"id": "pw", "type": "password", "label": "Password", "required": True,
         "icims_name": "PersonProfileFields.Password"},
        {"id": "pw2", "type": "password", "label": "Password (Re-enter)", "required": True,
         "icims_name": "PersonProfileFields.Password_Confirm"},
        {"id": "first", "type": "text", "label": "Legal First Name", "required": True,
         "icims_name": "PersonProfileFields.FirstName"},
    ]
    for f in fields:
        f.update(value="", error="", automation_id="", valid=True)
    controls = [{"id": "logout", "label": "Log Out", "href": ""}, {"id": "save", "label": save, "href": ""}]
    return {"url": PROFILE, "fields": fields, "controls": controls, "errors": list(errors),
            "headings": ["Candidate Profile", "Create a login"], "text": "Candidate Profile"}


SERVER_ERROR = "Error: An unexpected server error occurred. Please try again later."


async def test_the_signed_in_editor_is_a_form(service):
    assert adapter(service, editor()).classify(editor()) == Step.FORM


async def test_a_refused_save_keeps_the_signed_in_editor_a_form(service):
    refused = editor([SERVER_ERROR])
    value = adapter(service, refused)
    assert value.employer_profile_editor(refused)
    assert value.classify(refused) == Step.FORM


async def test_a_refused_save_is_still_no_proof_of_a_session_for_sign_in(service):
    # auth_outcome and the returning route still need an error-free signed-in page.
    assert not adapter(service, editor()).employer_session(editor([SERVER_ERROR]))


async def test_a_registration_profile_is_still_account_creation(service):
    # Submit/Create Profile registers the candidate; it never becomes the signed-in editor.
    page = editor([SERVER_ERROR], save="Submit Profile")
    assert adapter(service, page).classify(page) == Step.CREATE_ACCOUNT
