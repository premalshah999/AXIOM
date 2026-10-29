"""A control that only says "None of the Above", "Make Selection Here" or "Start & end date" reached the owner without its
question (Smithfield, Westinghouse, TikTok, 2026-10-29). forms.enrich_pending reads the question above it and the work or
education block it belongs to; owner_questions shows the complete question and refuses the ones with no question at all."""

from jobpilot import owner_questions as oq
from jobpilot.forms import ROW_CONTEXT_JS, apply_row_hints
from jobpilot.schemas import EducationEntry, Profile, WorkEntry


async def test_the_question_above_a_group_of_checkboxes_is_read(page):
    await page.set_content("""<form><h3>Tax credit screening</h3>
      <fieldset><p>Have you received any of the following benefits in the past year?</p>
      <label><input type=checkbox id=a> Food assistance</label>
      <label><input type=checkbox id=b> Housing assistance</label>
      <label><input type=checkbox id=c> None of the Above</label></fieldset></form>""")
    found = await page.locator("#c").evaluate(ROW_CONTEXT_JS)
    assert found["caption"] == "Have you received any of the following benefits in the past year?"
    assert found["heading"] == "Tax credit screening"


async def test_the_work_block_a_date_box_sits_in_is_identified(page):
    await page.set_content("""<form>
      <section class=job><label>Company <input id=c1 value='ExampleSoft'></label>
        <label>Title <input id=t1 value='Engineer'></label><label>Start &amp; end date <input id=d1></label></section>
      <section class=job><label>Company <input id=c2 value='Globex Labs'></label>
        <label>Title <input id=t2 value='Analyst'></label><label>Start &amp; end date <input id=d2></label></section></form>""")
    found = await page.locator("#d2").evaluate(ROW_CONTEXT_JS)
    assert found["kind"] == "work" and found["identity"] == {"company": "Globex Labs", "title": "Analyst"}
    assert found["index"] == 2


def test_a_hinted_control_takes_the_profile_entry_its_block_names():
    profile = Profile(
        name="Alex Example",
        work=[WorkEntry(company="ExampleSoft", title="Engineer", start="2024-01", current=True),
              WorkEntry(company="Globex Labs", title="Analyst", start="2021-06", end="2022-05")],
        education=[EducationEntry(school="Example University", degree="MS", start="2022-08", end="2024-05")],
    )
    fields = [{"id": "a", "dom_id": "d2", "label": "Start & end date"}, {"id": "b", "dom_id": "d9", "label": "x"},
              {"id": "c", "dom_id": "d3", "label": "Dates", "history_row": {"kind": "work", "index": 0}}]
    hints = {"d2": {"kind": "work", "identity": {"company": "Globex Labs"}},
             "d3": {"kind": "work", "identity": {"company": "Globex Labs"}}}
    apply_row_hints(fields, hints, profile)
    assert fields[0]["history_row"] == {"kind": "work", "index": 1}
    assert "history_row" not in fields[1]
    assert fields[2]["history_row"] == {"kind": "work", "index": 0}  # a marked row is never replaced
    # An unrecognised company places nothing.
    lone = [{"id": "z", "dom_id": "d5", "label": "Dates"}]
    apply_row_hints(lone, {"d5": {"kind": "work", "identity": {"company": "Nowhere Inc"}}}, profile)
    assert "history_row" not in lone[0]


def test_a_generic_caption_is_shown_with_its_question():
    context = {"type": "checkbox", "caption": "Have you received any of the following benefits in the past year?"}
    shown = oq.display("None of the Above", context)
    assert shown == "Have you received any of the following benefits in the past year? › None of the Above"
    # A real question keeps its own wording.
    assert oq.display("Do you have a security clearance?", context) == "Do you have a security clearance?"


def test_a_caption_without_any_question_is_not_asked():
    for label in ("None of the Above", "Make Selection Here", "Start & end date", "Other (free text)", "Begin Date"):
        assert oq.defect(label, [], {"type": "text"}) is not None, label
    # With its question, its heading or its row it is a question.
    assert oq.defect("None of the Above", [], {"caption": "Have you received any of the following benefits?"}) is None
    assert oq.defect("Start & end date", [], {"history_kind": "work", "history_identity": {"company": "ExampleSoft"}}) is None
    assert oq.defect("Make Selection Here", ["Yes", "No"], {"heading": "Export control status"}) is None


def test_a_choice_question_is_never_asked_without_its_choices():
    assert oq.defect("Which office?", [], {"type": "dropdown"}) is not None
    assert oq.defect("Which office?", [], {"type": "radiogroup"}) is not None
    assert oq.defect("Which office?", ["Austin", "Remote"], {"type": "dropdown"}) is None
    assert oq.defect("Which office?", [], {"type": "combobox"}) is None  # searchable: typed
    assert oq.defect("Which office?", [], {"type": "dropdown", "source": "repair"}) is None


def test_a_label_read_once_per_nested_wrapper_is_shown_once():
    assert oq.tidy("Company Name * Company Name * Company Name * Company Name * Company Name") == "Company Name"
    assert oq.tidy("Type of Business * Type of Business") == "Type of Business"
    assert oq.tidy("What is your name? What is your name?") == "What is your name?"
    assert oq.tidy("Do you agree? Yes, I agree") == "Do you agree? Yes, I agree"
