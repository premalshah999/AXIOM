"""ADP MyJobs (2026-10-27): "Phone Type" and "State" are sdf-select-simple custom elements; their filter and listbox
stay hidden until the host is clicked, so the scan observed no field and both required answers stayed empty."""

from jobpilot import widgets
from jobpilot.forms import SCAN

PAGE = """<div class=field><label id=lab>State *</label>
<sdf-select-simple id=state required aria-labelledby=lab>
  <sdf-select-item label="Alabama" value="AL"></sdf-select-item>
  <sdf-select-item label="Maryland" value="MD"></sdf-select-item>
  <sdf-select-item label="Virginia" value="VA"></sdf-select-item>
</sdf-select-simple></div>
<script>
customElements.define('sdf-select-item', class extends HTMLElement {
  connectedCallback() { this.style.display = 'block'; this.textContent = this.getAttribute('label'); }
});
customElements.define('sdf-select-simple', class extends HTMLElement {
  constructor() { super(); this.value = ''; this.open = false;
    const root = this.attachShadow({mode: 'open'});
    root.innerHTML = '<div class=shown>Select...</div><div role=listbox hidden><slot></slot></div>';
    this.addEventListener('click', e => {
      const item = e.target.closest && e.target.closest('sdf-select-item');
      if (item && this.open) {
        this.querySelectorAll('sdf-select-item').forEach(i => i.removeAttribute('selected'));
        item.setAttribute('selected', ''); this.value = item.getAttribute('value');
        root.querySelector('.shown').textContent = item.getAttribute('label');
        this.open = false; root.querySelector('[role=listbox]').hidden = true; return;
      }
      this.open = true; root.querySelector('[role=listbox]').hidden = false;
    });
  }
});
</script>"""


async def test_an_sdf_select_is_a_field_and_commits_its_item(page):
    await page.set_content(PAGE)
    data = await page.main_frame.evaluate(SCAN)
    field = next(f for f in data["fields"] if f.get("widget") == "sdf-select")
    assert field["label"].startswith("State") and field["required"] is True
    assert field["options"] == ["Alabama", "Maryland", "Virginia"] and field["value"] == ""
    assert await widgets.sdf_select(page.main_frame, page.locator("#state"), "MD") == "Maryland"  # the profile holds "MD"
    assert await page.evaluate("document.getElementById('state').value") == "MD"
