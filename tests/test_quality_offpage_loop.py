"""A blocked answer that is not on the current page must not loop the run (2026-10-29).

- Montenson (Oracle): the audit at the final submit blocked an answer written on the ADP tax-credit questionnaire
  (`on_page: false`, `revisit_history_row`); no control of Oracle's pages shows it, so fill / click Tax Credits repeated
  until the run stopped on "needs a fresh field observation".
- Belden (SuccessFactors): "Is this your current position?" was read as the job title and "Type of Company" as the
  company name, so two correct work-row answers were blocked the same way."""
from collections import Counter
from types import SimpleNamespace

from jobpilot import answer_quality as aq
from jobpilot.adapters.base import Adapter
from jobpilot.answers import Resolver
from jobpilot.browser import BrowserEngine
from jobpilot.forms import FormSession
from jobpilot.schemas import Answer, Profile

SF_WORK = "what is your previous work experience"


def profile():
    return Profile(
        email="alex@example.test",
        work=[
            {"company": "Acme Labs", "title": "Engineer", "start": "2025-12", "end": "", "current": True},
            {"company": "Beta Co", "title": "Assistant", "start": "2025-07", "end": "2026-05", "current": False},
        ],
    )


def record(label, answer, group="work experience 2", kind="text", **extra):
    return {
        "label": label, "type": kind, "slot": 0, "answer": answer, "verified": True,
        "field_key": [label.lower(), kind, 0, SF_WORK, group, "", ""], "evidence_ids": ["profile:confirmed"],
        "comparison": {"required": True}, **extra,
    }


def form(service, ledger):
    resolver = Resolver(profile(), service.db, service.config,
                        job={"ats": "successfactors", "url": "https://careers.example.test/x"})
    return SimpleNamespace(resolver=resolver, ledger=ledger, fields={}, optional_picker_omissions=set(),
                           field_key=lambda f: (f["label"].lower(), f["type"], f.get("slot", 0)), failed_strategies={})


def findings(value):
    return aq.local_corrections(value, aq.candidates(value))


def test_a_yes_no_current_position_question_is_not_the_job_title(service):
    no = form(service, {"a": record("Is this your current position?", "No", kind="radio")})
    assert findings(no) == {}  # the second row ended: "No" is the recorded fact
    yes = form(service, {"a": record("Is this your current position?", "Yes", kind="radio")})
    assert list(findings(yes)) == ["a"]  # a genuine mismatch is still caught, against "No" and not the title
    known = aq.supported_local(yes, aq.candidates(yes)[0])
    assert known.value == "No" and known.evidence_ids == ["profile:work.1.current"]


def test_a_company_classification_is_not_the_company_name(service):
    for label in ("Type of Company", "Company Size", "Employer type", "Industry"):
        assert findings(form(service, {"a": record(label, "Other")})) == {}, label
    # The name itself is still read from the profile.
    assert list(findings(form(service, {"a": record("Company", "Wrong Co")}))) == ["a"]


def test_an_answer_written_on_the_questionnaire_host_is_not_audited(service):
    wrong = record("Company", "Wrong Co", external=True)
    assert aq.candidates(form(service, {"a": wrong})) == []
    assert list(findings(form(service, {"a": record("Company", "Wrong Co")}))) == ["a"]


def test_the_ledger_marks_answers_recorded_on_the_questionnaire(service):
    def recorded(on_questionnaire):
        fake = SimpleNamespace(
            ledger={}, resolver=Resolver(profile(), service.db, service.config), field_key=lambda f: ("k",),
            catalog_digest=lambda f: "", question_shape=lambda f: "", on_questionnaire=lambda: on_questionnaire)
        field = {"id": "f1", "label": "Company", "type": "text"}
        FormSession.record_answer(fake, field, "Acme Labs", ["profile:work.0.company"], verified=True)
        return fake.ledger["f1"]["external"]

    assert recorded(True) is True and recorded(False) is False


def blocked_form(failed=None):
    """An unchanged earlier-step finding (the field is not on the page)."""
    field = {"id": "q", "label": "Company", "type": "text", "required": True, "value": "Wrong", "valid": True,
             "editable": False, "section": SF_WORK, "group": "work experience 2", "slot": 0, "name": "",
             "automation_id": ""}
    answer = Answer(field_id="q", disposition="answer", value="Beta Co", evidence_ids=["profile:work.1.company"])
    value = SimpleNamespace(failed_strategies=failed if failed is not None else {}, expected={}, expected_slots={})
    value.resolver = None
    return value, field, answer


async def audit(service, monkeypatch, value, field, answer):
    service.config.answer_quality_enabled = True
    value.resolver = Resolver(profile(), service.db, service.config)
    monkeypatch.setattr(aq, "candidates", lambda *args, **kwargs: [dict(field)])
    monkeypatch.setattr(aq, "local_corrections", lambda *args: {"q": (answer, "confirmed_fact_mismatch")})

    async def scan():
        return {}

    value.scan, value.emit = scan, lambda *args: None
    return await aq.audit_form(value)


async def test_a_row_whose_step_could_not_be_reopened_is_reported_once_with_its_name(service, monkeypatch):
    value, field, answer = blocked_form()
    first = await audit(service, monkeypatch, value, field, answer)
    assert first["pending"][0]["repair_action"] == "revisit_history_row" and first["pending"][0]["retryable"]
    aq.stop_revisit(value, first["pending"][0])
    stopped = (await audit(service, monkeypatch, value, field, answer))["pending"][0]
    assert stopped["retryable"] is False and "repair_action" not in stopped
    assert '"Company"' in stopped["reason"] and "Wrong" in stopped["reason"] and "Beta Co" in stopped["reason"]


def driver(failed):
    adapter = object.__new__(Adapter)
    adapter.committed, adapter.attempts, adapter.index = False, Counter(), 0

    async def scan():
        return {"url": "https://employer.test/apply", "headings": [], "fields": [], "controls": []}

    adapter.emit = lambda *args: None
    form = SimpleNamespace(scan=scan, failed_strategies=failed)
    adapter.e = SimpleNamespace(armed=False, form=form)
    return adapter


def finding(question="Company"):
    return {"report": {"pending": [{"question": question, "type": "text", "repair_action": "revisit_history_row",
                                    "scope": {"section": SF_WORK, "group": "work experience 2", "slot": 0}}]}}


async def test_a_failed_walk_back_marks_the_row_and_is_not_repeated():
    failed = {}
    adapter = driver(failed)
    assert not await adapter.revisit_history(finding())  # no Back control and no such field: the walk fails
    assert len(failed["__quality_revisit_failed__"]) == 1
    assert not await adapter.revisit_history(finding())
    assert len(failed["__quality_revisit_failed__"]) == 1


async def test_the_gate_stops_a_finding_reviewed_three_times_unchanged_and_names_it():
    value, field, answer = blocked_form()
    item = {"field_id": "q", "question": "Company", "type": "text", "retryable": True,
            "repair_action": "revisit_history_row", "blocker_type": "control_failure",
            "scope": {"section": SF_WORK, "group": "work experience 2", "slot": 0, "name": "", "automation_id": ""}}
    calls = []

    async def review(**kwargs):
        calls.append(1)
        if aq.revisit_key(item) in value.failed_strategies.get("__quality_revisit_failed__", {}):
            return {"ok": False, "pending": [{**item, "retryable": False, "repair_action": None,
                                              "reason": 'Earlier-step answer "Company" is "Wrong" but the confirmed '
                                                        'fact is "Beta Co", and its step cannot be reopened'}]}
        return {"ok": False, "pending": [dict(item)]}

    async def recover(report):
        raise AssertionError("an earlier-step finding is not repaired on the current page")

    value.quality_review, value.recover_controls = review, recover
    value.emit = lambda *args: None
    engine = object.__new__(BrowserEngine)
    engine.armed, engine.form = False, value
    engine.config = SimpleNamespace(answer_quality_enabled=True)
    engine.service = SimpleNamespace(review_kind=lambda q: "")
    first, second = await engine.quality_gate(), await engine.quality_gate()
    assert first["recoverable"] and second["recoverable"]
    assert first["reason"] == "Answer quality repair needs a fresh field observation"
    third = await engine.quality_gate()
    assert third["recoverable"] is False and third["recovery_cause"] is None
    assert 'Earlier-step answer "Company"' in third["reason"]
