"""DESRI (Rippling, 2026-10-31): a required textarea sat under a 270-character request with no question mark, so the
scan labelled it by its id ("field-59") and the owner was asked what to type in "field-59"."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

REQUEST = (
    "Please share links to applications, GitHub repositories, technical projects, publications, portfolios, or other "
    "work samples that demonstrate relevant experience. Please do not include any confidential or proprietary "
    "materials belonging to a current or former employer."
)


async def labels(page, service, tmp_path, html):
    await page.set_content(html)
    resolver = Resolver(Profile(), service.db, service.config, job={"ats": "custom", "url": "https://x.test"})
    form = FormSession(page, resolver, tmp_path / "unused.pdf", lambda *a: None)
    return [f["label"] for f in (await form.scan())["fields"]]


async def test_a_request_paragraph_above_a_lone_textarea_is_its_label(page, service, tmp_path):
    assert len(REQUEST) > 250
    found = await labels(page, service, tmp_path, f"""<form><div><div><p>{REQUEST}</p></div></div>
      <div data-testid="field"><div><div data-testid="q.1"><textarea id="field-59" required></textarea></div></div></div>
      </form>""")
    assert found == [REQUEST]


async def test_a_long_description_that_asks_nothing_is_still_not_borrowed(page, service, tmp_path):
    story = "We are a growing team building tools for the energy sector. " * 6
    found = await labels(page, service, tmp_path, f"""<form><div><div><p>{story}</p></div></div>
      <div data-testid="field"><div><div data-testid="q.1"><textarea id="field-60" required></textarea></div></div></div>
      </form>""")
    assert found == ["field-60"]
