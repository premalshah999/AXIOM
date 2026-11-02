"""The traps, one scenario each, each one a failure a real page caused and a person spotted by eye. All names are invented."""

import time

from support_react_select import markup as react_select
from .site import Scenario

STYLE = """<style>body{font:16px system-ui;max-width:760px;margin:24px auto;padding:0 16px;color:#222}
label{display:block;margin:14px 0 4px}input,select{display:block;padding:8px;width:90%}
button,.apply{display:inline-block;padding:10px 22px;background:#174f47;color:#fff;border:0;border-radius:6px;text-decoration:none}
.select__control{border:1px solid #888;position:relative;height:38px;width:90%}.select__input{border:0;width:100%;height:36px;padding:8px}
.select__menu{border:1px solid #888;background:#fff;width:90%}.select__option{padding:8px}.err{color:#b00020;height:48px;padding:6px 0}</style>"""

POSTING = """<h1>Software Engineer</h1><p class="meta">ExampleSoft · Remote · Full time</p>
<p>ExampleSoft builds invoicing software for small businesses. You will build and run the services behind the product with Python and
PostgreSQL, work closely with design and support, and carry a feature from a rough idea to something customers use every day.</p>
<h2>What you bring</h2><ul><li>Experience building web services</li><li>Care for the people who use your work</li></ul>"""

THANKS = """<!doctype html><html lang="en"><meta charset="utf-8"><title>Application received</title><body><h1>Thank you for applying!</h1>
<p>Your application has been received. We will be in touch.</p></body></html>"""

NAME_FIELDS = """<label for="f">First name</label><input id="f" name="first_name" required autocomplete="given-name">
<label for="l">Last name</label><input id="l" name="last_name" required autocomplete="family-name">"""
EMAIL_FIELD = """<label for="e">Email</label><input id="e" name="email" type="email" required>"""
RESUME_FIELD = """<label for="r">Resume</label><input id="r" name="resume" type="file" required accept=".pdf">"""
SUBMIT_BUTTON = """<p><button id="send" type="submit">Submit application</button></p>"""


def page(title, body):
    return f'<!doctype html><html lang="en"><meta charset="utf-8"><title>{title} at ExampleSoft</title>{STYLE}<body>{body}</body></html>'


def form(*parts, action="/submit/1"):
    return f'<form method="post" action="{action}" enctype="multipart/form-data">{"".join(parts)}</form>'


APPLY_LINK = '<p><a class="apply" href="/apply/1">Apply for this job</a></p>'
# The page's own witness of a lost click: a press whose click never reached the control pressed. The engine may recover by pressing again,
# so without this a run would pass and hide the very thing the trap is for. What matters is that the first press is not lost.
LOST_CLICK = """<script>let pressed=null,timer=null;
document.addEventListener('mousedown',e=>{pressed=e.target.closest('a,button');clearTimeout(timer);
  timer=pressed&&setTimeout(()=>navigator.sendBeacon('/lost-click'),600)},true);
document.addEventListener('click',e=>{if(pressed&&e.target.closest('a,button')===pressed)clearTimeout(timer)},true);</script>"""
PLAIN_FORM = page("Apply", "<h1>Apply: Software Engineer</h1>" + form(NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, SUBMIT_BUTTON))
THREE_PAGES = {"/apply/1": PLAIN_FORM, "/submit/1": THANKS}


# ---- the page around the Apply button ------------------------------------------------------------------------------------

plain = Scenario(
    "plain_form",
    "The baseline: a job page, Apply, a form, a resume, a thank-you page. If this fails nothing else means anything.",
    {"/jobs/1": page("Software Engineer", POSTING + APPLY_LINK), **THREE_PAGES},
)

search_box = Scenario(
    "search_box_beside_apply",
    "Apply beside a search box read as a form, and the engine filled the search instead of applying (2026-10-31).",
    {
        "/jobs/1": page(
            "Software Engineer",
            '<form action="/search" method="get" role="search"><label for="q">Search jobs</label><input id="q" name="q">'
            '<label for="w">Location</label><input id="w" name="where"><button type="submit">Search</button></form>' + POSTING + APPLY_LINK,
        ),
        "/search": page("Search results", "<h1>No jobs found</h1>"),
        **THREE_PAGES,
    },
    never=("/search",),
)

chat_widget = Scenario(
    "chat_widget_beside_apply",
    "A recruiting assistant's \"I'm interested\" was clicked instead of Apply, and the run ended with nothing sent (2026-10-31).",
    {
        "/jobs/1": page(
            "Software Engineer",
            """<aside id="assistant" style="position:fixed;right:16px;bottom:16px;width:300px;background:#fff;border:1px solid #888;padding:12px">
            <p>Hi, I'm Ava, ExampleSoft's assistant. Interested in this role?</p>
            <button id="yes" type="button" onclick="fetch('/chat/interested',{method:'POST'});
              document.getElementById('assistant').innerHTML='<p>Great! Tell me about yourself.</p><label for=c>Message</label><input id=c name=chat>'">I'm interested</button>
            <button type="button" onclick="fetch('/chat/later',{method:'POST'})">Not now</button></aside>""" + POSTING + APPLY_LINK,
        ),
        "/chat/interested": "{}",
        "/chat/later": "{}",
        **THREE_PAGES,
    },
    never=("/chat/interested", "/chat/later"),
)

blur_moves_apply = Scenario(
    "blur_moves_the_apply_link",
    "On-blur validation moved a link between mouse-down and mouse-up, and the click was lost with no error (2026-10-31).",
    {
        "/jobs/1": page(
            "Software Engineer",
            """<label for="a">Get job alerts by email</label><input id="a" name="alert" type="email" autofocus required>
            <div id="m"></div>""" + POSTING + APPLY_LINK + LOST_CLICK + """<script>
            document.getElementById('a').addEventListener('blur',e=>{ if(!e.target.value)
              document.getElementById('m').innerHTML='<div class="err">An email address is required.</div>' })</script>""",
        ),
        "/lost-click": "{}",
        **THREE_PAGES,
    },
    never=("/lost-click",),
)

autofocus_gate = Scenario(
    "autofocus_email_gate",
    "An empty email box with autofocus: the page opens with the field focused, and leaving it empty moves what is below it.",
    {
        "/jobs/1": page("Software Engineer", POSTING + APPLY_LINK),
        "/apply/1": page(
            "Apply",
            """<h1>Apply: Software Engineer</h1><form method="post" action="/apply/2" enctype="multipart/form-data" id="gate">
            <label for="e">Email</label><input id="e" name="email" type="email" required autofocus><div id="m"></div>
            <p><button type="submit">Continue</button></p></form><script>
            document.getElementById('e').addEventListener('blur',e=>{ document.getElementById('m').innerHTML=
              e.target.value?'':'<div class="err">An email address is required.</div>' })</script>""",
        ),
        "/apply/2": page("Apply", "<h1>A little more about you</h1>" + form(NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, SUBMIT_BUTTON)),
        "/submit/1": THANKS,
    },
)

blur_moves_submit = Scenario(
    "blur_moves_the_submit_button",
    "The same lost click on the final button: the employer's site showed the application as Incomplete while the run said sent.",
    {
        "/jobs/1": page("Software Engineer", POSTING + APPLY_LINK),
        "/apply/1": page(
            "Apply",
            "<h1>Apply: Software Engineer</h1>"
            + form(
                NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD,
                '<div id="m"></div>', SUBMIT_BUTTON,
            )
            + LOST_CLICK
            # Only when focus leaves for the button or for nothing, with the form complete: the press of Submit, and nothing before it.
            + """<script>let shown=false;const form=document.querySelector('form');form.addEventListener('focusout',e=>{
              const to=e.relatedTarget; if(shown||(to&&to.tagName!=='BUTTON')||![...form.elements].every(x=>x.tagName==='BUTTON'||x.value)) return; shown=true;
              document.getElementById('m').innerHTML='<div class="err">Please check your answers before you send them.</div>' })</script>""",
        ),
        "/lost-click": "{}",
        "/submit/1": THANKS,
    },
    never=("/lost-click",),
)

cookie_wall = Scenario(
    "cookie_wall_over_apply",
    "A consent dialog covering the whole page swallowed every click on Apply, and the run ended on a control failure.",
    {
        "/jobs/1": page(
            "Software Engineer",
            POSTING + APPLY_LINK + """<div id="consent" role="dialog" aria-label="Cookie consent" style="position:fixed;inset:0;background:rgba(0,0,0,.5)">
            <div style="background:#fff;margin:120px auto;padding:20px;width:420px"><p>We use cookies to improve your experience.</p>
            <button type="button" onclick="document.getElementById('consent').remove()">Accept all cookies</button></div></div>""",
        ),
        **THREE_PAGES,
    },
    terms=True,
)

new_tab = Scenario(
    "apply_opens_a_new_tab",
    "Apply opens the application in a new tab, which the run has to follow; the job page stays open behind it.",
    {
        "/jobs/1": page("Software Engineer", POSTING + '<p><a class="apply" href="/apply/1" target="_blank">Apply for this job</a></p>'),
        **THREE_PAGES,
    },
)

iframe_form = Scenario(
    "form_inside_an_iframe",
    "The employer's page embeds the application in an iframe: the fields and the submit button are in another document.",
    {
        "/jobs/1": page(
            "Software Engineer", POSTING + '<iframe id="grnhse_iframe" title="Application" src="/embed/1" style="width:100%;height:620px;border:0"></iframe>'
        ),
        "/embed/1": page("Apply", "<h2>Apply for this job</h2>" + form(NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, SUBMIT_BUTTON)),
        "/submit/1": THANKS,
    },
)

# ---- the form's own controls ----------------------------------------------------------------------------------------------

covered = react_select("country", "Country", ["Canada", "Mexico", "United States"])
react_select_covered = Scenario(
    "react_select_with_a_covered_input",
    "A select widget's own value element covers its input, and a click on the input read as covered and failed (2026-11-01).",
    {
        "/jobs/1": page("Software Engineer", POSTING + APPLY_LINK),
        "/apply/1": page(
            "Apply",
            "<h1>Apply: Software Engineer</h1>"
            + form(NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, covered, '<input type="hidden" name="country" id="picked">', SUBMIT_BUTTON)
            + """<script>document.querySelector('form').addEventListener('submit',()=>{
              const v=document.querySelector('.select__single-value');document.getElementById('picked').value=v?v.textContent:''});
              // Once picked, the value element sits over the input, as the real widget's does.
              new MutationObserver(()=>{const v=document.querySelector('.select__single-value');
                if(v){v.style.cssText='position:absolute;inset:0;background:#fff;padding:8px'}}).observe(document.querySelector('.select__control'),{childList:true,subtree:true})</script>""",
        ),
        "/submit/1": THANKS,
    },
    expect={"first_name": "Alex", "email": "alex@example.test", "country": "United States"},
    facts={"country": "United States"},
)

hidden_duplicates = Scenario(
    "hidden_duplicate_controls",
    "Hidden copies of the fields and buttons (a mobile layout, a template) were filled and clicked instead of the real ones (2026-11-01).",
    {
        "/jobs/1": page("Software Engineer", POSTING + '<p style="display:none"><a href="/decoy/apply">Apply for this job</a></p>' + APPLY_LINK),
        "/decoy/apply": page("Decoy", "<h1>This is the hidden copy's page</h1>"),
        "/apply/1": page(
            "Apply",
            "<h1>Apply: Software Engineer</h1>"
            + '<form method="post" action="/decoy/submit" style="display:none" enctype="multipart/form-data">'
            + '<label>First name<input name="first_name"></label><label>Email<input name="email" type="email"></label>'
            + "<button>Submit application</button></form>"
            + form(NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, '<button type="button" style="display:none">Submit application</button>', SUBMIT_BUTTON),
        ),
        "/decoy/submit": THANKS,
        "/submit/1": THANKS,
    },
    never=("/decoy/apply", "/decoy/submit"),
)


def second_page(request):
    request.slowly(4)  # A postback: the old page stays up, unresponsive-looking, and a nervous click would send it twice.
    return page("Apply", "<h1>Resume</h1>" + form(
        *(f'<input type="hidden" name="{k}" value="{request.form.get(k, "")}">' for k in ("first_name", "last_name", "email")),
        RESUME_FIELD, SUBMIT_BUTTON,
    ))


def slow_thanks(request):
    request.slowly(6)  # The commit itself: the request is out, and nothing on the page has changed yet.
    return THANKS


slow_postback = Scenario(
    "slow_postbacks",
    "A server that takes seconds to answer Next and Submit: the page does not change, and a second click would apply twice.",
    {
        "/jobs/1": page("Software Engineer", POSTING + APPLY_LINK),
        "/apply/1": page(
            "Apply", "<h1>About you</h1>" + form(NAME_FIELDS, EMAIL_FIELD, '<p><button type="submit">Next</button></p>', action="/apply/2")
        ),
        "/apply/2": second_page,
        "/submit/1": slow_thanks,
    },
)


def refuse_once(request):
    """The server's own validation: the first submission comes back with the field marked, as a postback does."""
    if request.form.get("phone"):
        return THANKS
    return page("Apply", '<h1>Apply: Software Engineer</h1><div role="alert" class="err">There was a problem with your application. Phone is required.</div>'
                + form(NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, PHONE_FIELD, SUBMIT_BUTTON))


PHONE_FIELD = '<label for="p">Phone</label><input id="p" name="phone" type="tel" required>'
server_refusal = Scenario(
    "server_refuses_then_accepts",
    "The first submit comes back with an error only the server knew about: a refusal is never a confirmation.",
    {
        "/jobs/1": page("Software Engineer", POSTING + APPLY_LINK),
        "/apply/1": PLAIN_FORM,
        "/submit/1": refuse_once,
    },
    applications=2,
    facts={"phone": "2025550100"},
)

# ---- what must not be called a success, or be sent twice --------------------------------------------------------------------

no_receipt = Scenario(
    "submit_with_no_confirmation",
    "A submit that lands on a page saying nothing: it is unknown, it is never called confirmed, and it is never sent again.",
    {
        "/jobs/1": page("Software Engineer", POSTING + APPLY_LINK),
        "/apply/1": PLAIN_FORM,
        "/submit/1": page("ExampleSoft", "<h1>ExampleSoft</h1><p>Welcome to our website.</p>"),
    },
    state="submission_unknown",
)

closed = Scenario(
    "closed_posting",
    "A posting that is gone: the run says closed and nothing is sent.",
    {"/jobs/1": page("Careers", "<h1>Software Engineer</h1><p>This job is no longer available. The position has been filled.</p>")},
    state="closed",
    applications=0,
    expect={},
)

# ---- questionnaires an application requires -------------------------------------------------------------------------------


def _survey_page(request, body):
    return page("Survey", f"<h1>Survey</h1><p>Voluntary work opportunity tax credit survey.</p>{body}")


def _survey_entry(request):
    return _survey_page(request, """<form action="/survey/2" method="get"><p>Dear Candidate, we participate in various incentive programs.</p>
    <label for="lang">Select a language</label><select id="lang" name="lang"><option>English</option><option>Español</option></select>
    <button type="submit">Next</button></form>""")


def _survey_checks(request):
    def box(name, text):
        return f'<div class="question"><input type="checkbox" id="{name}" name="{name}" class="checkbox-input"><label for="{name}">{text}</label></div>'

    return _survey_page(request, '<h2>Check all that apply</h2><p id="err" style="color:#b00020"></p><form id="wotc" action="/survey/done" method="get">'
        "<p>I or a household member receive or received the following government benefits:</p>"
        + box("snap", "Food Stamps (SNAP) / AFDC / TANF") + box("benefits_unsure", "Not sure") + box("benefits_none", "None of the Above")
        + "<p>I have:</p>"
        + box("military", "Served in the US Military") + box("rehab", "Received Rehab") + box("ssi", "Received SSI") + box("unemployed", "Been Unemployed")
        + box("felony", "Been Convicted of a Felony") + box("other_none", "None of the Above")
        + """<button type="submit">Next</button></form><script>document.getElementById('wotc').addEventListener('submit',e=>{
        if(!(benefits_none.checked||snap.checked||benefits_unsure.checked)||!(other_none.checked||military.checked||rehab.checked||ssi.checked||unemployed.checked||felony.checked)){
          e.preventDefault();document.getElementById('err').textContent='"None of the Above" is required.'}});</script>""")


def _survey_done(request):
    # The vendor's Done state: a thank-you message and one "Next", a link to the return address. Only that link tells the application.
    back = f"http://127.0.0.1:{request.site.port}/assessment/return"
    return _survey_page(request, f'<p>Thank you for completing this survey.</p><a class="button button-next" href="{back}">Next</a>')


def _assessment_return(request):
    request.site.state["told"] = time.monotonic()
    return "<!doctype html><title>Done</title><p>Returning to your application.</p><script>window.close()</script>"


def _assessment_step(request):
    told = request.site.state.get("told")
    if request.method == "POST" and told and time.monotonic() - told > 3:  # The employer records it a moment after the survey says so.
        return page("Apply", "<h1>Resume</h1>" + form(
            *(f'<input type="hidden" name="{k}" value="{request.site.state.get(k, "")}">' for k in ("first_name", "last_name", "email")),
            RESUME_FIELD, SUBMIT_BUTTON))
    if request.method == "POST":
        for key in ("first_name", "last_name", "email"):
            if request.form.get(key):
                request.site.state[key] = request.form[key]
    survey = f"http://localhost:{request.site.port}/survey/1"
    error = '<div role="alert" data-automation-id="errorMessage">Error - Page Error You must complete the assessment test to continue with your job application.</div>' if request.method == "POST" else ""
    return page("Application", f"""<h1>Take Assessment</h1>{error}
    <button type="button" data-automation-id="inlineAssessmentButton" onclick="window.open('{survey}')">Take Assessment</button>
    <form method="post" action="/apply/step">{"".join(f'<input type="hidden" name="{k}" value="{request.site.state.get(k, "")}">' for k in ("first_name", "last_name", "email"))}
    <button type="submit">Save and Continue</button></form>""")


def _first_step(request):
    return page("Application", "<h1>My Information</h1>" + form(NAME_FIELDS, EMAIL_FIELD, '<p><button type="submit">Save and Continue</button></p>', action="/apply/step"))


SURVEY_PAGES = {"/survey/1": _survey_entry, "/survey/2": _survey_checks, "/survey/done": _survey_done, "/assessment/return": _assessment_return}

survey_return_link = Scenario(
    "survey_tells_the_application_through_its_last_link",
    "The engine closed a finished survey on its thank-you page, so its last \"Next\" never told Workday: eight finished surveys and "
    "\"You must complete the assessment test\" each time (Smithfield 2026-10-31).",
    {"/jobs/1": page("Software Engineer", POSTING + APPLY_LINK), "/apply/1": _first_step, "/apply/step": _assessment_step, "/submit/1": THANKS, **SURVEY_PAGES},
    facts={"tax_credit_screening_no": True},
)


def _personal_entry(request):
    return page("Assessment", """<h1>PI Behavioral Assessment</h1><form action="/test/2" method="get">
    <label for="lang">Please select the language in which you would like to take this assessment</label><select id="lang" name="lang"><option>English</option></select>
    <button type="submit">Continue</button></form>""")


def _personal_step(request):
    survey = f"http://localhost:{request.site.port}/test/1"
    return page("Application", f"""<h1>Take Assessment</h1>
    <button type="button" data-automation-id="inlineAssessmentButton" onclick="window.open('{survey}')">Take Assessment</button>
    <form method="post" action="/apply/step"><button type="submit">Save and Continue</button></form>""")


personal_test = Scenario(
    "a_personality_test_is_left_to_the_person",
    "A required Predictive Index test (87 adjectives about yourself) went round ten runs and its login page (GreatAmerica 2026-10-30): "
    "it cannot be answered truthfully for anyone, so the run says so once and sends nothing.",
    {"/jobs/1": page("Software Engineer", POSTING + APPLY_LINK), "/apply/1": _personal_step, "/apply/step": _personal_step, "/test/1": _personal_entry,
     "/test/2": page("Assessment", "<h1>Choose the adjectives that describe you</h1>")},
    state="closed",
    applications=0,
    expect={},
    never=("/test/2",),
)


def _ssn_survey(request):
    return _survey_page(request, """<form action="/survey/done" method="get"><label for="ssn">Social Security Number</label><input id="ssn" name="ssn" required>
    <button type="submit">Next</button></form>""")


ssn_survey = Scenario(
    "a_survey_that_asks_for_an_ssn_closes_the_job",
    "A tax-credit survey asking for a Social Security number: the owner's rule removes the job, nothing is typed, nothing is sent (Epiq 2026-11-01).",
    {"/jobs/1": page("Software Engineer", POSTING + APPLY_LINK), "/apply/1": _first_step, "/apply/step": _assessment_step,
     **SURVEY_PAGES, "/survey/1": _ssn_survey},
    state="cancelled",
    applications=0,
    expect={},
)

# ---- questions and controls real forms stopped on (2026-11-01) ------------------------------------------------------------


def combo(name, label, options, extra=""):
    """A react-select: the menu exists only while open and rebuilds its rows, with a hidden input the form posts."""
    rows = repr(options)
    return f"""<label for="{name}">{label}</label>
    <div class="select__control" id="c-{name}"><input id="{name}" class="select__input" role="combobox" aria-haspopup="true" aria-autocomplete="list" aria-expanded="false" autocomplete="off" {extra}></div>
    <div id="m-{name}"></div><input type="hidden" name="{name}" id="h-{name}">
    <script>(function(){{const inp=document.getElementById({name!r}),box=document.getElementById('m-{name}'),all={rows};
      function show(items){{box.innerHTML='';if(!items)return;const l=document.createElement('div');l.className='select__menu-list';l.setAttribute('role','listbox');
        if(!items.length){{const n=document.createElement('div');n.className='select__menu-notice--no-options';n.textContent='No results';l.append(n)}}
        for(const t of items){{const o=document.createElement('div');o.className='select__option';o.setAttribute('role','option');o.textContent=t;
          o.addEventListener('mousedown',e=>{{e.preventDefault();inp.value=t;document.getElementById('h-{name}').value=t;inp.setAttribute('aria-expanded','false');box.innerHTML='';inp.dispatchEvent(new Event('change',{{bubbles:true}}))}});l.append(o)}}
        box.append(l);inp.setAttribute('aria-expanded','true')}}
      inp.addEventListener('focus',()=>show(inp.dataset.search?[]:all));inp.addEventListener('click',()=>show(inp.dataset.search?[]:all));
      inp.addEventListener('input',()=>{{const q=inp.value.toLowerCase();show(all.filter(t=>t.toLowerCase().includes(q)))}});
      inp.addEventListener('keydown',e=>{{if(e.key==='Escape')box.innerHTML=''}});inp.addEventListener('blur',()=>setTimeout(()=>box.innerHTML='',150));}})();</script>"""


simple_apply = {"/jobs/1": page("Software Engineer", POSTING + APPLY_LINK), "/submit/1": THANKS}

visa_if_yes = Scenario(
    "an_if_yes_question_after_a_no_takes_the_menus_na",
    "\"If yes, please enter visa type\" after No to sponsorship offered H1B, TN, F1 ... OTHER, NA; \"N/A\" was not found in it and five runs stopped (ALTEN 2026-11-01).",
    {**simple_apply, "/apply/1": page("Apply", "<h1>Apply: Software Engineer</h1>" + form(
        NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD,
        combo("sponsor", "Will you now or in the future require sponsorship ?", ["Yes", "No"], "required"),
        combo("visa", "If yes, please enter visa type", ["H1B", "TN", "F1", "E2", "L1", "L2", "OTHER", "NA"], "required"),
        SUBMIT_BUTTON))},
    expect={"sponsor": "No", "visa": "NA"},
    facts={"requires_future_sponsorship_US": False, "work_authorization_US": "Yes, authorized to work for any employer in the USA", "us_citizen": False, "permanent_resident": False, "has_asylee_status": False, "current_immigration_status_US": "OPT"},
)

form_sum = Scenario(
    "a_forms_own_sum_is_computed",
    "\"What is 10 times 10?\" as a required question went to the applicant and stopped the run (Arize 2026-11-01).",
    {**simple_apply, "/apply/1": page("Apply", "<h1>Apply: Software Engineer</h1>" + form(
        NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, '<label for="q">What is 10 times 10?</label><input id="q" name="check" required>', SUBMIT_BUTTON))},
    expect={"check": "100"},
)

_AGREE = "I confirm that I have read, understand and agree to the written terms of the Mutual Agreement to Arbitrate Claims."
_REFUSE = "I do not agree to the written terms of the Mutual Agreement to Arbitrate Claims."
long_agreements = Scenario(
    "required_agreements_in_long_legal_text",
    "An arbitration agreement mentioning \"the plans' sponsors\" read as a sponsorship question, and three paragraphs on polygraphs ending \"Have you read and understood "
    "these statements?\" as a background check: both refused as out of scope, eleven runs (Voya, Transamerica 2026-11-01).",
    {**simple_apply, "/apply/1": page("Apply", "<h1>Apply: Software Engineer</h1>" + form(
        NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD,
        f"""<label for="arb">MUTUAL AGREEMENT TO ARBITRATE CLAIMS I understand that ExampleSoft is requiring me, as a condition of my application for employment, to arbitrate
        claims. The Company and I mutually consent to arbitration of all claims against its benefit plans or the plans' sponsors, fiduciaries and agents. The agreement remains
        applicable even if I am not employed by the Company.</label><select id="arb" name="arbitration" required><option value="">Select One</option><option>{_AGREE}</option><option>{_REFUSE}</option></select>""",
        """<label for="pol">No ExampleSoft company engages in polygraph testing for pre-employment screening. Applicants, please note: I understand that it is unlawful to require a lie
        detector test as a condition of employment. Have you read and understood these statements?</label><select id="pol" name="statements" required><option value="">Select One</option><option>Yes</option><option>No</option></select>""",
        SUBMIT_BUTTON))},
    expect={"arbitration": _AGREE, "statements": "Yes"},
    terms=True,
)

resume_modal = Scenario(
    "a_popup_that_only_asks_for_the_resume",
    "\"Apply with resume\" over the form with one Upload button that opens the computer's file picker: its click changed nothing, four runs (Paylocity, CleanSlate 2026-11-01).",
    {**simple_apply, "/apply/1": page("Apply", """<h1>Associate Engineer</h1>
    <div class="modal in" role="dialog" aria-labelledby="t" id="gate" style="position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:505"><div class="modal-dialog" style="background:#fff;width:420px;margin:120px auto;padding:20px">
      <h4 id="t">Apply with resume</h4><p>Upload your resume and we'll automatically fill out your application</p>
      <input accept=".doc,.docx,.pdf" id="force" type="file" style="display:none" onchange="document.getElementById('gate').remove()">
      <button type="button" class="button" onclick="document.getElementById('force').click()"><span>Upload Resume</span></button></div></div>""" + form(NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, SUBMIT_BUTTON))},
)

_ADDRESS = {"address": {"line1": "1 Example Way", "city": "Exampleton", "state": "MD", "postal_code": "20000", "country": "United States of America"}, "location": "Exampleton, MD"}
country_location = Scenario(
    "a_location_box_that_lists_countries",
    "\"Location, Country you're currently residing in\" answered \"No results\" for the city on three runs (ElevenLabs on Ashby, 2026-11-01). A person types the country.",
    {**simple_apply, "/apply/1": page("Apply", "<h1>Apply: Software Engineer</h1>" + form(
        NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD,
        combo("location", "Location", ["United States", "United Kingdom", "Canada", "Poland", "India"],
              'required data-search="1" class="ashby-application-form-input-autocomplete"').replace('class="select__input" ', ""),
        SUBMIT_BUTTON))},
    expect={"location": "United States"},
    profile=_ADDRESS,
)

state_location = Scenario(
    "a_location_box_that_lists_states",
    "\"Where are you currently based? Please specify the state\" listed only regions (\"Maryland, United States\", \"Maryland, Liberia\"): the city found nothing and the run stopped (n8n on Ashby, 2026-11-01).",
    {**simple_apply, "/apply/1": page("Apply", "<h1>Apply: Software Engineer</h1>" + form(
        NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD,
        combo("location", "Where are you currently based?", ["Maryland, Liberia", "Maryland, United States", "Texas, United States", "United States"],
              'required data-search="1" class="ashby-application-form-input-autocomplete"').replace('class="select__input" ', ""),
        SUBMIT_BUTTON))},
    expect={"location": "Maryland, United States"},
    profile=_ADDRESS,
)

no_answer_saved = Scenario(
    "a_saved_not_answered_is_not_typed",
    "\"Not Answered\", chosen once from a county's menu labelled \"Other\", was typed into an optional \"Other\" website box and refused as an invalid URL, three runs (MSK's Workday, 2026-11-01).",
    {**simple_apply, "/apply/1": page("Apply", "<h1>Apply: Software Engineer</h1>" + form(
        NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, '<label for="o">Other</label><input id="o" name="other" type="text">', SUBMIT_BUTTON))},
    expect={"other": ""},
    profile={"reviewed_answers": [{"id": "r1", "question": "Other", "answer": "Not Answered", "options": ["Give 'Other' Explanation", "Not Answered"], "employer": "Example County", "scope": "personal", "layer": "fact"}]},
)

_MENTIONS_PASSWORD = ("Are you a current ExampleSoft employee? (If you are currently an ExampleSoft employee, you MUST apply using your ExampleSoft username and password "
                      "via the Jobs Hub in Workday)")
long_question_with_password = Scenario(
    "a_question_that_mentions_a_password_is_a_question",
    "Read as a credential field, a required Yes/No question was never answered and every write was refused as \"missing\", three runs (Brown University Health 2026-11-01).",
    {**simple_apply, "/apply/1": page("Apply", "<h1>Apply: Software Engineer</h1>" + form(
        NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, f'<label for="c">10. {_MENTIONS_PASSWORD}</label><select id="c" name="current" required><option value="">Select One</option><option>Yes</option><option>No</option></select>', SUBMIT_BUTTON))},
    expect={"current": "No"},
    profile={"reviewed_answers": [{"id": "r2", "question": "10. " + _MENTIONS_PASSWORD, "answer": "No", "options": ["Yes", "No"], "employer": "", "scope": "personal", "layer": "fact"}]},
)

work_upload = Scenario(
    "a_required_upload_for_your_work_takes_the_resume",
    "\"Upload a link to your best work\" beside the resume, a required file input whose button opens the file picker: eight runs ended on \"Your form needs corrections\" (Powertown 2026-11-01).",
    {**simple_apply, "/apply/1": page("Apply", "<h1>Apply: Software Engineer</h1>" + form(
        NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD,
        '<label for="w">Upload a link to your best work</label><input id="w" name="work" type="file" required accept=".pdf" style="display:none"><button type="button" onclick="document.getElementById(\'w\').click()">Upload File</button>',
        SUBMIT_BUTTON))},
    uploaded=("work",),
)


def pcty_select(name, label, rows, shown=""):
    """Paylocity's select: the wrapper names a role=list portal with aria-owns; a virtual list of plain rows opens on a click; while it is open the input holds
    the shown value, and typing filters on the input's whole text. Its label wraps the widget, so the shown value is part of the label's text."""
    return f"""<div data-automation-id="{name}-wrapper" type="dropdown-wrapper"><label for="{name}"><span>{label}</span>
    <div><div class="pcty-input-select-full-container" aria-expanded="false" aria-haspopup="listbox" aria-owns="{name}-dropdown-list-container" required="" id="{name}-select-wrapper">
      <div class="pcty-input-select-input-container"><div><div class="input-select-input-single-value" id="{name}-shown">{shown}</div>
      <div class="pcty-input-select__input"><input aria-autocomplete="list" autocapitalize="none" autocorrect="off" id="{name}" maxlength="250" required="" spellcheck="false" tabindex="0" type="text" placeholder="{'' if shown else 'Select a ' + label.lower()}"></div></div></div></div></div></label>
    <input type="hidden" name="{name}" id="h-{name}" value="{shown}">
    <script>(function(){{const w=document.getElementById('{name}-select-wrapper'),i=document.getElementById({name!r}),s=document.getElementById('{name}-shown'),h=document.getElementById('h-{name}'),all={rows!r};
      let list=null;
      function close(){{if(list){{list.remove();list=null}};w.setAttribute('aria-expanded','false');i.value=''}}
      function draw(q){{if(!list){{list=document.createElement('div');list.id='{name}-dropdown-list-container';list.setAttribute('role','list');list.style.cssText='position:absolute;background:#fff;border:1px solid #888;z-index:9999;width:370px';document.body.append(list);
          const r=w.getBoundingClientRect();list.style.left=r.left+'px';list.style.top=(r.bottom+scrollY)+'px'}}
        list.innerHTML='';const hit=all.filter(t=>t.toLowerCase().includes(q.toLowerCase()));
        if(!hit.length){{const n=document.createElement('div');n.className='ListItemEven';n.textContent='No Results Found';list.append(n)}}
        hit.forEach((t,k)=>{{const o=document.createElement('div');o.className=k%2?'ListItemOdd':'ListItemEven';o.setAttribute('role','listitem');o.textContent=t;
          o.addEventListener('mousedown',e=>{{e.preventDefault();s.textContent=t;h.value=t;close()}});list.append(o)}});w.setAttribute('aria-expanded','true')}}
      w.addEventListener('click',e=>{{if(!list){{i.value=s.textContent;draw('');i.focus();i.select()}}}});
      i.addEventListener('input',()=>draw(i.value));
      i.addEventListener('blur',()=>setTimeout(close,120));
      i.addEventListener('keydown',e=>{{if(e.key==='Escape')close()}});
      document.addEventListener('mousedown',e=>{{if(list&&!w.contains(e.target)&&!list.contains(e.target))close()}});
    }})();</script></div>"""


_ADDRESS_SUGGEST = """<label for="line1">Address Line 1</label>
<input id="line1" name="line1" role="combobox" aria-expanded="false" aria-controls="line1-list" required autocomplete="off">
<script>(function(){const i=document.getElementById('line1');let l=null;
  function close(){if(l){l.remove();l=null}i.setAttribute('aria-expanded','false')}
  i.addEventListener('input',()=>{close();if(!i.value)return;l=document.createElement('div');l.id='line1-list';l.setAttribute('role','listbox');i.after(l);
    for(const t of ['1 Adams St NW, Washington, DC 20001','1 Aviation Cir, Washington, DC 20001']){const o=document.createElement('div');o.setAttribute('role','option');o.textContent=t;
    o.addEventListener('mousedown',e=>{e.preventDefault();i.value=t;close()});l.append(o)}i.setAttribute('aria-expanded','true')});
  i.addEventListener('keydown',e=>{if(e.key==='Escape')close()});
  i.addEventListener('blur',()=>setTimeout(close,100));})();</script>"""

paylocity_address = Scenario(
    "a_paylocity_address_block_behind_its_resume_popup",
    "Commence (Paylocity, 2026-11-01): the resume pop-up, a Country select already showing United States whose list opens on the wrapper, a State select, and an Address Line 1 that "
    "suggests other streets but takes what is typed. Country stayed unaccepted on five runs.",
    {**simple_apply, "/apply/1": page("Apply", """<h1>Engineer</h1><div class="modal in" role="dialog" aria-labelledby="pt" id="gate" style="position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:505"><div class="modal-dialog" style="background:#fff;width:420px;margin:120px auto;padding:20px">
      <h4 id="pt">Apply with resume</h4><p>Upload your resume and we'll automatically fill out your application</p><input accept=".doc,.docx,.pdf" id="force" type="file" style="display:none" onchange="document.getElementById('gate').remove()">
      <button type="button" class="button" onclick="document.getElementById('force').click()"><span>Upload Resume</span></button></div></div>""" + form(
        NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD,
        pcty_select("country", "Country", ["Canada", "India", "United Kingdom", "United States"], shown="United States"),
        _ADDRESS_SUGGEST,
        '<label for="city">City</label><input id="city" name="city" required>',
        pcty_select("state", "State", ["California", "Maryland", "Virginia"]),
        '<label for="zip">Zip Code</label><input id="zip" name="zip" required>',
        SUBMIT_BUTTON).replace("<form ", "<form novalidate "))},  # The page checks its own fields; the selects' inputs stay empty.
    expect={"country": "United States", "state": "Maryland", "line1": "1 Example Way", "city": "Exampleton", "zip": "20000"},
    profile={"address": {"line1": "1 Example Way", "city": "Exampleton", "state": "MD", "postal_code": "20000", "country": "United States of America"}, "location": "Exampleton, MD"},
)

SCENARIOS = [
    plain,
    search_box,
    chat_widget,
    blur_moves_apply,
    autofocus_gate,
    blur_moves_submit,
    cookie_wall,
    new_tab,
    iframe_form,
    react_select_covered,
    hidden_duplicates,
    slow_postback,
    server_refusal,
    no_receipt,
    closed,
    survey_return_link,
    personal_test,
    ssn_survey,
    visa_if_yes,
    form_sum,
    long_agreements,
    resume_modal,
    country_location,
    state_location,
    no_answer_saved,
    long_question_with_password,
    work_upload,
    paylocity_address,
]
