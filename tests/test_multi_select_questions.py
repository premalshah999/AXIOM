"""Owner 2026-10-26: "there is no option to select multiple answers". A question whose form takes several answers is
marked, and a reply naming several options becomes one option per line (how checkbox groups are filled)."""

from jobpilot.owner_questions import allows_several, present
from jobpilot.telegram import multiple_picks

OPTIONS = ["Python", "SQL", "Tableau", "Excel"]


def test_several_answers_are_recognised():
    assert allows_several("Which tools have you used? Select all that apply.", {})
    assert allows_several("Tools", {"type": "checkboxgroup"})
    assert not allows_several("What is your highest degree?", {"type": "combobox"})


def test_a_reply_by_numbers_or_names_picks_each_option():
    assert multiple_picks("1, 3", OPTIONS) == ["Python", "Tableau"]
    assert multiple_picks("SQL and Excel", OPTIONS) == ["SQL", "Excel"]
    assert multiple_picks("2", OPTIONS) == []  # one pick is the ordinary single answer
    assert multiple_picks("1, 9", OPTIONS) == []  # every part must name an option


def test_the_dashboard_form_says_when_several_answers_are_allowed():
    review = {"question": "Which tools? Select all that apply", "options": OPTIONS, "context": {}, "reason": ""}
    assert present(review)["multiple"] is True
