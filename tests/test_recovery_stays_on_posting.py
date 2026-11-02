"""A recovery agent never reaches another posting (2026-11-02 safety review), and a real hand-off to the employer's own platform still works.

Three ways round the posting guard: the hand-off runs before the guard and took a job list's first Apply link, a control with no address
was never read, and a bare ?id= or ?pid= number was not compared. And "Senior Software Engineer" for "Software Engineer" passed the commit
check and the receipt check, so it would have read as confirmed. Names are invented."""

from types import SimpleNamespace

import pytest

from gallery.scenarios import EMAIL_FIELD, NAME_FIELDS, POSTING, RESUME_FIELD, SUBMIT_BUTTON, THANKS, form, page
from gallery.site import Scenario
from jobpilot.ats import classify_embedded, detect_embedded, handoff_entries
from jobpilot.browser import BrowserEngine, NavigationDecision
from jobpilot.posting_match import PostingWatch, posting_ids, receipt_conflict

WORKDAY = "https://acme.wd5.myworkdayjobs.com/External/job/NY/{}/apply"
OWN_PAGE = "https://careers.acme.test/jobs/1001"
JOB = {"url": OWN_PAGE, "title": "Software Engineer", "company": "ExampleSoft"}


def apply_links(*urls):
    return [("apply", url) for url in urls]


# ----- the hand-off -----------------------------------------------------------------------------------------------------------


def test_a_list_with_several_apply_targets_is_no_hand_off():
    entries = apply_links(WORKDAY.format("Data-Analyst_R100"), WORKDAY.format("Tech-Lead_R200"), WORKDAY.format("Credit-Associate_R300"))
    assert classify_embedded(handoff_entries(entries, [OWN_PAGE])) == (None, None, "")


def test_the_platforms_label_is_still_read_when_nothing_moves_the_run():
    """Naming the platform for a job's label opens nothing, so it isn't held back."""
    entries = apply_links(WORKDAY.format("Data-Analyst_R100"), WORKDAY.format("Tech-Lead_R200"))
    assert classify_embedded(entries)[0] == "workday"
    assert detect_embedded(entries)[0] == "workday"


@pytest.mark.parametrize(
    "entries",
    [
        apply_links(WORKDAY.format("Software-Engineer_R100")),
        # One posting, however many links: top and foot of the page, the posting and its /apply, a tracking parameter.
        apply_links(WORKDAY.format("Software-Engineer_R100"), WORKDAY.format("Software-Engineer_R100")),
        apply_links(WORKDAY.format("Software-Engineer_R100"), WORKDAY.format("Software-Engineer_R100").removesuffix("/apply")),
        apply_links(WORKDAY.format("Software-Engineer_R100") + "?src=top", WORKDAY.format("Software-Engineer_R100") + "?src=foot"),
        # The Apply link and the posting's own structured data.
        [("apply", WORKDAY.format("Software-Engineer_R100")), ("jsonld", WORKDAY.format("Software-Engineer_R100").removesuffix("/apply"))],
    ],
)
def test_a_real_hand_off_from_the_employers_page_to_its_own_platform_still_goes(entries):
    found, url, _ = classify_embedded(handoff_entries(entries, [OWN_PAGE]))
    assert found == "workday" and "Software-Engineer_R100" in url


def test_among_several_the_one_naming_this_job_is_the_hand_off():
    own = "https://careers.acme.test/jobs/R200"
    entries = apply_links(WORKDAY.format("Data-Analyst_R100"), WORKDAY.format("Software-Engineer_R200"), WORKDAY.format("Tech-Lead_R300"))
    found, url, _ = classify_embedded(handoff_entries(entries, [own]))
    assert found == "workday" and "R200" in url


def test_a_frame_holding_the_form_is_still_the_answer_beside_a_list():
    frame = "https://boards.greenhouse.io/embed/job_app?for=acme&token=123"
    entries = [("frame", frame), *apply_links(WORKDAY.format("Data-Analyst_R100"), WORKDAY.format("Tech-Lead_R200"))]
    assert classify_embedded(handoff_entries(entries, [OWN_PAGE]))[:2] == ("greenhouse", frame)


def engine(page_url, entries=(), *, watch=None, obs=None, ats="custom"):
    e = object.__new__(BrowserEngine)
    e.armed, e.result, e.job = False, None, {"ats": ats, "url": OWN_PAGE, "id": "job", "title": "Software Engineer", "company": "ExampleSoft"}
    e.opened, e.started, e.events, e.returned = [], [], [], []

    async def evaluate(_script):
        return list(entries)

    async def goto(url, **_kwargs):
        e.opened.append(url)
        e.page.url = url

    async def scan():
        return obs or {"url": page_url, "fields": [], "controls": [], "text": "", "headings": []}

    async def run_adapters(target):
        e.started.append(target)
        return {"state": "confirmed", "reason": "Website confirmation captured"}

    async def return_to_job():
        e.returned.append(True)

    async def nothing(*_a):
        return None

    e.page = SimpleNamespace(url=page_url, evaluate=evaluate, goto=goto)
    e.form = SimpleNamespace(scan=scan, page=e.page, ledger={}, uploaded=False, reset_document_verification=lambda: None)
    e.emit = lambda kind, message, data=None: e.events.append(kind)
    e.run_adapters, e.return_to_job, e.page_acknowledged = run_adapters, return_to_job, nothing
    e.application_entry_url = OWN_PAGE
    e.persist_observed_ats = lambda *_a: None
    e.db = SimpleNamespace(get_setting=lambda *_a: {})
    if watch:
        e.posting_watch = watch
    return e


async def test_the_navigators_hand_off_takes_no_apply_link_from_a_job_list():
    list_page = "https://careers.acme.test/careers"
    entries = apply_links(WORKDAY.format("Data-Analyst_R100"), WORKDAY.format("Tech-Lead_R200"))
    e = engine(list_page, entries)
    assert await e.navigator_handoff() is None
    assert e.opened == [] and e.started == [] and e.job["url"] == OWN_PAGE


async def test_the_navigators_hand_off_still_follows_the_one_apply_link():
    e = engine(OWN_PAGE, apply_links(WORKDAY.format("Software-Engineer_R100")))
    e.job["url"] = OWN_PAGE
    assert (await e.navigator_handoff())["state"] == "confirmed"
    assert e.started == ["workday"] and len(e.opened) == 1


LIST_OBS = {
    "url": "https://acme.wd5.myworkdayjobs.com/External",
    "fields": [],
    "headings": ["Open roles"],
    "text": "Open roles",
    "controls": [{"id": str(n), "label": label, "href": f"/External/job/{n}"} for n, label in enumerate(
        ("Data Analyst", "Credit Risk Associate", "Kubernetes Tech Lead", "Product Designer"))],
}


async def test_a_job_list_on_another_platform_is_read_before_any_hand_off():
    """The guard ran after the hand-off, so a list on another platform went straight to that platform's adapter as this job's."""
    e = engine(LIST_OBS["url"], watch=PostingWatch(dict(JOB), entry_url=OWN_PAGE), obs=LIST_OBS)
    assert await e.navigator_handoff() is None
    assert e.started == [] and e.returned == [True] and e.job["url"] == OWN_PAGE
    assert "posting_guard" in e.events


async def test_the_employers_own_posting_on_its_platform_still_hands_off():
    url = WORKDAY.format("Software-Engineer_R100").removesuffix("/apply")
    obs = {"url": url, "fields": [], "headings": ["Software Engineer"], "text": "Software Engineer",
           "controls": [{"id": "a", "label": "Apply", "href": url + "/apply"}]}
    e = engine(url, watch=PostingWatch(dict(JOB), entry_url=OWN_PAGE), obs=obs)
    assert (await e.navigator_handoff())["state"] == "confirmed"
    assert e.started == ["workday"] and e.returned == []


# ----- a control with no address --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("href", ["", None, "#", "javascript:void(0)"])
def test_a_control_with_no_address_is_read_by_its_label(href):
    watch = PostingWatch(dict(JOB))
    assert watch.other_posting_link(href, OWN_PAGE, "Data Analyst")
    assert watch.other_posting_link(href, OWN_PAGE, "Senior Software Engineer")


@pytest.mark.parametrize("label", ["Apply", "Next", "Continue", "Software Engineer", "Open the data role", "Save", ""])
def test_a_control_with_no_address_and_no_other_role_in_its_label_stays(label):
    assert not PostingWatch(dict(JOB)).other_posting_link("", OWN_PAGE, label)


def test_on_a_form_a_button_labelled_as_a_role_is_an_answer():
    """"Which role interests you?" with a button for each: the page is asking, and an address-less button is one of its choices."""
    assert not PostingWatch(dict(JOB)).other_posting_link("", OWN_PAGE, "Data Analyst", asking=True)


def test_mail_and_phone_links_are_never_a_posting():
    assert not PostingWatch(dict(JOB)).other_posting_link("mailto:hr@example.test", OWN_PAGE, "Hiring Manager")


# ----- a bare number in the address ---------------------------------------------------------------------------------------------


def test_a_bare_number_names_a_posting_only_when_nothing_better_does():
    assert posting_ids("https://acme.com/job?id=1234") == {"1234"}
    assert posting_ids("https://acme.com/job/56789?id=1234") == {"56789"}
    assert posting_ids("https://acme.com/job?id=eng") == set()
    assert posting_ids("https://acme.com/careers?pid=563431&domain=acme.com") == {"563431"}


def test_another_number_on_the_same_page_is_another_posting_for_the_watch():
    own = "https://acme.com/job?id=1234"
    watch = PostingWatch({**JOB, "url": own})
    assert watch.other_address("https://acme.com/job?id=5678")
    assert watch.other_posting_link("https://acme.com/job?id=5678", own, "Apply")
    assert not watch.other_address(own)
    assert not watch.other_posting_link(own + "&step=2", own, "Apply")
    assert watch.judge("https://acme.com/job?id=5678", {"fields": [], "controls": [], "headings": []}).kind == "other_posting"


# ----- a different seniority word ---------------------------------------------------------------------------------------------


def receipt(words, url="https://careers.acme.test/thanks"):
    return {"confirmation": words, "url": url}


def test_a_receipt_for_the_senior_role_is_not_a_confirmation_of_this_job():
    assert receipt_conflict(JOB, receipt("Your application for Senior Software Engineer has been submitted."))
    assert receipt_conflict({**JOB, "title": "Senior Software Engineer"}, receipt("Your application for Software Engineer has been submitted."))


@pytest.mark.parametrize(
    "words",
    [
        "Your application for Software Engineer has been submitted.",
        "Thank you for applying!",  # Names nothing, and confirms.
        "Your application for Software Engineer, Platform has been submitted.",
    ],
)
def test_a_receipt_for_this_role_or_for_nothing_still_confirms(words):
    assert receipt_conflict(JOB, receipt(words)) is None


def test_the_jobs_own_number_outweighs_a_renamed_title():
    own = {**JOB, "url": "https://careers.acme.test/jobs/1001"}
    assert receipt_conflict(own, receipt("Your application for Senior Software Engineer has been submitted.",
                                          "https://careers.acme.test/jobs/1001/thanks")) is None
    assert PostingWatch(own).other_posting("https://careers.acme.test/jobs/1001/apply", {"headings": ["Apply for Senior Software Engineer"]}) == ""


def test_the_commit_check_stops_on_a_form_for_the_senior_role():
    watch = PostingWatch(dict(JOB))
    assert "Senior Software Engineer" in watch.other_posting("https://careers.acme.test/apply/1", {"headings": ["Apply for Senior Software Engineer"]})
    assert watch.other_posting("https://careers.acme.test/apply/1", {"headings": ["Apply for Software Engineer"]}) == ""


# ----- through the real engine ----------------------------------------------------------------------------------------------------


@pytest.mark.browser
@pytest.mark.gallery
@pytest.mark.integration
async def test_the_model_is_never_offered_a_role_button_with_no_address(service, monkeypatch):
    from test_gallery import explain, run_scenario

    seen = []

    async def structured(_config, _db, schema, _system, payload, *_args, **_kwargs):
        import json

        assert schema is NavigationDecision
        controls = json.loads(payload)["observation"]["controls"]
        seen.append([c["label"] for c in controls])
        for control in controls:
            if control["label"] == "Data Analyst":
                return NavigationDecision(action="click", control_id=control["id"], reason="wander")
        return NavigationDecision(action="help", reason="Nothing here for this job")

    monkeypatch.setattr("jobpilot.models.structured", structured)
    other = "/careers/JobDetail/Data-Analyst/62002"
    scenario = Scenario(
        "role_button", "",
        {"/jobs/1": page("Software Engineer", POSTING + f"""<button type="button" onclick="location.href='{other}'">Data Analyst</button>"""),
         other: page("Data Analyst", '<h1>Data Analyst</h1><p><a class="apply" href="/apply/2">Apply</a></p>')},
    )  # fmt: skip
    site, outcome, events = await run_scenario(service, scenario)
    why = explain(scenario, site, outcome, events, service.config.data_dir)
    assert seen, why
    assert not any("Data Analyst" in labels for labels in seen), why
    assert other not in site.hits and site.posts == [], why


@pytest.mark.browser
@pytest.mark.gallery
@pytest.mark.integration
@pytest.mark.parametrize(
    "heading,state,applications",
    [
        ("Apply for Senior Software Engineer", "waiting_browser", 0),  # Stopped at the commit, with nothing sent.
        ("Apply for Software Engineer", "confirmed", 1),
    ],
)
async def test_the_commit_stops_on_the_senior_form_and_sends_the_right_one(service, heading, state, applications):
    from test_gallery import explain, run_scenario

    scenario = Scenario(
        "senior_form", "",
        {"/jobs/1": page("Software Engineer", POSTING + '<p><a class="apply" href="/apply/1">Apply for this job</a></p>'),
         "/apply/1": page("Apply", f"<h1>{heading}</h1>" + form(NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, SUBMIT_BUTTON)),
         "/submit/1": THANKS},
    )  # fmt: skip
    site, outcome, events = await run_scenario(service, scenario)
    why = explain(scenario, site, outcome, events, service.config.data_dir)
    assert outcome["state"] == state, why
    assert len(site.applications()) == applications, why


@pytest.mark.browser
@pytest.mark.gallery
@pytest.mark.integration
@pytest.mark.parametrize(
    "words,state",
    [
        ("Your application for Senior Software Engineer has been submitted.", "submission_unknown"),
        ("Your application for Software Engineer has been submitted.", "confirmed"),
    ],
)
async def test_a_receipt_for_the_senior_role_never_reads_as_confirmed(service, words, state):
    from test_gallery import explain, run_scenario

    scenario = Scenario(
        "senior_receipt", "",
        {"/jobs/1": page("Software Engineer", POSTING + '<p><a class="apply" href="/apply/1">Apply for this job</a></p>'),
         "/apply/1": page("Apply", "<h1>Application</h1>" + form(NAME_FIELDS, EMAIL_FIELD, RESUME_FIELD, SUBMIT_BUTTON)),
         "/submit/1": page("Thank you", f"<h1>Thank you</h1><p>{words}</p>")},
    )  # fmt: skip
    site, outcome, events = await run_scenario(service, scenario)
    why = explain(scenario, site, outcome, events, service.config.data_dir)
    assert outcome["state"] == state, why
    assert len(site.applications()) == 1, why
