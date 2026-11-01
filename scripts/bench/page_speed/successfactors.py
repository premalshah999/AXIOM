"""A SuccessFactors-style application page: the RCM form's field names (fbclc_*), a native phone-code select, and paginated picklists that
fetch their rows from a slow server call (tests/fixtures/rcm_paginated_faithful.html, docs/rea/maps/successfactors.md). Names are invented."""

import json

QUESTIONS = [
    ("Are you legally authorized to work in the country of this position?", ["Yes", "No"]),
    ("Will you require sponsorship now or in the future?", ["Yes", "No"]),
    ("Highest level of education completed", ["High School", "Associate's Degree", "Bachelor's Degree", "Master's Degree", "Doctorate"]),
    ("Gender", ["Male", "Female", "Prefer not to say"]),
]

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Career Opportunities: Data Analyst (1001)</title>
<style>body{font:15px system-ui;max-width:780px;margin:20px auto}label{display:block;margin:12px 0 3px}input,select{padding:6px;width:60%}
#sfOverlayMgr{position:absolute;left:0;top:0;z-index:9}.sf-combo-listselect{height:200px;overflow:auto;background:#fff;border:1px solid #888;width:320px}
ul{margin:0;padding:0;list-style:none}li{height:24px}</style></head><body>
<h1>Career Opportunities: Data Analyst (1001)</h1>
<form id="frmApply" method="post" action="/careers/submit">
<label for="fbclc_resume">Resume</label><input type="file" id="fbclc_resume" name="fbclc_resume" accept=".pdf,.doc,.docx,.txt"><span id="resume_state"></span>
<label for="fbclc_fName">First Name *</label><input id="fbclc_fName" name="fbclc_fName" maxlength="40" required>
<label for="fbclc_lName">Last Name *</label><input id="fbclc_lName" name="fbclc_lName" maxlength="40" required>
<label for="fbclc_ituCode">Phone Country Code *</label><select id="fbclc_ituCode" name="fbclc_ituCode"><option value="">Select</option><option value="1">United States (+1)</option><option value="44">United Kingdom (+44)</option><option value="91">India (+91)</option></select>
<label for="fbclc_phoneNumber">Phone Number *</label><input id="fbclc_phoneNumber" name="fbclc_phoneNumber" type="tel" required>
<label for="fbclc_country">Country/Region of Residence *</label><select id="fbclc_country" name="fbclc_country"><option value="">Select</option><option value="US">United States</option><option value="CA">Canada</option><option value="GB">United Kingdom</option></select>
<div id="questions"></div>
<label><input type="checkbox" id="fbclc_notif" name="fbclc_notif" style="width:auto"> Email me about similar jobs</label>
<label><input type="checkbox" id="dataPrivacyId" name="dataPrivacyId" style="width:auto"> I have read the Privacy Notice *</label>
<p><button type="submit" id="fbqa_apply">Apply</button></p>
</form><div id="sfOverlayMgr"></div>
<script>
const QS = __QS__, DWR_MS = 450, DEBOUNCE = 250;
const mgr = document.getElementById('sfOverlayMgr');
function picklist(n, label, catalog) {
  const wrap = document.createElement('div'); wrap.className = 'RCMFormField';
  const base = 9 + n * 4;
  wrap.innerHTML = '<label for="' + base + ':_input">' + label + ' *</label><span id="' + (base - 1) + ':" class="sfCascadingPicklist"><div id="' + base + ':_selectContainer" class="paginatedPicklistContainer">' +
    '<input id="' + base + ':_input" role="combobox" class="rcmpaginatedselectinput rcmpaginatedselectitem rcmpaginatedselect_accessible" aria-owns="' + (base + 1) + ':_listSelect" aria-expanded="false" autocomplete="off" placeholder="No Selection" type="text" aria-required="true">' +
    '<button id="' + base + ':_selectButton" type="button" class="rcmpaginatedselectbutton">v</button><input type="hidden" id="tor__fcustq' + n + '" name="tor__fcustq' + n + '"></div></span>';
  document.getElementById('questions').appendChild(wrap);
  const input = wrap.querySelector('input[role=combobox]'), hidden = wrap.querySelector('input[type=hidden]'), OWN = (base + 1) + ':';
  let committed = '', box = null, shown = false, timer = null, seq = 0, idx = -1;
  const matching = f => (f ? catalog.filter(s => s.toLowerCase().includes(f.toLowerCase())) : ['No Selection', ...catalog]);
  function render(items) { const ul = document.createElement('ul'); ul.id = OWN + '_listSelect'; ul.setAttribute('role', 'listbox');
    items.forEach(l => { const li = document.createElement('li'); idx++; li.id = OWN + 'item' + idx; li.setAttribute('role', 'option');
      const a = document.createElement('a'); a.setAttribute('role', 'menuitem'); a.textContent = l; li.appendChild(a); li.onclick = () => select(l); ul.appendChild(li); }); return ul; }
  function open(f) { if (box) box.remove(); idx = -1; const mine = ++seq;
    setTimeout(() => { if (mine !== seq) return; box = document.createElement('div'); box.id = OWN; box.className = 'sf-combo-listselect';
      const r = input.getBoundingClientRect(); mgr.style.top = (window.scrollY + r.bottom) + 'px'; mgr.style.left = r.left + 'px';
      mgr.appendChild(box); box.appendChild(render(matching(f))); shown = true; }, DWR_MS); }
  function close() { if (box) { box.remove(); box = null; } shown = false; }
  function select(l) { close(); input.value = l; committed = l; hidden.value = l === 'No Selection' ? '' : l; input.setAttribute('aria-expanded', 'false'); }
  wrap.querySelector('button').onclick = () => { input.focus(); input.setAttribute('aria-expanded', 'true'); open(null); };
  input.onclick = () => { input.setAttribute('aria-expanded', 'true'); if (!shown && !box) open(null); };
  input.onkeyup = ev => { if (ev.key.length === 1 || ev.key === 'Backspace') { close(); input.setAttribute('aria-expanded', 'false'); clearTimeout(timer); timer = setTimeout(() => open(input.value || null), DEBOUNCE); }
    if (ev.key === 'Escape') { close(); input.setAttribute('aria-expanded', 'false'); } };
  input.onkeydown = ev => { if (ev.key === 'Enter') { ev.preventDefault(); const a = input.getAttribute('aria-activedescendant'); const li = a && document.getElementById(a); if (li) li.click(); } };
  input.onblur = () => setTimeout(() => { if (shown) return; if (input.value !== committed) input.value = committed; }, 0);
}
QS.forEach((q, n) => picklist(n, q[0], q[1]));
document.getElementById('fbclc_resume').addEventListener('change', e => { setTimeout(() => { document.getElementById('resume_state').textContent = e.target.files[0].name + ' uploaded'; }, 500); });
document.getElementById('frmApply').addEventListener('submit', async e => {
  e.preventDefault(); const r = await fetch('/careers/submit', {method: 'POST', body: new FormData(e.target)});
  if (r.ok) location.assign('/careers/submitted');
});
</script></body></html>"""

THANKS = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Career Opportunities: Thank You</title></head><body>
<h1>Thank you for applying</h1><p>Your application has been submitted successfully. We will contact you if there is a match.</p></body></html>"""

JOB_URL = "/careers?company=examplesoft&career_ns=job_application&career_job_req_id=1001"


def serve(site, path, method, body):
    if method == "POST" and path.startswith("/careers/submit"):
        site.record("successfactors", body)
        return 200, "{}"
    if path.startswith("/careers/submitted"):
        return 200, THANKS
    if path.startswith("/careers"):
        return 200, PAGE.replace("__QS__", json.dumps(QUESTIONS))
    return 404, "<h1>Not found</h1>"
