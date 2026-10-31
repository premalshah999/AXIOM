"""One application, one story: no work-authorization contradiction reaches the final submit (2026-10)."""

from types import SimpleNamespace

from jobpilot.answer_quality import supported_local, work_status_problems, wrong_citizenship
from jobpilot.answers import Resolver
from jobpilot.browser import BrowserEngine
from jobpilot.schemas import Profile

OPT = {
    "us_citizen": False,
    "country_of_citizenship": "Freedonia",
    "work_authorization_US": "Yes, authorized to work for any employer in the USA (applicant confirmed)",
    "requires_future_sponsorship_US": False,
    "current_immigration_status_US": "OPT; expiration July 2029. Provide only when specifically asked for status or expiration.",
}
CITIZEN = {"us_citizen": True, "work_authorization_US": True, "requires_future_sponsorship_US": False}


def profile(facts):
    return Profile(facts=facts)


def engine(facts, fields=None, ledger=None):
    e = object.__new__(BrowserEngine)
    e.form = SimpleNamespace(
        resolver=SimpleNamespace(profile=profile(facts)), fields=fields or {}, ledger=ledger or {}, failed_strategies={}
    )
    e.emit = lambda *a, **k: None
    return e


def row(field_id, label, value, kind="select", options=("Yes", "No"), **extra):
    return {
        "id": field_id,
        "label": label,
        "type": kind,
        "value": value,
        "options": list(options),
        "section": "",
        "group": "",
        "slot": 0,
        "required": True,
        "valid": True,
        **extra,
    }


def check(facts, *rows, ledger=None):
    return work_status_problems(engine(facts, {r["id"]: r for r in rows}, ledger).form)


def test_a_contradiction_with_the_facts_is_a_problem_and_stops_the_submit():
    credence = row("a", "Are you legally authorized to work in the U.S. as a U.S. citizen or Fully Naturalized U.S. Citizen?", "Yes")
    assert len(check(OPT, credence)) == 1
    assert not check(OPT, {**credence, "value": "No"})
    assert not check(CITIZEN, credence)
    e = engine(OPT, {"a": credence})
    stop = e.work_status_stop()
    assert stop["state"] == "waiting_browser" and stop["recoverable"] is False and "U.S. citizen" in stop["reason"]
    assert engine(OPT, {"a": {**credence, "value": "No"}}).work_status_stop() is None


def test_a_saved_page_is_checked_too():
    record = {
        "label": "Are you a U.S. citizen?",
        "type": "select",
        "answer": "Yes",
        "verified": True,
        "options": ["Yes", "No"],
        "field_key": ["are you a us citizen", "select", 0, "", "", "", "", ""],
        "evidence_ids": ["fact:us_citizen"],
    }
    assert len(check(OPT, ledger={"old": record})) == 1


def test_a_false_status_option_and_a_typed_status_are_problems():
    assert check(OPT, row("a", "Citizenship Status", "(a) U.S. citizen or national of the United States", options=["(a) U.S. citizen or national of the United States", "(f) Other (please explain)"]))
    assert not check(OPT, row("a", "Citizenship Status", "(f) Other (please explain)", options=["(a) U.S. citizen or national of the United States", "(f) Other (please explain)"]))
    assert check(OPT, row("a", "Will you now or in the future require sponsorship for employment visa status (e.g., H-1B visa status)?", "F-1 OPT", "text", ()))


def test_answers_the_facts_cannot_settle_must_agree():
    first = row("a", "Will you now or in the future require visa sponsorship?", "No")
    second = row("b", "Do you now or will you in the future need sponsorship?", "Yes")
    assert len(check({}, first, second)) == 1
    assert not check({}, first, {**second, "value": "No"})
    # With the fact recorded each is checked against it, and a definition may truthfully differ from the plain answer.
    defined = row("c", "Will you require sponsorship (e.g., OPT, H-1B)?", "Yes")
    assert not check(OPT, first, defined)


def test_a_wrong_country_of_citizenship():
    menu = {"label": "In which country/region do you have citizenship?", "type": "combobox"}
    assert wrong_citizenship({**menu, "value": "Laos\nLatvia"}, OPT)
    assert wrong_citizenship({**menu, "value": "Freedonia\nLatvia"}, OPT)
    assert not wrong_citizenship({**menu, "value": "Freedonia"}, OPT)
    status = {"label": "Citizenship Status", "type": "prompt"}
    assert not wrong_citizenship({**status, "value": "Citizen (Freedonia)"}, OPT)
    assert wrong_citizenship({**status, "value": "Non-citizen (Afghanistan)"}, OPT)
    assert not wrong_citizenship({"label": "Country", "type": "combobox", "value": "Laos"}, OPT)
    assert not wrong_citizenship({**status, "value": "(f) Other (please explain)"}, OPT)
    assert not wrong_citizenship({**menu, "value": "My country of citizenship is not on this list"}, OPT)


def form_with(service, facts):
    return SimpleNamespace(resolver=Resolver(profile(facts), service.db, service.config))


def test_the_audit_holds_the_definition_above_saved_answers(service):
    form = form_with(service, OPT)
    field = row("a", "Do you have the indefinite right to work in the United States?", "Yes")
    known = supported_local(form, field)
    assert known.value == "No" and any(ref.startswith("fact:") for ref in known.evidence_ids)
    # Same polarity in the form's own words: nothing to repair, so no loop on it.
    worded = row("b", "Do you have the indefinite right to work in the United States?", "No, not indefinitely", options=("Yes, I do", "No, not indefinitely"))
    assert supported_local(form, worded).value == "No, not indefinitely"
