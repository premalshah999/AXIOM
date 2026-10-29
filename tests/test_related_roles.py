"""Curated feeds counted 360 of 1,198 postings off_role (2026-10-29); false negatives among them were titles that are
specialisations of a role the profile names."""

import pytest

from jobpilot.features.discovery.roles import matching_role, related_role

ROLES = ["Data Science", "Machine Learning Engineer", "AI Engineer", "Software Development Engineer", "Full Stack Engineer",
         "Platform Engineer", "Data Analyst"]


@pytest.mark.parametrize("title", [
    "Software Engineer I", "Associate Data Scientist", "AI Engineer, New Grad", "SDE, New Grad", "Data Scientist I",
])
def test_the_named_forms_match(title):
    assert matching_role(title, ROLES)


@pytest.mark.parametrize("title", [
    "Front End Engineer II - Amazon Leo", "Android Engineer", "iOS / UI Engineer", "Associate Programmer",
    "Programmer Analyst", "Junior Front-End Engineer", "Research Scientist New Grad - Conversational AI",
    "AI Research Scientist", "Business Intelligence Analyst", "Junior Business Intelligence Analyst",
    "DevOps Engineer I - Azure", "Site Reliability Engineer", "Cloud Engineer",
])
def test_specialisations_of_a_named_family_match(title):
    assert matching_role(title, ROLES) or related_role(title, ROLES)


@pytest.mark.parametrize("title", [
    "General AI Data Annotator", "AI Video Creator", "AI Trainer - Voice and Speech Evaluation", "Postdoctoral Researcher - AI",
    "Generative AI Consultant", "Cloud Hardware Development Engineer 1 - Early Career", "Cloud Security Engineer",
    "Sales Engineer", "Mechanical Engineer", "Software Support Specialist",
])
def test_noise_and_outside_the_family_stay_out(title):
    assert not (matching_role(title, ROLES) or related_role(title, ROLES))


def test_a_family_the_profile_did_not_name_is_not_added():
    assert not related_role("Android Engineer", ["Data Analyst"])
    assert not related_role("DevOps Engineer", ["Software Engineer"])
    assert not related_role("AI Research Scientist", ["Product Manager"])
