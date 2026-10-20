"""Toast (employer-hosted Greenhouse) asks for a "validation code" after submit; it must read as a code step."""

from jobpilot.email_browser import EmailBrowser


def field(label, **extra):
    return {"id": "f1", "label": label, "type": "text", "maxlength": 0, **extra}


def state(fields, text):
    return EmailBrowser.state(object.__new__(EmailBrowser), {"fields": fields, "text": text})


def test_please_enter_your_validation_code_is_a_code_field():
    kind, codes = state(
        [field("Please enter your validation code:")],
        "We have emailed you a code to verify your identity. Please enter your validation code:",
    )
    assert kind == "code" and [c["id"] for c in codes] == ["f1"]


def test_validation_code_label_without_please_is_a_code_field():
    assert state([field("Validation code")], "")[0] == "code"


def test_unrelated_labels_are_not_code_fields_and_waiting_text_stays_a_link_wait():
    assert state([field("Please enter your first name")], "Hello")[0] is None
    assert state([], "We have emailed you a code")[0] == "link"


def test_code_like_field_inside_a_full_application_form_is_not_a_code_step():
    form = [
        field("Code"),
        {"id": "n", "label": "First Name", "type": "text", "maxlength": 0},
        {"id": "e", "label": "Email", "type": "text", "maxlength": 0},
        {"id": "p", "label": "Phone", "type": "tel", "maxlength": 0},
    ]
    assert state(form, "Apply for this job")[0] is None
    # An explicit verification-code label is always a code step (mixed pages are reviewed).
    assert state([field("Verification code"), *form[1:]], "")[0] == "code"


def test_six_unlabeled_boxes_with_code_text_are_a_code_step():
    boxes = [{"id": f"d{i}", "label": "", "type": "text", "maxlength": 0} for i in range(6)]
    kind, codes = state(boxes, "Check your email. We just sent an email to you. Enter the code below")
    assert kind == "code" and len(codes) == 6


def test_starbucks_otp_character_boxes_are_a_code_step():
    boxes = [
        {"id": f"d{i}", "label": f"Please enter OTP character {i + 1}", "type": "text", "maxlength": -1}
        for i in range(6)
    ]
    kind, codes = state(boxes, "Check your email. We just sent an email to you. Enter the code below")
    assert kind == "code" and len(codes) == 6
