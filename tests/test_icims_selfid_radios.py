"""iCIMS's self-identification forms (icims2 servlet) give radios no label: ICE's veteran question and all three choices
read as the field name "icims_f_Veteran", and the run asked the applicant (2026-10-19). A readable value is the choice
and the name names the topic. Radios with labels keep them."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

SELF_ID = """<form><table><tr><td>
<input type="radio" name="icims_f_Veteran" value="ProtectedVeteran">
<input type="radio" name="icims_f_Veteran" value="NotProtectedVeteran">
<input type="radio" name="icims_f_Veteran" value="optout">
</td></tr></table>
<fieldset><legend>Are you over 18?</legend>
<label><input type="radio" name="adult" value="1">Yes</label><label><input type="radio" name="adult" value="0">No</label>
</fieldset></form>"""


async def test_unlabelled_self_id_radios_read_their_values(page, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(SELF_ID)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a, **k: None)
    obs = await form.scan()
    veteran = next(f for f in obs["fields"] if f.get("name") == "icims_f_Veteran")
    assert veteran["label"] == "Veteran status"
    assert veteran["options"] == [
        "I identify as one or more of the classifications of protected veteran",
        "I am not a protected veteran",
        "I don't wish to answer",
    ]
    adult = next(f for f in obs["fields"] if f.get("name") == "adult")
    assert adult["options"] == ["Yes", "No"]  # labelled radios keep their labels, never their values


async def test_an_unlabelled_self_id_radio_is_selected_by_its_value(page, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(SELF_ID)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a, **k: None)
    obs = await form.scan()
    veteran = next(f for f in obs["fields"] if f.get("name") == "icims_f_Veteran")
    await form.fill(veteran, "I am not a protected veteran")
    assert await page.evaluate(
        "document.querySelector('input[name=icims_f_Veteran]:checked')?.value"
    ) == "NotProtectedVeteran"  # ICE's own code, as a person submitted it at REI Systems


FORMS_MODULE = """<form><table>
<tr><td>Are you age 18 or over? <input type="radio" name="icims_f_Over_18" id="icims_f_Over_18_Yes" value="Yes"> Yes
<input type="radio" name="icims_f_Over_18" id="icims_f_Over_18_No" value="No"> No</td></tr>
<tr><td>Yes <input type="radio" name="icims_f_Work_In_US" id="icims_f_Work_In_US_Yes" value="Yes">
Yes <input type="radio" name="icims_f_Work_In_US" id="icims_f_Work_In_US_No" value="No"></td></tr>
</table></form>"""


async def test_forms_module_radios_read_their_values_when_captions_repeat(page, tmp_path):
    """Cooper Health's employment application (2026-10-22) read [question, "Yes"] and ["Yes", "Yes"]."""
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(FORMS_MODULE)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a, **k: None)
    obs = await form.scan()
    for name in ("icims_f_Over_18", "icims_f_Work_In_US"):
        radio = next(f for f in obs["fields"] if f.get("name") == name)
        assert radio["options"] == ["Yes", "No"], (name, radio["options"])
    work = next(f for f in obs["fields"] if f.get("name") == "icims_f_Work_In_US")
    await form.fill(work, "No")
    assert await page.evaluate("document.querySelector('input[name=icims_f_Work_In_US]:checked')?.value") == "No"
