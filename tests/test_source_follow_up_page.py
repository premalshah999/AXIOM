"""ADP, 2026-10-29 (ExampleSoft stands for the employer): the saved candidate profile pre-filled "Social Media" in
"Candidate Source", and its required follow-up "Social Media Source" offered only Facebook/Instagram/Meta. All pages here
are synthetic."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PROFILE = {
    "email": "alexample.apply@gmail.com",
    "linkedin": "https://www.linkedin.com/in/alex-example",
    "github": "https://github.com/alexexample",
}


def session(page, service, tmp_path, employer="ExampleSoft Inc", **profile):
    resolver = Resolver(
        Profile(**{**PROFILE, **profile}), service.db, service.config, employer=employer,
        job={"ats": "custom", "url": "https://x.test"},
    )
    return FormSession(page, resolver, tmp_path / "unused.pdf", lambda *a: None)


SOURCES = """<form>
  <label for=p>Candidate Source</label>
  <select id=p required onchange="
      document.getElementById('sm').hidden = this.value !== 'Social Media';
      document.getElementById('jb').hidden = this.value !== 'Job Board'">
    <option value="">-</option><option>ExampleSoft Website</option><option>Job Board</option><option>Social Media</option>
  </select>
  <div id=sm hidden><label for=sms>Social Media Source</label>
    <select id=sms required><option value="">-</option><option>Facebook/Instagram/Meta</option></select></div>
  <div id=jb hidden><label for=jbs>Job Board Source</label>
    <select id=jbs required><option value="">-</option><option>Indeed</option><option>LinkedIn</option></select></div>
</form>"""


async def test_a_prefilled_social_media_source_is_replaced_by_the_policys_best(page, service, tmp_path):
    await page.set_content(SOURCES)
    await page.select_option("#p", "Social Media")  # the saved candidate profile pre-filled it
    rules = {"source_order": ["linkedin", "company website", "job board", "social media"], "source_any_channel": True}
    form = session(page, service, tmp_path, application_source="LinkedIn", answer_rules=rules)
    await form.scan()
    await form.fill_current(accept_prefilled=True)
    assert await page.locator("#p").input_value() == "ExampleSoft Website"


async def test_a_follow_up_with_no_truthful_option_goes_back_to_the_next_channel(page, service, tmp_path):
    # The owner's list puts social media first here, so the first answer is the dead end the real form had.
    await page.set_content(SOURCES)
    rules = {"source_order": ["social media", "job board", "company website"], "source_any_channel": True}
    form = session(page, service, tmp_path, application_source="LinkedIn", answer_rules=rules)
    await form.scan()
    first = await form.fill_current(accept_prefilled=True)
    assert await page.locator("#p").input_value() == "Social Media"
    assert await page.locator("#sms").input_value() == ""  # Facebook/Instagram/Meta is not the applicant's channel
    assert not first["ok"] and form.resolver.dead_sources == {"social media"}
    second = await form.fill_current(accept_prefilled=True)
    assert await page.locator("#p").input_value() == "Job Board"
    assert await page.locator("#jbs").input_value() == "LinkedIn"
    assert second["ok"], second["problems"]
