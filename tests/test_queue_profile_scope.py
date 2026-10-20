import pytest
from jobpilot.db import Job
from jobpilot.admission import QueueBlocked


@pytest.mark.parametrize("title,code",[("Data Analyst","target_role"),("Senior Software Engineer","seniority"),
                                      ("Software Engineering SMTS - Brain Team", "seniority"),
                                      ("Software Engineer LMTS", "seniority"),
                                      ("Software Engineer Intern","internship"),
                                      ("AI Product Evangelist & Community Builder","target_role")])
async def test_submit_rechecks_current_profile_scope(service,title,code):
    service.db.set_setting("profile",{"target_roles":["Software Development Engineer"]})
    with service.db.exclusive() as s:
        s.add(Job(id="scope",identity="scope",url="https://example.test/job/123",title=title,status="ready"))
    with pytest.raises(QueueBlocked) as err:
        await service.queue("scope","submit")
    assert err.value.code==code
