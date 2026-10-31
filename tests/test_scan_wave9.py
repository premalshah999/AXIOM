"""Labels the scan misread (Wave 9), one synthetic page each."""

from jobpilot.forms import SCAN

BOSCH = """<form>
<label for=deg>Can you please confirm the current degree that you are obtaining?</label>
<select id=deg required><option>BA</option><option selected>Other</option></select>
<label for=o1>Other (free text)</label><input id=o1 type=text required>
<label for=cls>What is your current class standing?</label>
<select id=cls required><option>Senior</option><option selected>Other</option></select>
<label for=o2>Other (free text)</label><input id=o2 type=text required>
</form>"""


async def test_an_other_box_after_an_other_pick_is_that_questions_other(page):
    await page.set_content(BOSCH)
    data = await page.main_frame.evaluate(SCAN)
    labels = [f["label"] for f in data["fields"]]
    assert "Can you please confirm the current degree that you are obtaining? (Other, please specify)" in labels
    assert "What is your current class standing? (Other, please specify)" in labels
    assert "Other (free text)" not in labels


async def test_an_other_box_after_another_pick_keeps_its_label(page):
    await page.set_content(BOSCH.replace("<option selected>Other</option>", "<option>Other</option>"))
    data = await page.main_frame.evaluate(SCAN)
    assert [f["label"] for f in data["fields"]].count("Other (free text)") == 2


SAP = """<form>
<input type=checkbox name=data.news style="display:none"><input type=checkbox name=data.mkt style="display:none">
<div><input type=checkbox name=data.news id=n><label for=n>Receive new job posting notifications</label></div>
<div><input type=checkbox name=data.mkt id=m><label for=m>Hear more about career opportunities</label></div>
<input type=checkbox name=data.news style="display:none">
</form>"""


async def test_hidden_copies_of_a_checkbox_do_not_make_a_group(page):
    await page.set_content(SAP)
    data = await page.main_frame.evaluate(SCAN)
    got = {f["label"]: f["type"] for f in data["fields"]}
    assert got.get("Receive new job posting notifications") == "checkbox"
    assert not [f for f in data["fields"] if f["type"] == "checkboxgroup"]


PHONE = """<form>
<label for=p aria-label="Cell Number . Required. Format as xxx-xxx-xxxx">Cell Number</label>
<input id=p type=text required>
<label for=c>Country phone code</label><input id=c type=text value="+1">
</form>"""


async def test_a_phone_box_that_states_its_format_takes_it(page):
    await page.set_content(PHONE)
    data = await page.main_frame.evaluate(SCAN)
    phone = next(f for f in data["fields"] if f["label"] == "Cell Number")
    assert phone.get("phone_display") == "us-hyphenated"


BENEFITS = """<form>
<h3>Check all that apply</h3>
<p>I or a household member receive or received the following government benefits:</p>
<div><input type=checkbox id=a><label for=a>Food Stamps (SNAP) / AFDC / TANF</label></div>
<div><input type=checkbox id=b><label for=b>Not sure</label></div>
<div><input type=checkbox id=c><label for=c>None of the Above</label></div>
<p>I have:</p>
<div><input type=checkbox id=d><label for=d>Served in the US Military</label></div>
<div><input type=checkbox id=e><label for=e>None of the Above</label></div>
</form>"""


async def test_none_of_the_above_carries_its_list(page):
    await page.set_content(BENEFITS)
    data = await page.main_frame.evaluate(SCAN)
    nones = [f for f in data["fields"] if f["label"] == "None of the Above"]
    assert [f["list_items"] for f in nones] == [["Food Stamps (SNAP) / AFDC / TANF", "Not sure"], ["Served in the US Military"]]
    assert nones[0]["list_caption"] == "I or a household member receive or received the following government benefits:"
    assert nones[1]["list_caption"] == "I have:"


ADP = """<form>
<p>Please indicate which type of device you are using to apply.</p>
<sdf-select-simple aria-label="Add response."><sdf-select-item label="Computer/Laptop">Computer/Laptop</sdf-select-item>
<sdf-select-item label="Mobile Phone">Mobile Phone</sdf-select-item></sdf-select-simple>
</form>"""


async def test_an_add_response_prompt_is_not_the_question(page):
    await page.set_content(ADP)
    data = await page.main_frame.evaluate(SCAN)
    labels = [f["label"] for f in data["fields"]]
    assert "Please indicate which type of device you are using to apply." in labels
    assert "Add response." not in labels


WHATSAPP = """<form>
<label for=ph>Phone Number</label><input id=ph type=tel required>
<div><input type=radio name=whatsAppConsent id=w1><label for=w1>Yes - I consent to receiving WhatsApp messages</label>
<input type=radio name=whatsAppConsent id=w2><label for=w2>No - I do not consent to receiving WhatsApp messages</label></div>
</form>"""


async def test_a_whatsapp_consent_beside_the_phone_is_its_own_question(page):
    await page.set_content(WHATSAPP)
    data = await page.main_frame.evaluate(SCAN)
    labels = [f["label"] for f in data["fields"]]
    assert "I consent to receiving WhatsApp messages" in labels
    assert labels.count("Phone Number") == 1
