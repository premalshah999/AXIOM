"""Answer rules inside the resolver: demographic and education chains, owner-edited."""

from jobpilot.answers import Resolver
from jobpilot.schemas import EducationEntry, Profile


def resolver(service, **profile):
    return Resolver(Profile(**profile), service.db, service.config, job={"ats": "custom", "url": "https://example.test/job"})


def field(label, options, required=True):
    return {"id": "q", "label": label, "type": "select", "required": required, "value": "", "valid": True,
            "group": "", "section": "", "options": options}


RACES = ["Asian", "White", "Black or African American", "Two or More Races"]


def test_declining_stays_the_default_whenever_a_decline_is_offered(service):
    r = resolver(service)
    a = r.local(field("Race/Ethnicity", [*RACES, "I do not wish to self-identify"]))
    assert a.value == "I do not wish to self-identify"


def test_owner_answer_then_closest_offered_option_when_no_decline_exists(service):
    rules = {"demographics": {"race": {"decline_first": True, "answer": "Asian Indian", "when_missing": "closest"}}}
    a = resolver(service, answer_rules=rules).local(field("Race/Ethnicity", RACES))
    assert a.value == "Asian" and a.evidence_ids == ["rule:demographics.race"]


def test_owner_answer_not_offered_falls_to_other_or_asks(service):
    other = {"demographics": {"gender": {"decline_first": True, "answer": "Genderqueer", "when_missing": "other"}}}
    a = resolver(service, answer_rules=other).local(field("Gender", ["Male", "Female", "Other"]))
    assert a.value == "Other"
    ask = {"demographics": {"gender": {"decline_first": True, "answer": "Genderqueer", "when_missing": "ask"}}}
    a = resolver(service, answer_rules=ask).local(field("Gender", ["Male", "Female"]))
    assert a.disposition == "review"


def test_education_closest_then_top_three_then_other(service):
    edu = [EducationEntry(school="University of Michigan, Ann Arbor", degree="Master of Science", field_of_study="Data Science")]
    r = resolver(service, education=edu)
    # The owner's approved equivalent (Computer Science) still wins whenever it is offered.
    assert r.local(field("Major", ["Computer Science", "Data Analytics", "History"])).value == "Computer Science"
    r = resolver(service, education=edu)
    a = r.local(field("Major", ["Data Analytics", "Information Science", "Mathematics", "History"]))
    assert a is None and set(r.narrowed["q"]) >= {"Data Analytics", "Information Science"}
    assert "History" not in r.narrowed["q"]
    r = resolver(service, education=edu, answer_rules={"education": ["other", "ask"]})
    a = r.local(field("Major", ["Data Analytics", "History", "Other"]))
    assert a.value == "Other"
    r = resolver(service, education=edu, answer_rules={"education": ["ask"]})
    assert r.local(field("Major", ["History", "Biology"])).disposition == "review"


async def test_narrowed_choices_are_what_the_answer_model_sees(service, monkeypatch):
    edu = [EducationEntry(school="Example State", degree="Master of Science", field_of_study="Data Science")]
    r = resolver(service, education=edu)
    seen = {}

    async def fake_pending(self, pending, *args, **kwargs):
        seen["options"] = [f["options"] for f in pending]
        return []

    f = field("Major", ["Data Analytics", "Information Science", "Mathematics", "History"])
    r.local(f)
    assert r.narrowed["q"]
    narrowed = {**f, "options": r.narrowed["q"]}
    assert "History" not in narrowed["options"]


OWNER_FACTS = {"gender": "Male", "race_preference": ["Asian", "South Asian"], "hispanic_or_latino": False,
               "veteran": False, "disability": False, "pronouns": "He/him", "gender_identity": "Cisgender man",
               "transgender": False, "sexual_orientation": "Heterosexual or straight"}
OWNER_RULES = {"demographics": {t: {"decline_first": True, "answer": "Male" if t == "gender" else "",
                                    "when_missing": "closest"}
                                for t in ("gender", "race", "hispanic", "veteran", "disability", "sexual_orientation",
                                          "gender_identity", "transgender", "pronouns")}}


def owner(service):
    return resolver(service, facts=OWNER_FACTS, answer_rules=OWNER_RULES)


def test_owner_2026_10_18_answers_without_a_decline_option(service):
    cases = {
        ("Gender", ("Male", "Female", "Non-binary")): "Male",
        ("Race", ("White", "Black or African American", "Asian (Not Hispanic or Latino)")): "Asian (Not Hispanic or Latino)",
        ("Race / Ethnicity", ("White", "South Asian", "East Asian", "Black")): "South Asian",
        ("Are you Hispanic or Latino?", ("Hispanic or Latino", "Not Hispanic or Latino")): "Not Hispanic or Latino",
        ("Veteran Status", ("I identify as one or more of the classifications of protected veteran",
                            "I am not a protected veteran")): "I am not a protected veteran",
        ("Disability Status", ("Yes, I have a disability (or previously had a disability)",
                               "No, I do not have a disability and have not had one in the past")):
            "No, I do not have a disability and have not had one in the past",
        ("What is your sexual orientation?", ("Gay or Lesbian", "Bisexual", "Heterosexual or straight", "Queer")):
            "Heterosexual or straight",
        ("Do you identify as transgender?", ("Yes", "No")): "No",
        ("What is your gender identity?", ("Cisgender man", "Cisgender woman", "Non-binary", "Transgender man")):
            "Cisgender man",
        ("Pronouns", ("He/him/his", "She/her/hers", "They/them/theirs")): "He/him/his",
    }
    for (label, options), expected in cases.items():
        a = owner(service).local(field(label, list(options)))
        assert a is not None and a.value == expected, (label, a)


def test_decline_still_wins_when_offered(service):
    for label, options in (("Gender", ["Male", "Female", "I don't wish to answer"]),
                           ("What is your sexual orientation?", ["Heterosexual", "Gay", "Prefer not to say"]),
                           ("Disability Status", ["Yes, I have a disability", "No, I do not have a disability",
                                                  "I do not want to answer"])):
        a = owner(service).local(field(label, options))
        assert a is not None and a.value == options[-1], (label, a)


def test_gender_identity_is_not_answered_as_the_sex_question(service):
    from jobpilot.answers import demographic_topic

    assert demographic_topic("What is your gender identity?") == "gender_identity"
    assert demographic_topic("Gender") == "gender"
    assert demographic_topic("What is your sexual orientation?") == "sexual_orientation"
