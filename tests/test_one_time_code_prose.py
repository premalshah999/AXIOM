"""John Deere (Eightfold 2026-10-16) sets autocomplete=one-time-code on a prose question to stop autofill. The scan
masked its value as a secret, so "NA" never read back and every run stalled; real code fields stay masked."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

QUESTION = (
    "If you currently, or will in the future, require assistance in obtaining work status in the United States, "
    'please explain your situation (Enter "NA" if not applicable).'
)
PAGE = f"""<label for="q">{QUESTION}</label><input id="q" type="text" autocomplete="one-time-code">
<label for="otp">Verification code</label><input id="otp" type="text" autocomplete="one-time-code" value="482913">"""


async def test_a_prose_question_marked_one_time_code_reads_back(page, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(PAGE)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a, **k: None)
    obs = await form.scan()
    question = next(f for f in obs["fields"] if f["label"].startswith("If you currently"))
    await form.fill(question, "NA")
    report = await form.verify()
    assert not [p for p in report["problems"] if "If you currently" in p]
    code = next(f for f in (await form.scan())["fields"] if f["label"] == "Verification code")
    assert code["value"] == "********"
