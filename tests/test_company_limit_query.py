import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.application_pacing import LIMIT_KEY, require_company_limit
from jobpilot.db import Job, Run


@pytest.mark.parametrize('state,mode,verification,blocked', [
    ('confirmed','submit',False,True), ('queued','submit',False,True),
    ('submitting','submit',False,True), ('running','dry_run',False,False),
    ('waiting_browser','submit',False,False), ('confirmed','submit',True,False),
])
def test_company_scope_preserves_confirmations_reservations_and_exemptions(service,state,mode,verification,blocked):
    service.db.set_setting(LIMIT_KEY, {'limit':1})
    with service.db.exclusive() as s:
        prior=Job(identity='prior',url='https://example.test/1',company='Acme',status='ready')
        target=Job(identity='target',url='https://example.test/2',company='Acme',status='ready')
        unrelated=Job(identity='other',url='https://elsewhere.test/3',company='Other',status='confirmed')
        s.add_all([prior,target,unrelated]);s.flush()
        s.add_all([Run(job_id=prior.id,state=state,mode=mode,packet={'verification_only':verification}),
                   Run(job_id=unrelated.id,state='confirmed',mode='submit',packet={'large':'x'*100000})]);s.flush()
        if blocked:
            with pytest.raises(QueueBlocked): require_company_limit(s,target)
        else:
            require_company_limit(s,target)


def test_a_job_confirmed_by_status_alone_still_fills_the_employer_cap(service):
    """The cap reads only confirmed jobs and jobs with a cap-relevant run (2026-10-22); imported history whose job is
    marked confirmed without any run must still count."""
    service.db.set_setting(LIMIT_KEY, {"limit": 1})
    with service.db.exclusive() as s:
        s.add_all(
            [
                Job(identity="legacy", url="https://example.test/1", company="Acme", status="confirmed"),
                Job(identity="other", url="https://example.test/3", company="Other", status="ready"),
            ]
        )
        target = Job(identity="target", url="https://example.test/2", company="Acme", status="ready")
        s.add(target)
        s.flush()
        with pytest.raises(QueueBlocked) as err:
            require_company_limit(s, target)
    assert err.value.code == "company_limit"


def test_an_edited_employer_alias_is_never_answered_from_an_older_key(service):
    """Employer keys are remembered across admissions (2026-10-22), keyed by the alias table's contents."""
    from jobpilot.features.knowledge.tables import KnAlias

    service.db.set_setting(LIMIT_KEY, {"limit": 1})
    with service.db.exclusive() as s:
        s.add(Job(identity="prior", url="https://example.test/1", company="Globex", status="confirmed"))
        target = Job(identity="target", url="https://example.test/2", company="Hooli", status="ready")
        s.add(target)
        s.flush()
        require_company_limit(s, target)  # Different names and no alias: different employers.
        s.add_all(
            [
                KnAlias(alias="globex", employer_id="parent", kind="name"),
                KnAlias(alias="hooli", employer_id="parent", kind="name"),
            ]
        )
        s.flush()
        with pytest.raises(QueueBlocked):
            require_company_limit(s, target)  # The new alias joins them.
