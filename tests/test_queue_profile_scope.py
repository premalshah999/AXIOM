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
    service.db.set_setting("job_board_settings",{"early_career_only":True})
    with service.db.exclusive() as s:
        s.add(Job(id="scope",identity="scope",url="https://example.test/job/123",title=title,status="ready"))
    with pytest.raises(QueueBlocked) as err:
        await service.queue("scope","submit")
    assert err.value.code==code


async def test_internships_and_seniors_pass_when_the_profile_allows_them(service):
    service.db.set_setting("profile", {"target_roles": ["Software Development Engineer"], "include_internships": True})
    with service.db.exclusive() as s:
        s.add(Job(id="i1", identity="i1", url="https://example.test/job/1", title="Software Engineer Intern", status="ready"))
        s.add(Job(id="i2", identity="i2", url="https://example.test/job/2", title="Senior Software Engineer", status="ready"))
    for job in ("i1", "i2"):
        try:
            await service.queue(job, "submit")
        except QueueBlocked as err:  # Later gates (provider, budget) may block; never these two.
            assert err.code not in {"internship", "seniority"}
        except ValueError:  # Past the scope checks: no resume is uploaded in this synthetic database.
            pass
