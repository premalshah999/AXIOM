"""A role that names only a level ("Graduate Engineer", "New Grad Engineer") matched every kind of engineer a new graduate can be:
on the owner's profile 36 and 99 of 1,186 open postings fit them, among them Process, Controls, Geotechnical and Silicon Photonics
Engineers beside the software, data and ML roles the profile also names (2026-11-02). A level says how senior, not what field.
When a profile names a field too, a level-only role adds nothing; a profile that names nothing else keeps it as written."""

import pytest

from jobpilot.features.discovery.roles import fitting_roles, level_only, role_fits

FIELDS = ["Data Science", "Machine Learning Engineer", "Software Development Engineer", "Full Stack Engineer", "Data Analyst"]
LEVELS = ["Graduate Engineer", "New Grad Engineer"]


@pytest.mark.parametrize("role", ["Graduate Engineer", "New Grad Engineer", "Entry Level Engineer", "Junior Analyst", "Engineer"])
def test_a_role_with_only_a_level_and_a_generic_noun_is_level_only(role):
    assert level_only(role)


@pytest.mark.parametrize("role", ["Data Engineer", "Developer", "Platform Engineer", "Software Engineer", "Junior Developer"])
def test_a_role_with_a_field_or_a_craft_is_not(role):
    assert not level_only(role)


@pytest.mark.parametrize("title", [
    "Process Engineer (New Grad)", "Controls Engineer - New Grad", "Graduate Geotechnical Engineer",
    "Silicon Photonics Device Engineer (2027 New College Graduate)", "New Grad Applications Engineer",
    "Manufacturing Engineer - New Graduate Opportunity", "Power System Graduate Engineer",
])
def test_a_new_grad_engineer_of_another_field_is_not_a_fit_for_a_software_and_data_profile(title):
    assert not role_fits(title, FIELDS + LEVELS)
    assert fitting_roles(title, FIELDS + LEVELS) == []


@pytest.mark.parametrize("title", [
    "New Grad 2027: Software Engineer", "Software Engineer New Grad", "Graduate Data Scientist", "New Graduate Engineer, Software",
    "Machine Learning Engineer, University Graduate",
])
def test_the_fields_the_profile_names_still_match_with_the_level_roles_beside_them(title):
    assert role_fits(title, FIELDS + LEVELS)
    assert role_fits(title, FIELDS) == role_fits(title, FIELDS + LEVELS)


def test_a_profile_naming_only_levels_keeps_them():
    assert role_fits("Process Engineer (New Grad)", LEVELS)
    assert fitting_roles("Process Engineer (New Grad)", LEVELS) == ["New Grad Engineer"]


def test_the_role_filter_lists_only_the_roles_a_title_fits():
    assert fitting_roles("Data Scientist, Fraud", FIELDS + LEVELS) == ["Data Science"]


@pytest.mark.parametrize("title", [
    "CNC Machinist - Programmer 1st shift 6am-2:30pm $32/hr", "CNC Programmer Lynn, MA", "Professor of Data Science",
    "Adjunct Faculty - Software Engineering", "Lecturer in Computer Science",
])
def test_trades_and_teaching_posts_are_not_software_roles(title):
    assert not role_fits(title, FIELDS)
