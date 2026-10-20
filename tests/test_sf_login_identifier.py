"""SuccessFactors sign-in pages label the email input "Valid Email" (Capgemini) or "Email Address:"
(EY). The account step filled only the password, so every sign-in was rejected (2026-10-15)."""

from jobpilot.accounts import login_identifier


def test_successfactors_login_labels_are_identifiers():
    for label in ("Valid Email", "Valid Email *", "Email Address:", "Email", "Username", "Your email"):
        assert login_identifier({"label": label, "type": "text"}), label
    assert login_identifier({"label": "Anything", "type": "email"})


def test_other_inputs_are_not_identifiers():
    for label in ("Retype Valid Email Address of your manager", "Phone Number", "Legal First Name"):
        assert not login_identifier({"label": label, "type": "text"}), label


def test_adp_email_or_mobile_number_is_the_login_identifier():
    from jobpilot.accounts import login_identifier

    assert login_identifier({"label": "Email or Mobile Number", "type": "text"})
    assert not login_identifier({"label": "Mobile Number", "type": "text"})
