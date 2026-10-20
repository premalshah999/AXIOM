"""John Deere (Eightfold 2026-10-16): the email field showed the correct address with a stale "Email is invalid"
error. Refills skipped typing because the value was already shown, so the site never validated it again."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """<label for="em">Email</label>
<input id="em" aria-describedby="em_err" aria-invalid="true" value="alex@example.test">
<div id="em_err" role="alert">Email is invalid. Use the format "example@email.com".</div>
<script>
em.addEventListener('input', () => {
  const ok = /^[^@\\s]+@[^@\\s]+\\.[a-z]{2,}$/i.test(em.value);
  em_err.textContent = ok ? '' : 'Email is invalid. Use the format "example@email.com".';
  em.setAttribute('aria-invalid', String(!ok));
});
</script>"""


async def test_a_shown_value_with_a_site_error_is_typed_again(page, tmp_path):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(PAGE)
    form = FormSession(page, Resolver(Profile(), None, None), resume, lambda *a, **k: None)
    obs = await form.scan()
    field = next(f for f in obs["fields"] if f["label"] == "Email")
    assert field.get("error")
    await form.fill(field, "alex@example.test")
    assert await page.locator("#em_err").text_content() == ""
    report = await form.verify()
    assert not [p for p in report["problems"] if p.endswith(": Email")]
