"""A page offering to apply without an account is taken at its word (Textron's Taleo, 2026-10-28..31), and a removed job can be
brought back unless an application may have reached the employer."""

from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from jobpilot.accounts.pages import GUEST
from jobpilot.adapters.base import Adapter
from jobpilot.answer_rules import AnswerRules
from jobpilot.db import Event, Job, Run, Setting
from jobpilot.owner_apply import restore


@pytest.mark.parametrize(
    "label",
    ["Apply as guest", "Apply as a Guest", "Apply without an account", "Continue without signing in", "Quick Apply",
     "Easy Apply", "Express Apply", "Skip sign in", "Continue as guest", "Apply without registering"],
)
def test_ways_of_saying_no_account_needed(label):
    assert GUEST.fullmatch(label)


@pytest.mark.parametrize("label", ["Sign in", "Create account", "Apply", "Apply now", "Skip to main content", "Guest speakers"])
def test_other_controls_are_not_a_guest_route(label):
    assert not GUEST.fullmatch(label)


def adapter(guest_first=True):
    value = Adapter.__new__(Adapter)
    value.attempts = Counter()
    value.emit = lambda *a, **k: None
    value.click = AsyncMock()
    value.e = SimpleNamespace(form=SimpleNamespace(resolver=SimpleNamespace(answer_rules=AnswerRules(guest_apply_first=guest_first))))
    return value


GUEST_PAGE = {"controls": [{"id": "a", "label": "Sign in"}, {"id": "b", "label": "Apply as guest"}]}


async def test_the_guest_route_is_clicked_once():
    value = adapter()
    assert await value.take_guest_route(GUEST_PAGE)
    assert value.click.await_args.args[0]["id"] == "b"
    assert not await value.take_guest_route(GUEST_PAGE)
    assert value.click.await_count == 1


async def test_a_profile_that_declines_guest_first_or_a_page_without_one_is_left_alone():
    value = adapter(guest_first=False)
    assert not await value.take_guest_route(GUEST_PAGE)
    value = adapter()
    assert not await value.take_guest_route({"controls": [{"id": "a", "label": "Sign in"}]})
    assert value.click.await_count == 0


def removed(service, key, run_state=None, event=None):
    with service.db.session() as s:
        job = Job(identity=key, url=f"https://jobs.example.test/{key}", status="archived")
        s.add(job)
        s.flush()
        job_id = job.id
        if run_state:
            run = Run(job_id=job_id, state=run_state, packet={})
            s.add(run)
            s.flush()
            if event:
                s.add(Event(run_id=run.id, kind=event, message="", data={}))
        s.add(Setting(key="archived_application:" + job_id, value={"reason": "x"}))
    return job_id


def test_a_removed_job_with_nothing_sent_comes_back(service):
    job_id = removed(service, "a", "waiting_browser")
    assert restore(service, job_id)["state"] == "ready"
    with service.db.session() as s:
        assert s.get(Job, job_id).status == "ready"
        assert s.get(Setting, "archived_application:" + job_id) is None


@pytest.mark.parametrize(
    "state,event",
    [("confirmed", None), ("already_applied", None), ("submission_unknown", None), ("failed", "submitting")],
)
def test_anything_that_may_have_been_sent_stays_removed(service, state, event):
    job_id = removed(service, "a", state, event)
    with pytest.raises(ValueError, match="stays removed"):
        restore(service, job_id)
    with service.db.session() as s:
        assert s.scalar(select(Job.status).where(Job.id == job_id)) == "archived"


def test_a_job_that_was_not_removed_is_left_as_it_is(service):
    with service.db.session() as s:
        job = Job(identity="b", url="https://jobs.example.test/b", status="ready")
        s.add(job)
        s.flush()
        job_id = job.id
    assert restore(service, job_id)["message"] == "This job was not removed."


def test_restoring_clears_the_deletion_so_the_job_can_run_but_keeps_a_pause(service):
    job_id = removed(service, "a", "waiting_browser")
    other = removed(service, "b", "waiting_browser")
    with service.db.session() as s:
        s.add(Setting(key="job_control:" + job_id, value={"state": "deleted"}))
        s.add(Setting(key="job_control:" + other, value={"state": "paused"}))
    restore(service, job_id)
    restore(service, other)
    with service.db.session() as s:
        assert s.get(Setting, "job_control:" + job_id) is None
        assert s.get(Setting, "job_control:" + other).value["state"] == "paused"

