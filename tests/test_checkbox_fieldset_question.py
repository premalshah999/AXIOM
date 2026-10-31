"""Credence (Workable, 2026-10-26): "Please indicate which active clearances you have" offered eleven checkboxes in one
fieldset. Each read as its own required item (filled false, "Required field incomplete" x11), so the run stalled on
the one choice that matches the applicant. A fieldset whose legend asks a question owns its boxes as alternatives;
consents and Yes/No pairs keep reading as separate boxes."""

from jobpilot.forms import SCAN


def box(i, text):
    return f'<label><input type="checkbox" id="b{i}" required> {text}</label>'


def fieldset(legend, *options):
    return f"<fieldset><legend>{legend} *</legend>{''.join(box(i, t) for i, t in enumerate(options))}</fieldset>"


async def scan(page, html):
    await page.set_content(f"<form>{html}</form>")
    return (await page.main_frame.evaluate(SCAN))["fields"]


async def test_a_fieldset_that_asks_a_question_is_one_choice_group(page):
    fields = await scan(page, fieldset(
        "Please indicate which active clearances you have", "Confidential", "Secret", "Top Secret",
        "I currently do not have an active security clearance"))
    groups = [f for f in fields if f["type"] == "checkboxgroup"]
    assert len(groups) == 1 and not [f for f in fields if f["type"] == "checkbox"]
    assert groups[0]["label"].startswith("Please indicate which active clearances")
    assert groups[0]["options"][-1] == "I currently do not have an active security clearance"
    assert groups[0]["required"] and not groups[0]["valid"]
    await page.check("#b3")
    group = next(f for f in (await page.main_frame.evaluate(SCAN))["fields"] if f["type"] == "checkboxgroup")
    assert group["value"] == "I currently do not have an active security clearance" and group["valid"]


async def test_consents_and_yes_no_boxes_stay_separate(page):
    consents = await scan(page, fieldset(
        "Which agreements apply to you?", "I agree to the Privacy Statement", "I consent to receive text messages"))
    assert [f["type"] for f in consents if f["type"].startswith("checkbox")] == ["checkbox", "checkbox"]
    yes_no = await scan(page, fieldset("Are you legally authorized to work in the United States?", "Yes", "No"))
    assert not [f for f in yes_no if f["type"] == "checkboxgroup"]
