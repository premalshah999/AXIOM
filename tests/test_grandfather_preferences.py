"""The one-time grandfather migration: history gets the old built-in values, a fresh install the neutral defaults."""

import pytest

from jobpilot.answer_rules import rules_for
from jobpilot.db import Database, Run, Setting
from jobpilot.grandfather import MARKER, grandfather_preferences
from jobpilot.jobboard import settings as board_settings
from jobpilot.schemas import Profile


@pytest.fixture
def db(tmp_path):
    return Database(f"sqlite:///{tmp_path}/test.db")


def seed(db, *, runs, profile=None, board=None, resubmit=None):
    with db.session() as s:
        if runs:
            s.add(Run(job_id="job-1", state="confirmed"))
        for key, value in (("profile", profile), ("job_board_settings", board), ("unknown_resubmit_policy", resubmit)):
            if value is not None:
                s.add(Setting(key=key, value=value))


def test_installation_with_history_keeps_its_old_behaviour(db):
    seed(db, runs=True, profile={"target_roles": ["Data Analyst"], "answer_rules": {"salary_stance": "Flexible"}})
    written = grandfather_preferences(db)
    assert "major_fallback" in written["answer_rules"] and "salary_stance" not in written["answer_rules"]
    profile = Profile.model_validate(db.get_setting("profile"))
    rules = rules_for(profile)
    assert rules.major_fallback == "Computer Science"
    assert rules.salary_stance == "Flexible"  # an existing key is never overwritten
    assert rules.travel_willingness == "open to travel"
    assert rules.employment_type_sought == "full-time early-career employment"
    assert rules.source_order[0] == "linkedin" and rules.source_any_channel is True
    assert rules.join_talent_communities is False and profile.include_internships is False
    assert profile.target_roles == ["Data Analyst"]
    assert board_settings(db).early_career_only is True
    assert db.get_setting("unknown_resubmit_policy")["enabled"] is True
    assert db.get_setting(MARKER)["history"] is True


def test_saved_values_win_including_a_deliberate_off(db):
    seed(
        db,
        runs=True,
        profile={"answer_rules": {"major_fallback": ""}},
        board={"early_career_only": False, "interval_hours": 3},
        resubmit={"max_resubmits": 1, "enabled": False},
    )
    grandfather_preferences(db)
    assert rules_for(Profile.model_validate(db.get_setting("profile"))).major_fallback == ""
    assert board_settings(db).early_career_only is False and board_settings(db).interval_hours == 3
    assert db.get_setting("unknown_resubmit_policy") == {"max_resubmits": 1, "enabled": False}


def test_resubmit_policy_saved_without_enabled_keeps_the_old_behaviour(db):
    seed(db, runs=True, profile={}, resubmit={"max_resubmits": 2})
    grandfather_preferences(db)
    assert db.get_setting("unknown_resubmit_policy") == {"max_resubmits": 2, "enabled": True}


def test_fresh_install_keeps_neutral_defaults_and_stays_neutral(db):
    seed(db, runs=False, profile={"target_roles": ["Data Analyst"]})
    assert grandfather_preferences(db) == {}
    assert db.get_setting(MARKER)["history"] is False
    assert db.get_setting("profile") == {"target_roles": ["Data Analyst"]}
    assert board_settings(db).early_career_only is False
    assert db.get_setting("unknown_resubmit_policy") is None
    # Runs appear later: the migration already ran, so nothing is borrowed from the old defaults.
    with db.session() as s:
        s.add(Run(job_id="job-2", state="confirmed"))
    assert grandfather_preferences(db) == {}
    assert db.get_setting("profile") == {"target_roles": ["Data Analyst"]}
    assert db.get_setting("unknown_resubmit_policy") is None


def test_runs_once(db):
    seed(db, runs=True, profile={})
    assert grandfather_preferences(db)
    first = db.get_setting("profile")
    with db.session() as s:
        s.get(Setting, "profile").value = {"answer_rules": {}}  # the applicant clears their rules afterwards
    assert grandfather_preferences(db) == {}
    assert db.get_setting("profile") == {"answer_rules": {}} and first["answer_rules"]["major_fallback"]


def test_history_without_a_profile_still_records_the_run(db):
    seed(db, runs=True)
    written = grandfather_preferences(db)
    assert "answer_rules" not in written and db.get_setting("profile") is None
    assert board_settings(db).early_career_only is True
