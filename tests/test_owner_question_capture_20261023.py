"""Screenshots sent with owner questions, and documents the owner sent (owner 2026-10-23).

"the screenshots don't show the right questions ... cannot even get the intent of the field, i.e. if it is uploading
something". Production help.jpg files sent with "End date month" and "Please upload your unofficial transcript."
showed other parts of the form: the capture scrolled only "if needed" (a control already at the viewport's edge, or a
hidden file input, stayed out of sight) and sent the whole viewport for every question of the run. Each asked question
now gets its own crop, centred on its label and control and outlined; a saved document is attached to the next form
asking for that kind of document.
"""

from jobpilot import owner_questions as oq
from jobpilot.answers import Resolver
from jobpilot.diagnostics import capture_help, validated_help_bytes
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

FILLER = "".join(f"<p style='height:60px'>Section text {n}</p>" for n in range(20))
PAGE = (
    "<form>"
    + FILLER
    + "<div class=field><label for=month>End date month</label>"
    "<select id=month required><option value=''>Select...</option><option>May</option><option>June</option></select>"
    "</div>"
    + FILLER
    + "<div class=field><label for=doc>Please upload your unofficial transcript.</label>"
    "<input id=doc type=file required style='display:none'><button type=button>Attach</button></div>"
    + FILLER
    + "</form>"
)


def jpeg_size(image):
    """(width, height) from a JPEG's start-of-frame segment."""
    at = 2
    while at < len(image):
        marker, length = image[at + 1], int.from_bytes(image[at + 2 : at + 4], "big")
        if marker in (0xC0, 0xC2):
            return int.from_bytes(image[at + 7 : at + 9], "big"), int.from_bytes(image[at + 5 : at + 7], "big")
        at += 2 + length
    raise ValueError("no frame")


def session(page, service, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    return FormSession(page, Resolver(Profile(), service.db, service.config), resume, lambda *a, **k: None)


async def test_each_asked_question_gets_its_own_centred_crop(page, service, tmp_path):
    await page.set_viewport_size({"width": 1000, "height": 700})
    await page.set_content(PAGE)
    form = session(page, service, tmp_path)
    await form.scan()
    month = next(f for f in form.fields.values() if f["label"].startswith("End date month"))
    upload = next(f for f in form.fields.values() if f["type"] == "file")
    reviews = [
        {"question": month["label"], "key": "k-month", "field_id": month["id"], "blocker_type": "missing_fact"},
        {"question": upload["label"], "key": "k-doc", "field_id": upload["id"], "blocker_type": "missing_fact"},
    ]
    folder = tmp_path / "run"
    metadata = await capture_help(
        form, folder, run_id="r", job_id="j", result={"state": "waiting_answer", "reviews": reviews}
    )
    assert metadata and [c["file"] for c in metadata["crops"]] == ["help-q1.jpg", "help-q2.jpg"]
    expected = dict(run_id="r", job_id="j", state="waiting_answer", expected=metadata)
    for number, item in enumerate(reviews, 1):
        crop = validated_help_bytes(
            folder, question=item["question"], review_key=item["key"], field_id=item["field_id"], **expected
        )
        assert crop == (folder / f"help-q{number}.jpg").read_bytes()
        width, height = jpeg_size(crop)
        assert height < 700 and width <= 1000  # The question's box, not the whole viewport.
    viewport = (folder / "help.jpg").read_bytes()
    assert jpeg_size(viewport) == (1000, 700)
    # A question the capture never centred on gets no picture rather than another question's.
    assert validated_help_bytes(folder, question="What is your country of birth?", **expected) is None


async def test_a_document_the_owner_sent_is_attached_to_the_next_form_asking_for_it(page, service, tmp_path):
    oq.save_document(
        service.db, service.config.data_dir, "Please upload your unofficial transcript.", "transcript.pdf",
        b"%PDF-1.4 synthetic transcript",
    )
    await page.set_content(
        "<label for=t>Academic transcript</label><input id=t type=file required>"
        "<label for=w>Writing sample</label><input id=w type=file required>"
    )
    form = session(page, service, tmp_path)
    await form.scan()
    assert await form.attach_owner_documents() is True
    names = await page.evaluate("[...document.querySelectorAll('input[type=file]')].map(e => e.files[0]?.name || '')")
    assert names == ["transcript.pdf", ""]  # No writing sample was sent; that field is still the owner's question.
