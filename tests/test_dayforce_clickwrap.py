"""Dayforce ID registration (2026-10-16): the agreement is an <evr-checkbox> whose shadow input ignores
clicks (the component owns its state), and it counts only after both linked documents were opened.
Next stayed inert, so the invisible reCAPTCHA never executed and every registration stalled."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PAGE = """
<script>
customElements.define('evr-checkbox', class extends HTMLElement {
  connectedCallback() {
    this.setAttribute('checkedstate', 'unchecked');
    const root = this.attachShadow({mode: 'open'});
    root.innerHTML = '<input type="checkbox" aria-label="' + this.getAttribute('arialabelcheckbox') + '">';
    root.querySelector('input').addEventListener('click', e => e.preventDefault());
    this.addEventListener('click', () => {
      const next = this.getAttribute('checkedstate') === 'checked' ? 'unchecked' : 'checked';
      this.setAttribute('checkedstate', next);
    });
  }
});
window.opened = [];
</script>
<a href="https://example.test/privacy" target="_blank" onclick="window.opened.push('privacy'); return false">Acme's Global Privacy Statement</a>
<a href="https://example.test/terms" target="_blank" onclick="window.opened.push('terms'); return false">Terms of Use</a>
<label><evr-checkbox arialabelcheckbox="I agree to Acme's Global Privacy Statement and to the Terms of Use, I clicked the links above and reviewed these documents."></evr-checkbox>
I agree to Acme's Global Privacy Statement and to the Terms of Use, I clicked the links above and reviewed these documents.</label>
"""


async def test_agreement_component_opens_its_documents_then_toggles_on_the_host(page, tmp_path, monkeypatch):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    await page.set_content(PAGE)
    form = FormSession(page, Resolver(Profile(accept_all_application_terms=True), None, None), resume, lambda *a: None)
    obs = await form.scan()
    agree = next(f for f in obs["fields"] if f["type"] == "checkbox")
    assert agree["value"] == "false"

    class Popup:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        @property
        async def value(self):
            class Tab:
                async def close(self):
                    pass

            return Tab()

    monkeypatch.setattr(type(page.context), "expect_page", lambda self, timeout=None: Popup())
    await form.fill(agree, "true")
    assert await page.evaluate("window.opened") == ["privacy", "terms"]
    assert await page.locator("evr-checkbox").get_attribute("checkedstate") == "checked"
    obs = await form.scan()
    assert next(f for f in obs["fields"] if f["type"] == "checkbox")["value"] == "true"

