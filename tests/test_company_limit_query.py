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
