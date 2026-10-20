"""Merck (Workday) 2026-10-15: a footer link "View a country's privacy page or submit a request." is not a
second final submit control."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile


async def test_privacy_request_link_is_not_a_submit(page, tmp_path):
    await page.set_content("""<h2>Review</h2><button type=submit>Submit</button>
      <footer><a href="https://example.test/privacy">View a country's privacy page or submit a request.</a></footer>""")
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a: None)
    await form.scan()
    finals = await form.application_submit_controls()
    assert [b["label"] for b in finals] == ["Submit"]
