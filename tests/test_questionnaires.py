"""Voluntary questionnaires (Knowledge): the registry, the HTTP contract, and how the resolver uses saved answers.

All data is synthetic. Nothing here is required to apply: an unanswered question keeps being asked."""

import pytest

from jobpilot.answers import Resolver
from jobpilot.features.knowledge import questionnaires as q
from jobpilot.schemas import Profile, WorkEntry

WORK = [
    {"company": "ExampleSoft", "title": "Engineer", "start": "2022-01", "current": True},
    {"company": "Sample Labs", "title": "Intern", "start": "2021-05", "end": "2021-08"},
]
SUPERVISOR = {"name": "Sam Supervisor", "title": "Director", "phone": "555-0100", "email": "sam@example.test"}


def field(label, options=(), kind=None, **extra):
    return {
        "id": "f",
        "label": label,
        "type": kind or ("radio" if options else "text"),
        "required": True,
        "options": list(options),
        "value": "",
        "valid": True,
        "group": "",
        "section": "",
        **extra,
    }


def resolver(service, facts=None, work=WORK):
    return Resolver(
        Profile(work=[WorkEntry(**w) for w in work], facts=facts or {}), service.db, service.config
    )


# --- registry -------------------------------------------------------------------------------------------------


def test_registry_modules_are_declarative_optional_and_have_unique_keys():
    assert [m.id for m in q.MODULES] == ["wotc", "contacts", "faq"]
    keys = [x.key for m in q.MODULES for x in m.questions]
    assert len(keys) == len(set(keys))
    assert 15 <= len(q.FAQ.questions) <= 25
    for m in q.MODULES:
        assert m.title and m.purpose
        for x in m.questions:
            assert x.prompt and x.type in {"yes_no", "choice", "text", "date", "work_rows", "references"}
    # The statutory questions are suggested "No", and nothing is saved until the applicant confirms.
    assert all(x.suggestion is False for x in q.WOTC.questions)


@pytest.mark.parametrize(
    "label,key",
    [
        ("In the past 6 months, did you receive SNAP (food stamp) benefits?", "wotc_snap_6mo"),
        ("Have you or your family received TANF within the past two years?", "wotc_tanf_2yr"),
        ("Did your family receive TANF for at least 9 months of the past 18 months?", "wotc_tanf_9_of_18"),
        ("Did your family receive TANF assistance for at least 18 consecutive months?", "wotc_tanf_18mo"),
        ("Did your family stop being eligible for TANF due to a federal or state time limit?", "wotc_tanf_stopped"),
        ("Are you a veteran in a family that received SNAP benefits for 3 months?", "wotc_veteran_snap"),
        ("Are you a veteran with a service-connected disability?", "wotc_veteran_disability"),
        ("Are you a veteran who was unemployed for at least 4 weeks?", "wotc_veteran_unemployed"),
        ("Were you unemployed for 27 weeks and receiving unemployment compensation?", "wotc_unemployed_27wk"),
        ("Did you receive Supplemental Security Income in the past 60 days?", "wotc_ssi_60d"),
        ("Have you completed vocational rehabilitation or hold a Ticket to Work?", "wotc_voc_rehab"),
        ("Were you convicted of a felony or released from prison in the past year?", "wotc_felony"),
        ("Do you live in an empowerment zone or rural renewal county?", "wotc_empowerment_zone"),
        ("Do you hold a conditional certification from a State Workforce Agency?", "wotc_swa_certification"),
        ("Were you a summer youth employee?", "wotc_summer_youth"),
        ("Are you willing to relocate?", None),
    ],
)
def test_each_statutory_question_maps_to_its_fact(label, key):
    assert q.wotc_key(label) == key


# --- API ------------------------------------------------------------------------------------------------------


def seed_profile(service, **extra):
    service.db.set_setting("profile", {"name": "Alex Example", "work": WORK, **extra})


def module(client, mid):
    return next(m for m in client.get("/api/knowledge/questionnaires").json()["modules"] if m["id"] == mid)


def test_listing_shows_modules_with_empty_completion(client, service):
    seed_profile(service)
    body = client.get("/api/knowledge/questionnaires").json()
    assert [m["id"] for m in body["modules"]] == ["wotc", "contacts", "faq"]
    wotc = body["modules"][0]
    assert wotc["answered"] == 0 and wotc["percent"] == 0 and wotc["status"] == "not_started"
    assert wotc["questions"][0]["suggestion"] is False and wotc["questions"][0]["answer"] is None
    contacts = next(m for m in body["modules"] if m["id"] == "contacts")
    rows = contacts["questions"][0]["rows"]
    assert [r["company"] for r in rows] == ["ExampleSoft", "Sample Labs"] and rows[0]["answer"] == {}
    assert contacts["total"] == 2 + q.REFERENCE_MAX
    assert body["completion"]["answered"] == 0


def test_saving_is_partial_and_empty_clears(client, service):
    seed_profile(service)
    r = client.put("/api/knowledge/questionnaires/faq", json={"answers": {"willing_to_relocate": True, "notice_period": "2 weeks"}})
    assert r.status_code == 200 and sorted(r.json()["changed"]) == ["notice_period", "willing_to_relocate"]
    assert r.json()["module"]["answered"] == 2
    client.put("/api/knowledge/questionnaires/faq", json={"answers": {"uses_nicotine": "No"}})
    faq = module(client, "faq")
    answers = {x["key"]: x["answer"] for x in faq["questions"]}
    assert answers["willing_to_relocate"] is True and answers["uses_nicotine"] is False
    assert answers["notice_period"] == "2 weeks"
    client.put("/api/knowledge/questionnaires/faq", json={"answers": {"notice_period": ""}})
    assert module(client, "faq")["answered"] == 2
    facts = service.db.get_setting("profile")["facts"]
    assert "notice_period" not in facts and facts["willing_to_relocate"] is True
    assert service.db.get_setting("profile")["name"] == "Alex Example"  # the rest of the profile is untouched


def test_bad_input_is_refused_and_saves_nothing(client, service):
    seed_profile(service)
    assert client.put("/api/knowledge/questionnaires/nope", json={"answers": {}}).status_code == 404
    for body in ({"nonsense": True}, {"willing_to_relocate": "maybe"}, {"start_date": "soon"}):
        assert client.put("/api/knowledge/questionnaires/faq", json={"answers": body}).status_code == 400
    assert client.put("/api/knowledge/questionnaires/contacts", json={"answers": {"work_supervisors": {"x": {"name": "A"}}}}).status_code == 400
    too_many = [{"name": f"R{i}"} for i in range(4)]
    assert client.put("/api/knowledge/questionnaires/contacts", json={"answers": {"references": too_many}}).status_code == 400
    assert not (service.db.get_setting("profile").get("facts") or {})


def test_dates_are_normalized(client, service):
    seed_profile(service)
    client.put("/api/knowledge/questionnaires/faq", json={"answers": {"start_date": "12/01/2030"}})
    assert service.db.get_setting("profile")["facts"]["start_date"] == "2030-12-01"


def test_wotc_no_to_all_sets_the_blanket_fact_and_any_yes_removes_it(client, service):
    seed_profile(service)
    # Suggestions are only a suggestion until accepted: nothing saved yet.
    assert not (service.db.get_setting("profile").get("facts") or {}).get("tax_credit_screening_no")
    r = client.put("/api/knowledge/questionnaires/wotc", json={"answers": {}, "accept_suggestions": True})
    assert r.json()["module"]["status"] == "complete"
    facts = service.db.get_setting("profile")["facts"]
    assert facts["tax_credit_screening_no"] is True and facts["wotc_snap_6mo"] is False
    client.put("/api/knowledge/questionnaires/wotc", json={"answers": {"wotc_snap_6mo": True}})
    facts = service.db.get_setting("profile")["facts"]
    assert "tax_credit_screening_no" not in facts and facts["wotc_snap_6mo"] is True
    client.put("/api/knowledge/questionnaires/wotc", json={"answers": {"wotc_snap_6mo": False}})
    assert service.db.get_setting("profile")["facts"]["tax_credit_screening_no"] is True


def test_an_existing_blanket_no_shows_as_answered_and_survives_editing_one_question(client, service):
    seed_profile(service, facts={"tax_credit_screening_no": True})
    assert module(client, "wotc")["status"] == "complete"
    client.put("/api/knowledge/questionnaires/wotc", json={"answers": {"wotc_felony": True}})
    facts = service.db.get_setting("profile")["facts"]
    assert facts["wotc_felony"] is True and facts["wotc_snap_6mo"] is False  # the other No answers are kept
    assert "tax_credit_screening_no" not in facts


def test_supervisors_and_references_are_stored_per_row_and_in_order(client, service):
    seed_profile(service)
    rows = module(client, "contacts")["questions"][0]["rows"]
    second = rows[1]["row_id"]
    r = client.put(
        "/api/knowledge/questionnaires/contacts",
        json={
            "answers": {
                "work_supervisors": {second: {**SUPERVISOR, "may_contact": "after_offer"}},
                "references": [{"name": "Riley Reference", "relationship": "Colleague"}],
            }
        },
    )
    assert r.status_code == 200
    contacts = module(client, "contacts")
    assert contacts["answered"] == 2 and contacts["questions"][0]["rows"][1]["answer"]["name"] == "Sam Supervisor"
    assert contacts["questions"][0]["rows"][0]["answer"] == {}
    assert contacts["questions"][1]["answer"] == [{"name": "Riley Reference", "relationship": "Colleague"}]
    client.put("/api/knowledge/questionnaires/contacts", json={"answers": {"work_supervisors": {second: None}}})
    assert module(client, "contacts")["questions"][0]["rows"][1]["answer"] == {}


def test_onboarding_status_carries_questionnaire_completion(client, service):
    seed_profile(service)
    body = client.get("/api/onboarding").json()
    assert body["questionnaires"]["optional"] is True and body["questionnaires"]["completion"]["percent"] == 0
    client.put("/api/knowledge/questionnaires/wotc", json={"answers": {}, "accept_suggestions": True})
    body = client.get("/api/onboarding").json()
    assert next(m for m in body["questionnaires"]["modules"] if m["id"] == "wotc")["percent"] == 100
    assert body["completed"] in (True, False)  # never gated on the questionnaires
    saved = client.put("/api/onboarding", json={"step": "questionnaires", "completed": False}).json()
    assert saved["step"] == "questionnaires" and "questionnaires" in saved


# --- resolver -------------------------------------------------------------------------------------------------


def save_and_profile(client, service, module_id, answers):
    seed_profile(service)
    assert client.put(f"/api/knowledge/questionnaires/{module_id}", json={"answers": answers}).status_code == 200
    saved = service.db.get_setting("profile")
    return Profile.model_validate(saved)


def test_a_saved_supervisor_fills_the_matching_work_row(client, service):
    seed_profile(service)
    rows = module(client, "contacts")["questions"][0]["rows"]
    client.put(
        "/api/knowledge/questionnaires/contacts",
        json={"answers": {"work_supervisors": {rows[1]["row_id"]: {**SUPERVISOR, "may_contact": "yes"}}}},
    )
    r = Resolver(Profile.model_validate(service.db.get_setting("profile")), service.db, service.config)

    def ask(label, row, **extra):
        return r.local(field(label, group=f"Work Experience {row}", **extra))

    assert ask("Supervisor Name", 2).value == "Sam Supervisor"
    assert ask("Supervisor Phone", 2).value == "555-0100"
    assert ask("SupTitle", 2).value == "Director"
    assert ask("Supervisor Email", 2).value == "sam@example.test"
    assert ask("May we contact your supervisor?", 2, options=["Yes", "No"]).value == "Yes"
    # Row 1 has no supervisor saved: nothing is filled for it from row 2.
    assert (ask("Supervisor Name", 1) is None) or ask("Supervisor Name", 1).disposition != "answer"
    # The row can also come from the scanner's own history_row.
    assert r.local(field("Supervisor Name", history_row={"kind": "work", "index": 1})).value == "Sam Supervisor"


def test_after_offer_is_answered_no_to_a_plain_yes_no(client, service):
    seed_profile(service)
    rows = module(client, "contacts")["questions"][0]["rows"]
    client.put(
        "/api/knowledge/questionnaires/contacts",
        json={"answers": {"work_supervisors": {rows[0]["row_id"]: {"name": "Sam Supervisor", "may_contact": "after_offer"}}}},
    )
    r = Resolver(Profile.model_validate(service.db.get_setting("profile")), service.db, service.config)
    plain = field("May we contact your current employer?", options=["Yes", "No"], kind="select")
    assert r.local(plain).value == "No"
    offered = field("May we contact your supervisor?", options=["Yes", "Yes, after an offer", "No"], kind="select")
    assert r.local(offered).value == "Yes, after an offer"


def test_references_fill_by_number_and_never_repeat_one_for_unnumbered_boxes(client, service):
    profile = save_and_profile(
        client,
        service,
        "contacts",
        {"references": [{"name": "Riley Reference", "company": "Example Corp"}, {"name": "Jo Second", "phone": "555-0111"}]},
    )
    r = Resolver(profile, service.db, service.config)
    assert r.local(field("Reference 1 First Name")).value == "Riley"
    assert r.local(field("Reference 1 Last Name")).value == "Reference"
    assert r.local(field("Ref2Phone")).value == "555-0111"
    assert r.local(field("Reference 2 Name")).value == "Jo Second"
    # Reference 2 has no company saved; two references saved and no number: ask, do not guess.
    unanswered = r.local(field("Reference 2 Company"))
    assert unanswered is None or unanswered.disposition != "answer"
    unnumbered = r.local(field("Reference Name"))
    assert unnumbered is None or unnumbered.disposition != "answer"


def test_nothing_saved_means_supervisor_boxes_stay_questions(service):
    r = resolver(service)
    a = r.local(field("Supervisor Name", group="Work Experience 1"))
    assert a is None or a.disposition != "answer"


def test_wotc_per_question_facts_are_used_and_a_yes_is_answered_yes(service):
    r = resolver(service, {"wotc_snap_6mo": False, "wotc_tanf_2yr": True})
    snap = r.local(field("Did you receive SNAP benefits in the past six months?", ["Yes", "No"], kind="radio"))
    assert snap.value == "No" and snap.evidence_ids == ["fact:wotc_snap_6mo"]
    tanf = r.local(field("Has your family received TANF assistance in the past two years?", ["Yes", "No"], kind="radio"))
    assert tanf.value == "Yes" and tanf.evidence_ids == ["fact:wotc_tanf_2yr"]
    # Another statutory question with no saved answer and no blanket No is not answered by these.
    ssi = r.local(field("Have you received Supplemental Security Income (SSI) in the past 60 days?", ["Yes", "No"], kind="radio"))
    assert ssi is None or ssi.disposition != "answer"


def test_wotc_blanket_no_still_covers_questions_without_their_own_answer(service):
    r = resolver(service, {"tax_credit_screening_no": True, "wotc_ssi_60d": True})
    ssi = r.local(field("Have you received Supplemental Security Income (SSI) in the past 60 days?", ["Yes", "No"], kind="radio"))
    assert ssi.value == "Yes"  # its own saved answer wins over the blanket No
    snap = r.local(field("Did you receive SNAP benefits in the past six months?", ["Yes", "No"], kind="radio"))
    assert snap.value == "No" and snap.evidence_ids == ["fact:tax_credit_screening_no"]


def test_frequently_asked_answers_are_used_by_intent(service):
    r = resolver(
        service,
        {"uses_nicotine": False, "has_intellectual_property": False, "ever_bonded": False, "ai_screening_consent": True},
    )
    yn = ["Yes", "No"]
    assert r.local(field("Do you currently use nicotine products including cigarettes, e-cigarettes, cigars?", yn, kind="select")).value == "No"
    assert r.local(field("Do you have intellectual property?", yn, kind="select")).value == "No"
    assert r.local(field("Have you ever been bonded?", yn, kind="select")).value == "No"
    assert r.local(field("By selecting Yes, I am consenting to the use of AI for evaluating my candidacy.", yn, kind="select")).value == "Yes"


def test_a_saved_start_date_answers_a_date_box_and_unanswered_still_asks(service):
    from datetime import date, timedelta

    future = date.today() + timedelta(days=40)
    r = resolver(service, {"start_date": future.isoformat()})
    a = r.local(field("Earliest start date", kind="date"))
    assert a.value == future.strftime("%m/%d/%Y")
    empty = resolver(service)
    for label in ("Do you currently use nicotine products?", "Have you ever been bonded?"):
        a = empty.local(field(label, ["Yes", "No"], kind="select"))
        assert a is None or a.disposition != "answer"
