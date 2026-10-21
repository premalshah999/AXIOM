"""Eightfold's job search box is a site search, not an application question; a real combobox still is."""

from jobpilot.answers import Resolver, job_catalog_search_box
from jobpilot.email_browser import EmailBrowser
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

EIGHTFOLD = """
<form>
  <label for=q>Search for job title or keywords</label>
  <input id=q role=combobox aria-autocomplete=list aria-haspopup=listbox aria-expanded=true
         aria-label="Search for job title or keywords" placeholder="Search for job title or keywords">
  <ul role=listbox>
    <li role=option id=o1>Backend Software Engineer</li>
    <li role=option id=o2>Backend Developer</li>
  </ul>
  <label for=loc>Search for location</label>
  <input id=loc placeholder="Search for location">
  <button type=button>Search jobs</button>
  <label for=country>Country</label>
  <input id=country role=combobox aria-autocomplete=list aria-haspopup=listbox>
</form>
"""


def field(label, **extra):
    return {"id": "f1", "label": label, "type": "text", "required": False, "maxlength": -1, **extra}


def state(fields, text):
    return EmailBrowser.state(object.__new__(EmailBrowser), {"fields": fields, "text": text})


async def test_scan_types_the_site_search_as_a_combobox_the_predicate_skips(page):
    await page.set_content(EIGHTFOLD)
    from jobpilot.forms import SCAN

    fields = {f["label"]: f for f in (await page.main_frame.evaluate(SCAN))["fields"]}
    search = fields["Search for job title or keywords"]
    assert search["type"] == "combobox" and search["required"] is False
    assert job_catalog_search_box(search)
    assert not job_catalog_search_box(fields["Country"])


async def test_resolver_skips_the_site_search_box_before_any_answer_is_built(service, page):
    await page.set_content(EIGHTFOLD)
    from jobpilot.forms import SCAN

    fields = {f["label"]: f for f in (await page.main_frame.evaluate(SCAN))["fields"]}
    resolver = Resolver(Profile(), service.db, service.config)
    answers = await resolver._resolve([fields["Search for job title or keywords"]])
    assert [(a.field_id, a.disposition) for a in answers] == [
        (fields["Search for job title or keywords"]["id"], "skip")
    ]
    assert resolver.context_packets == {}


async def test_choosing_the_site_search_suggestion_is_not_an_answer_validation_retry(service, page, tmp_path):
    await page.set_content(EIGHTFOLD)
    form = FormSession(
        page, Resolver(Profile(), service.db, service.config), tmp_path / "resume.pdf", lambda *a: None
    )
    obs = await form.scan()
    search = next(f for f in obs["fields"] if f["label"] == "Search for job title or keywords")
    options = (await form.inspect_control_options(search))["options"]
    suggestion = next(o for o in options if o["text"] == "Backend Developer")
    result = await form.control_action(
        "choose", search["id"], (await form.scan())["revision"], option_id=suggestion["id"]
    )
    # browser.py counts only details that start with "Answer validation rejected" toward waiting_browser.
    assert result.failure_category == "unsupported"
    assert not result.detail.startswith("Answer validation rejected")


def test_site_search_box_is_not_a_required_field_or_a_country_choice():
    assert job_catalog_search_box(field("Search for job title or keywords", type="combobox"))
    assert not job_catalog_search_box(
        field("Search for job title or keywords", type="combobox", required=True)
    )
    assert not job_catalog_search_box(field("Country", type="combobox"))


def test_page_search_box_does_not_make_an_email_code_step_mixed():
    search = {**field("Search for job title or keywords", type="combobox"), "id": "s"}
    digits = [{**field("", maxlength=1), "id": f"d{i}"} for i in range(6)]
    obs = {"fields": [search, *digits], "text": "Enter the code we sent to your email"}
    kind, codes = state(obs["fields"], obs["text"])
    assert kind == "code" and len(codes) == 6
    assert EmailBrowser.application_fields(obs, {d["id"] for d in codes}) == []


def test_real_application_field_still_makes_an_email_code_step_mixed():
    search = {**field("Search for job title or keywords", type="combobox"), "id": "s"}
    phone = {**field("Phone number"), "id": "p"}
    digits = [{**field("", maxlength=1), "id": f"d{i}"} for i in range(6)]
    obs = {"fields": [search, phone, *digits], "text": "Enter the code we sent to your email"}
    kind, codes = state(obs["fields"], obs["text"])
    assert kind == "code"
    assert [f["id"] for f in EmailBrowser.application_fields(obs, {d["id"] for d in codes})] == ["p"]


def test_page_text_alone_never_opens_a_code_wait():
    assert state([], "Enter the code we sent to your email")[0] is None
    assert state([field("Please enter your first name")], "Verification code needed")[0] is None


def test_code_wait_branch_is_recorded_for_the_event_log():
    browser = object.__new__(EmailBrowser)
    code = {**field("Verification code"), "id": "c"}
    assert EmailBrowser.state(browser, {"fields": [code], "text": ""})[0] == "code"
    assert browser.state_branch == "code_input"
    digits = [{**field("", maxlength=1), "id": f"d{i}"} for i in range(6)]
    assert EmailBrowser.state(browser, {"fields": digits, "text": "Enter the code"})[0] == "code"
    assert browser.state_branch == "digit_boxes"
    assert EmailBrowser.state(browser, {"fields": [], "text": "Check your email for a link"})[0] == "link"
    assert browser.state_branch == "check_email_text"
    assert EmailBrowser.state(browser, {"fields": [], "text": ""})[0] is None
    assert browser.state_branch == "none"


def test_a_portal_header_search_box_is_never_an_application_field():
    """Planview iCIMS, run 81cdfaf4ee294e99b32314ceaf5bde38, 2026-10-21: the portal header's required "Search for:"
    box (name s) stopped the candidate profile as "The account form needs an answer"."""
    from jobpilot.forms import site_search

    assert site_search({"type": "text", "label": "Search for:"})
    assert site_search({"type": "text", "label": "Search"}) and site_search({"type": "search", "label": "Keywords:"})
    assert not site_search({"type": "text", "label": "Search firm name"})
    assert not site_search({"type": "text", "label": "How did you hear about us?"})
