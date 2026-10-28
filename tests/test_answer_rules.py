"""Owner-editable fallback chains: closest offered option, top-3 narrowing, Other, then ask."""

from types import SimpleNamespace

from jobpilot.answer_rules import AnswerRules, closest_option, other_option, rules_for, top_options


def test_closest_option_uses_synonyms_containment_and_degree_level():
    assert closest_option("Male", ["Man", "Woman", "Non-binary", "Prefer not to say"]) == "Man"
    races = ["Asian", "White", "Black or African American", "Native Hawaiian or Other Pacific Islander"]
    assert closest_option("Asian Indian", races) == "Asian"
    degrees = ["High School", "Bachelor's Degree", "Master's Degree", "Doctorate"]
    assert closest_option("MS Data Science", degrees) == "Master's Degree"
    schools = ["University of Michigan", "University of Michigan-Dearborn", "Michigan Technological University"]
    assert closest_option("University of Michigan, Ann Arbor", schools) == "University of Michigan"


def test_negation_and_ambiguity_never_pick_an_option():
    assert closest_option("No", ["Yes, I have a disability"]) is None
    assert closest_option("I am not a protected veteran", ["Protected veteran", "Veteran"]) is None
    majors = ["Computer Science", "Data Analytics", "Mathematics", "Statistics"]
    assert closest_option("Data Science", majors) is None  # tied: the top-3 step decides
    assert set(top_options("Data Science", majors)) >= {"Computer Science", "Data Analytics"}


def test_other_is_found_only_when_unique():
    assert other_option(["Harvard University", "Other"]) == "Other"
    assert other_option(["My school is not listed", "Stanford"]) == "My school is not listed"
    assert other_option(["Other", "Others"]) is None
    # Dell (Oracle, 2026-10-25): its major catalog's Other.
    assert other_option(["Information Technology", "Other - Field of Study Not Listed"]) == (
        "Other - Field of Study Not Listed")
    assert other_option(["Other (Not Listed)", "Physics"]) == "Other (Not Listed)"


def test_defaults_follow_the_profile_decline_switch_until_rules_are_saved():
    rules = rules_for(SimpleNamespace(decline_demographics=False, model_extra={}))
    assert rules.demographics["gender"].decline_first is False
    assert rules.education == ["closest", "pick3", "other", "ask"]
    saved = AnswerRules(demographics={"race": {"decline_first": False, "answer": "Asian", "when_missing": "closest"}})
    rules = rules_for(SimpleNamespace(decline_demographics=True, model_extra={"answer_rules": saved.model_dump()}))
    assert rules.demographics["race"].answer == "Asian" and rules.demographics["gender"].decline_first is True
