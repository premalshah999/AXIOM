"""A combobox that cannot commit the answer says what it did offer (Oracle's month lists, 2026-10-24: "No matching option
is offered" with no rows recorded, so the format the list wanted could not be read from the failure)."""

import json

import pytest

from jobpilot import widgets

LIST = """<div class="cx-select-container"><input id=month role=combobox aria-controls=month-listbox
    oninput="search(this.value)" onclick="search('')"><div role=grid id=month-listbox class=cx-select__list hidden></div></div>
  <script>
  const ALL = %s;
  function search(q){const m=document.getElementById('month-listbox');m.hidden=false;
    const items = ALL.filter(t => t.toLowerCase().includes(q.toLowerCase()));
    m.innerHTML = items.map(t => '<div role=row><div role=gridcell class="cx-select__list-item" onclick="document.getElementById(\\'month\\').value=this.textContent;this.closest(\\'[role=grid]\\').hidden=true"><span>' + t + '</span></div></div>').join('');}
  </script>"""


async def test_a_refused_combobox_names_the_rows_it_offered(page):
    await page.set_content(LIST % json.dumps(["01 - January", "02 - February", "03 - March"]))
    with pytest.raises(ValueError) as refused:
        await widgets.combobox(page.main_frame, page.locator("#month"), "Smarch")
    assert str(refused.value) == "No matching option is offered"
    assert refused.value.offered["rows"] == ["01 - January", "02 - February", "03 - March"]
    assert widgets.interaction_failure(refused.value) == "unsupported_choice"
