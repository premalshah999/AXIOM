"""The shapes real company career sites failed on, one flow each, rebuilt from saved runs and a logged-out look at the live pages.
All names are invented. tests/test_custom_site_flows.py drives each through the real engine with no model."""

import re

from .scenarios import APPLY_LINK, EMAIL_FIELD, NAME_FIELDS, POSTING, PLAIN_FORM, RESUME_FIELD, SUBMIT_BUTTON, THANKS, form, page
from .site import Scenario

EDUCATION = {"education": [{"school": "State University", "degree": "Bachelor's degree", "field_of_study": "Computer Science",
                            "start": "2020-09", "end": "2024-06"}]}
THE_PERIOD = {"first_name": "Alex", "last_name": "Example", "email": "alex@example.test", "start": "2020-09", "end": "2024-06"}


def _period_row(shown=("", "")):
    """A month-period picker as it stands on the page: a zero-height input, two halves that show the dates, and a panel of year and
    month lists under the half pressed. The class names are its CSS's own shape. Only the two hidden boxes carry the dates to the server."""
    item = "date-picker-period-month-panel-list-item"
    years = "".join(f'<li class="{item}">{y}</li>' for y in range(2016, 2027))
    months = "".join(f'<li class="{item}">{m:02d}</li>' for m in range(1, 13))
    half = lambda n, value: (
        f'<div class="date-picker-period-month-label" data-side="{n}" style="flex:1;padding:8px">'
        + (f"<span>{value[:4]}</span><span>-</span><span>{value[5:]}</span>" if value else "<span>YYYY</span><span>-</span><span>MM</span>")
        + "</div>"
    )
    return (
        """<div class="row"><h3>Education 1</h3>
<label for="sch">School name</label><input id="sch" name="school" required>
<label for="deg">Degree</label><select id="deg" name="degree"><option value=""></option><option>Bachelor's degree</option><option>Master's degree</option></select>
<label for="maj">Field of study</label><input id="maj" name="major">
<div class="form-item"><label>Start &amp; end date *</label><div class="form-item-control"><span class="form-item-children">
<div class="date-picker date-picker-period-month" id="period" style="display:flex;width:480px;height:40px;border:1px solid #888;position:relative">"""
        + half(0, shown[0]) + '<div class="date-picker-period-line" style="padding:8px">|</div>' + half(1, shown[1])
        + """<input class="date-picker-period-hidden-input" style="border:none;height:0;min-width:0;flex:0 1;padding:0">
</div></span></div></div><input type="hidden" name="start" value=\"""" + shown[0] + """\"><input type="hidden" name="end" value=\"""" + shown[1] + """\"></div>
<div id="panel" class="date-picker-period-month-panel" style="display:none;position:absolute;z-index:9;background:#fff;border:1px solid #888;height:180px;overflow:hidden">
<ul class="date-picker-period-month-panel-list" id="ys" style="float:left;height:180px;overflow:auto;margin:0;padding:0 16px;list-style:none">""" + years
        + """</ul><ul class="date-picker-period-month-panel-list" id="ms" style="float:left;height:180px;overflow:auto;margin:0;padding:0 16px;list-style:none">""" + months
        + """</ul></div>
<script>(() => {
  const picker = document.getElementById('period'), panel = document.getElementById('panel');
  let side = 0, year = '';
  picker.addEventListener('mousedown', e => { const r = picker.getBoundingClientRect(); side = e.clientX - r.left < r.width / 2 ? 0 : 1; year = '';
    panel.style.display = 'block'; panel.style.left = r.left + scrollX + (side ? r.width / 2 : 0) + 'px'; panel.style.top = r.bottom + scrollY + 'px'; e.preventDefault(); });
  panel.addEventListener('click', e => { const t = e.target.textContent.trim(); if (e.target.tagName !== 'LI') return;
    if (e.target.parentElement.id === 'ys') { year = t; return; }
    if (!year) return;
    const v = year + '-' + t; document.getElementsByName(side ? 'end' : 'start')[0].value = v;
    picker.querySelector('[data-side="' + side + '"]').innerHTML = '<span>' + year + '</span><span>-</span><span>' + t + '</span>'; panel.style.display = 'none'; });
  document.addEventListener('mousedown', e => { if (!panel.contains(e.target) && !picker.contains(e.target)) panel.style.display = 'none'; });
})();</script>"""
    )


period_empty = Scenario(
    "period_picker_to_fill",
    "TikTok's \"Start & end date\": a zero-height input in a month-period picker; every write timed out as hidden, 140 of them on 8 runs (2026-10-28..11-02).",
    {"/jobs/1": page("Apply", "<h1>Apply: Software Engineer</h1>" + form(NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, _period_row(), SUBMIT_BUTTON)), "/submit/1": THANKS},
    expect=THE_PERIOD,
    profile=EDUCATION,
)

period_shown = Scenario(
    "period_picker_already_shows_the_dates",
    "The same picker with both dates on screen from the site's own profile, and the required row read as empty, so the run stopped there (2026-11-02).",
    {"/jobs/1": page("Apply", "<h1>Apply: Software Engineer</h1>" + form(NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, _period_row(("2020-09", "2024-06")), SUBMIT_BUTTON)), "/submit/1": THANKS},
    expect=THE_PERIOD,
    profile=EDUCATION,
)

period_wrong = Scenario(
    "period_picker_shows_other_dates",
    "A picker showing dates that aren't the applicant's: they are changed to the recorded ones, never sent as found.",
    {"/jobs/1": page("Apply", "<h1>Apply: Software Engineer</h1>" + form(NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, _period_row(("2019-01", "2021-02")), SUBMIT_BUTTON)), "/submit/1": THANKS},
    expect=THE_PERIOD,
    profile=EDUCATION,
)

COVERED_SELECT = """<div class="field"><div class="q">Are you legally authorized to work in the US without restriction? *</div>
<div class="ud__select" id="wrap" style="width:480px;height:40px;border:1px solid #888">
 <div class="ud__select__selector ud__select__selector-readOnly" style="display:flex;height:100%;cursor:pointer"><div class="ud__select__selector__content" style="flex:1;position:relative;overflow:hidden">
 <div class="ud__select__selector__placeholder" style="padding:8px">Please select</div>
 <div class="ud__select__selector__search" style="position:absolute;top:0;left:0;width:100%;pointer-events:none"><input role="combobox" type="search" readonly aria-expanded="false" class="ud__select__selector__search__input" style="width:100%;height:36px;border:0;background:transparent"></div></div></div>
</div><input type="hidden" name="authorized"></div>
<div id="menu" role="listbox" style="display:none;border:1px solid #888;width:480px"><div role="option" style="padding:8px">Yes</div><div role="option" style="padding:8px">No</div></div>
<script>(() => { const wrap = document.getElementById('wrap'), menu = document.getElementById('menu'), box = wrap.querySelector('.ud__select__selector'), shown = wrap.querySelector('.ud__select__selector__placeholder');
  const input = wrap.querySelector('input'), set = open => { menu.style.display = open ? 'block' : 'none'; input.setAttribute('aria-expanded', String(open)); };
  box.addEventListener('mousedown', e => { set(menu.style.display === 'none'); e.preventDefault(); });
  document.addEventListener('mousedown', e => { if (!menu.contains(e.target) && !box.contains(e.target)) set(false); });
  menu.addEventListener('click', e => { if (e.target.getAttribute('role') !== 'option') return; document.getElementsByName('authorized')[0].value = e.target.textContent;
    shown.className = 'ud__select__selector__selectItem'; shown.textContent = e.target.textContent; set(false); });
})();</script>"""

own_cover = Scenario(
    "combobox_input_takes_no_pointer",
    "TikTok's work-authorization menus: the input is the combobox, the box around it has pointer-events none (the live page's CSS), no wrapper says so; 20 writes refused as covered on 8 runs.",
    {"/jobs/1": page("Apply", "<h1>Apply: Software Engineer</h1>" + form(NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, COVERED_SELECT, SUBMIT_BUTTON)), "/submit/1": THANKS},
    expect={"first_name": "Alex", "authorized": "Yes"},
    facts={"requires_future_sponsorship_US": False, "work_authorization_US": "Yes, authorized to work for any employer in the USA", "us_citizen": False, "permanent_resident": False},
)

# Every control of the form is wrapped in aria-hidden inside the open dialog, as Paycom's are.
MODAL_FORM = form(
    *(re.sub(r"(<input [^>]*>)", r'<span aria-hidden="true">\1</span>', part) for part in (NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD)),
    SUBMIT_BUTTON,
)
late_modal = Scenario(
    "late_page_opens_a_modal_form",
    "GSI on Paycom: nothing on the page for about 10 s after it loads, then Apply opens the form in a dialog whose controls are all aria-hidden; the scan saw no fields (2026-11-01).",
    {
        "/jobs/1": page(
            "Software Engineer",
            '<div id="root"></div><div id="dlg" role="dialog" aria-modal="true" style="display:none;position:fixed;inset:5%;background:#fff;overflow:auto;padding:16px"><h2>Your application</h2>'
            + MODAL_FORM
            + "</div><script>setTimeout(() => { document.getElementById('root').innerHTML = `" + POSTING
            + """<p><button class="apply" type="button" onclick="document.getElementById('dlg').style.display='block'">Apply</button></p>`; }, 5000);</script>""",
        ),
        "/submit/1": THANKS,
    },
)

tracking_bar = Scenario(
    "tracking_bar_over_the_submit",
    "Soni on JobDiva: a bar about analytics and tracking, with no word about cookies, lay over Submit; every click read as covered, six runs (2026-10-31).",
    {
        "/jobs/1": page("Software Engineer", POSTING + APPLY_LINK),
        "/apply/1": page(
            "Apply",
            '<div role="dialog" aria-modal="true" style="position:fixed;left:10%;right:10%;bottom:0;top:5%;background:#fff;overflow:auto;padding:16px"><h1>Apply</h1>'
            + form(NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, '<div style="position:absolute;bottom:0">' + SUBMIT_BUTTON + "</div>").replace(
                '<form ', '<form style="min-height:88%;position:relative" '
            )
            + """</div><div id="bar" style="position:fixed;left:0;right:0;bottom:0;height:80px;background:#222;color:#fff;z-index:50;padding:8px">
This website uses analytics and tracking technologies to understand how you use it.
<button type="button" onclick="document.getElementById('bar').remove()">Accept</button>
<button type="button" onclick="document.getElementById('bar').remove()">Decline</button></div>""",
        ),
        "/submit/1": THANKS,
    },
)

submit_resume_link = Scenario(
    "submit_resume_is_the_way_in",
    "Apple's posting links \"Submit Resume\" to its application: read as the final submit, the click was refused until the recovery budget ended, four runs (2026-10-27).",
    {"/jobs/1": page("Software Engineer", POSTING + '<p><a class="apply" href="/apply/1">Submit Resume</a></p>'), "/apply/1": PLAIN_FORM, "/submit/1": THANKS},
)

no_longer_accepting = Scenario(
    "no_longer_accepting_applications",
    "Microsoft's posting, live 2026-11-02: the page stands, its Apply is gone and a banner says \"No longer accepting applications.\"",
    {"/jobs/1": page("Software Engineer", "<h1>Software Engineer</h1><div role=\"alert\">No longer accepting applications.</div><p>Job number 2000</p><p>Work site: remote</p>")},
    state="closed",
    applications=0,
)

CUSTOM_SITES = [
    period_empty,
    period_shown,
    period_wrong,
    own_cover,
    late_modal,
    tracking_bar,
    submit_resume_link,
    no_longer_accepting,
]
