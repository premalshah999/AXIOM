"""Synthetic Greenhouse-style react-select comboboxes: ids, classes and menu states copied from the REA map.

Everything here is invented (cities, schools, degrees). The page behaves like the observed widget: the listbox
`#react-select-{id}-listbox` exists only while the menu is open, an async field fetches after a debounce, the
menu shows a loading notice while it waits and a "No options" notice when a search finds nothing, and a pick
shows `.select__single-value`.
"""

import json

PAGE = """<label for="{id}">{label}</label>
<div class="select__control" id=ctl><div class="select__value-container">
  <div class="select__input-container"><input id="{id}" class="select__input" role="combobox" aria-haspopup="true"
    aria-autocomplete="list" aria-expanded="false" aria-controls="react-select-{id}-listbox" autocomplete="off"></div>
</div></div>
<div id=host></div>
<script>
const CATALOG = {catalog};
const REMOTE = {remote};
const DEBOUNCE = {debounce}, LATENCY = {latency};
const inp = document.getElementById('{id}'), host = document.getElementById('host'), ctl = document.getElementById('ctl');
let seq = 0, timer = null;
function show(html) {{
  host.innerHTML = html ? '<div class="select__menu"><div class="select__menu-list" role="listbox" id="react-select-{id}-listbox">' + html + '</div></div>' : '';
  inp.setAttribute('aria-expanded', String(!!html));
  host.querySelectorAll('[role=option]').forEach(o => o.onmousedown = e => {{ e.preventDefault(); pick(o.textContent) }});
}}
const options = items => items.slice(0, 100).map((t, i) => '<div role="option" class="select__option" id="react-select-{id}-option-' + i + '">' + t + '</div>').join('');
const notice = (cls, text) => '<div class="select__menu-notice ' + cls + '">' + text + '</div>';
function open() {{
  if (REMOTE) show(inp.value ? '' : (CATALOG.length && {open_catalog} ? options(CATALOG) : notice('select__menu-notice--no-options', 'Start typing to search')));
  else show(options(CATALOG.filter(c => c.toLowerCase().includes(inp.value.toLowerCase()))) ||
    notice('select__menu-notice--no-options', 'No options'));
}}
function pick(text) {{
  ctl.querySelectorAll('.select__single-value').forEach(e => e.remove());
  ctl.querySelector('.select__value-container').insertAdjacentHTML('afterbegin', '<div class="select__single-value">' + text + '</div>');
  inp.value = ''; show(''); window.picked = text;
}}
inp.addEventListener('click', open); inp.addEventListener('focus', open);
inp.addEventListener('keydown', e => {{ if (e.key === 'Escape') show(''); if (e.key === 'ArrowDown' && inp.getAttribute('aria-expanded') !== 'true') open() }});
inp.addEventListener('input', () => {{
  const q = inp.value.toLowerCase(), mine = ++seq;
  clearTimeout(timer);
  if (!REMOTE) return open();
  if (!q) return open();
  if ({flash_empty}) show(notice('select__menu-notice--no-options', 'No options'));
  timer = setTimeout(() => {{
    if (mine !== seq) return;
    show(notice('select__menu-notice--loading', 'Loading...'));
    setTimeout(() => {{
      if (mine !== seq) return;
      const STATES = {{md: 'maryland', oh: 'ohio'}};
      const words = q.replace(/[^a-z0-9 ]/g, ' ').split(/\\s+/).filter(Boolean).map(w => STATES[w] || w);
      const hits = CATALOG.filter(c => words.every(w => c.toLowerCase().includes(w)));
      show(hits.length ? options(hits) : notice('select__menu-notice--no-options', 'No options'));
    }}, LATENCY);
  }}, DEBOUNCE);
}});
</script>"""


def markup(field_id, label, catalog, *, remote=False, debounce=300, latency=400, open_catalog=False, flash_empty=False):
    return PAGE.format(
        id=field_id,
        label=label,
        catalog=json.dumps(catalog),
        remote="true" if remote else "false",
        debounce=debounce,
        latency=latency,
        open_catalog="true" if open_catalog else "false",
        flash_empty="true" if flash_empty else "false",
    )
