"""Fixes from the 2026-10-24 review of the night's stall fixes: each guards a way the new code could submit, lose a
receipt, misread a page or ask the model twice. All pages here are synthetic."""

from unittest.mock import AsyncMock

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Answer, Profile


def session(page, service, tmp_path, profile=None):
    resolver = Resolver(profile or Profile(), service.db, service.config, job={"ats": "custom", "url": "https://x.test"})
    return FormSession(page, resolver, tmp_path / "unused.pdf", lambda *a: None)


async def field_named(form, text):
    return next(f for f in (await form.scan())["fields"] if text in f["label"])


async def test_a_native_date_records_the_day_it_typed(page, service, tmp_path):
    await page.set_content("<form><label for=d>Start date</label><input id=d type=date></form>")
    form = session(page, service, tmp_path, Profile.model_validate({"answer_rules": {"history_day": "first"}}))
    assert await form.fill(await field_named(form, "Start date"), "2026-05") == "2026-05-01"
    assert await page.locator("#d").input_value() == "2026-05-01"


DIALOG = """<div role=dialog aria-modal=true><h2>{title}</h2><p>{text}</p><button id=ok>Okay</button></div>
<form><label for=q>Question</label><input id=q></form>"""


async def test_an_acknowledgement_is_never_clicked_after_the_submit(page, service, tmp_path):
    await page.set_content(DIALOG.format(title="Quick tip before you begin", text="Not sure? Select Yes."))
    await page.evaluate("() => { window.__jpArmed = true }")
    await page.evaluate("() => { document.getElementById('ok').addEventListener('click', () => { window.clicked = true }) }")
    await session(page, service, tmp_path).scan()
    assert not await page.evaluate("Boolean(window.clicked)")


async def test_a_thank_you_or_cookie_dialog_is_not_an_acknowledgement(page, service, tmp_path):
    for title, text in [("Thank you for applying!", "We will be in touch."),
                        ("Cookies", "By continuing you accept our use of cookies.")]:
        await page.set_content(DIALOG.format(title=title, text=text))
        await page.evaluate("() => { document.getElementById('ok').addEventListener('click', () => { window.clicked = true }) }")
        await session(page, service, tmp_path).scan()
        assert not await page.evaluate("Boolean(window.clicked)"), title


async def test_a_talent_community_with_interest_checkboxes_is_still_a_signup(page, service, tmp_path):
    boxes = "".join(f"<label><input type=checkbox name=c{i}> Area {i}</label>" for i in range(10))
    await page.set_content(f"""<header><button type=button>Apply</button></header>
      <form><h3>Join our Talent Community</h3><input name=first><input name=email><input name=phone>{boxes}
      <button type=submit id=go>Submit</button></form>""")
    form = session(page, service, tmp_path)
    await form.scan()
    final = await form.application_submit_controls()
    assert await form.signup_form(final[0]["id"]) is True


async def test_a_day_month_year_datepicker_keeps_its_day(page, service, tmp_path):
    await page.set_content("<form><label for=d>Start date (dd/mm/yyyy)</label><input id=d class=datepicker></form>")
    field = await field_named(session(page, service, tmp_path), "Start date")
    assert field["widget"] == "date-text" and field["date_format"] != "month-year"


async def test_icims_self_id_radios_keep_their_topic_and_a_marker_does_not_hide_the_question(page, service, tmp_path):
    await page.set_content("""<form><p>Please check one of the boxes below:</p>
      <input type=radio name=icims_f_Veteran id=v1 value=NotProtectedVeteran><input type=radio name=icims_f_Veteran
      id=v2 value=optout>
      <p>Are you a resident of the State of Texas?</p><span>*</span>
      <table><tr><td><input type=radio name="ctl00$Main$q1" id=r1><label for=r1>Yes</label></td></tr>
      <tr><td><input type=radio name="ctl00$Main$q1" id=r2><label for=r2>No</label></td></tr></table></form>""")
    labels = [f["label"] for f in (await session(page, service, tmp_path).scan())["fields"] if f["type"] == "radio"]
    assert "Veteran status" in labels and "Are you a resident of the State of Texas?" in labels


async def test_an_upload_in_an_unlabelled_inner_group_takes_its_sections_name(page, service, tmp_path):
    section = """<div role=group aria-labelledby="{key}h"><h4 id="{key}h">{title}</h4><div role=group>
      <label>Upload a file (5MB max)</label><input type=file data-slot="{key}"></div></div>"""
    await page.set_content("<form>" + section.format(key="r", title="Resume/CV") +
                           section.format(key="a", title="Additional Attachments") + "</form>")
    form = session(page, service, tmp_path)
    await form.scan()
    chosen = form.resume_fields()
    assert len(chosen) == 1 and await form.locator(chosen[0]["id"]).evaluate("e => e.dataset.slot") == "r"


def test_completion_dates_are_the_highest_degrees_and_never_a_certifications(service):
    profile = Profile.model_validate({"education": [
        {"school": "Institute of Technology", "degree": "BE", "end": "2024-06"},
        {"school": "State University", "degree": "Master of Science", "end": "2026-05"}]})
    resolver = Resolver(profile, service.db, service.config)

    def ask(label):
        return resolver.local({"id": "d", "label": label, "type": "text", "required": True, "value": "", "valid": True,
                               "group": "", "section": "", "options": []})

    highest = ask("Completion date of your highest degree (mm/yyyy)")
    assert highest.value == "05/2026" and highest.evidence_ids == ["profile:education.1.end"]
    certification = ask("Certification / Training - Date of completion")
    assert certification is None or certification.evidence_ids != ["profile:education.0.end"]


async def test_a_stale_inline_choice_asks_the_model_once(page, service, tmp_path):
    await page.set_content("""<form><p>Do you need sponsorship?</p>
      <input type=radio name=s id=y value=Yes><label for=y>Yes</label>
      <input type=radio name=s id=n value=No><label for=n>No</label></form>""")
    form = session(page, service, tmp_path)
    field = await field_named(form, "sponsorship")
    form.resolver.resolve = AsyncMock(return_value=[Answer(field_id=field["id"], value="No", disposition="answer")])
    await form.control_action("choose", field["id"], form.revision, option_id="gone")
    assert form.resolver.resolve.await_count == 1


async def test_a_control_rerendered_after_the_scan_is_stale_quickly_not_a_crash(page, service, tmp_path):
    import time

    import pytest

    await page.set_content('<main><a href="#apply" id=apply role=button>Apply Now</a></main>')
    form = session(page, service, tmp_path)
    obs = await form.scan()
    button = next(c for c in obs["controls"] if c["label"] == "Apply Now")
    await page.evaluate("() => document.getElementById('apply').remove()")
    started = time.monotonic()
    with pytest.raises(ValueError, match="stale"):
        await form.click(button["id"], step_control=True)
    assert time.monotonic() - started < 10
