"""FTI's iCIMS career site (FRONTIER TECHNOLOGY, 2026-10-27) adds an about:blank "font-resize-helper" iframe at
top:-9999px on every load. Eleven of them filled the twelve-frame scan budget, so the Portal Specific Forms frame,
nested in the content frame, was never scanned: the step read as a page with no fields and no Submit. Frames placed
wholly outside the page are measuring helpers and are skipped."""

from jobpilot.answers import Resolver
from jobpilot.forms import MAX_FRAMES, FormSession
from jobpilot.schemas import Profile

HELPER = (
    '<iframe class="font-resize-helper" '
    'style="position:absolute;width:100em;height:10px;top:-9999px;left:-9999px;border-width:0"></iframe>'
)
PACKET = (
    "<h2>Voluntary Self-Identification</h2>"
    "<label for=v>Protected veteran status</label>"
    "<select id=v required><option></option><option>I am not a protected veteran</option></select>"
    "<button type=submit>Submit</button>"
)


async def test_a_form_behind_many_helper_frames_is_scanned(page, service, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    content = PACKET.replace('"', "&quot;")
    await page.set_content(
        "<h1>Associate Software Developer</h1>" + HELPER * MAX_FRAMES
        + f'<iframe id=icims_content_iframe style="width:100%;height:400px" srcdoc="{content}"></iframe>'
    )
    await page.wait_for_function("document.querySelector('#icims_content_iframe').contentDocument?.querySelector('#v')")
    form = FormSession(page, Resolver(Profile(), service.db, service.config), resume, lambda *a, **k: None)
    obs = await form.scan()
    assert [f["label"] for f in obs["fields"]] == ["Protected veteran status"]
    assert "Submit" in [c["label"] for c in obs["controls"]]
    assert len(await form.application_frames()) == 2  # the page and its content frame, not the helpers
