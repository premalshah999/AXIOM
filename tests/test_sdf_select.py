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


# Midco's hosts render their items into the floating pane only once opened, with labels set as properties, and State
# shows a window of 20 until its filter input is typed into: "Maryland" lies past "Louisiana" (2026-10-27).
LAZY = """<div class=field><label id=lab2>State *</label><sdf-select-simple id=lazy aria-labelledby=lab2></sdf-select-simple></div>
<script>
const NAMES = ['-', 'Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado', 'Connecticut', 'Delaware',
  'Florida', 'Georgia', 'Hawaii', 'Idaho', 'Illinois', 'Indiana', 'Iowa', 'Kansas', 'Kentucky', 'Louisiana', 'Maine',
  'Maryland', 'Massachusetts', 'Virginia'];
customElements.define('sdf-select-item', class extends HTMLElement {
  connectedCallback() { this.style.display = 'block'; this.textContent = this.label; }
});
customElements.define('sdf-select-simple', class extends HTMLElement {
  constructor() { super(); this.value = ''; this.open = false;
    const root = this.attachShadow({mode: 'open'});
    root.innerHTML = '<div class=shown>Select...</div><div class=pane hidden><input class=select-filter-input role=combobox>'
      + '<div role=listbox></div></div>';
    const render = () => {
      const box = root.querySelector('[role=listbox]'); box.innerHTML = '';
      const typed = root.querySelector('input').value.toLowerCase();
      for (const name of NAMES.filter(n => n.toLowerCase().includes(typed)).slice(0, 20)) {
        const item = document.createElement('sdf-select-item'); item.label = name; item.value = 'v' + NAMES.indexOf(name);
        item.addEventListener('click', e => { e.stopPropagation(); this.value = item.value;
          root.querySelector('.shown').textContent = name; root.querySelector('.pane').hidden = true;
          this.open = false; this.chosen = name; box.querySelectorAll('[selected]').forEach(i => i.removeAttribute('selected'));
          item.setAttribute('selected', ''); });
        box.appendChild(item);
      }
      const chosen = [...box.children].find(i => i.label === this.chosen);
      if (chosen) chosen.setAttribute('selected', '');
    };
    root.querySelector('input').addEventListener('input', render);
    root.querySelector('input').addEventListener('click', e => e.stopPropagation());
    this.addEventListener('click', () => { if (this.open) return; this.open = true;
      root.querySelector('.pane').hidden = false; render(); });
  }
});
</script>"""


async def test_a_lazy_filterable_sdf_select_is_filtered_to_the_answer(page):
    await page.set_content(LAZY)
    data = await page.main_frame.evaluate(SCAN)
    field = next(f for f in data["fields"] if f.get("widget") == "sdf-select")
    assert field["options"] == []  # nothing is rendered before the pane opens
    assert await widgets.sdf_select(page.main_frame, page.locator("#lazy"), "MD") == "Maryland"
    assert await page.evaluate("document.getElementById('lazy').chosen") == "Maryland"


# Midco (2026-10-28): State renders a page of 20 and loads the next as its list scrolls; the host toggles closed when
# clicked open; each item also has an unrendered light-DOM copy; Phone Type's duplicated "Cell" read as ambiguous.
STATES = ['-', 'Alabama', 'Alaska', 'Arizona', 'Arkansas', 'California', 'Colorado', 'Connecticut', 'Delaware',
          'District Of Columbia', 'Florida', 'Georgia', 'Guam', 'Hawaii', 'Idaho', 'Illinois', 'Indiana', 'Iowa',
          'Kansas', 'Kentucky', 'Louisiana', 'Maine', 'Maryland', 'Massachusetts', 'Michigan', 'Virginia']
PAGED = """<div class=field><label id=lab3>State *</label><sdf-select-simple id=paged aria-labelledby=lab3></sdf-select-simple></div>
<script>
const ALL = %s;
customElements.define('sdf-select-item', class extends HTMLElement {
  connectedCallback() { this.style.display = 'block'; this.style.height = '20px';
    if (this.getRootNode() !== document) this.textContent = this.label; }
});
customElements.define('sdf-select-simple', class extends HTMLElement {
  constructor() { super(); this.value = ''; this.shown = 0;
    const root = this.attachShadow({mode: 'open'});
    root.innerHTML = '<div class=shown>Select...</div><div class=pane hidden style="height:120px;overflow:auto">'
      + '<div role=listbox></div></div>';
    const pane = root.querySelector('.pane'), box = root.querySelector('[role=listbox]');
    const more = () => { for (const name of ALL.slice(this.shown, this.shown + 20)) {
        const item = document.createElement('sdf-select-item'); item.label = name; item.value = 'v' + ALL.indexOf(name);
        item.addEventListener('click', e => { e.stopPropagation(); this.value = item.value; this.chosen = name;
          box.querySelectorAll('[selected]').forEach(i => i.removeAttribute('selected')); item.setAttribute('selected', '');
          root.querySelector('.shown').textContent = name; pane.hidden = true; this.classList.remove('open'); });
        box.appendChild(item);
        const copy = document.createElement('sdf-select-item'); copy.label = name; copy.style.display = 'none';
        this.appendChild(copy);
      } this.shown += 20; };
    pane.addEventListener('scroll', () => { if (pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 2) more(); });
    this.addEventListener('click', () => {
      if (this.classList.contains('open')) { this.classList.remove('open'); pane.hidden = true; return; }
      this.classList.add('open'); pane.hidden = false; if (!this.shown) more(); });
  }
});
</script>""" % STATES


async def test_a_paged_sdf_select_scrolls_to_the_answer_without_closing_an_open_host(page):
    await page.set_content(PAGED)
    host = page.locator("#paged")
    await host.click()  # The navigator left it open.
    assert await widgets.sdf_select(page.main_frame, host, "MD") == "Maryland"
    assert await page.evaluate("document.getElementById('paged').chosen") == "Maryland"
    texts = widgets.sdf_texts(await host.evaluate(widgets.SDF_ITEMS_JS))
    assert len(texts) == len(set(texts))


def test_one_choice_listed_twice_is_one_choice():
    from jobpilot.answers.text import option_value

    assert option_value("Cell", ["-", "Cell", "Home", "Work", "-", "Cell", "Home", "Work"]) == "Cell"
