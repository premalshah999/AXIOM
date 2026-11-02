"""NYU Langone (2026-11-01): "* Please select Yes or No." sat under a paragraph holding the real question, and reached the
applicant with no subject. The instruction alone is a generic caption: the question above it is shown with it, and an
answer saved for it is saved with that question."""

from jobpilot import owner_questions as oq

CAPTION = (
    "Have you ever been employed by Example Health System or any of its predecessor entities?"
)


def test_an_instruction_only_label_is_shown_with_the_question_above_it():
    context = {"caption": CAPTION, "section": "Additional Information"}
    shown = oq.display("* Please select Yes or No.", context)
    assert "Have you ever been employed by Example Health System" in shown
    assert oq.knowledge_question("* Please select Yes or No.", context) == shown


def test_an_instruction_only_label_is_never_reused_across_forms():
    class KB:
        def lookup(self, *args):
            return ("q", "No")

    assert oq.reusable_answer(KB(), "* Please select Yes or No.", ["Yes", "No"], {}, "", "Initech") is None
    assert oq.reusable_answer(KB(), "Are you willing to relocate?", ["Yes", "No"], {}, "", "Initech") == "No"


def test_without_the_question_it_is_a_misread_not_an_owner_question():
    assert oq.defect("* Please select Yes or No.", ["Yes", "No"], {"type": "select"}) is not None
    assert oq.defect("* Please select Yes or No.", ["Yes", "No"], {"type": "select", "caption": CAPTION}) is None
