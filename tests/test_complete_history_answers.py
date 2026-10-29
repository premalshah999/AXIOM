import pytest

from jobpilot.answers import Resolver
from jobpilot.schemas import Profile
from jobpilot.features.knowledge import attest
from jobpilot.features.knowledge.attestations import EMPLOYMENT_HISTORY


@pytest.mark.parametrize("value", ["Negotiable", "$120,000", "100000-120000", "120000 USD"])
def test_numeric_widget_replans_non_numeric_answers_before_browser_fill(service, value):
    from jobpilot.schemas import Answer

    resolver = Resolver(Profile(), service.db, service.config)
    question = {**field("Desired Annual Compensation"), "type": "number", "options": []}
    assert resolver._ans(question, value, ["fact:desired_salary"]) is None
    answer = Answer(field_id="q", disposition="answer", value=value, evidence_ids=["fact:desired_salary"])
    result = resolver._validate_answer(question, answer, {"fact:desired_salary": value}, {}, {})
    assert result.blocker_type == "model_output" and result.retryable
    assert "numeric" in result.reason


def test_numeric_widget_preserves_supported_numeric_amount(service):
    from jobpilot.schemas import Answer

    resolver = Resolver(Profile(), service.db, service.config)
    question = {**field("Desired Annual Compensation"), "type": "number", "options": []}
    answer = Answer(field_id="q", disposition="answer", value="120000", evidence_ids=["fact:desired_salary"])
    result = resolver._validate_answer(question, answer, {"fact:desired_salary": "120000"}, {}, {})
    assert result.disposition == "answer" and result.value == "120000"


async def test_length_repair_supplies_actual_limit_and_previous_length(service, monkeypatch):
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from jobpilot.schemas import AnswerBatch, Answer

    question = {**field("Reason for leaving"), "type": "text", "options": [], "maxlength": 12}
    resolver = Resolver(Profile(), service.db, service.config)
    resolver.context_packets["q"] = SimpleNamespace(items=[])
    answers = [
        AnswerBatch(
            answers=[Answer(field_id="q", disposition="answer", value=value, evidence_ids=["fact:leaving"])]
        )
        for value in ["The contract has ended", "Contract end"]
    ]
    call = AsyncMock(side_effect=answers)
    monkeypatch.setattr("jobpilot.answers.model_batch.structured", call)
    result = await resolver._answer_batch(
        [question], "fixture", {"questions": [question]}, {"fact:leaving": "Contract end"}, {}, {}
    )
    assert result["q"].value == "Contract end" and call.await_count == 2
    feedback = json.loads(call.call_args.args[4])["repair_feedback"]["q"]
    assert feedback["max_characters"] == 12
    assert feedback["previous_character_count"] == len("The contract has ended")


@pytest.mark.parametrize("repair_succeeds", [True, False])
async def test_malformed_answer_gets_one_schema_repair_not_provider_outage(
    service, monkeypatch, repair_succeeds
):
    from unittest.mock import AsyncMock
    from pydantic import ValidationError
    from jobpilot.schemas import AnswerBatch, Answer

    try:
        AnswerBatch.model_validate({"answers": [{"field_id": "q", "disposition": "answer", "value": 123}]})
    except ValidationError as exc:
        malformed = exc
    valid = AnswerBatch(
        answers=[Answer(field_id="q", disposition="answer", value="123", evidence_ids=["fact:salary"])]
    )
    call = AsyncMock(side_effect=[malformed, valid if repair_succeeds else malformed])
    monkeypatch.setattr("jobpilot.answers.model_batch.structured", call)
    resolver = Resolver(Profile(), service.db, service.config)
    monkeypatch.setattr(resolver, "_validate_answer", lambda field, answer, *args: answer)
    results = await resolver._answer_batch(
        [field("Desired Annual Compensation")], "fixture", {"questions": [{"id": "q"}]}, {}, {}, {}
    )
    assert call.await_count == 2
    assert "schema_errors" in call.call_args.args[4]
    assert results["q"].blocker_type == (None if repair_succeeds else "model_output")


def field(label):
    return {
        "id": "q",
        "label": label,
        "type": "radio",
        "required": True,
        "options": ["Yes", "No"],
        "value": "",
        "valid": True,
    }


def test_demographic_undeclared_honors_existing_decline_policy(service):
    resolver = Resolver(Profile(decline_demographics=True), service.db, service.config)
    question = {
        **field("What race do you most closely associate with?"),
        "options": ["Asian", "White", "Two or more races", "Undeclared"],
    }
    answer = resolver.local(question)
    assert answer.disposition == "answer"
    assert answer.value == "Undeclared"
    assert answer.evidence_ids == ["policy:demographics"]
    # A missing decline choice cannot authorize an identity inference.
    question["options"] = ["Asian", "White"]
    assert resolver.local(question).disposition == "review"


def test_withhold_is_a_decline_choice(service):
    resolver = Resolver(Profile(decline_demographics=True), service.db, service.config)
    question = {
        **field("What is your gender?"),
        "type": "dropdown",
        "options": ["Female", "Male", "Withhold"],
    }
    answer = resolver.local(question)
    assert (answer.disposition, answer.value) == ("answer", "Withhold")


@pytest.mark.parametrize(
    "facts,options,expected",
    [
        ({"hispanic_or_latino": False}, ["Yes", "No"], "No"),
        (
            {"veteran": False},
            [
                "I am a veteran, but not a protected veteran",
                "I am not a veteran",
                "I identify as a protected veteran",
            ],
            "I am not a veteran",
        ),
        ({}, ["Yes", "No"], None),  # Nothing confirmed: still a question.
    ],
)
def test_confirmed_self_identification_used_only_without_decline_choice(service, facts, options, expected):
    resolver = Resolver(Profile(decline_demographics=True, facts=facts), service.db, service.config)
    label = (
        "Please indicate if you are Hispanic or Latino?"
        if "hispanic_or_latino" in facts or not facts
        else "If you believe you belong to any of the classifications of protected veterans, please indicate"
    )
    answer = resolver.local({**field(label), "options": options})
    if expected is None:
        assert answer is None or answer.disposition != "answer"
    else:
        assert (answer.disposition, answer.value) == ("answer", expected)
    # A decline choice still wins over a confirmed identity answer.
    declined = resolver.local({**field(label), "options": [*options, "I don't wish to answer"]})
    assert declined.value == "I don't wish to answer"


def test_complete_history_picks_single_not_applicable(service):
    resolver = Resolver(
        Profile(facts={"previously_employed_at_applying_company": False}),
        service.db,
        service.config,
        employer="Acme",
        job={"company": "Acme", "url": "https://acme.wd5.myworkdayjobs.com/job/42"},
    )
    f = {
        **field(
            "Are you currently, or have you previously been employed by Acme, or any of our subsidiaries?"
        ),
        "type": "dropdown",
        "options": [
            "Current Associate with Acme",
            "Former Associate of Acme, or subsidiary",
            "Not Applicable",
        ],
    }
    answer = resolver.complete_history_answer(f, resolver.question_context(f))
    assert answer and answer.value == "Not Applicable"


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Have you previously been employed by Acme?", "No"),
        ("Has a member of your family been employed by Acme?", None),
        ("Have you worked for a client of Acme?", None),
    ],
)
def test_explicit_prior_employer_no_is_scoped_to_applying_company(service, label, expected):
    resolver = Resolver(
        Profile(facts={"previously_employed_at_applying_company": False}),
        service.db,
        service.config,
        employer="Acme",
        job={"company": "Acme", "url": "https://acme.wd5.myworkdayjobs.com/job/42"},
    )
    f = field(label)
    answer = resolver.complete_history_answer(f, resolver.question_context(f))
    assert (answer.value if answer else None) == expected
    if answer:
        assert answer.evidence_ids == ["fact:previously_employed_at_applying_company"]
        validated = resolver._validate_answer(
            f, answer, {"fact:previously_employed_at_applying_company": False}, {}, {}
        )
        assert validated.disposition == "answer" and validated.value == "No"


def test_navigation_supplies_more_than_six_questions_without_duplicating_knowledge(service, monkeypatch):
    from types import SimpleNamespace
    from jobpilot.continuous_agent import navigation_context

    fields = [{**field(f"Screening question {i}"), "id": str(i)} for i in range(12)]
    resolver = Resolver(Profile(), service.db, service.config)
    form = SimpleNamespace(resolver=resolver, fields={f["id"]: f for f in fields})

    def retrieve(_, ids):
        return {fid: {"items": [{"reference": "knowledge:shared", "value": "confirmed"}]} for fid in ids}

    monkeypatch.setattr("jobpilot.continuous_agent.retrieve", retrieve)
    payload = navigation_context(form, {"fields": fields})
    assert set(payload["questions"]) == {str(i) for i in range(12)}
    assert len(payload["knowledge"]) == 1
    assert all(q["references"] == ["knowledge:shared"] for q in payload["questions"].values())


@pytest.mark.parametrize(
    "options,required,partial,expected",
    [
        (["Actual University", "Other"], True, False, "Other"),
        # No offered Other: the actual school is truthful (Roblox/Superhuman were parked).
        (["Actual University"], False, False, "Actual University"),
        # Searchable catalog: search the actual school; the widget still falls back to Other.
        ([], True, True, "Actual University"),
    ],
)
def test_applicant_other_first_school_preference(service, options, required, partial, expected):
    resolver = Resolver(Profile(facts={"school_catalog_other_first": True}), service.db, service.config)
    f = {
        **field("School"),
        "type": "combobox",
        "options": options,
        "required": required,
        "options_partial": partial,
    }
    answer = resolver.school_answer(f, "Actual University", ["profile:education.0.school"])
    assert (answer.value if answer.disposition == "answer" else None) == expected
    if not required and expected is None:
        assert answer.disposition == "skip"


def test_opt_definition_in_field_help_prevents_generic_sponsorship_shortcut(service):
    from jobpilot.answer_context import question_identity

    resolver = Resolver(
        Profile(facts={"requires_future_sponsorship_US": False, "current_immigration_status_US": "F-1 OPT"}),
        service.db,
        service.config,
    )
    f = field("Will you require employment-based visa sponsorship?")
    f["hint"] = "Select YES if you are an F-1 student working under OPT or STEM OPT."
    # The field help defines Yes for the recorded status; the stored "no sponsorship" flag must not answer No.
    answer = resolver.local(f)
    assert answer.value == "Yes" and answer.evidence_ids == ["fact:current_immigration_status_US"]
    assert question_identity(f) != question_identity({**f, "hint": ""})
    # Without the definition, the plain flag still answers the plain question.
    assert resolver.local({**f, "hint": ""}).value == "No"


@pytest.mark.parametrize("confirmed", [False, True])
def test_broad_reputation_disclosure_needs_its_own_fact_instead_of_criminal_history(service, confirmed):
    facts = {"criminal_conviction": False}
    if confirmed:
        facts["has_circumstances_affecting_employer_reputation"] = False
    resolver = Resolver(Profile(facts=facts), service.db, service.config)
    f = {
        **field(
            "Are there any circumstances, such as any pending or completed disciplinary action against you, whether by the Teaching Regulation Agency or otherwise, that could cause the employer's reputation damage?"
        ),
        "type": "pills",
        "options": ["Yes", "No"],
    }
    answer = resolver.local(f)
    assert answer.disposition == ("answer" if confirmed else "review")
    if confirmed:
        assert answer.value == "No"
        assert answer.evidence_ids == ["fact:has_circumstances_affecting_employer_reputation"]
    else:
        assert answer.blocker_type == "missing_fact"


def test_graduation_month_uses_only_supported_month_without_appending_year(service):
    resolver = Resolver(
        Profile(education=[{"school": "Fixture", "degree": "MS", "end": "2027-05"}]),
        service.db,
        service.config,
    )
    f = {
        **field("What is your expected (or actual) graduation month?"),
        "type": "combobox",
        "options": ["April", "May", "June"],
    }
    answer = resolver.local(f)
    assert answer.value == "May" and answer.evidence_ids == ["profile:education.0.end"]


ICIMS_DATE = ["Day", "Month", "Year"]


def graduation_resolver(service, reviewed=()):
    return Resolver(
        Profile(
            education=[{"school": "Fixture", "degree": "MS", "end": "2027-05"}],
            reviewed_answers=[
                {"id": f"r{i}", "question": q, "answer": a, "scope": "personal", "layer": "fact"}
                for i, (q, a) in enumerate(reviewed)
            ],
        ),
        service.db,
        service.config,
    )


def date_part(label, kind="select", options=(), required=False):
    return {**field(label), "type": kind, "options": list(options), "required": required, "date_parts": ICIMS_DATE}


def test_split_graduation_date_takes_the_reviewed_day(service):
    # iCIMS (Schwab 2026-10-21): "Graduation Date Year" received "05/2027" and kept "05/2"; the date needs all parts.
    resolver = graduation_resolver(service, [("Graduation Date", "21st May 2027")])
    year = resolver.local(date_part("Graduation Date Year", "text"))
    month = resolver.local(date_part("Graduation Date Month", options=["Jan", "Apr", "May", "Jun"]))
    day = resolver.local(date_part("Graduation Date Day", options=[str(n) for n in range(1, 32)]))
    assert (year.value, month.value, day.value) == ("2027", "May", "21")
    assert day.evidence_ids == ["profile:education.0.end", "reviewed:r0"]


def test_split_graduation_date_is_whole_or_blank(service):
    resolver = graduation_resolver(service, [("Graduation Date", "05/2027")])  # no day anywhere
    for label, kind in [("Graduation Date Year", "text"), ("Graduation Date Month", "select"),
                        ("Graduation Date Day", "select")]:
        assert resolver.local(date_part(label, kind, ["May", "1", "21"])) is None
    # Required, the first of the month, as iCIMS's own resume parser fills its rows.
    required = resolver.local(date_part("Graduation Date Day", options=["1", "2", "21"], required=True))
    assert required.value == "1"


def test_a_lone_graduation_year_needs_no_day(service):
    answer = graduation_resolver(service).local({**field("Graduation Year"), "type": "text", "options": []})
    assert answer.value == "2027"


@pytest.mark.parametrize("confirmed", [False, True])
def test_missing_employer_requires_explicit_complete_history_attestation(service, confirmed):
    if confirmed:
        attest(service.db, "employment_history_complete", EMPLOYMENT_HISTORY["statement"])
    profile = Profile(work=[{"company": "Globex", "title": "Developer"}])
    resolver = Resolver(
        profile,
        service.db,
        service.config,
        employer="Acme",
        job={"company": "Acme", "url": "https://acme.wd5.myworkdayjobs.com/job/42"},
    )
    f = field("Have you previously been employed by Acme?")
    a = resolver.local(f)
    assert (a is not None) is confirmed
    if a:
        assert a.value == "No" and a.evidence_ids[0].startswith("knowledge:")
        assert "profile:work.0" in a.evidence_ids


@pytest.mark.parametrize(
    "label",
    [
        "Have you previously been employed by Globex?",
        "Have you never worked for Acme?",
        "Has a member of your family been employed by Acme?",
        "Have you worked for a client of Acme?",
    ],
)
def test_complete_history_does_not_answer_other_employers_relatives_clients_or_negation(service, label):
    attest(service.db, "employment_history_complete", EMPLOYMENT_HISTORY["statement"])
    resolver = Resolver(
        Profile(work=[{"company": "Globex", "title": "Developer"}]),
        service.db,
        service.config,
        employer="Acme",
        job={"url": "https://acme.wd5.myworkdayjobs.com/job/42"},
    )
    f = field(label)
    packet = resolver.question_context(f)
    assert resolver.complete_history_answer(f, packet) is None


def test_known_alias_in_history_prevents_false_no(service):
    attest(service.db, "employment_history_complete", EMPLOYMENT_HISTORY["statement"])
    resolver = Resolver(
        Profile(work=[{"company": "Acme Corporation", "title": "Developer"}]),
        service.db,
        service.config,
        employer="Acme",
        job={"url": "https://acme.wd5.myworkdayjobs.com/job/42"},
    )
    f = field("Are you a current or former employee of Acme?")
    assert resolver.complete_history_answer(f, resolver.question_context(f)) is None


@pytest.mark.parametrize("known_value,accepted", [("Yes", True), ("No", False)])
def test_critical_answer_accepted_only_when_deterministic_resolver_agrees(
    service, monkeypatch, known_value, accepted
):
    from jobpilot.schemas import Answer

    resolver = Resolver(Profile(), service.db, service.config)
    question = field("Are you authorized to be employed in the United States?")
    known = Answer(field_id="q", disposition="answer", value=known_value, evidence_ids=["knowledge:auth"])
    monkeypatch.setattr(resolver, "local", lambda f: known)
    model = Answer(field_id="q", disposition="answer", value="Yes", evidence_ids=["knowledge:unrecognized"])
    result = resolver._validate_answer(question, model, {}, {}, {})
    assert (result.disposition == "answer") is accepted
    if accepted:
        assert result.evidence_ids == ["knowledge:auth"]


def test_located_in_state_options_use_the_address(service):
    from jobpilot.schemas import Address

    resolver = Resolver(
        Profile(address=Address(city="Springfield", state="MD", country="United States")),
        service.db,
        service.config,
    )
    q = {
        **field(
            "Please Acknowledge: it is unlawful in Massachusetts to require a lie detector test; "
            "criminal penalties apply."
        ),
        "options": ["I am located in Massachusetts, I acknowledge", "I am not located in Massachusetts"],
    }
    answer = resolver.local(q)
    assert (answer.disposition, answer.value) == ("answer", "I am not located in Massachusetts")


def test_related_person_question_is_not_own_prior_employment():
    from jobpilot.knowledge import relevant_fact, topic

    label = "Do you have any related person’s currently employed with the Vizient family of companies?"
    assert topic(label) == "referral" and relevant_fact(label, "has_relatives_at_employer")
    assert topic("Have you previously been employed by Vizient?") == "prior_employment"


@pytest.mark.parametrize(
    "label",
    [
        'If you answered "yes" to the question above, please provide the name(s) of such employee(s). If you answered "no", please list N/A.',
        '(a) If the answer is "Yes," please provide details below:',
    ],
)
def test_inactive_conditional_followup_is_not_applicable(service, label):
    resolver = Resolver(
        Profile(facts={"willing_to_relocate": True}), service.db, service.config, employer="Acme"
    )
    parent = "Are you willing to relocate?"
    f = {
        **field(label),
        "type": "text",
        "options": [],
        "related_question": {"label": parent, "value": "No", "options": ["Yes", "No"]},
    }
    # The parent resolves to Yes here, so a "yes" follow-up is active and must not be N/A.
    assert resolver.local(f) is None or resolver.local(f).value not in {"N/A", "Not applicable"}
    resolver.profile.facts["willing_to_relocate"] = False
    f["related_question"]["value"] = "No"
    answer = resolver.local(f)
    assert answer and answer.value in {"N/A", "Not applicable"}


def test_gender_fact_only_without_decline(service):
    resolver = Resolver(Profile(facts={"gender": "Male"}), service.db, service.config, employer="Acme")
    f = {**field("Gender"), "type": "radio", "options": ["Male", "Female", "Non-binary"]}
    assert resolver.local(f).value == "Male"
    declined = resolver.local({**f, "options": ["Male", "Female", "Decline to self-identify"]})
    assert declined.value == "Decline to self-identify"


def test_reviewed_race_answer_reused_only_without_decline(service):
    from jobpilot.schemas import ReviewedAnswer

    review = ReviewedAnswer(
        id="r1",
        question="How would you describe your racial/ethnic background?",
        answer="South Asian",
        scope="personal",
    )
    resolver = Resolver(Profile(reviewed_answers=[review]), service.db, service.config, employer="Acme")
    options = ["East Asian", "South Asian", "White or European", "I prefer to self-describe"]
    f = {
        **field("How would you describe your racial/ethnic background? (mark all that apply)"),
        "type": "checkboxgroup",
        "options": options,
    }
    assert resolver.local(f).value == "South Asian"
    assert (
        resolver.local({**f, "options": [*options, "I don't wish to answer"]}).value
        == "I don't wish to answer"
    )


def test_conflicting_demographic_answers_use_same_employer_answer(service):
    from jobpilot.schemas import ReviewedAnswer

    q = "How would you describe your racial/ethnic background?"
    reviews = [
        ReviewedAnswer(id="a", question=q, answer="South Asian", scope="personal", employer="Block"),
        ReviewedAnswer(
            id="b", question=q, answer="I prefer to self-describe", scope="personal", employer="Roblox"
        ),
    ]
    options = ["East Asian", "South Asian", "I prefer to self-describe"]
    f = {**field(q), "type": "checkboxgroup", "options": options}
    roblox = Resolver(Profile(reviewed_answers=reviews), service.db, service.config, employer="Roblox")
    assert roblox.local(f).value == "I prefer to self-describe"
    block = Resolver(Profile(reviewed_answers=reviews), service.db, service.config, employer="Block")
    assert block.confirmed_demographic(f, options).value == "South Asian"


def test_source_falls_back_to_a_truthful_general_category(service):
    rules = {"source_order": ["company website", "internet search"], "source_any_channel": True}
    resolver = Resolver(
        Profile(application_source="LinkedIn", answer_rules=rules), service.db, service.config, employer="ICF"
    )
    options = [
        "Browsing the internet",
        "ICF Career Page",
        "I was contacted by a Recruiter",
        "Saw ICF on social media",
    ]
    f = {**field("How Did You Hear About Us?"), "type": "dropdown", "options": options}
    assert resolver.local(f).value == "Saw ICF on social media"
    f["options"] = ["Browsing the internet", "ICF Career Page", "Met ICF on campus"]
    assert resolver.local(f).value == "Browsing the internet"
    # The profile lists the employer's careers page as an accepted source.
    f["options"] = ["ICF Career Page", "Met ICF on campus"]
    assert resolver.local(f).value == "ICF Career Page"
    f["options"] = ["Met ICF on campus", "Employee referral"]
    assert resolver.local(f).disposition != "answer"


def test_employer_boilerplate_does_not_negate_the_question(service):
    resolver = Resolver(Profile(facts={"has_non_compete": False}), service.db, service.config, employer="ICF")
    label = (
        "Are you subject to any continuing employment restrictions or obligations with your current or former "
        "employer? These restrictions (for example, non-competition or non-solicitation clauses) are often found "
        "in offer letters. Note: Your response does not immediately disqualify you from employment with ICF."
    )
    f = {**field(label), "options": ["Yes", "No"]}
    assert resolver.local(f).value == "No"


def test_state_notice_options_are_answered_from_the_address(service):
    from jobpilot.schemas import Address

    options = [
        "Maine - Acknowledgment of Receipt I acknowledge that I received and reviewed this notice.",
        "Not a resident of Maine - does not apply",
    ]
    f = {**field("NOTICE OF ELECTRONIC MONITORING"), "type": "dropdown", "options": options}
    md = Resolver(
        Profile(address=Address(city="Springfield", state="MD", country="United States")),
        service.db,
        service.config,
        employer="Lowe's",
    )
    assert md.local(f).value == "Not a resident of Maine - does not apply"
    me = Resolver(
        Profile(address=Address(city="Portland", state="ME", country="United States")),
        service.db,
        service.config,
        employer="Lowe's",
    )
    assert me.local(f).value.startswith("Maine - Acknowledgment")


def test_language_row_is_answered_from_the_confirmed_languages(service):
    resolver = Resolver(
        Profile(facts={"languages": ["English", "Hindi"]}), service.db, service.config, employer="Acme"
    )
    options = ["Arabic", "English", "Hindi"]

    def ask(group, label="Language", **extra):
        return resolver.local(
            {**field(label), "type": "dropdown", "options": options, "group": group, **extra}
        )

    assert ask("Languages 1").value == "English"
    assert ask("Languages 2").value == "Hindi"
    assert ask("Languages 3") is None or ask("Languages 3").disposition != "answer"
    assert (
        ask("", label="Language of instruction") is None
        or ask("", label="Language of instruction").disposition != "answer"
    )


@pytest.mark.parametrize("label", [
    "Are you, or have you ever been, a contractor for Acme?",
    "Have you ever been an employee for Acme?",
])
def test_ever_been_a_contractor_or_employee_is_prior_employment(service, label):
    # The Federal Reserve System (2026-10-26): the contractor wording went to the model, which copied "Yes" from the
    # complete-history fact and opened an entity checklist the applicant has no answer for.
    attest(service.db, "employment_history_complete", EMPLOYMENT_HISTORY["statement"])
    resolver = Resolver(Profile(work=[{"company": "Globex", "title": "Developer"}]), service.db, service.config,
                        employer="Acme", job={"company": "Acme", "url": "https://acme.wd5.myworkdayjobs.com/job/42"})
    answer = resolver.local({**field(label), "options": ["Yes", "No"], "type": "select"})
    assert answer is not None and answer.value == "No"


@pytest.mark.parametrize("label,value", [
    ("Do you use Duolingo?", "Yes"),
    ("Are you, or have you ever been, a contractor for Acme?", "Yes"),
    ("Are you willing to relocate?", "No"),
])
def test_the_history_attestation_never_backs_another_subject_or_a_yes(service, label, value):
    # Duolingo and the Federal Reserve System (2026-10-26): the model cited "my employment history is complete" (Yes)
    # for "Do you use Duolingo?" and for a contractor question, and both went out as Yes.
    from jobpilot.schemas import Answer

    fact = attest(service.db, "employment_history_complete", EMPLOYMENT_HISTORY["statement"])
    resolver = Resolver(Profile(), service.db, service.config, employer="Acme")
    ref = "knowledge:" + (fact.fact_id if hasattr(fact, "fact_id") else str(fact))
    answer = Answer(field_id="q", disposition="answer", value=value, evidence_ids=[ref])
    assert resolver.relationship_evidence_misused({"id": "q", "label": label}, answer, {})


def test_the_history_attestation_still_backs_a_no_on_prior_employment(service):
    from jobpilot.schemas import Answer

    fact = attest(service.db, "employment_history_complete", EMPLOYMENT_HISTORY["statement"])
    resolver = Resolver(Profile(), service.db, service.config, employer="Acme")
    ref = "knowledge:" + (fact.fact_id if hasattr(fact, "fact_id") else str(fact))
    answer = Answer(field_id="q", disposition="answer", value="No", evidence_ids=[ref])
    assert not resolver.relationship_evidence_misused({"id": "q", "label": "Have you previously worked for Acme?"},
                                                      answer, {})


@pytest.mark.parametrize("ref", ["knowledge:m:review:abc", "reviewed:abc"])
def test_a_saved_answer_never_fills_a_bare_history_date(service, ref):
    # Cintas and Supermicro (SuccessFactors 2026-10-27): "End Date: 05/13/2003", saved once without its row, became
    # every row's end date and "End date must be after Start date" stalled both.
    from jobpilot.schemas import Answer

    resolver = Resolver(Profile(), service.db, service.config)
    question = {**field("End Date"), "type": "text", "options": []}
    answer = Answer(field_id="q", disposition="answer", value="05/13/2003", evidence_ids=[ref])
    result = resolver._validate_answer(question, answer, {}, {ref: "05/13/2003"}, {})
    assert result.disposition != "answer"


@pytest.mark.parametrize("label,ok", [("Date:", False), ("End Date", False), ("Date Obtained 1", False),
                                      ("Date of Birth", True)])
def test_the_date_of_birth_answers_only_a_birth_question(service, label, ok):
    # Supermicro (2026-10-27): "Date:" next to a signature took the date of birth.
    from jobpilot.schemas import Answer

    resolver = Resolver(Profile(facts={"date_of_birth": "05/13/1990"}), service.db, service.config)
    question = {**field(label), "type": "text", "options": []}
    answer = Answer(field_id="q", disposition="answer", value="05/13/1990", evidence_ids=["fact:date_of_birth"])
    result = resolver._validate_answer(question, answer, {"fact:date_of_birth": "05/13/1990"}, {}, {})
    assert (result.disposition == "answer") is ok


@pytest.mark.parametrize("kind", ["checkboxgroup", "select"])
def test_no_clearance_picks_the_offered_none_option(service, kind):
    # Credence (Workable 2026-10-27): the list offered "I currently do not have an active security clearance".
    options = ["Confidential", "Secret", "Top Secret", "I currently do not have an active security clearance"]
    resolver = Resolver(Profile(facts={"active_security_clearance": False}), service.db, service.config)
    answer = resolver.local({"id": "q", "label": "Please indicate which active clearances you have.", "type": kind,
                             "options": options, "required": True})
    assert answer and answer.value == "I currently do not have an active security clearance"


def test_a_date_asked_of_a_named_employer_comes_from_its_row(service):
    # Leonardo DRS (2026-10-27) asked the owner "What was your end date at ExampleSoft?".
    profile = Profile(work=[{"company": "Globex", "title": "Engineer", "start": "2022-03", "end": "2022-06"}])
    resolver = Resolver(profile, service.db, service.config)
    answer = resolver.local({"id": "q", "label": "What was your end date at Globex?", "type": "text", "options": []})
    assert answer and answer.value and "profile:work.0" in answer.evidence_ids


@pytest.mark.parametrize("state,index", [("MA", 0), ("Massachusetts", 0), ("CA", 1)])
def test_reside_notice_options_use_saved_state(service, state, index):
    from jobpilot.schemas import Address

    resolver = Resolver(Profile(address=Address(state=state, country="United States")), service.db, service.config)
    options = [
        "I reside in Massachusetts and acknowledge that I have received notice of this statement pursuant to G.L. c. 149, § 19B.",
        "I don’t reside in Massachusetts.",
    ]
    q = {**field("It is unlawful in Massachusetts to require a lie detector test; criminal penalties apply."),
         "options": options}
    answer = resolver.local(q)
    assert answer.disposition == "answer"
    assert answer.value == options[index]
    assert answer.evidence_ids == ["profile:address"]


def test_residence_parser_does_not_answer_an_additional_personal_claim(service):
    from jobpilot.schemas import Address

    resolver = Resolver(Profile(address=Address(state="CA")), service.db, service.config)
    q = {**field("Please select"), "options": [
        "I reside in Massachusetts and have never been convicted.", "I don’t reside in Massachusetts."]}
    assert resolver.located_in_answer(q) is None


@pytest.mark.parametrize("apostrophe", ["'", "’"])
def test_graduated_category_with_a_contraction_is_the_completed_choice(service, apostrophe):
    # DV Trading's Greenhouse (2026-10-29): "I've already graduated" normalizes to "i ve already graduated", read as no
    # category, so the local answer typed a date into the menu and the commit failed on every run.
    resolver = Resolver(
        Profile(education=[{"school": "Fixture", "degree": "MS", "end": "2026-05"}]), service.db, service.config
    )
    done = f"I{apostrophe}ve already graduated"
    f = {
        **field("Please re-confirm your expected graduation date"),
        "type": "combobox",
        "options": [done, "August 2026 - December 2026", "January 2027 - July 2027"],
    }
    assert resolver.graduation_choice_valid(f, done) is True
    assert resolver.graduation_choice_valid(f, f"I{apostrophe}ll graduate after August 2026") is False
    assert resolver.local(f).value == done
