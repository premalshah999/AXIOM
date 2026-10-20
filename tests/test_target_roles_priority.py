"""Search filters and new-grad priority come from each profile (2026-10-17). The owner's targets are Data Science,
Machine Learning Engineer, AI Engineer and Software Development Engineer, with new-grad postings applied to first;
other users set their own in Setup."""

from datetime import UTC, datetime

from jobpilot.autopilot import dispatch_priority
from jobpilot.features.discovery.feeds import Posting, ingest
from jobpilot.features.discovery.roles import matching_role, search_terms
from jobpilot.jobboard import role_matches
from jobpilot.jobsearch import queries
from jobpilot.schemas import Profile

ROLES = ["Data Science", "Machine Learning Engineer", "AI Engineer", "Software Development Engineer"]


def test_titles_match_the_roles_through_their_usual_names():
    assert matching_role("Software Engineer II", ROLES) == "Software Development Engineer"
    assert matching_role("SDE, New Grad 2026", ROLES) == "Software Development Engineer"
    assert matching_role("Software Developer - Early Career", ROLES) == "Software Development Engineer"
    assert matching_role("Data Scientist I", ROLES) == "Data Science"
    assert matching_role("Engineer, Machine Learning (New Grad)", ROLES) == "Machine Learning Engineer"
    assert matching_role("ML Engineer", ROLES) == "Machine Learning Engineer"
    assert matching_role("Applied AI Engineer", ROLES) == "AI Engineer"
    assert matching_role("AI/ML Engineer", ROLES) in {"Machine Learning Engineer", "AI Engineer"}
    assert matching_role("Data Analyst", ROLES) is None
    assert matching_role("Mechanical Engineer", ROLES) is None


def test_other_profiles_get_their_own_roles():
    assert matching_role("Senior Product Manager", ["Product Manager"]) == "Product Manager"
    assert matching_role("Software Engineer", ["Product Manager"]) is None
    assert matching_role("Quantitative Researcher", ["Quantitative Researcher"]) == "Quantitative Researcher"


def test_search_terms_add_the_common_alternate_title():
    terms = search_terms(ROLES)
    assert "Software Development Engineer" in terms and "Software Engineer" in terms
    assert "Data Science" in terms and "Data Scientist" in terms


def test_new_grad_postings_go_first_unless_the_profile_prefers_best_fit():
    jobs = [
        {"id": "experienced", "score": 95, "career_level": "Experience unspecified", "title": "Data Scientist"},
        {"id": "new-grad", "score": 78, "career_level": "New grad", "title": "Software Engineer, New Grad"},
        {"id": "early", "score": 88, "career_level": "Early career", "title": "ML Engineer I"},
    ]
    first = sorted(jobs, key=lambda j: dispatch_priority(j, "new_grad_first"), reverse=True)
    assert [j["id"] for j in first] == ["new-grad", "early", "experienced"]
    best = sorted(jobs, key=lambda j: dispatch_priority(j, "best_fit_first"), reverse=True)
    assert [j["id"] for j in best] == ["experienced", "early", "new-grad"]


def test_web_search_uses_the_profiles_roles_level_and_places():
    profile = Profile(target_roles=["AI Engineer"], target_locations=["Maryland"])
    built = queries(profile, 0, 50)
    assert all('"AI Engineer"' in q and '"new grad"' in q and '"Maryland"' in q for q in built)
    experienced = Profile(target_roles=["Product Manager"], job_priority="best_fit_first")
    assert not any('"new grad"' in q for q in queries(experienced, 0, 50))


def test_curated_feed_postings_outside_the_profiles_roles_are_not_imported(service):
    service.db.set_setting("profile", {"target_roles": ROLES})
    now = datetime.now(UTC).isoformat()

    def posting(title, n):
        return Posting(company="Acme", title=title, url=f"https://boards.greenhouse.io/acme/jobs/{n}",
                       locations=["Remote, US"], posted_at=now, basis="verified", source="simplify")

    counts = ingest(service.db, [posting("Software Engineer, New Grad", 1), posting("Data Analyst", 2)])
    assert counts["created"] == 1 and counts["off_role"] == 1


def test_employer_board_screening_uses_the_synonyms():
    profile = Profile(target_roles=ROLES)
    assert role_matches("Software Developer I", "", profile)
    assert not role_matches("Sales Engineer", "", profile)


def test_workday_sweeps_also_search_the_profiles_roles(service):
    from jobpilot.features.discovery.feeds import WORKDAY_QUERIES, workday_queries

    service.db.set_setting("profile", {"target_roles": ROLES})
    queries = workday_queries(service.db)
    assert queries[: len(WORKDAY_QUERIES)] == WORKDAY_QUERIES
    assert "Machine Learning Engineer" in queries and len(queries) <= len(WORKDAY_QUERIES) + 4


def test_workday_tenant_searches_come_from_the_profile():
    from jobpilot.jobboard import workday_searches

    owner = workday_searches(Profile(target_roles=ROLES))
    assert "Software Engineer" in owner and "Data Scientist" in owner and owner[-1] == "new grad"
    other = workday_searches(Profile(target_roles=["Product Manager"], job_priority="best_fit_first"))
    assert other == ["Product Manager"]


def test_a_profiles_own_role_wins_over_the_generic_exclusions():
    assert role_matches("Associate Product Manager", "", Profile(target_roles=["Product Manager"]))
    assert not role_matches("Associate Product Manager", "", Profile(target_roles=ROLES))
    assert not role_matches("Software Engineer Intern", "", Profile(target_roles=ROLES))
