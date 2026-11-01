"""A step is a personal test when it is the test or asks to start it: the words of a test inside a hiring process's stage list, a
status label or a privacy notice are not one. Replayed over every saved page: 33 mentioned such words and none was a test."""

from jobpilot.adapters.base import personal_test

REAL = [
    "Take Assessment. Predictive Index Behavioral Assessment. Please complete this assessment: you will be shown 87 adjectives.",
    "Take Assessment Coding Challenge",
    "You have been invited to complete a cognitive ability assessment. It takes about 25 minutes and is timed.",
    "Instructions: this personality questionnaire has 60 questions. Please answer honestly.",
]
MENTIONS = [
    "Stage 1: Application Review Stage 2: Technical Assessment Stage 3: Technical Interview Stage 4: Behavioral Interview Stage 5: Offer",
    '{"M - T1 - Skills Assessment Sent": "Submitted", "M - T1 - Skills Assessment First Follow Up": "Submitted"}',
    '"SCREEN - Technical Assessment": "Under consideration", "ASSESSMENT": "Under consideration"',
    ("We may keep information included in personality, cognitive, behavioral, and psychometric tests that you undertake. Your "
     "decision to apply is voluntary. " + "We process personal data under applicable law and our privacy notice. " * 80 + "Take Assessment"),
]


def test_a_page_that_is_the_test_or_asks_to_start_it_closes():
    for text in REAL:
        assert personal_test(text), text


def test_stage_lists_status_labels_and_privacy_text_do_not():
    for text in MENTIONS:
        assert not personal_test(text), text[:80]


def test_a_tax_credit_questionnaire_is_never_a_personal_test():
    assert not personal_test("Work Opportunity Tax Credit screening. Please complete this skills assessment survey.")
