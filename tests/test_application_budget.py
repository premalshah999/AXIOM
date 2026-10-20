from datetime import UTC, datetime, timedelta
import pytest
from jobpilot.db import Job, Run, Setting
from jobpilot.admission import QueueBlocked
from jobpilot.application_budget import HOST_OVERLOADED, require_allowance

AT = datetime(2026, 10, 17, 7, tzinfo=UTC)


def seed(service, starts):
    with service.db.exclusive() as s:
        s.add(Job(id='budget', identity='budget', url='https://example.test/job/123'))
        s.flush()
        for i, ago in enumerate(starts):
            s.add(Run(id=f'r{i}', job_id='budget', mode='submit', state='timed_out',
                      started_at=(AT-timedelta(seconds=ago)).isoformat()))


def test_two_retries_share_first_start_deadline(service):
    seed(service, [400, 200])
    with service.db.session() as s:
        _, remaining = require_allowance(s, 'budget', at=AT)
        assert remaining == 80


def test_three_starts_exhaust_all_recovery_paths(service):
    seed(service, [100, 70, 30])
    with service.db.session() as s:
        with pytest.raises(QueueBlocked) as e:
            require_allowance(s, 'budget', at=AT)
        assert e.value.code == 'application_retry_limit'
        assert require_allowance(s, 'budget', exclude_run='r2', at=AT)[1] == 380


def test_waiting_time_cannot_be_reset_by_a_new_run(service):
    seed(service, [481])
    with service.db.session() as s:
        with pytest.raises(QueueBlocked, match='eight-minute'):
            require_allowance(s, 'budget', at=AT, new_attempt=False)


def test_alias_job_shares_deadline_and_retry_count(service):
    seed(service, [481])
    with service.db.exclusive() as s:
        s.add(Job(id='alias', identity='legacy-alias', url='https://example.test/job/123?utm_source=other'))
    with service.db.session() as s:
        with pytest.raises(QueueBlocked, match='eight-minute'):
            require_allowance(s, 'alias', at=AT)


def test_validation_retries_cannot_stack_with_run_retries(service):
    seed(service, [60, 30])
    with service.db.exclusive() as s:
        s.add(Setting(key='validation_retry:r0', value={'count':1}))
    with service.db.session() as s:
        with pytest.raises(QueueBlocked, match='two retries'):
            require_allowance(s, 'budget', at=AT)


def test_initial_queue_time_is_free(service):
    seed(service, [])
    with service.db.exclusive() as s:
        s.add(Run(id='queued', job_id='budget', mode='submit', state='queued',
                  created_at=(AT-timedelta(hours=2)).isoformat()))
    with service.db.session() as s:
        assert require_allowance(s, 'budget', at=AT)[1] == 480


async def test_queued_retry_expiry_stops_before_browser_claim(service):
    seed(service, [481])
    with service.db.exclusive() as s:
        original=s.get(Run,'r0')
        original.started_at=(datetime.now(UTC)-timedelta(seconds=481)).isoformat()
        s.add(Run(id='expired-queue',job_id='budget',state='queued',mode='submit',packet={}))
    await service._execute_owned('expired-queue')
    with service.db.session() as s:
        run=s.get(Run,'expired-queue')
        assert run.state=='timed_out' and run.started_at is None
        assert 'eight-minute' in run.reason


async def test_expired_verification_keeps_uncertain_submission_protection(service):
    seed(service, [481])
    with service.db.exclusive() as s:
        s.get(Run,'r0').started_at=(datetime.now(UTC)-timedelta(seconds=481)).isoformat()
        s.add(Run(id='expired-verify',job_id='budget',state='queued',mode='submit',packet={'verification_only':True}))
    await service._execute_owned('expired-verify')
    with service.db.session() as s:
        assert s.get(Run,'expired-verify').state=='submission_unknown'
        assert s.get(Job,'budget').status=='submission_unknown'


def test_free_timeouts_do_not_use_up_a_jobs_three_starts(service):
    """An overloaded host's deadline timeout (host_health.host_overloaded_result) sent nothing: not a start."""
    at = datetime(2026, 10, 20, 7, tzinfo=UTC)
    free = HOST_OVERLOADED + " and the application ran out of time before anything was sent; it is retried"
    with service.db.exclusive() as s:
        s.add(Job(id="budget", identity="budget", url="https://example.test/job/9"))
        s.flush()
        for i in range(3):
            s.add(Run(id=f"free{i}", job_id="budget", mode="submit", state="timed_out", reason=free,
                      started_at=(at - timedelta(seconds=300 - i)).isoformat()))
    with service.db.session() as s:
        assert require_allowance(s, "budget", at=at)  # still three starts left
    with service.db.exclusive() as s:
        for i in range(3):
            s.add(Run(id=f"spent{i}", job_id="budget", mode="submit", state="timed_out",
                      reason="Application exceeded the 600-second execution budget", started_at=(at - timedelta(seconds=60 - i)).isoformat()))
    with service.db.session() as s, pytest.raises(QueueBlocked) as caught:
        require_allowance(s, "budget", at=at)
    assert caught.value.code == "application_retry_limit"


@pytest.mark.parametrize(
    "reason",
    [
        "Open the ApplyPilot Chrome window: the browser is responding, but its automation connection is unavailable.",
        # Three runs ended with this text on 2026-10-11 and were counted as starts.
        "The local Chrome debugging endpoint could not be reached after a bounded retry. No employer form was opened.",
        "Chrome worker tab initialization failed at create_target (TimeoutError). No employer form was opened.",
        "The worker host was overloaded and the application ran out of time before anything was sent",
    ],
)
def test_a_dead_chrome_or_an_overloaded_host_sent_nothing(reason):
    from jobpilot.application_budget import nothing_sent

    assert nothing_sent("timed_out", reason) and nothing_sent("waiting_browser", reason)
    assert not nothing_sent("submission_unknown", reason)  # a commit in flight is never "nothing sent"
