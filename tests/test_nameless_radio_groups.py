"""Nuvo (Gem, 2026-10-31): Gem's Yes/No questions are nameless radios in no fieldset, the question a bare span above
the choices. They read as four lone "Yes"/"No" fields, the answers never stuck, and four runs stalled on the unchanged
page ("Final submit control was not unique"). Each pair is one question."""

from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

QUESTION = """<div><span>{q}<span> *</span></span></div><div><div><div>
  <div><input type=radio id={a}><label for={a}>Yes</label></div><div><input type=radio id={b}><label for={b}>No</label></div></div></div></div>"""
FORM = "<form>" + QUESTION.format(q="Are you graduating in 2027?", a="r1", b="r2") + \
    QUESTION.format(q="Is your degree in Computer Science?", a="r3", b="r4") + "</form>"


async def scanned(page, tmp_path, html):
    await page.set_content(html)
    return await FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a, **k: None).scan()


async def test_nameless_yes_no_pairs_are_one_question_each(page, tmp_path):
    obs = await scanned(page, tmp_path, FORM)
    groups = [f for f in obs["fields"] if f["type"] == "radio"]
    assert [g["label"] for g in groups] == ["Are you graduating in 2027?", "Is your degree in Computer Science?"]
    assert all(g["options"] == ["Yes", "No"] and g["required"] for g in groups)


async def test_named_radios_and_a_lone_nameless_radio_are_unchanged(page, tmp_path):
    html = "<form><fieldset><legend>Pick</legend><input type=radio name=p id=p1><label for=p1>A</label>" \
           "<input type=radio name=p id=p2><label for=p2>B</label></fieldset><p>Remember me</p>" \
           "<input type=radio id=solo><label for=solo>Subscribe</label></form>"
    obs = await scanned(page, tmp_path, html)
    radios = {f["label"]: f["options"] for f in obs["fields"] if f["type"] == "radio"}
    assert radios["Pick"] == ["A", "B"] and len(radios) == 2
