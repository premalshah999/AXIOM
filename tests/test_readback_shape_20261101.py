"""Read-back must not call a wrong value right because its digits match.

2026-10-31 Oscar Health (Greenhouse): a phone box held "+ de1 am2éric4a05013450", keystrokes that also reached the
country search, and was read back as the right number because only its digits were compared.
2026-10-30 Flourish Research (Workable): the zip "20,874" was accepted for 20874 by the salary rule for formatted amounts.
"""

import pytest

from jobpilot.widgets import same_value

PHONE = {"type": "tel", "label": "Phone"}
ZIP = {"type": "text", "label": "* Zip Code"}
SALARY = {"type": "text", "label": "Expected annual base salary (USD)"}


@pytest.mark.parametrize("shown", ["+ de1 am2éric4a05013450", "1240abc5013450", "+1 240 501 3450 ext"])
def test_a_phone_with_letters_among_the_digits_is_not_the_number(shown):
    assert not same_value(PHONE, shown, "+1 240 501 3450")


@pytest.mark.parametrize("shown", ["+1 240-501-3450", "(240) 501-3450", "240.501.3450", "2405013450", "+1 (240) 501-3450 x12"])
def test_a_formatted_phone_is_still_the_number(shown):
    assert same_value(PHONE, shown, "2405013450")


@pytest.mark.parametrize("label", ["* Zip Code", "Postal code", "Phone number", "Social Security Number", "Employee ID"])
def test_a_grouped_code_is_not_the_typed_code(label):
    assert not same_value({"type": "text", "label": label}, "20,874", "20874")


def test_the_same_zip_is_the_same():
    assert same_value(ZIP, "20874", "20874")


def test_a_grouped_salary_is_still_the_typed_salary():
    assert same_value(SALARY, "80,000", "80000")
