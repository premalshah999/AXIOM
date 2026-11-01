"""A local copy of a Greenhouse hosted job board application: ids, classes and menu behaviour from docs/rea/maps/greenhouse.md.

One page: the job text, then `form#application-form` with plain inputs, react-select comboboxes (sync and async with a debounce and a
loading notice), a resume tile that lists the file after a short upload, free-text and choice questions, the EEO block and a submit
that posts JSON and navigates to the confirmation route. Every name, school and city is invented.
"""

import json

COUNTRIES = ["Canada", "Mexico", "United Kingdom", "United States"]
CITIES = [
    "Baltimore, Maryland, United States",
    "Baltimore, Ohio, United States",
    "Baltimore Heights, Maryland, United States",
]
SCHOOLS = ["Aldermoor University", "Brightwater College", "Cedar Ridge Institute of Technology", "Example University", "Northfield State University"]
DEGREES = ["Associate's Degree", "Bachelor's Degree", "Master's Degree", "Doctorate", "Other"]
DISCIPLINES = ["Computer Science", "Data Science", "Information Systems", "Mathematics", "Statistics", "Other"]
YESNO = ["Yes", "No"]
HEARD = ["Company website", "Job board", "Referral", "Other"]
GENDER = ["Male", "Female", "Decline To Self Identify"]
RACE = ["Asian (Not Hispanic or Latino)", "White (Not Hispanic or Latino)", "Two or More Races (Not Hispanic or Latino)", "Decline To Self Identify"]
VETERAN = ["I am not a protected veteran", "I identify as one or more of the classifications of protected veteran", "I don't wish to answer"]
DISABILITY = ["Yes, I have a disability, or have had one in the past", "No, I do not have a disability and have not had one in the past", "I do not want to answer"]

JOB = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Job Application for Data Analyst at ExampleSoft</title>
<style>body{font:16px system-ui;max-width:760px;margin:24px auto;padding:0 16px}label{display:block;margin:12px 0 4px}
input,textarea{padding:8px;width:90%}.select__control{border:1px solid #888;position:relative;min-height:38px;width:90%}.select__input{border:0;width:100%;padding:8px}
.select__menu{border:1px solid #888;background:#fff;width:90%}.select__option{padding:8px}.btn{padding:10px 22px;background:#174f47;color:#fff;border:0}
.iti__dropdown-content.iti__hide{display:none}</style></head><body>
<div class="job__header"><h1>Data Analyst</h1><p>ExampleSoft - Remote</p>
<button type="button" class="btn btn--pill" aria-label="Apply" onclick="document.getElementById('application-form').scrollIntoView()">Submit Application</button></div>
<div class="job__description"><p>ExampleSoft builds invoicing software for small businesses. You will analyse product data and report to the product team.</p></div>
<div class="application--header"><button type="button" class="btn btn--pill btn--secondary">Autofill my application</button></div>
<form id="application-form" class="application--form" novalidate>
<h2>Apply for this job</h2>
<div class="text-input-wrapper"><label for="first_name">First Name<span aria-hidden="true">*</span></label><input id="first_name" name="first_name" type="text" required aria-label="First Name" maxlength="255" autocomplete="given-name"></div>
<div class="text-input-wrapper"><label for="last_name">Last Name<span aria-hidden="true">*</span></label><input id="last_name" name="last_name" type="text" required aria-label="Last Name" maxlength="255" autocomplete="family-name"></div>
<div class="text-input-wrapper"><label for="email">Email<span aria-hidden="true">*</span></label><input id="email" name="email" type="text" required aria-label="Email" maxlength="255" autocomplete="email"></div>
<div id="slot-country"></div>
<div class="iti iti--allow-dropdown iti--show-flags iti--inline-dropdown"><label for="phone">Phone<span aria-hidden="true">*</span></label>
<button type="button" class="iti__selected-country" aria-haspopup="dialog" aria-controls="iti-0__dropdown-content" aria-label="Select country"></button>
<div class="iti__dropdown-content iti__hide" id="iti-0__dropdown-content" role="dialog"><ul class="iti__country-list" role="listbox"></ul></div>
<input id="phone" name="phone" type="tel" required data-intl-tel-input-id="0"></div>
<div class="text-input-wrapper"><label for="candidate-location">City<span aria-hidden="true">*</span></label><input id="candidate-location" name="candidate-location" type="text" required autocomplete="address-level2"></div>
<div class="field"><label for="resume">Resume/CV<span aria-hidden="true">*</span></label>
<div id="resume-tile"><button type="button" class="btn" onclick="document.getElementById('resume').click()">Attach</button>
<span class="file-upload__filename" id="resume-name"></span>
<input id="resume" name="resume" type="file" accept=".pdf,.doc,.docx,.txt,.rtf" style="display:none"></div></div>
<h3>Education</h3>
<div id="slot-school--0"></div><div id="slot-degree--0"></div><div id="slot-discipline--0"></div>
<h3>Questions</h3>
<div class="text-input-wrapper"><label for="question_1001">LinkedIn Profile</label><input id="question_1001" name="question_1001" type="text"></div>
<div id="slot-question_1002"></div><div id="slot-question_1003"></div><div id="slot-question_1004"></div>
<h3>Voluntary Self-Identification</h3>
<div id="slot-gender"></div><div id="slot-race"></div><div id="slot-veteran_status"></div><div id="slot-disability_status"></div>
<div class="application--submit"><button type="submit" class="btn btn--pill" aria-disabled="false">Submit application</button></div>
</form>
<script>
window.__remixRouteModules = {};
const SEL = __SELECTS__;
const DEBOUNCE = 300, LATENCY = 350;
function makeSelect(cfg) {
  const id = cfg.id, slot = document.getElementById('slot-' + id);
  slot.innerHTML = '<label for="' + id + '">' + cfg.label + (cfg.required ? '<span aria-hidden="true">*</span>' : '') + '</label>' +
    '<div class="select__control"><div class="select__value-container"><div class="select__input-container">' +
    '<input id="' + id + '" class="select__input" role="combobox" aria-haspopup="true" aria-autocomplete="list" aria-expanded="false" ' +
    'aria-controls="react-select-' + id + '-listbox" aria-describedby="react-select-' + id + '-placeholder" autocomplete="off"' + (cfg.required ? ' aria-required="true"' : '') + '></div></div></div>' +
    '<div class="menu-host"></div><input type="hidden" name="' + id + '" value="">';
  const inp = slot.querySelector('#' + CSS.escape(id)), host = slot.querySelector('.menu-host'), ctl = slot.querySelector('.select__control'), hid = slot.querySelector('input[type=hidden]');
  let seq = 0, timer = null;
  const options = items => items.slice(0, 100).map((t, i) => '<div role="option" class="select__option" id="react-select-' + id + '-option-' + i + '">' + t + '</div>').join('');
  const notice = (c, t) => '<div class="select__menu-notice ' + c + '">' + t + '</div>';
  function show(html) {
    host.innerHTML = html ? '<div class="select__menu"><div class="select__menu-list" role="listbox" id="react-select-' + id + '-listbox">' + html + '</div></div>' : '';
    inp.setAttribute('aria-expanded', String(!!html));
    host.querySelectorAll('[role=option]').forEach(o => o.onmousedown = e => { e.preventDefault(); pick(o.textContent) });
  }
  function open() {
    if (cfg.remote) show(inp.value ? '' : cfg.open_catalog ? options(cfg.catalog) : notice('select__menu-notice--no-options', 'Start typing to search'));
    else { const q = inp.value.toLowerCase(); const hits = cfg.catalog.filter(c => c.toLowerCase().includes(q)); show(hits.length ? options(hits) : notice('select__menu-notice--no-options', 'No options')); }
  }
  function pick(text) {
    ctl.querySelectorAll('.select__single-value').forEach(e => e.remove());
    ctl.querySelector('.select__value-container').insertAdjacentHTML('afterbegin', '<div class="select__single-value">' + text + '</div>');
    inp.value = ''; hid.value = text; show('');
  }
  inp.addEventListener('click', open); inp.addEventListener('focus', open);
  inp.addEventListener('keydown', e => { if (e.key === 'Escape') show(''); if (e.key === 'ArrowDown' && inp.getAttribute('aria-expanded') !== 'true') open(); });
  inp.addEventListener('input', () => {
    const q = inp.value.toLowerCase(), mine = ++seq; clearTimeout(timer);
    if (!cfg.remote || !q) return open();
    timer = setTimeout(() => {
      if (mine !== seq) return; show(notice('select__menu-notice--loading', 'Loading...'));
      setTimeout(() => {
        if (mine !== seq) return;
        const words = q.replace(/[^a-z0-9 ]/g, ' ').split(/\\s+/).filter(Boolean);
        const hits = cfg.catalog.filter(c => words.every(w => c.toLowerCase().includes(w)));
        show(hits.length ? options(hits) : notice('select__menu-notice--no-options', 'No options'));
      }, LATENCY);
    }, DEBOUNCE);
  });
}
SEL.forEach(makeSelect);
document.getElementById('resume').addEventListener('change', e => {
  const f = e.target.files[0]; if (!f) return;
  setTimeout(() => { document.getElementById('resume-name').textContent = f.name; }, 600);   // the presigned S3 upload
});
document.getElementById('application-form').addEventListener('submit', async e => {
  e.preventDefault();
  const body = new FormData(e.target);
  const r = await fetch(location.pathname.replace(/\\/$/, '') + '/submit', {method: 'POST', body});
  if (r.ok) location.assign(location.pathname.replace(/\\/$/, '') + '/confirmation');
});
</script></body></html>"""

THANKS = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Thank you for applying</title></head><body>
<h1>Thank you for applying.</h1><p>Your application has been received. If there is a fit, someone will be getting back to you.</p></body></html>"""


def selects():
    def s(id, label, catalog, required=True, remote=False, open_catalog=False):
        return {"id": id, "label": label, "catalog": catalog, "required": required, "remote": remote, "open_catalog": open_catalog}

    return json.dumps(
        [
            s("country", "Country", COUNTRIES),
            s("school--0", "School", SCHOOLS, remote=True, open_catalog=True),
            s("degree--0", "Degree", DEGREES),
            s("discipline--0", "Discipline", DISCIPLINES),
            s("question_1002", "Are you legally authorized to work in the United States?", YESNO),
            s("question_1003", "Will you now or in the future require sponsorship for employment visa status?", YESNO),
            s("question_1004", "How did you hear about this job?", HEARD, required=False),
            s("gender", "Gender", GENDER),
            s("race", "Race", RACE),
            s("veteran_status", "Veteran Status", VETERAN),
            s("disability_status", "Disability Status", DISABILITY),
        ]
    )


def serve(site, path, method, body):
    """(status, html) for a path on job-boards.greenhouse.io."""
    base = "/examplesoft/jobs/4000001"
    if method == "POST" and path == base + "/submit":
        site.record("greenhouse", body)
        return 200, "{}"
    if path == base + "/confirmation":
        return 200, THANKS
    if path == base:
        return 200, JOB.replace("__SELECTS__", selects())
    return 404, "<h1>Not found</h1>"


JOB_URL = "/examplesoft/jobs/4000001"
