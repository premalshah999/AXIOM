"""Owner 2026-11-01: tick boxes reached Telegram with nothing to tap, upload-widget text sat in question captions and a
video URL was an ordinary answer box. Synthetic data only."""

from jobpilot import owner_questions as oq


def test_a_lone_tick_box_without_options_is_a_yes_no_choice():
    ctx = {"type": "checkbox", "group": "Preferred shift (Mark all that apply)"}
    assert oq.tick_options([], ctx) == ["Yes", "No"]
    assert oq.kind("Third Shift", [], ctx) == "choice"
    assert not oq.free_text_allowed([], ctx)
    assert oq.display("Third Shift", ctx) == "Preferred shift (Mark all that apply) › Third Shift"
    assert oq.present({"question": "Third Shift", "options": [], "context": ctx})["options"] == ["Yes", "No"]


def test_a_certification_tick_shows_its_statement():
    ctx = {"type": "checkbox", "caption": "APPLICATION CERTIFICATION STATEMENT AND ACKNOWLEDGEMENT"}
    assert oq.display("Tick", ctx).startswith("APPLICATION CERTIFICATION")


def test_upload_widget_captions_are_not_part_of_a_question():
    ctx = {"type": "text", "section": "Profile", "caption": "or drag and drop here", "heading": "Profile"}
    assert oq.display("Supervisor Name & Title:", ctx) == "Supervisor Name & Title:"
    assert "caption" not in oq.context_from({"context": {"caption": "required field"}})


def test_a_video_link_is_a_job_the_app_cannot_finish():
    text, hard = oq.cannot_automate("Apex Video URL", [], {"type": "text"})
    assert hard and "video" in text


def test_an_employer_attribute_without_its_row_is_a_misread():
    assert oq.defect("Employer Phone Number", [], {"date_parts": []})
    assert not oq.defect("Employer Phone Number", [], {"history_kind": "work", "history_identity": {"company": "Acme"}})
