"""A Workday-style application flow, from My Information to Submit, with the widgets production's slow field writes are in.

Markup copied from the tests' decoded pages and docs/rea/maps/workday.md: `formField-*` wrappers, listbox buttons for Country, State,
Degree and the voluntary disclosures, search-first multiselect prompts for School and Field of Study (typing does nothing; Enter
searches; one match is selected on its own), the resume upload with "Successfully Uploaded!", a footer with Back and "Save and
Continue", and a "Congratulations!" page after Submit. One document switches the steps, as the real single-page flow does. The
account step is not part of it (a signed-in applicant). Every name and school is invented.
"""

import json

SCHOOLS = ["Aldermoor University", "Brightwater College", "Example University", "Northfield State University", "Riverton Community College"]
FIELDS = ["Accounting", "Computer Science", "Computer and Information Science", "Data Science", "Information Technology", "Mathematics", "Statistics"]
DEGREES = ["Associate's Degree", "Bachelor's Degree", "Master's Degree", "Doctorate", "High School Diploma"]
COUNTRIES = ["Canada", "India", "United Kingdom", "United States of America"]
STATES = ["Maryland", "Texas", "Virginia", "Washington"]
PHONE_TYPES = ["Home", "Mobile", "Work"]
YESNO = ["Yes", "No"]
GENDER = ["Male", "Female", "I do not wish to answer"]
ETHNICITY = ["Asian (Not Hispanic or Latino) (United States of America)", "White (Not Hispanic or Latino) (United States of America)", "I do not wish to answer"]
VETERAN = ["I am not a protected veteran", "I identify as one or more of the classifications of protected veteran", "I do not wish to answer"]

PAGE = """<!doctype html><html lang="en-US"><head><meta charset="utf-8"><title>Data Analyst - Application - ExampleSoft Careers</title>
<style>body{font:15px system-ui;max-width:800px;margin:20px auto}label{display:block;margin:12px 0 3px}input,button.dd{padding:7px;width:60%%;text-align:left}
ul[role=listbox]{border:1px solid #888;background:#fff;width:60%%;padding:0;list-style:none;margin:0;position:absolute;z-index:5}li{padding:6px}
.step{display:none}.step.on{display:block}.spinner{display:none}.pill{display:inline-block;border:1px solid #888;margin:2px;padding:2px 6px}</style></head><body>
<div data-automation-id="progressBar"><ol><li>My Information</li><li>My Experience</li><li>Application Questions</li><li>Voluntary Disclosures</li><li>Review</li></ol></div>
<div class="spinner" data-automation-id="loadingSpinner"></div>
<main id="main"></main>
<template id="t1"><section id="step1" data-automation-id="stepMyInformation"><h2>My Information</h2>
<div data-automation-id="formField-country"><label for="countryDropdown">Country</label><div><button class="dd" type="button" aria-haspopup="listbox" id="countryDropdown" name="country" data-automation-id="countryDropdown" data-dd="__COUNTRIES__" aria-label="Country Select One">Select One</button></div></div>
<div data-automation-id="formField-legalName--firstName"><label for="legalNameSection_firstName">First Name</label><input id="legalNameSection_firstName" name="legalName--firstName" required></div>
<div data-automation-id="formField-legalName--lastName"><label for="legalNameSection_lastName">Last Name</label><input id="legalNameSection_lastName" name="legalName--lastName" required></div>
<div data-automation-id="formField-addressLine1"><label for="addressSection_addressLine1">Address Line 1</label><input id="addressSection_addressLine1" name="addressLine1" required></div>
<div data-automation-id="formField-city"><label for="addressSection_city">City</label><input id="addressSection_city" name="city" required></div>
<div data-automation-id="formField-countryRegion"><label for="addressSection_countryRegion">State</label><div><button class="dd" type="button" aria-haspopup="listbox" id="addressSection_countryRegion" name="countryRegion" data-dd="__STATES__" aria-label="State Select One">Select One</button></div></div>
<div data-automation-id="formField-postalCode"><label for="addressSection_postalCode">Postal Code</label><input id="addressSection_postalCode" name="postalCode" required></div>
<div data-automation-id="formField-phoneType"><label for="phone-device-type">Phone Device Type</label><div><button class="dd" type="button" aria-haspopup="listbox" id="phone-device-type" name="phoneType" data-dd="__PHONETYPES__" aria-label="Phone Device Type Select One">Select One</button></div></div>
<div data-automation-id="formField-phoneNumber"><label for="phone-number">Phone Number</label><input id="phone-number" name="phoneNumber" type="tel" required></div>
</section></template>
<template id="t2"><section id="step2" data-automation-id="stepMyExperience"><h2>My Experience</h2>
<h3>Education</h3>
<div data-automation-id="formField-schoolName" class="prompt" data-catalog="__SCHOOLS__"><label for="education-1--school">School or University</label>
<div data-automation-id="multiSelectContainer"><div data-automation-id="multiselectInputContainer"><input id="education-1--school" name="schoolName" placeholder="Search" autocomplete="off"><ul role="listbox" data-automation-id="selectedItemList" aria-label="items selected"></ul></div><div class="popup" hidden><div data-automation-id="multiSelectListHeader"></div><ul role="listbox"></ul></div></div></div>
<div data-automation-id="formField-degree"><label for="education-1--degree">Degree</label><div><button class="dd" type="button" aria-haspopup="listbox" id="education-1--degree" name="degree" data-dd="__DEGREES__" aria-label="Degree Select One">Select One</button></div></div>
<div data-automation-id="formField-fieldOfStudy" class="prompt" data-catalog="__FIELDS__"><label for="education-1--fieldOfStudy">Field of Study</label>
<div data-automation-id="multiSelectContainer"><div data-automation-id="multiselectInputContainer"><input id="education-1--fieldOfStudy" name="fieldOfStudy" placeholder="Search" autocomplete="off"><ul role="listbox" data-automation-id="selectedItemList" aria-label="items selected"></ul></div><div class="popup" hidden><div data-automation-id="multiSelectListHeader"></div><ul role="listbox"></ul></div></div></div>
<h3>Resume/CV/Cover Letter/Supporting Documents</h3>
<div role="group" aria-labelledby="Resume-section"><h4 id="Resume-section">Resume/CV</h4><div data-automation-id="formField-" data-fkit-id="resumeAttachments--attachments"><label id="lresume">Upload a file (5MB max)</label>
<div data-automation-id="attachments-FileUpload" aria-labelledby="lresume"><div data-automation-id="file-upload-drop-zone"><button type="button" data-automation-id="select-files" id="resumeAttachments--attachments" onclick="document.getElementById('resumeFile').click()">Select files</button></div>
<input data-automation-id="file-upload-input-ref" id="resumeFile" type="file" style="display:none"><div id="uploaded"></div></div></div></div>
</section></template>
<template id="t3"><section id="step3" data-automation-id="stepApplicationQuestions"><h2>Application Questions</h2>
<div data-automation-id="formField-q1"><label for="q1">Are you legally authorized to work in the United States?</label><div><button class="dd" type="button" aria-haspopup="listbox" id="q1" name="q1" data-dd="__YESNO__" aria-label="Are you legally authorized to work in the United States? Select One" data-required="1">Select One</button></div></div>
<div data-automation-id="formField-q2"><label for="q2">Will you now or in the future require sponsorship for employment visa status?</label><div><button class="dd" type="button" aria-haspopup="listbox" id="q2" name="q2" data-dd="__YESNO__" aria-label="Will you now or in the future require sponsorship for employment visa status? Select One" data-required="1">Select One</button></div></div>
</section></template>
<template id="t4"><section id="step4" data-automation-id="stepVoluntary"><h2>Voluntary Disclosures</h2>
<div data-automation-id="formField-gender"><label for="personalInfoUS--gender">Please select your gender.</label><div><button class="dd" type="button" aria-haspopup="listbox" id="personalInfoUS--gender" name="gender" data-dd="__GENDER__" aria-label="Please select your gender. Select One">Select One</button></div></div>
<div data-automation-id="formField-ethnicity"><label for="personalInfoUS--ethnicity">Please select your race/ethnicity.</label><div><button class="dd" type="button" aria-haspopup="listbox" id="personalInfoUS--ethnicity" name="ethnicity" data-dd="__ETHNICITY__" aria-label="Please select your race/ethnicity. Select One">Select One</button></div></div>
<div data-automation-id="formField-veteranStatus"><label for="personalInfoUS--veteranStatus">Please select your veteran status.</label><div><button class="dd" type="button" aria-haspopup="listbox" id="personalInfoUS--veteranStatus" name="veteranStatus" data-dd="__VETERAN__" aria-label="Please select your veteran status. Select One">Select One</button></div></div>
</section></template>
<template id="t5"><section id="step5" data-automation-id="stepReview"><h2>Review</h2><p>Check your answers, then submit.</p></section></template>
<ul id="ddlist" role="listbox" hidden></ul>
<footer><button type="button" data-automation-id="pageFooterBackButton" id="back">Back</button>
<button type="button" data-automation-id="pageFooterNextButton" id="next">Save and Continue</button></footer>
<script>
let step = 0; const $ = id => document.getElementById(id);
const sp = document.querySelector('.spinner');
const list = $('ddlist'); let owner = null;
function show(n) { step = n; const main = $('main'); main.replaceChildren($('t' + n).content.cloneNode(true)); bind(main);
  $('next').textContent = n === 5 ? 'Submit' : 'Save and Continue'; window.scrollTo(0, 0); }
function go(n) { sp.style.display = 'block'; $('main').replaceChildren(); setTimeout(() => { sp.style.display = 'none'; show(n); }, 500); }
function valid(n) { if (n === 4) return true; const s = $('step' + n); let ok = true;
  s.querySelectorAll('input[required]').forEach(i => { if (!i.value) ok = false; });
  s.querySelectorAll('button.dd').forEach(b => { if (b.textContent === 'Select One') ok = false; });
  if (n === 2 && !s.querySelector('[data-name]')) ok = false; if (n === 2 && !$('uploaded').textContent) ok = false; return ok; }
$('next').onclick = async () => {
  if (!valid(step)) { if (!$('banner')) { const d = document.createElement('div'); d.id = 'banner'; d.setAttribute('role', 'alert'); d.textContent = 'Errors Found'; $('main').prepend(d); } return; }
  if (step < 5) return go(step + 1);
  const r = await fetch('/apply/submit', {method: 'POST', body: JSON.stringify({ok: 1})}); if (r.ok) location.assign('/apply/submitted');
};
$('back').onclick = () => { if (step > 1) go(step - 1); };
function bind(root) {
  root.querySelectorAll('button.dd').forEach(b => { b.onclick = () => { if (owner === b && !list.hidden) { list.hidden = true; return; } owner = b;
    const items = JSON.parse(b.dataset.dd); const r = b.getBoundingClientRect(); list.style.top = (window.scrollY + r.bottom) + 'px'; list.style.left = r.left + 'px';
    list.innerHTML = items.map(t => '<li role="option" data-automation-id="menuItem">' + t + '</li>').join(''); list.hidden = false; b.setAttribute('aria-expanded', 'true');
    list.querySelectorAll('li').forEach(li => li.onclick = () => { b.textContent = li.textContent; b.setAttribute('aria-label', b.closest('div[data-automation-id]').querySelector('label').textContent + ' ' + li.textContent); list.hidden = true; b.setAttribute('aria-expanded', 'false'); }); }; });
  root.querySelectorAll('.prompt').forEach(p => {
    const cat = JSON.parse(p.dataset.catalog), inp = p.querySelector('input'), popup = p.querySelector('.popup'), hdr = p.querySelector('[data-automation-id=multiSelectListHeader]'), ul = popup.querySelector('ul'), pills = p.querySelector('[data-automation-id=selectedItemList]');
    const words = s => s.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
    const match = q => { const t = words(q); return t.length ? cat.filter(n => t.every(x => words(n).some(w => w.startsWith(x)))) : cat; };
    const add = n => { pills.innerHTML = '<li role="presentation" data-name="' + n + '"><div role="option" data-automation-id="selectedItem"><div data-automation-id="DELETE_charm"></div><p data-automation-id="promptOption">' + n + '</p></div></li>'; };
    const render = (items, q) => { popup.hidden = false; hdr.textContent = !items.length ? 'No matches found' : q ? 'Search Results (' + items.length + ')' : '';
      ul.innerHTML = items.map(n => '<li role="option" data-automation-id="menuItem"><div data-automation-id="promptLeafNode"><p data-automation-id="promptOption">' + n + '</p></div></li>').join('');
      ul.querySelectorAll('[data-automation-id=promptLeafNode]').forEach(el => el.onclick = () => { add(el.textContent); popup.hidden = true; }); };
    inp.addEventListener('keydown', e => { if (e.key === 'Escape') { popup.hidden = true; return; } if (e.key !== 'Enter') return; e.preventDefault(); const q = inp.value;
      sp.style.display = 'block'; setTimeout(() => { sp.style.display = 'none'; const r = match(q); if (r.length === 1 && q) add(r[0]); render(r, q); }, 400); });
    inp.addEventListener('click', () => { if (!inp.value) render(cat.slice(0, 10), ''); });
  });
  const f = root.querySelector('#resumeFile');
  if (f) f.addEventListener('change', e => { const file = e.target.files[0]; if (!file) return; setTimeout(() => { $('uploaded').innerHTML = '<div data-automation-id="file-upload-successful"><p>' + file.name + '</p><p>Successfully Uploaded!</p></div>'; }, 700); });
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') list.hidden = true; });
document.addEventListener('click', e => { if (!e.target.closest('.prompt')) document.querySelectorAll('.popup').forEach(p => p.hidden = true); if (!e.target.closest('button.dd,#ddlist')) list.hidden = true; });
show(1);
</script></body></html>"""

DONE = """<!doctype html><html lang="en-US"><head><meta charset="utf-8"><title>Application Submitted - ExampleSoft Careers</title></head><body>
<h1>Congratulations!</h1><p>Your application has successfully been submitted.</p></body></html>"""

JOB_URL = "/en-US/ExampleSite/job/Remote/Data-Analyst_R1001"
JOB = """<!doctype html><html lang="en-US"><head><meta charset="utf-8"><title>Data Analyst - ExampleSoft Careers</title></head><body>
<h1 data-automation-id="jobPostingHeader">Data Analyst</h1><p>ExampleSoft, Remote. A synthetic posting.</p>
<a data-automation-id="adventureButton" href="/en-US/ExampleSite/job/Remote/Data-Analyst_R1001/apply" role="button">Apply</a></body></html>"""
METHOD = """<!doctype html><html lang="en-US"><head><meta charset="utf-8"><title>Start Your Application</title></head><body>
<h2>Start Your Application</h2><a data-automation-id="autofillWithResume" href="/en-US/ExampleSite/job/Remote/Data-Analyst_R1001/apply/autofillWithResume" role="button">Autofill with Resume</a>
<a data-automation-id="applyManually" href="/en-US/ExampleSite/job/Remote/Data-Analyst_R1001/apply/applyManually" role="button">Apply Manually</a></body></html>"""


def quote(items):
    return json.dumps(items).replace('"', "&quot;")


def serve(site, path, method, body):
    if method == "POST" and path.startswith("/apply/submit"):
        site.record("workday", body)
        return 200, "{}"
    if path.startswith("/apply/submitted"):
        return 200, DONE
    if "/apply/applyManually" in path:
        html = PAGE.replace("%%", "%")
        for key, items in (
            ("COUNTRIES", COUNTRIES), ("STATES", STATES), ("PHONETYPES", PHONE_TYPES), ("SCHOOLS", SCHOOLS), ("DEGREES", DEGREES),
            ("FIELDS", FIELDS), ("YESNO", YESNO), ("GENDER", GENDER), ("ETHNICITY", ETHNICITY), ("VETERAN", VETERAN),
        ):
            html = html.replace(f"__{key}__", quote(items))
        return 200, html
    clean = path.split("?")[0].rstrip("/")
    if clean.endswith("/apply"):
        return 200, METHOD
    if clean.endswith("Data-Analyst_R1001"):
        return 200, JOB
    return 404, "<h1>Not found</h1>"
