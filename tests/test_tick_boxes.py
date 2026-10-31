"""Tick boxes reach the owner as something they can answer (owner 2026-10-31: "they'd come to me on telegram in a helpless
state"). Smithfield asked "None of the Above" and National Louis asked "Tick", each with nothing to tap."""

from types import SimpleNamespace

import pytest

from jobpilot import owner_questions as oq
from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile
from jobpilot.telegram import question_payload
from jobpilot.widgets import checkbox_value

BESIDE = """<form>
<input type=checkbox id=ack required><label for=ack style="display:none">Tick</label>
<span>I acknowledge that I have read and understand the above statements and hereby grant permission to</span>
<p>confirm the information supplied on this application by me.</p>
<label for=first>First name</label><input id=first>
</form>"""
NOTHING_BESIDE = """<form><input type=checkbox id=a><label for=a>Tick</label><input id=b aria-label="Other"></form>"""
GROUP = """<form><p>Do any of the following apply to you?</p>
<label><input type=checkbox id=x> Food assistance</label><label><input type=checkbox id=y> Served in the military</label>
<label><input type=checkbox id=z required> None of the Above</label></form>"""


def session(page, service, tmp_path, profile=None):
    return FormSession(
        page, Resolver(profile or Profile(), service.db, service.config), tmp_path / "r.pdf", lambda *a, **k: None
    )


async def test_a_generic_label_reads_the_words_printed_beside_the_box(page, service, tmp_path):
    await page.set_content(BESIDE)
    fields = (await session(page, service, tmp_path).scan())["fields"]
    box = next(f for f in fields if f["type"] == "checkbox")
    assert box["label"].startswith("I acknowledge that I have read and understand")
    assert box["label"].endswith("on this application by me.")
    assert "First name" not in box["label"]


async def test_with_nothing_beside_it_the_label_stays(page, service, tmp_path):
    await page.set_content(NOTHING_BESIDE)
    fields = (await session(page, service, tmp_path).scan())["fields"]
    assert next(f for f in fields if f["type"] == "checkbox")["label"] == "Tick"


async def test_a_lone_box_is_asked_with_tap_answers_and_its_neighbours(page, service, tmp_path):
    await page.set_content(GROUP)
    form = session(page, service, tmp_path)
    fields = (await form.scan())["fields"]
    box = next(f for f in fields if f["label"] == "None of the Above")
    form._pending(box, "Missing fact")
    await form.enrich_pending()
    item = form.pending[0]
    assert item["options"] == ["Yes", "No"]

    context = oq.context_from(item)
    assert context["caption"] == "Do any of the following apply to you?"
    assert "Food assistance" in context["peers"] and "Served in the military" in context["peers"]
    assert oq.defect(item["question"], item["options"], context, item["reason"]) is None
    assert oq.kind(item["question"], item["options"], context) == "choice"

    line = oq.requirement(item["question"], item["options"], context)
    assert line.startswith("Tick box: Yes ticks it, No leaves it empty")
    assert "Food assistance" in line


def test_telegram_shows_buttons_and_the_whole_question():
    service = SimpleNamespace(config=SimpleNamespace(telegram_user_id=1))
    context = {"type": "checkbox", "caption": "Do any of the following apply to you?", "peers": "Food assistance | Military"}
    payload = question_payload(service, 7, "None of the Above", ["Yes", "No"], context, "", "Example Foods", "Analyst")
    buttons = [row[0]["text"] for row in payload["reply_markup"]["inline_keyboard"]]
    assert buttons == ["1. Yes", "2. No"]
    assert "Do any of the following apply to you? › None of the Above" in payload["text"]
    assert "Food assistance, Military" in payload["text"]
    assert "Reply to this message" not in payload["text"]


@pytest.mark.parametrize("reply,state", [("Yes", "true"), ("No", "false")])
def test_a_tapped_answer_sets_the_box(reply, state):
    assert checkbox_value(reply) == state


@pytest.mark.parametrize(
    "label",
    [
        "I acknowledge that I have read and understand the above statements and hereby grant permission to confirm the "
        "information supplied on this application by me.",
    ],
)
def test_an_acknowledgement_read_from_beside_the_box_needs_no_question(service, label):
    profile = Profile(accept_all_application_terms=True)
    answer = Resolver(profile, service.db, service.config).local(
        {"id": "q", "label": label, "type": "checkbox", "required": True, "options": [], "value": "", "valid": True}
    )
    assert answer is not None and answer.value == "true"


async def test_a_generic_aria_label_reads_the_words_printed_beside_the_box(page, service, tmp_path):
    await page.set_content(
        """<form><input type=checkbox id=ack required aria-label="Tick">
        <span>I acknowledge that I have read and understand the above statements.</span>
        <label for=first>First name</label><input id=first></form>"""
    )
    fields = (await session(page, service, tmp_path).scan())["fields"]
    assert next(f for f in fields if f["type"] == "checkbox")["label"].startswith("I acknowledge that I have read")


def test_a_signature_box_under_the_terms_policy_is_ticked(service):
    answer = Resolver(Profile(accept_all_application_terms=True), service.db, service.config).local(
        {"id": "q", "label": "Signature", "type": "checkbox", "required": True, "options": [], "value": "", "valid": True,
         "hint": "(checking the checkbox above is equivalent to a handwritten signature)"}
    )
    assert answer is not None and answer.value == "true"


@pytest.mark.parametrize("label", ["Are you a current employee of Example Foods?", "Are you a former employee of Example Foods?"])
def test_a_current_or_former_employee_question_follows_the_saved_no(service, label):
    profile = Profile(facts={"previously_employed_here": False})
    answer = Resolver(profile, service.db, service.config).local(
        {"id": "q", "label": label, "type": "radio", "required": True, "options": ["Yes", "No"], "value": "", "valid": True}
    )
    assert answer is not None and answer.value == "No"


def test_a_recorded_employer_is_not_answered_no(service):
    profile = Profile(facts={"previously_employed_here": False}, work=[{"company": "Example Foods", "title": "Analyst"}])
    answer = Resolver(profile, service.db, service.config).local(
        {"id": "q", "label": "Are you a former employee of Example Foods?", "type": "radio", "required": True,
         "options": ["Yes", "No"], "value": "", "valid": True}
    )
    assert answer is None or answer.value != "No"

