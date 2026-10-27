"""Node.Digital (Workable) 2026-10-27: 'If you answered "yes" above, please list' was required under a No answer, had
no parent question, and went to the owner. It is linked to the question above and answered N/A."""

from jobpilot.answers import Resolver
from jobpilot.forms import SCAN
from jobpilot.schemas import Profile

FOLLOW = 'If you answered "yes" above, please list'


async def test_an_answered_yes_follow_up_is_linked_to_the_question_above(page):
    await page.set_content(f"""<form>
      <fieldset><legend>Do you have an active security clearance?</legend>
        <label><input type=radio name=c value=YES>YES</label><label><input type=radio name=c value=NO checked>NO</label>
      </fieldset>
      <label for=t>{FOLLOW}</label><textarea id=t required></textarea></form>""")
    data = await page.main_frame.evaluate(SCAN)
    follow = next(f for f in data["fields"] if f["label"].startswith("If you answered"))
    assert follow["related_question"]["label"] == "Do you have an active security clearance?"
    assert follow["related_question"]["value"] == "NO"


def test_the_follow_up_of_a_no_is_not_applicable(service):
    resolver = Resolver(Profile(facts={"active_security_clearance": False}), service.db, service.config)
    field = {"id": "q", "label": FOLLOW, "type": "textarea", "required": True, "options": [], "value": "",
             "related_question": {"label": "Do you have an active security clearance?", "value": "NO",
                                  "options": ["YES", "NO"]}}
    answer = resolver.local(field)
    assert answer is not None and answer.value in {"Not applicable", "N/A", "None"}
