"""Every unfinished run kept its tab open; two Chrome profiles reached 47 GB each on a 16 GB Mac (2026-10-26). A draft
whose next run reopens its saved route lets its tab go; anything that may need the live page keeps it."""

import pytest

from jobpilot.browser import BrowserEngine


def engine(state, armed=False):
    value = object.__new__(BrowserEngine)
    value.result, value.armed = {"state": state}, armed
    return value


@pytest.mark.parametrize("state", ["waiting_browser", "timed_out", "needs_review", "waiting_answer"])
def test_a_saved_draft_releases_its_tab(state):
    assert engine(state).releases_retained_tab()


@pytest.mark.parametrize("state", ["submission_unknown", "waiting_email", "paused", "dry_run_passed"])
def test_drafts_that_may_need_the_live_page_keep_it(state):
    assert not engine(state).releases_retained_tab()


def test_an_armed_commit_never_releases_its_tab():
    assert not engine("waiting_browser", armed=True).releases_retained_tab()
