"""Supermicro (SuccessFactors) 2026-10-27: its rows carry no group, so each date box is a bare "End Date" / "From Date".
A reviewed "End Date" (the applicant's birth date, saved by an earlier build) and a repair agent's "From Date" answered
every row; the draft kept them and the page refused "End date must be after Start date"."""

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile, ReviewedAnswer


def bare(label):
    return {"id": "d", "label": label, "type": "text", "required": False, "value": "", "valid": True, "options": [],
            "section": None, "group": ""}


def test_a_reviewed_bare_end_date_is_not_reused(service):
    profile = Profile(reviewed_answers=[ReviewedAnswer(id="rv1", question="End Date", answer="01/02/1990",
                                                       scope="personal")])
    answer = Resolver(profile, service.db, service.config).local(bare("End Date"))
    assert answer is None or "01/02/1990" not in str(answer.value)


def test_a_repair_correction_of_a_bare_from_date_is_not_reused(service):
    service.db.set_setting("repair_overrides:job-9", {"from date": {"label": "From Date", "value": "05/01/2024",
                                                                     "run_id": "r"}})
    resolver = Resolver(Profile(), service.db, service.config, job={"id": "job-9", "ats": "successfactors",
                                                                     "url": "https://career4.successfactors.com/x"})
    answer = resolver.local(bare("From Date"))
    assert answer is None or answer.evidence_ids != ["repair:r"]


def test_a_repair_correction_is_not_reused_across_history_rows(service):
    # Lennar (Workday 2026-10-21): the fix "School or University" = the first row's school answered the second row too.
    service.db.set_setting("repair_overrides:job-9", {"school or university": {
        "label": "School or University", "value": "Example State University", "run_id": "r"}})
    resolver = Resolver(Profile(), service.db, service.config, job={"id": "job-9", "ats": "workday",
                                                                     "url": "https://acme.wd1.myworkdayjobs.com/x"})
    for row in ({"group": "Education 2"}, {"group": "", "history_kind": "education"}):
        field = {**bare("School or University"), "type": "prompt", **row}
        answer = resolver.local(field)
        assert answer is None or answer.evidence_ids != ["repair:r"]
    # A question outside any history row still takes its checked correction.
    assert resolver.repair_override({**bare("Preferred Name"), "label": "School or University"}).evidence_ids == ["repair:r"]
