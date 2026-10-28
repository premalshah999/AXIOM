"""Gem (Imagen Technologies, 2026-10-28): "Congratulations! Your application for Software Engineer has been received"
names the role between "application" and "received"; three runs read it as unconfirmed while the page showed it."""

from jobpilot.adapters.base import CONFIRM_TEXT


def test_a_receipt_that_names_the_role_is_a_receipt():
    assert CONFIRM_TEXT.search("Congratulations! Your application for Software Engineer has been received.")
    assert CONFIRM_TEXT.search("Your application for Data Engineer (Req ID: R512) was received")
    assert CONFIRM_TEXT.search("Your application for the Analyst role was successfully submitted")


def test_instructions_are_not_receipts():
    assert not CONFIRM_TEXT.search("Your application for this role will be reviewed once submitted.")
    assert not CONFIRM_TEXT.search("Complete your application for Software Engineer. Required fields are marked.")
