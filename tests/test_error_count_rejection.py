"""Starbucks (Eightfold) 2026-10-14: an error count after Submit is a validation refusal, not an unknown submission."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """<h3>Work Opportunity Tax Credit (WOTC)*</h3><div>1 error found</div>
<p>Click on button to fill out WOTC Survey.</p><button>Complete WOTC</button>
<div>Error: This field cannot be left blank.</div><button>Submit application</button>"""


async def rejection(page, tmp_path, body):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(body)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a: None)
    return await form.rejection()


async def test_error_count_names_the_refused_step(page, tmp_path):
    r = await rejection(page, tmp_path, PAGE)
    assert r["category"] == "validation"
    assert r["missing_fields"] == ["Work Opportunity Tax Credit (WOTC)"]


async def test_plain_form_is_not_a_rejection(page, tmp_path):
    assert await rejection(page, tmp_path, "<h3>Thanks</h3><p>We received your application.</p>") is None
