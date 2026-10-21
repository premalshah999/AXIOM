"""SuccessFactors create-account pages (NetApp, Entergy, L3Harris, Farmers 2026-10-21): "Data Privacy
Acknowledgement *" is a link that opens the employer's privacy notice. Create Account answered "Please open the data
privacy agreement to accept the terms" until its Acknowledge button stored the statement id, and 30 jobs ended
"Could not sign in"."""

from types import SimpleNamespace

from jobpilot.browser import BrowserEngine
from jobpilot.privacy import acknowledge_privacy_notice

# The observed markup: the link validates the country, then opens an SAP Fiori dialog.
PAGE = """
<select id=fbclc_country><option value="">- Select -</option><option value=US>United States</option></select>
<a id=dataPrivacyId role=button onclick="if (!document.getElementById('fbclc_country').value) return false;
   document.getElementById('dlg').className='dialogBoxWrapper fd-dialog fd-dialog--active';
   document.getElementById('dlg').style.display='block'; return false;">Please read our Candidate Privacy Notice</a>
<input type=hidden id=fbclc_dpcsId name=fbclc_dpcsId value="">
<div id=dlg class="dialogBoxWrapper fd-dialog" style="display:none">NetApp Candidate Privacy Notice
  <button id="dlgButton_21:" onclick="document.getElementById('fbclc_dpcsId').value='141';
     document.getElementById('dlg').style.display='none'">Acknowledge</button>
  <button id="dlgButton_22:" onclick="window.declined=true">Decline</button>
  <button id="dlgButton_23:">Print</button>
</div>
<button id=fbclc_createAccountButton>Create Account</button>
"""


async def test_the_notice_is_opened_and_acknowledged_after_the_country_is_chosen(page):
    await page.set_content(PAGE)
    await page.select_option("#fbclc_country", "US")
    assert await acknowledge_privacy_notice(page, authorized=True)
    assert await page.input_value("#fbclc_dpcsId") == "141"
    assert not await page.evaluate("!!window.declined")
    # An acknowledged notice is left alone on the next attempt.
    assert not await acknowledge_privacy_notice(page, authorized=True)


async def test_without_saved_consent_or_a_notice_nothing_is_clicked(page):
    await page.set_content(PAGE)
    await page.select_option("#fbclc_country", "US")
    assert not await acknowledge_privacy_notice(page, authorized=False)
    assert await page.input_value("#fbclc_dpcsId") == ""
    await page.set_content("<button id=create>Create Account</button>")
    assert not await acknowledge_privacy_notice(page, authorized=True)
    assert not await acknowledge_privacy_notice(None, authorized=True)


class FakeForm:
    def __init__(self):
        self.clicked, self.page = [], None
        self.resolver = SimpleNamespace(profile=SimpleNamespace(accept_all_application_terms=True))

    async def click(self, control_id, auth_control=False):
        self.clicked.append(control_id)


async def test_an_open_notice_dialog_is_acknowledged_by_its_own_button():
    controls = [{"id": "ack", "label": "Acknowledge"}, {"id": "no", "label": "Decline"}, {"id": "p", "label": "Print"}]
    obs = {
        "dialogs": [{"text": "NetApp Candidate Privacy Notice", "control_ids": ["ack", "no", "p"], "field_ids": []}],
        "fields": [],
        "controls": controls,
    }
    engine = SimpleNamespace(form=FakeForm(), page=None, emit=lambda *a, **k: None)
    assert await BrowserEngine.accept_policy_dialog(engine, obs)
    assert engine.form.clicked == ["ack"]
