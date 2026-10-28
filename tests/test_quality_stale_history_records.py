"""Earlier records of history rows blocked submission as "confirmed_fact_mismatch" that no control could repair, and
the run looped on "Answer quality repair needs a fresh field observation" until it stalled (2026-10-27):

- Medpace (iCIMS): the profile re-rendered its work rows under renumbered sections; an earlier render's record of the
  ongoing role's "End Date Day" (written as that day's date) was audited beside the page's own control.
- Belden (SuccessFactors): a resumed draft's records held the ongoing role's end date from two days before, and an
  unnumbered second "Institution" was read against the most recent school."""

from datetime import date
from types import SimpleNamespace

from jobpilot import answer_quality as aq
from jobpilot.answers import Resolver
from jobpilot.schemas import Profile

WORK = "professional experience starting with most recent 3"
SF_WORK, SF_EDUCATION = "what is your previous work experience", "what is your educational background"


def profile():
    return Profile(
        email="alex@example.test",
        work=[
            {"company": "Acme Labs", "title": "Engineer", "start": "2025-12", "end": "", "current": True},
            {"company": "Beta Co", "title": "Assistant", "start": "2025-07", "end": "2026-05", "current": False},
        ],
        education=[
            {"school": "University A", "degree": "Master of Science"},
            {"school": "University B", "degree": "Bachelor of Engineering"},
        ],
    )


def record(label, kind, section, group, answer, slot=0, **comparison):
    return {
        "label": label, "type": kind, "slot": slot, "answer": answer, "verified": True,
        "field_key": [label.lower(), kind, slot, section, group, "", ""], "evidence_ids": ["profile:confirmed"],
        "comparison": {"required": True, **comparison},
    }


def form(service, ats, ledger, fields=()):
    resolver = Resolver(profile(), service.db, service.config, job={"ats": ats, "url": "https://careers.example.test/x"})
    return SimpleNamespace(
        resolver=resolver, ledger=ledger, fields={f["id"]: f for f in fields}, optional_picker_omissions=set(),
        field_key=lambda f: (f["label"].lower(), f["type"], f.get("slot", 0)), failed_strategies={},
    )


def findings(value):
    return aq.local_corrections(value, aq.candidates(value))


def test_an_earlier_render_of_a_row_the_page_shows_again_is_not_audited(service):
    stale = record("End Date Day", "select", WORK, "work experience 1", "26")
    shown = {"id": "day3", "label": "End Date Day", "type": "select", "value": "1", "required": True, "slot": 0,
             "section": "Professional Experience (Starting with Most Recent) (3)", "group": "Work Experience 3",
             "options": [str(d) for d in range(1, 32)], "name": "", "automation_id": "", "valid": True}
    value = form(service, "icims", {"old": stale}, [shown])
    assert [f["id"] for f in aq.candidates(value)] == ["day3"]
    assert findings(value) == {}


def test_an_earlier_steps_ongoing_end_date_is_not_a_mismatch_a_day_later(service):
    old = record("End Date", "text", SF_WORK, "work experience 1", "10/25/2026", widget="date-text", date_format="mdy")
    value = form(service, "successfactors", {"old": old})
    fields = aq.candidates(value)
    known = aq.supported_local(value, fields[0])
    assert known.value == date.today().isoformat() and aq.ongoing_end(value, known)  # the form's "today"
    assert findings(value) == {}


def test_an_unnumbered_rows_record_is_compared_only_with_its_own_row(service):
    second = record("Institution", "text", SF_EDUCATION, "", "University B", slot=1)
    assert findings(form(service, "successfactors", {"old": second})) == {}  # its row was not kept
    placed = record("Institution", "text", SF_EDUCATION, "", "University B", slot=1,
                    history_row={"kind": "education", "index": 1})
    assert findings(form(service, "successfactors", {"old": placed})) == {}
    wrong = record("Institution", "text", SF_EDUCATION, "", "University C", slot=1,
                   history_row={"kind": "education", "index": 1})
    assert list(findings(form(service, "successfactors", {"old": wrong}))) == ["old"]


def test_a_blocked_row_is_named_with_its_scope_and_values():
    known = SimpleNamespace(value="2026-05-01", evidence_ids=["profile:work.1.end"])
    field = {"id": "x", "label": "End Date", "section": SF_WORK, "group": "work experience 2", "slot": 0,
             "editable": False, "value": "06/01/2026"}
    rows = aq.blocked_detail(
        [{"field_id": "x", "question": "End Date", "repair_action": "revisit_history_row"}], {"x": field},
        {"x": (known, "confirmed_fact_mismatch")},
    )
    assert rows == [{"label": "End Date", "section": SF_WORK, "group": "work experience 2", "slot": 0,
                     "on_page": False, "observed": "06/01/2026", "known": "2026-05-01",
                     "evidence": ["profile:work.1.end"], "action": "revisit_history_row"}]
