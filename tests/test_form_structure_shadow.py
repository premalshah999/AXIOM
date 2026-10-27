"""ADP MyJobs (2026-10-27): "Phone Type" and "State" are custom elements whose controls live in shadow DOM; the step
record's form structure must show them, value-free."""

from jobpilot.browser import FORM_STRUCTURE_JS

PAGE = """<label>City<input id=city value="Springfield"></label>
<fancy-select id=state label="State" required></fancy-select>
<script>
customElements.define('fancy-select', class extends HTMLElement {
  constructor() { super(); this.attachShadow({mode: 'open'}).innerHTML =
    '<button aria-haspopup="listbox">Select...</button><div role="listbox"></div>'; }
});
</script>"""


async def test_custom_elements_and_their_shadow_controls_are_recorded(page):
    await page.set_content(PAGE)
    rows = await page.evaluate(FORM_STRUCTURE_JS)
    tags = [(r["tag"], r["host"]) for r in rows]
    assert ("fancy-select", "") in tags and ("button", "fancy-select") in tags
    state = next(r for r in rows if r["tag"] == "fancy-select")
    assert state["label"] == "State" and state["required"] is True
    assert all("Springfield" not in str(r) for r in rows), "values are never recorded"
