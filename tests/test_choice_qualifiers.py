from jobpilot.widgets import best_option, same_value


def test_choice_never_discards_qualifiers_from_the_answer():
    for options, wanted in [
        (["Engineering", "Computer Engineering"], "Electrical Engineering"),
        (["Science", "Art"], "Data Science"),
        (["Other"], "Other Asian"),
        (["White", "Black"], "Not Hispanic or Latino White"),
        (["Veteran", "Disabled Veteran"], "I am not a protected veteran"),
        (["Disability", "Veteran"], "No, I do not have a disability"),
    ]:
        assert best_option(options, wanted) is None


def test_known_aliases_and_unambiguous_phone_choices_still_match():
    assert best_option(["Canada (+1)", "United States (+1)"], "United States") == "United States (+1)"
    assert best_option(["Mobile", "Landline"], "Mobile Phone") == "Mobile"
    assert best_option(["Canada (+1)", "United States (+1)"], "+1") is None


def test_prompt_readback_requires_an_entire_pill_or_alias():
    field = {"type": "prompt"}
    assert same_value(field, "Accounting\nData Science", "Data Science")
    assert same_value(field, "United States", "USA")
    assert not same_value(field, "Data Science", "Science")
    assert not same_value(field, "Science", "Data Science")
    assert not same_value(field, "Computer Science and Engineering", "Computer Science")
