"""A select widget whose open list is plain rows (Paylocity's Country and State, Commence 2026-11-01): the wrapper names a
role=list portal with aria-owns, a value element covers the input, and the rows are role=listitem. Every fill read "no
offered choices" and the job stopped on a Country a person would pick in a second."""

from jobpilot import widgets

PAGE = """
<div id=wrap aria-haspopup=listbox aria-expanded=false aria-owns=list role=presentation style="width:300px;position:relative;height:32px;border:1px solid">
  <div id=shown class=input-select-input-single-value style="position:absolute;inset:0;background:#fff;z-index:2"></div>
  <input id=c aria-label=Country style="position:absolute;left:0;top:0;width:100%;height:100%">
</div>
<ul role=list><li role=listitem>A list on the page, not a menu</li></ul>
<script>
  const choices = ['Canada', 'France', 'United States'];
  const wrap = document.getElementById('wrap'), input = document.getElementById('c'), shown = document.getElementById('shown');
  function render() {
    document.getElementById('list')?.remove();
    const list = document.createElement('div');
    list.id = 'list'; list.setAttribute('role', 'list');
    list.style = 'position:fixed;top:60px;left:0;width:300px;z-index:9;background:#fff';
    for (const c of choices.filter(c => c.toLowerCase().includes(input.value.toLowerCase()))) {
      const row = document.createElement('div');
      row.setAttribute('role', 'listitem'); row.textContent = c; row.style = 'height:24px';
      row.addEventListener('mousedown', e => { e.preventDefault(); input.value = c; shown.textContent = c; list.remove(); shown.style.display = ''; });
      list.append(row);
    }
    document.body.append(list);
  }
  shown.addEventListener('mousedown', e => { e.preventDefault(); shown.style.display = 'none'; input.focus(); render(); });
  input.addEventListener('mousedown', () => render());
  input.addEventListener('input', render);
</script>"""


async def test_the_list_a_widget_names_is_its_menu(page):
    await page.set_content(PAGE)
    assert await widgets.visible_options(page.main_frame) == []  # Closed; the page's own list is no menu.
    await widgets.open_combobox(page.locator("#c"))
    texts = [o["text"] for o in await widgets.visible_options(page.main_frame)]
    assert texts == ["Canada", "France", "United States"]


async def test_a_listed_choice_commits(page):
    await page.set_content(PAGE)
    el = page.locator("#c")
    await widgets.combobox(page.main_frame, el, "United States")
    assert await page.locator("#shown").inner_text() == "United States"
