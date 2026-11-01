"""EXL's Oracle disability form (2026-10-29): after "Do you have a disability?" a bare "Category" combobox, named
US-STANDARD-ORA_DISABILITY_CATEGORY-STANDARD, offers "Not applicable" and "None". Its label has no demographic wording, so
the self-identification rules never saw it and the application stopped asking the owner. The field's own name gives the
topic, and the answer comes only from the applicant's disability fact."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """<main><label for=cat>Category</label>
<input id=cat name="US-STANDARD-ORA_DISABILITY_CATEGORY-STANDARD" role=combobox aria-autocomplete=list>
<label for=who>Category</label><input id=who name="job_category" role=combobox aria-autocomplete=list>
<label for=sig>Name</label><input id=sig name="eeoc_disability_signature-value" type=text></main>"""

OPTIONS = ["Not applicable", "None"]


def field(label, options=OPTIONS):
    return {"id": "q", "label": label, "type": "combobox", "required": True, "options": list(options), "value": "",
            "valid": False}


def answer(service, label, disability, options=OPTIONS):
    profile = Profile(name="Alex Example", facts={} if disability is None else {"disability": disability})
    return Resolver(profile, service.db, service.config).local(field(label, options))


async def test_a_bare_label_takes_the_topic_its_own_field_name_carries(page, tmp_path):
    await page.set_content(f"<html><body>{PAGE}</body></html>")
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a: None)
    await form.scan()
    labels = {f["html_id"]: f["label"] for f in form.fields.values()}
    assert labels["cat"] == "Disability category"
    assert labels["who"] == "Category"  # a name that does not say disability changes nothing
    assert labels["sig"] == "Name"  # a signature box is not a pick list


def test_none_is_chosen_from_the_applicants_own_no_disability_fact(service):
    assert answer(service, "Disability category", False).value == "None"


def test_a_yes_or_missing_fact_never_picks_none(service):
    got = answer(service, "Disability category", True)
    assert got is None or got.value not in {"None", "Not applicable"}
    missing = answer(service, "Disability category", None)
    assert missing is None or missing.evidence_ids[0] != "fact:disability"
