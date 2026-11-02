"""A page that offers a few large clickable cards and no form exposes them as controls (JobDiva's Register with Us)."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

CARDS = """<a href="#">Register With Us</a>
<div id=list>
  <div class=card style="cursor:pointer;width:600px;height:90px" onclick="window.picked='resume'">
    <b>Start with my resume</b><p>Have a resume already? We'll scan it to pre-fill many of the fields.</p></div>
  <div class=card style="cursor:pointer;width:600px;height:90px" onclick="window.picked='none'">
    <b>I don't have a resume</b><p>No resume? No problem. We'll help you fill out any necessary info.</p></div>
</div>"""


def _session(page, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    session = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)
    session.emit = lambda *a, **k: None
    return session


async def test_two_large_cards_with_no_form_are_listed_as_controls_and_clickable(page, tmp_path):
    await page.set_content(CARDS)
    session = _session(page, tmp_path)
    obs = await session.scan(force=True)
    labels = [b["label"] for b in obs["controls"]]
    assert "Start with my resume" in labels and "I don't have a resume" in labels
    card = next(b for b in session.buttons.values() if b["label"] == "Start with my resume")
    await session.click(card["id"], revision=obs["revision"], owner_id=obs["owner_id"])
    assert await page.evaluate("window.picked") == "resume"


async def test_a_list_of_jobs_with_links_is_not_turned_into_cards(page, tmp_path):
    jobs = "".join(
        f'<div style="cursor:pointer;width:600px;height:90px"><a href="/job/{i}">Engineer {i}</a> Houston, TX</div>'
        for i in range(3)
    )
    await page.set_content(jobs)
    session = _session(page, tmp_path)
    obs = await session.scan(force=True)
    assert not any(b.get("card") for b in obs["controls"])


async def test_cards_are_not_added_to_a_page_with_a_form(page, tmp_path):
    await page.set_content(CARDS + "<label for=n>Name</label><input id=n>")
    session = _session(page, tmp_path)
    obs = await session.scan(force=True)
    assert not any(b.get("card") for b in obs["controls"])


async def test_one_or_five_cards_are_left_alone(page, tmp_path):
    card = '<div style="cursor:pointer;width:600px;height:90px">Option number {i} that is long enough</div>'
    for count in (1, 5):
        await page.set_content("".join(card.format(i=i) for i in range(count)))
        session = _session(page, tmp_path)
        obs = await session.scan(force=True)
        assert not any(b.get("card") for b in obs["controls"])
