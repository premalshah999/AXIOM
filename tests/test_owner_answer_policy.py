"""Owner answer policy (2026-10-13), all ATSs: offered degree/major/source equivalents and
"No" for prior-employment, relatives and government/anti-corruption questions."""

from jobpilot.answers import Resolver
from jobpilot.schemas import EducationEntry, Profile, WorkEntry

NO_FACTS = {
    "previously_employed_here": False,
    "has_relatives_at_employer": False,
    "government_employment_history": "No (confirmed by applicant)",
    "government_official": False,
}


def profile(**extra):
    return Profile(
        education=[
            EducationEntry(
                school="Example University", degree="Master of Science", field_of_study="Data Science"
            ),
            EducationEntry(
                school="Example Institute",
                degree="Bachelor of Technology",
                field_of_study="Information Technology",
            ),
        ],
        work=[WorkEntry(company="Acme Analytics Inc", title="Analyst")],
        application_source="LinkedIn",
        facts=dict(NO_FACTS),
        **extra,
    )


def field(label, options, **extra):
    return {
        "id": "q",
        "label": label,
        "type": "select",
        "required": True,
        "options": options,
        "value": "",
        "valid": True,
        **extra,
    }


def test_major_not_offered_falls_back_to_the_profiles_fallback_major(service):
    r = Resolver(profile(answer_rules={"major_fallback": "Computer Science"}), service.db, service.config)
    options = ["Biology", "Computer Science", "Economics"]
    assert r.policy_choice(field("Field of Study", options), options) == "Computer Science"
    # The recorded major still wins when offered.
    options = ["Data Science", "Computer Science"]
    assert r.policy_choice(field("Field of Study", options), options) == "Data Science"
    # Another applicant's fallback is their own.
    other = Resolver(profile(answer_rules={"major_fallback": "Statistics"}), service.db, service.config)
    options = ["Biology", "Computer Science", "Statistics (B.S.)"]
    assert other.policy_choice(field("Field of Study", options), options) == "Statistics (B.S.)"


def test_a_profile_without_a_fallback_major_chooses_none(service):
    r = Resolver(profile(), service.db, service.config)
    options = ["Biology", "Computer Science", "Economics"]
    assert r.policy_choice(field("Field of Study", options), options) is None


def test_degree_not_offered_uses_same_level_generic_option(service):
    r = Resolver(profile(), service.db, service.config)
    options = ["High School", "Bachelor's Degree", "Master's Degree", "MBA", "Doctorate"]
    assert r.policy_choice(field("Degree", options, group="Education 1"), options) == "Master's Degree"
    assert r.policy_choice(field("Degree", options, group="Education 2"), options) == "Bachelor's Degree"
    options = ["BA", "BS", "MBA", "MS"]
    assert r.policy_choice(field("Degree", options, group="Education 1"), options) == "MS"
    assert r.policy_choice(field("Degree", options, group="Education 2"), options) == "BS"
    # Never an arts or business degree as the equivalent.
    options = ["Bachelor of Arts", "Master of Business Administration"]
    assert r.policy_choice(field("Degree", options, group="Education 1"), options) is None


def test_degree_policy_ignores_yes_no_questions(service):
    r = Resolver(profile(), service.db, service.config)
    assert r.policy_choice(field("Do you have a Bachelor's degree?", ["Yes", "No"]), ["Yes", "No"]) is None


def test_source_picks_any_listed_source_in_owner_order(service):
    r = Resolver(profile(), service.db, service.config)
    options = ["Employee Referral", "Indeed", "Career Fair"]
    assert r.policy_choice(field("How did you hear about us?", options), options) == "Indeed"
    options = ["Agency", "Company Website", "Other"]
    assert (
        r.policy_choice(field("How did you hear about this position?", options), options) == "Company Website"
    )
    # Internal-only catalogs have no true answer.
    options = ["Pfizer Contractor/Consultant", "Refugee", "US/PR Colleague in WARN Period"]
    assert r.policy_choice(field("How Did You Hear About Us?", options), options) is None


def test_local_source_answer_uses_fallback_when_saved_source_not_offered(service):
    r = Resolver(profile(), service.db, service.config)
    a = r.local(field("How did you hear about us?", ["Referral", "ZipRecruiter", "Newspaper"]))
    assert a.disposition == "answer" and a.value == "ZipRecruiter"


def yes_no(label):
    return {
        "id": "q",
        "label": label,
        "type": "radio",
        "required": True,
        "options": ["Yes", "No"],
        "value": "",
        "valid": True,
    }


def test_prior_employment_parent_and_sister_companies_answer_no(service):
    r = Resolver(profile(), service.db, service.config, employer="Globex")
    for label in (
        "Have you ever worked for Globex or any of its subsidiaries or affiliates?",
        "Have you been employed by any of the following companies: Initech, Umbrella, Hooli?",
        "Have you previously worked for our parent company?",
    ):
        a = r.local(yes_no(label))
        assert a and a.disposition == "answer" and a.value == "No", label


def test_prior_employment_naming_a_recorded_employer_is_not_auto_no(service):
    r = Resolver(profile(), service.db, service.config, employer="Acme Analytics")
    a = r.local(yes_no("Have you ever worked for Acme Analytics before?"))
    assert not (a and a.disposition == "answer" and a.value == "No")


def test_relatives_spouse_and_knowing_someone_answer_no(service):
    r = Resolver(profile(), service.db, service.config, employer="Globex")
    for label in (
        "Is your spouse or domestic partner employed by Globex?",
        "Do you know anyone who currently works at Globex?",
        "Do you have any close relatives working for the company?",
    ):
        a = r.local(yes_no(label))
        assert a and a.disposition == "answer" and a.value == "No", label


def test_government_and_anti_corruption_questions_answer_no(service):
    r = Resolver(profile(), service.db, service.config, employer="Globex")
    for label in (
        "Are you a government official or an employee of a state-owned enterprise?",
        "Are you or any family member a current or former government employee?",
        "Do you hold any public office or political position?",
        "Have you worked for any federal, state or local government in the past 5 years?",
    ):
        a = r.local(yes_no(label))
        assert a and a.disposition == "answer" and a.value == "No", label


def test_location_city_picks_recorded_city_in_recorded_state(service):
    from jobpilot.schemas import Address

    r = Resolver(
        profile(address=Address(city="Springfield", state="MD", country="United States")),
        service.db,
        service.config,
    )
    options = [
        "Springfield, Illinois, United States",
        "Springfield, Maryland, United States",
        "Springfield Gardens, NY",
    ]
    assert (
        r.policy_choice(field("Location (City)", options), options) == "Springfield, Maryland, United States"
    )
    assert (
        r.policy_choice(field("City", ["Baltimore, MD", "Rockville, MD"]), ["Baltimore, MD", "Rockville, MD"])
        is None
    )


def test_source_any_plain_channel_when_no_listed_source(service):
    r = Resolver(profile(), service.db, service.config)
    for options, expected in (
        (["Facebook", "Twitter"], "Facebook"),
        (["Employee"], None),
        (["Displaced (US)", "LOA - Job Search (US)"], None),
        (["Employee Referral", "Recruiter", "Radio Advertisement"], "Radio Advertisement"),
    ):
        assert r.policy_choice(field("How Did You Hear About Us?", options), options) == expected, options


BOOZ_7 = (
    "7. To the best of your knowledge, are you a current or former employee of an organization ('Client') "
    "that you believe is or may soon become (1) a Booz Allen Hamilton client other than a U.S. Federal "
    "Government or a state, local or non-U.S. government client, (2) a prime contractor that has hired Booz "
    "Allen Hamilton, or (3) a Booz Allen Hamilton partner?"
)
BOOZ_8 = (
    "8. To the best of your knowledge, is any member of your family or household a current Booz Allen "
    "Hamilton employee or member of the Board of Directors?"
)


def test_sentence_options_pick_the_no_choice(service):
    r = Resolver(profile(), service.db, service.config, employer="Booz Allen Hamilton")
    for label in (BOOZ_7, BOOZ_8):
        options = ["Yes, I am", "No, I am not"]
        a = r.local(field(label, options))
        assert a and a.value == "No, I am not", label
    r = Resolver(profile(), service.db, service.config, employer="SpaceX")
    options = [
        "I have never worked for SpaceX, SpaceXAI, xAI, X, or Twitter",
        "I am a former SpaceX, SpaceXAI, xAI, X, or Twitter employee",
        "I am a current SpaceX employee",
    ]
    a = r.local(field("SpaceX & SpaceXAI Employment History", options))
    assert a and a.value.startswith("I have never worked")


def test_owner_no_never_answers_willingness_or_consent(service):
    r = Resolver(profile(), service.db, service.config, employer="Globex")
    for label in (
        "Are you willing to work with government clients?",
        "Do you consent to a government background check as a condition of employment?",
        "Are you currently employed?",
    ):
        a = r.owner_policy_answer(field(label, ["Yes", "No"]))
        assert a is None, label


def test_gpa_dropdown_uses_recorded_gpa_truncated_and_na_for_other_scales(service):
    p = profile()
    p.education[0].gpa = "3.96/4.0"
    p.education[1].gpa = "8.1/10"
    r = Resolver(p, service.db, service.config)
    grad = ["Other/Not Applicable", "4.0 out of 4.0", "3.9 out of 4.0", "3.8 out of 4.0"]
    assert r.local(field("GPA (Graduate)", grad)).value == "3.9 out of 4.0"
    under = ["Not applicable/Do not recall", "4.0 out of 4.0", "3.3 out of 4.0"]
    assert r.local(field("GPA (Undergraduate)", under)).value == "Not applicable/Do not recall"


def test_not_to_my_knowledge_is_the_no_choice(service):
    r = Resolver(profile(), service.db, service.config, employer="Booz Allen Hamilton")
    label = (
        "Are you currently or have you been employed by the U.S. Federal Government in the past two years?"
    )
    a = r.local(field(label, ["Yes", "Not to my knowledge"]))
    assert a and a.value == "Not to my knowledge"


def test_profiles_without_confirmed_no_facts_get_no_automatic_no(service):
    friend = Profile(education=profile().education, work=profile().work)  # no confirmed facts
    r = Resolver(friend, service.db, service.config, employer="Booz Allen Hamilton")
    assert (
        r.owner_policy_answer(field(BOOZ_8, ["Yes, a family member is", "No, I am not aware of any"])) is None
    )


def test_plain_yes_no_conflict_question_about_another_company_is_no(service):
    r = Resolver(profile(), service.db, service.config, employer="American Express")
    a = r.local(
        yes_no("Have you ever been employed by PricewaterhouseCoopers (PwC) or any of its affiliates?")
    )
    assert a and a.value == "No"


def test_daca_question_answers_from_the_recorded_immigration_status(service):
    p = profile()
    p.facts["current_immigration_status_US"] = "F-1 student (OPT)"
    r = Resolver(p, service.db, service.config, employer="John Deere")
    label = "Are you a current recipient of the Deferred Action for Childhood Arrivals (DACA) program?"
    assert r.local(yes_no(label)).value == "No"
    assert (
        Resolver(profile(), service.db, service.config).owner_policy_answer(yes_no(label)) is None
    )  # no status stored


def test_tax_credit_screener_and_healthcare_exclusion_answer_no_only_with_the_owner_facts(service):
    p = profile()
    p.facts.update({"tax_credit_screening_no": True, "excluded_from_federal_healthcare_programs": False})
    r = Resolver(p, service.db, service.config, employer="Caesars")
    assert r.local(yes_no("Have you received SNAP (food stamp) benefits in the last 6 months?")).value == "No"
    assert r.local(yes_no("Has anyone in your family received TANF in the last 18 months?")).value == "No"
    assert (
        r.local(
            yes_no(
                "Have you ever been excluded from participation in Medicare, Medicaid or any federal health care program?"
            )
        ).value
        == "No"
    )
    plain = Resolver(profile(), service.db, service.config)
    assert (
        plain.owner_policy_answer(
            yes_no("Have you received SNAP (food stamp) benefits in the last 6 months?")
        )
        is None
    )


def test_race_without_decline_uses_the_confirmed_preference(service):
    p = profile()
    p.facts["race_preference"] = ["Asian", "South Asian"]
    r = Resolver(p, service.db, service.config)
    options = [
        "White",
        "Black or African American",
        "Native Hawaiian or Other Pacific Islander",
        "Asian",
        "Two or More Races",
    ]
    a = r.confirmed_demographic(field("What is your race?", options), options)
    assert a and a.value == "Asian"
    options = ["East Asian", "South Asian", "Southeast Asian", "White"]
    a = r.confirmed_demographic(
        field("How would you describe your racial/ethnic background?", options), options
    )
    assert a and a.value == "South Asian"


BOOZ_SPONSORSHIP = (
    "4. Will you now, or in the future, require sponsorship by your employer to legally work in the country where "
    "this role is located? (i.e., Visa, SOFA, DOCPER) If this role is located in the United States, answer 'Yes/No' "
    "based on your need for sponsorship to work in the United States. If this role is not located in the United "
    "States, answer 'Yes/No' based on your need for sponsorship in the country where the role is located."
)


def test_sponsorship_with_trailing_instructions_keeps_the_fact_polarity(service):
    for needs, expected in ((False, "No"), (True, "Yes")):
        r = Resolver(Profile(facts={"requires_future_sponsorship_US": needs}), service.db, service.config)
        a = r.local(
            {
                "id": "q",
                "label": BOOZ_SPONSORSHIP,
                "type": "select",
                "required": True,
                "options": ["Yes", "No"],
                "value": "",
                "valid": True,
            }
        )
        assert a is None or a.value == expected, (needs, a)


def test_audit_accepts_a_same_level_degree_for_highest_education(service):
    from jobpilot.answer_quality import local_corrections
    from types import SimpleNamespace

    p = profile()
    r = Resolver(p, service.db, service.config)
    field_ = {
        "id": "h",
        "label": "What is the highest level of education you have completed?",
        "type": "dropdown",
        "required": True,
        "options": ["Bachelor's Degree", "Master's Degree", "Doctorate"],
        "value": "Master's Degree",
        "valid": True,
    }
    form = SimpleNamespace(
        resolver=r,
        fields={"h": field_},
        quality_answer=lambda f: None,
        control_choices={},
        field_key=lambda f: ("h",),
    )
    result = local_corrections(form, [field_])
    assert "h" not in result or result["h"][1] != "confirmed_fact_mismatch"


def test_covenant_not_to_compete_wording_is_not_a_negated_question(service):
    r = Resolver(
        Profile(facts={"has_non_compete": False, "has_employment_restrictions": False}),
        service.db,
        service.config,
        employer="Thomson Reuters",
    )
    label = (
        "Are you subject to any restrictive covenants, covenants not to compete, confidentiality agreements or other "
        "contractual obligations that would prohibit or limit your performance of any job duties at Thomson Reuters?"
    )
    a = r.local(
        {
            "id": "q",
            "label": label,
            "type": "select",
            "required": True,
            "options": ["Yes", "No"],
            "value": "",
            "valid": True,
        }
    )
    assert a and a.value == "No"


def test_validator_accepts_the_policy_city_option(service):
    from jobpilot.schemas import Address, Answer

    p = profile(address=Address(city="Springfield", state="MD", country="United States"))
    r = Resolver(p, service.db, service.config)
    options = ["Springfield, MD, USA", "Springfield, Honeygo, MD, USA", "Fairfield Inn Springfield, MD"]
    f = field("Location", options)
    ok = r._validate_answer(
        f, Answer(field_id="q", value="Springfield, MD, USA", disposition="answer"), {}, {}, {}
    )
    assert ok.disposition == "answer"
    bad = r._validate_answer(
        f, Answer(field_id="q", value="Fairfield Inn Springfield, MD", disposition="answer"), {}, {}, {}
    )
    assert bad.disposition != "answer" or bad.value != "Fairfield Inn Springfield, MD"


def test_device_type_picks_the_saved_phone_type(service):
    r = Resolver(profile(), service.db, service.config)
    f = {**field("Device type", ["Mobile", "Landline"]), "type": "combobox"}
    assert r.policy_choice(f, f["options"]) == "Mobile"


def test_dod_section_847_official_question_answers_no(service):
    # Leidos 2026-10-14: "Authorization" in the Act's name used to exclude it as a work-authorization question.
    r = Resolver(profile(), service.db, service.config, employer="Leidos")
    label = ("2. Section 847 of the National Defense Authorization Act for Fiscal Year 2008 (Pub. L. No. 110-181) "
             "requires certain current and former Department of Defense (DoD) officials to request a written opinion "
             "from a DoD ethics official before accepting compensation from a DoD contractor. Are you a current or "
             "former DoD official covered by Section 847?")
    a = r.local(yes_no(label))
    assert a and a.disposition == "answer" and a.value == "No"
    # Work authorization questions are still not owner-"No" questions.
    auth = r.local(yes_no("Are you legally authorized to work in the United States?"))
    assert auth is None or "policy:owner_no" not in (auth.evidence_ids or [])


def test_accommodation_questions_follow_the_profile_disability_fact(service):
    for label in (
        "Do you require any reasonable accommodations to participate in the interview process?",
        "Gas South is committed to equal opportunity for individuals with disabilities. Do you need a reasonable "
        "accommodation to participate in the application or interview process?",
    ):
        owner = profile()
        owner.facts["disability"] = False
        r = Resolver(owner, service.db, service.config, employer="Accenture")
        a = r.local(yes_no(label))
        assert a and a.value == "No" and a.evidence_ids == ["fact:disability"], label
    # Without a confirmed fact the question is not answered automatically.
    r = Resolver(profile(), service.db, service.config, employer="Accenture")
    a = r.local(yes_no("Do you require any reasonable accommodations to participate in the interview process?"))
    assert not (a and a.value == "No" and a.evidence_ids == ["fact:disability"])


def test_a_questionnaire_answered_date_is_today_not_a_disability_question(service):
    r = Resolver(profile(), service.db, service.config, employer="HH Global")
    field = {"id": "q", "label": "Disability Questionnaire Answered Date", "type": "date", "required": True,
             "value": "", "valid": True, "group": "", "section": "", "options": []}
    a = r.local(field)
    assert a and a.disposition == "answer" and a.evidence_ids == ["policy:today"]


def test_a_government_position_question_answers_no(service):
    # Goldman Sachs 2026-10-18 (Oracle): the question asks about holding a position, not about being employed.
    r = Resolver(profile(), service.db, service.config, employer="Goldman Sachs")
    label = ("Do you currently hold, or have you held in the past five years, a position with a government, "
             "regulatory, or Intergovernmental Organization (IGO)?")
    a = r.local(yes_no(label))
    assert a and a.disposition == "answer" and a.value == "No"
