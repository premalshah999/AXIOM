"""A saved menu's "Not Answered" typed into another form's free-text "Other" box was refused (Workday, 2026-11-01). The same rule
must not refuse a self-identification menu's own decline choice ("Not specified", "Not provided"): replayed over 19,860 saved pages,
the first version would have refused it wherever the field's options were not read yet."""

from jobpilot.widgets import foreign_fallback

MENU = ["Male", "Female", "Not specified"]


def test_a_saved_not_answered_is_not_typed_into_a_free_text_box():
    assert foreign_fallback("Not Answered", ["County A", "Other", "Not Answered"], [], "Other")


def test_a_self_identification_decline_choice_is_not_a_fallback():
    for question in ("Gender", "Please select your sex.", "Please select the ethnicity which most accurately describes you",
                     "I identify my race as", "Veteran status"):
        assert not foreign_fallback("Not specified", MENU, [], question), question
        assert not foreign_fallback("Not Provided", MENU, ["Male", "Female"], question), question


def test_a_menu_that_offers_the_choice_never_counted():
    assert not foreign_fallback("Not Answered", ["Other", "Not Answered"], ["Other", "Not Answered"], "Other")
