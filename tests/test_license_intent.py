"""Holding a license answers only the question about holding one."""


def field(label, options=(), type="select"):
    return {"id": "q", "label": label, "type": type, "required": False, "value": "", "valid": True,
            "group": "", "section": "", "options": list(options)}


def test_holding_a_license_answers_only_the_question_about_holding_one(service):
    """Energy Transfer 2026-10-29..31: "Has your driver's license ever been denied, limited, suspended or revoked?" was
    answered Yes from the valid-license fact, as were a commercial license and another state's license."""
    from jobpilot.answers import Resolver
    from jobpilot.schemas import Profile

    r = Resolver(Profile(facts={"valid_drivers_license": "Yes", "state_of_residence": "Maryland"}), service.db, service.config,
                 job={"ats": "custom", "url": "https://example.test/job"})
    yes_no = ["Yes", "No"]
    assert r.local(field("Do you have a valid driver's license?", yes_no)).value == "Yes"
    assert r.local(field("Do you have a current and valid Maryland driver's license?", yes_no)).value == "Yes"
    for label in ("Has your driver's license ever been denied, limited, suspended or revoked?",
                  "Do you have a Commercial Driver's License?",
                  "If the position requires driving, do you have a current and valid California Driver's License?"):
        answer = r.local(field(label, yes_no))
        assert answer is None or answer.value != "Yes", label
