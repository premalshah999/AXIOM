from datetime import UTC,datetime,timedelta

import pytest

from jobpilot.admission import QueueBlocked
from jobpilot.db import Job
from jobpilot.workday import planned_maintenance_until

PAGE='https://community.workday.com/maintenance-page'
TEXT='Workday is currently unavailable. Workday is performing planned maintenance during the following period: October 17, 2026 at 2:00 AM EDT - October 17, 2026 at 1:00 PM EDT'


def test_trusted_maintenance_schedule_is_converted_to_utc():
    at=datetime(2026,10,17,6,tzinfo=UTC)
    assert planned_maintenance_until(PAGE,TEXT,at)=='2026-10-17T17:00:00+00:00'
    assert planned_maintenance_until('https://untrusted.test/maintenance-page',TEXT,at) is None
    assert planned_maintenance_until(PAGE,'Normal page',at) is None
    assert planned_maintenance_until(PAGE,'Workday is currently unavailable. planned maintenance',at)=='2026-10-17T07:00:00+00:00'


def test_maintenance_defers_only_affected_ats_and_expires(service):
    future=(datetime.now(UTC)+timedelta(hours=1)).isoformat()
    service.db.set_setting('ats_outage:workday',{'until':future})
    wd=Job(company='Acme',url='https://acme.wd1.myworkdayjobs.com/jobs/123',ats='workday')
    other=Job(company='Other',url='https://example.test/jobs/123',ats='oracle')
    with service.db.session() as s:
        with pytest.raises(QueueBlocked) as err:service.check_cooldown(s,wd)
        assert err.value.code=='ats_maintenance'
        service.check_cooldown(s,other)
    service.db.set_setting('ats_outage:workday',{'until':(datetime.now(UTC)-timedelta(seconds=1)).isoformat()})
    with service.db.session() as s:service.check_cooldown(s,wd)


def test_the_maintenance_page_url_alone_defers_workday():
    from datetime import UTC, datetime, timedelta

    from jobpilot.workday import planned_maintenance_until

    at = datetime(2026, 10, 17, 6, 5, tzinfo=UTC)
    until = planned_maintenance_until("https://community.workday.com/maintenance-page?d=5&s=1&e=1",
                                      "Workday is down for scheduled maintenance. We'll be back soon.", at=at)
    assert until == (at + timedelta(hours=1)).isoformat()
    assert planned_maintenance_until("https://community.workday.com/forums", "anything", at=at) is None
