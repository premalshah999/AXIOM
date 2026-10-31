"""A question whose wording the engine never read is not put to the owner, and what no answer can complete is marked.

Caesars (2026-10-26): "three required Yes/No questions whose text wasn't captured" was answered "Yes", and that one Yes
went into three radio groups. DESRI "field-59", Adzuna "What?"/"Where?": the same. Synthetic pages and wording only."""

import pytest

from jobpilot import owner_questions as oq
from jobpilot.db import Review, Run
from jobpilot.forms import name_borrowed_consent
from jobpilot.repair_agent import OwnerQuestion, RepairPlan, mask, repair_run
from test_repair_agent import seed


@pytest.mark.parametrize("question", [
    "The screening page asks three required Yes/No questions whose text wasn't captured. What is the wording?",
    "What should be entered in the required free-text field (field-59) on the application form?",
    "The form asks 'What?' and 'Where?' and a 'Yes, I certify' checkbox: what should be entered?",
    "The required radio group is mislabeled 'Phone Number'; what is it?",
])
def test_a_question_that_names_no_real_question_is_unread(question):
    assert oq.unread(question)
    assert oq.defect(question, ["Yes", "No"], {"source": "repair"})


@pytest.mark.parametrize("question", [
    "Do you consent to receiving WhatsApp messages?",
    "What is your current contract end date (enter as MM/DD/YYYY)?",
    "Which field of study did you complete?",
])
def test_a_readable_question_is_asked(question):
    assert not oq.unread(question)


def test_a_reason_that_admits_the_text_is_missing_makes_a_bare_label_unread():
    assert oq.defect("Questionnaire", ["Yes", "No"], {}, "The questionnaire question text is unavailable, so ...")


async def test_a_blind_repair_question_is_not_sent_and_the_page_is_read_again(service, tmp_path, monkeypatch):
    run_id, job_id, _ = seed(service, tmp_path)
    plan = RepairPlan(diagnosis="A required textarea has no label.", category="missing_fact", owner_question=OwnerQuestion(
        question="What should be entered in the required free-text field (field-59)?"))

    async def fake_structured(*args, **kwargs):
        return plan

    monkeypatch.setattr("jobpilot.models.structured", fake_structured)
    summary = await repair_run(service, run_id)
    assert summary["outcome"] == "unread_field"
    with service.db.session() as s:
        assert s.query(Review).filter(Review.run_id == run_id).count() == 0
        assert s.get(Run, run_id).state == "waiting_browser"


def test_a_date_answer_is_not_masked_like_a_phone_number():
    """The agent read "9999-99-99" for "2026-05-13" and called it a placeholder the app typed (OmegaHires 2026-10-27)."""
    assert mask("answer: 2026-05-13 and 05/13/2026") == "answer: 2026-05-13 and 05/13/2026"
    assert mask("call (202) 555-0100 or 202-555-0100") == "call (999) 999-9999 or 999-999-9999"


def test_a_consent_group_beside_a_phone_box_is_not_named_phone_number():
    fields = [
        {"label": "Phone Number", "type": "tel", "options": []},
        {"label": "Phone Number", "type": "radio",
         "options": ["Yes - I consent to receiving WhatsApp messages", "No - I do not consent to receiving WhatsApp messages"]},
    ]
    name_borrowed_consent(fields)
    assert fields[0]["label"] == "Phone Number" and fields[1]["label"] == "I consent to receiving WhatsApp messages"
    alone = [{"label": "Gender", "type": "radio", "options": ["Yes - I agree", "No - I do not agree"]}]
    name_borrowed_consent(alone)
    assert alone[0]["label"] == "Gender"  # no text box shares its label: nothing was borrowed


@pytest.mark.parametrize("question, kind_, hard", [
    ("Please provide a brief video file about the most impactful thing you have worked on", "text", True),
    ("For your application, please upload a self recorded video explaining a project", "upload", True),
    ("Record a video describing why you're the best person. Include a link in your answer.", "text", True),
    ("Apex Video URL", "text", False),  # a link to a video the owner already has can be typed
    ("Please share a recent writing sample from the past year", "upload", False),
    ("Portfolio - File upload", "upload", False),
])
def test_what_no_answer_completes_is_marked(question, kind_, hard):
    context = {"type": "file"} if kind_ == "upload" else {}
    reason, is_hard = oq.cannot_automate(question, [], context, "")
    if question == "Apex Video URL":
        assert reason == ""
        return
    assert reason and is_hard is hard
    shown = oq.present({"id": "r1", "question": question, "options": [], "context": context, "reason": ""})
    assert shown["cannot_automate"] == reason
    assert shown["kind"] == ("cannot_automate" if hard else "upload")


def test_a_polar_question_about_video_interviews_is_still_a_question():
    reason, hard = oq.cannot_automate("Are you comfortable with a recorded video interview?", ["Yes", "No"], {}, "")
    assert reason == "" and not hard
    assert oq.present({"id": "r2", "question": "Do you have a driver's licence?", "options": ["Yes", "No"],
                       "context": {}, "reason": ""})["cannot_automate"] == ""
