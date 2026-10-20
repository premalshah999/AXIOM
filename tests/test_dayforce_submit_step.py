"""Dayforce's last step (mPulse 2026-10-16): Submit stays disabled until "I agree to the Candidate
Acknowledgement" is ticked. The scan omits disabled buttons, so the progress step labelled "Submit" looked
final; clicking it changed nothing and the run was recorded as an uncertain submission."""

from types import SimpleNamespace

from jobpilot.answers import Resolver
from jobpilot.browser import BrowserEngine
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """
<div class="ant-steps"><div class="ant-steps-item"><div role="button" tabindex="0">Candidate Info</div></div>
<div class="ant-steps-item"><div role="button" tabindex="0">Submit</div></div></div>
<p>Please review the Candidate Acknowledgement statement below.</p>
<label><input type="checkbox" id="ack" onchange="document.getElementById('go').disabled=!this.checked">
I agree to the Candidate Acknowledgement</label>
<button type="button">Previous</button><button id="go" type="button" disabled>Submit</button>
"""


async def form_for(page, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(PAGE)
    return FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)


async def test_step_indicator_is_never_the_final_control_and_a_disabled_submit_is_reported(page, tmp_path):
    form = await form_for(page, tmp_path)
    await form.scan()
    assert [b["label"] for b in form.buttons.values() if b.get("stepper")] == ["Candidate Info", "Submit"]
    assert await form.application_submit_controls() == []
    assert form.meta["disabled_actions"] == ["Submit"]
    await page.check("#ack")
    await form.scan()
    final = await form.application_submit_controls()
    assert [b["label"] for b in final] == ["Submit"] and form.meta["disabled_actions"] == []


async def test_a_disabled_final_runs_one_completion_pass_before_any_commit():
    calls = []

    class Form:
        meta = {"disabled_actions": ["Submit"]}

        async def fill_current(self):
            calls.append("fill")

        async def scan(self):
            calls.append("scan")

    engine = SimpleNamespace(armed=False, form=Form())
    assert await BrowserEngine.complete_disabled_final(engine)
    assert calls == ["fill", "scan"]
    engine.form.meta = {"disabled_actions": ["Previous"]}
    assert not await BrowserEngine.complete_disabled_final(engine)


def test_adapter_final_control_ignores_step_indicators():
    from types import SimpleNamespace

    from jobpilot.adapters.others import Generic

    adapter = Generic.__new__(Generic)
    adapter.final = __import__("jobpilot.adapters.base", fromlist=["FINAL"]).FINAL
    obs = {"controls": [{"id": "s", "label": "Submit", "stepper": True}, {"id": "p", "label": "Previous"}]}
    assert Generic.find_final(adapter, obs) is None
    obs["controls"].append({"id": "go", "label": "Submit"})
    assert Generic.find_final(adapter, obs)["id"] == "go"


async def test_dayforce_try_again_toast_is_a_retryable_refusal(page, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content("<div role=alert>Your application wasn't submitted. Try again later.</div><button>Submit</button>")
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)
    rejection = await form.rejection()
    assert rejection["category"] == "security"
