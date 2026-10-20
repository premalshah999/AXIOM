import pytest
from jobpilot.application_pacing import require_ats_admission, require_ats_focus
from jobpilot.admission import QueueBlocked
from jobpilot.db import Job, Run


@pytest.mark.parametrize("ats", ["greenhouse", "lever", "ashby"])
async def test_direct_queue_observes_saved_ats_focus(service, ats):
    service.db.set_setting("ats_focus", {"exclude": ["greenhouse", "lever", "ashby"]})
    with service.db.exclusive() as session:
        session.add(Job(id="focus", identity="focus", url="https://example.test/job/123", ats=ats,
                        title="Software Engineer", status="ready"))
    with pytest.raises(QueueBlocked) as caught:
        await service.queue("focus", "submit")
    assert caught.value.code == "ats_focus"


def test_scope_still_allows_selected_platforms_and_verification(service):
    service.db.set_setting("ats_focus", {"exclude": ["greenhouse"]})
    with service.db.session() as session:
        require_ats_admission(session, Job(ats="workday", demo=False))
        require_ats_focus(session, Job(ats="greenhouse", demo=False),
                          Run(packet={"verification_only": True}))


def test_final_reservation_rechecks_scope_before_spending_submission(service):
    service.db.set_setting("ats_focus", {"exclude": ["greenhouse"]})
    service.db.set_setting("control", {"auto_submit": True})
    with service.db.exclusive() as session:
        session.add(Job(id="focus", identity="focus", url="https://example.test/job/123", ats="greenhouse"))
        session.add(Run(id="run", job_id="focus", mode="submit", state="running", packet={}))
    with pytest.raises(QueueBlocked) as caught:
        service.reserve_submission("run")
    assert caught.value.code == "ats_focus"
    with service.db.session() as session:
        assert session.get(Run, "run").state == "running"
