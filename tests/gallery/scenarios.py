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
]
