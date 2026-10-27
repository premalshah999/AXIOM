"""ADP MyJobs (REA 2026-10-27): its phone input lives in a custom element whose own value is set by key events only;
a value write showed the digits while the component stayed empty and registration said "Phone Number is required"."""

from jobpilot import widgets

PAGE = """<phone-box id=host></phone-box>
<script>
customElements.define('phone-box', class extends HTMLElement {
  constructor() { super(); this.value = ''; const root = this.attachShadow({mode: 'open'});
    root.innerHTML = '<input type=tel>';
    root.querySelector('input').addEventListener('keyup', e => { this.value = e.target.value; }); }
});
</script>"""


async def test_a_web_component_phone_is_recognised_and_typed(page):
    await page.set_content(PAGE)
    inner = page.locator("phone-box input")
    assert await widgets.web_component_phone(inner)
    await inner.fill("")
    await inner.press_sequentially(widgets.national_digits("+1 240 555 0123"), delay=10)
    assert await page.evaluate("document.getElementById('host').value") == "2405550123"
    await page.set_content("<input type=tel id=plain>")
    assert not await widgets.web_component_phone(page.locator("#plain"))
