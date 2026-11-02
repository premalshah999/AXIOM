"""The owner's rules for every tailored resume (2026-11-01), on synthetic documents and on a really compiled PDF."""

import asyncio
import shutil

import pytest

from jobpilot.features.resumes import editor, modes
from jobpilot.features.resumes.compiler import compile_pdf
from jobpilot.features.resumes.editor import Bullet, Entry, ResumeDoc, Section
from jobpilot.features.resumes.modes import Draft, check_rules, draft_leaks
from jobpilot.features.resumes.rules import bullet_window
from jobpilot.features.resumes.validate import check_typography, font_runs, parse_check

LONG = "Built a reporting pipeline in SQL and Python that replaced a manual spreadsheet process for the finance team"
OTHER = "Reviewed monthly close variances with the controller and documented every adjustment for the external audit"
PROJECT = "Wrote a small command line tool in Python that checks invoices against purchase orders every night"


def source():
    return ResumeDoc(
        name="Alex Example",
        contact=["alex@example.test", "+1 202 555 0100"],
        sections=[
            Section(
                id="experience",
                title="Experience",
                kind="experience",
                entries=[
                    Entry(
                        id="r1",
                        heading="Data Analyst",
                        subheading="ExampleSoft",
                        dates="06/2023 - Present",
                        location="Sampleville, MD",
                        bullets=[Bullet(id="b1", text=LONG), Bullet(id="b2", text=OTHER)],
                    )
                ],
            ),
            Section(
                id="education",
                title="Education",
                kind="education",
                entries=[Entry(id="e1", heading="Example University", subheading="B.S., Statistics", dates="08/2019 - 05/2023")],
            ),
            Section(
                id="projects",
                title="Projects",
                kind="projects",
                entries=[Entry(id="p1", heading="InvoiceCheck", bullets=[Bullet(id="p1b1", text=PROJECT)])],
            ),
            Section(id="skills", title="Skills", kind="skills", text="Languages: Python, SQL\nTools: Excel, Tableau"),
            Section(id="awards", title="Awards", kind="text", text="Dean's List, Example University"),
        ],
    )


def edit(fn):
    doc = source()
    fn(doc)
    return doc


def codes(report):
    return {p["code"] for p in report["problems"]}


def sec(doc, sid):
    return next(s for s in doc.sections if s.id == sid)


def test_an_unchanged_resume_passes():
    assert check_rules(source(), source())["ok"]


def test_the_window_is_the_source_length_down_to_ten_percent_shorter():
    assert bullet_window("x" * 100) == (90, 100)
    assert bullet_window("x" * 101) == (91, 101)


@pytest.mark.parametrize("n,code", [(len(LONG) + 1, "bullet_too_long"), (int(len(LONG) * 0.89), "bullet_too_short")])
def test_a_bullet_outside_the_window_fails(n, code):
    doc = edit(lambda d: setattr(sec(d, "experience").entries[0].bullets[0], "text", ("y" * n)))
    assert code in codes(check_rules(doc, source()))


@pytest.mark.parametrize("n", [len(LONG), int(len(LONG) * 0.95)])
def test_a_bullet_inside_the_window_passes(n):
    words = ("Built a reporting pipeline in SQL and Python for the finance team " * 3)[:n].rstrip()
    words = words + "." * (n - len(words))
    doc = edit(lambda d: setattr(sec(d, "experience").entries[0].bullets[0], "text", words))
    report = check_rules(doc, source())
    assert not codes(report) & {"bullet_too_long", "bullet_too_short"}, report


def test_a_dropped_bullet_fails_the_count_on_experience_and_on_projects():
    doc = edit(lambda d: sec(d, "experience").entries[0].bullets.pop())
    assert "bullet_count" in codes(check_rules(doc, source()))
    doc = edit(lambda d: setattr(sec(d, "projects").entries[0].bullets[0], "include", False))
    assert "bullet_count" in codes(check_rules(doc, source()))


def test_an_added_bullet_fails_the_count():
    doc = edit(lambda d: sec(d, "experience").entries[0].bullets.append(Bullet(id="new", text=LONG)))
    assert {"bullet_count", "unknown_bullet"} <= codes(check_rules(doc, source()))


@pytest.mark.parametrize("sid", ["experience", "education"])
def test_an_entry_can_never_be_dropped(sid):
    doc = edit(lambda d: setattr(sec(d, sid).entries[0], "include", False))
    assert "entry_dropped" in codes(check_rules(doc, source()))


def test_removing_a_whole_section_fails():
    doc = edit(lambda d: d.sections.pop(1))
    assert "section_dropped" in codes(check_rules(doc, source()))


def test_a_title_or_date_cant_change():
    doc = edit(lambda d: setattr(sec(d, "experience").entries[0], "heading", "Senior Data Scientist"))
    assert "entry_changed" in codes(check_rules(doc, source()))
    doc = edit(lambda d: setattr(sec(d, "experience").entries[0], "dates", "06/2020 - Present"))
    assert "entry_changed" in codes(check_rules(doc, source()))


def test_a_skill_can_be_added_only_if_real_and_never_removed():
    doc = edit(lambda d: setattr(sec(d, "skills"), "text", "Languages: Python\nTools: Excel, Tableau"))
    assert "skill_removed" in codes(check_rules(doc, source()))
    doc = edit(lambda d: setattr(sec(d, "skills"), "text", "Languages: Python, SQL, Rust\nTools: Excel, Tableau"))
    assert "unsupported_skill" in codes(check_rules(doc, source()))
    assert check_rules(doc, source(), allowed_skills=["Rust"])["ok"]


def test_skills_may_be_regrouped_under_new_labels():
    doc = edit(lambda d: setattr(sec(d, "skills"), "text", "Reporting: Tableau, Excel, SQL\nCode: Python"))
    assert check_rules(doc, source())["ok"]


def test_a_project_stays_unless_the_user_removes_it():
    doc = edit(lambda d: setattr(sec(d, "projects").entries[0], "include", False))
    assert "entry_dropped" in codes(check_rules(doc, source()))
    assert check_rules(doc, source(), removed_projects=["p1"])["ok"]


def test_a_new_project_needs_confirmation_and_keeps_its_bullet_count():
    built = Entry(id="built:1", heading="LedgerLens", bullets=[Bullet(id="x1", text=PROJECT), Bullet(id="x2", text=OTHER)])
    doc = edit(lambda d: sec(d, "projects").entries.append(built))
    assert "unknown_project" in codes(check_rules(doc, source()))
    assert check_rules(doc, source(), added_projects=[built])["ok"]
    short = built.model_copy(deep=True)
    short.bullets.pop()
    doc = edit(lambda d: sec(d, "projects").entries.append(short))
    assert "bullet_count" in codes(check_rules(doc, source(), added_projects=[built]))


def test_a_confirmed_project_can_supply_an_added_skill():
    doc = edit(lambda d: setattr(sec(d, "skills"), "text", "Languages: Python, SQL, Rust\nTools: Excel, Tableau"))
    assert check_rules(doc, source(), allowed_skills=["Rust"])["ok"]


def test_a_summary_may_be_added_but_not_with_an_invented_claim():
    good = Section(id="summary", title="Summary", kind="text", text="Data analyst who builds reporting pipelines in Python and SQL.")
    assert check_rules(edit(lambda d: d.sections.insert(0, good)), source())["ok"]
    bad = Section(id="summary", title="Summary", kind="text", text="Data analyst with 9 years at Kubernetes shops.")
    report = check_rules(edit(lambda d: d.sections.insert(0, bad)), source())
    assert "unsupported_claim" in codes(report)


def test_a_summary_the_user_typed_warns_where_a_generated_one_fails():
    bad = Section(id="summary", title="Summary", kind="text", text="Data analyst with 9 years at Kubernetes shops.")
    report = check_rules(edit(lambda d: d.sections.insert(0, bad)), source(), edited=["summary"])
    assert report["ok"] and {w["code"] for w in report["warnings"]} == {"unsupported_claim"}


def test_a_real_name_ending_a_sentence_isnt_an_invented_one():
    from jobpilot.features.resumes import claims

    assert claims.check("Wrote reports in Python.", "Wrote reports in Python", max_growth=2) == []
    assert claims.check("Ran it on Node.js.", "Ran it on Node.js", max_growth=2) == []
    assert "unsupported name or tool: Rust" in claims.check("Wrote reports in Rust.", "Wrote reports in Python", max_growth=2)


def test_any_other_new_section_fails_and_a_kept_one_stays_as_written():
    extra = Section(id="x", title="Publications", kind="text", text="Nature, 2024")
    assert "unknown_section" in codes(check_rules(edit(lambda d: d.sections.append(extra)), source()))
    doc = edit(lambda d: setattr(sec(d, "awards"), "text", "Dean's List and Rhodes Scholar"))
    assert "section_changed" in codes(check_rules(doc, source()))


def test_a_new_number_or_tool_in_a_bullet_fails_unless_the_user_typed_it():
    text = "Built a reporting pipeline in SQL and Kubernetes that replaced a manual spreadsheet process by 40 percent"
    doc = edit(lambda d: setattr(sec(d, "experience").entries[0].bullets[0], "text", text))
    assert "unsupported_claim" in codes(check_rules(doc, source()))
    typed = check_rules(doc, source(), edited=["b1"])
    assert "unsupported_claim" not in codes(typed)
    assert {w["code"] for w in typed["warnings"]} == {"your_statement"}


def test_an_unconfirmed_draft_fails_wherever_it_appears():
    draft = Draft(id="d1", title="ReadmitLens triage", bullets=["Deployed a triage tool that scores discharge summaries, cutting review to [your result]"])
    doc = edit(lambda d: setattr(sec(d, "experience").entries[0].bullets[0], "text", "ReadmitLens triage " + LONG[:80]))
    assert "unconfirmed_draft" in codes(check_rules(doc, source(), unconfirmed=[draft]))
    assert draft_leaks("Deployed a triage tool that scores discharge summaries, cutting review to 4 minutes", [draft])
    assert not draft_leaks("Deployed a triage tool", [draft])
    assert not draft_leaks(draft.title, [draft.model_copy(update={"confirmed": True})])


@pytest.mark.skipif(not shutil.which("pdflatex"), reason="needs TeX")
@pytest.mark.parametrize("font", ["times", "helvetica"])
def test_the_compiled_pdf_prints_the_owners_type_sizes(font):
    doc = source()
    built = asyncio.run(compile_pdf(editor.render(doc, editor.tailored_style(editor.Style(font=font)))))
    assert built.ok, built.detail
    check = check_typography(built.pdf, doc)
    assert check["ok"], check
    assert check["sizes"]["name"] == [16.0]
    assert check["sizes"]["section"] == [10.5]
    assert check["sizes"]["sub"] == [10.0]
    assert check["sizes"]["bullet"] == [9.0]
    date = next(r for r in font_runs(built.pdf) if "06/2023" in r["text"])
    assert "Ital" in date["font"] or "Obli" in date["font"]
    assert check_rules(doc, source(), pdf=built.pdf)["ok"]


@pytest.mark.skipif(not shutil.which("pdflatex"), reason="needs TeX")
def test_a_pdf_in_the_editors_own_sizes_fails_the_type_check():
    doc = source()
    built = asyncio.run(compile_pdf(editor.render(doc, editor.Style())))
    assert built.ok
    report = check_rules(doc, source(), pdf=built.pdf)
    assert "typography" in codes(report)


@pytest.mark.skipif(not shutil.which("pdflatex"), reason="needs TeX")
def test_a_draft_that_got_into_the_pdf_itself_is_caught():
    draft = Draft(id="d1", title="InvoiceCheck", bullets=[])
    built = asyncio.run(compile_pdf(editor.render(source(), editor.tailored_style())))
    assert "unconfirmed_draft" in codes(check_rules(source(), source(), pdf=built.pdf, unconfirmed=[draft]))


@pytest.mark.skipif(not shutil.which("pdflatex"), reason="needs TeX")
def test_an_overflow_is_reported_plainly_and_nothing_is_dropped():
    doc = source()
    entries = sec(doc, "experience").entries
    entries[0].bullets = [Bullet(id=f"b{i}", text=LONG) for i in range(20)]
    for i in range(1, 7):
        entries.append(entries[0].model_copy(deep=True, update={"id": f"r{i + 1}", "heading": f"Analyst {i}"}))
    built = asyncio.run(compile_pdf(editor.render(doc, editor.tailored_style())))
    assert built.ok
    report = check_rules(doc, doc, pdf=built.pdf)
    assert report["pages"] > 1 and report["ok"]
    assert [w["code"] for w in report["warnings"]] == ["overflow"]


def _with_bullets(*texts):
    doc = source()
    sec(doc, "experience").entries[0].bullets = [Bullet(id=f"b{i}", text=t) for i, t in enumerate(texts)]
    return doc


SAME_OPENING = "Built a reporting pipeline in SQL and Python for the "
THREE = (SAME_OPENING + "finance team, replacing a manual spreadsheet", OTHER, SAME_OPENING + "sales team, replacing a shared weekly export file")


@pytest.mark.skipif(not shutil.which("pdflatex"), reason="needs TeX")
def test_two_bullets_opening_the_same_way_read_in_order():
    """Each bullet was found from the start of the text, so a later bullet sharing the first one's opening landed back on it and read as out of order."""
    doc = _with_bullets(*THREE)
    built = asyncio.run(compile_pdf(editor.render(doc, editor.tailored_style())))
    assert built.ok
    items = {i["label"]: i for i in parse_check(built.pdf, doc)["items"]}
    assert items["Bullets read top to bottom"]["ok"], items["Bullets read top to bottom"]


@pytest.mark.skipif(not shutil.which("pdflatex"), reason="needs TeX")
def test_bullets_really_out_of_order_still_fail():
    doc = _with_bullets(*THREE)
    built = asyncio.run(compile_pdf(editor.render(doc, editor.tailored_style())))
    assert built.ok
    shuffled = _with_bullets(THREE[1], THREE[0], THREE[2])
    items = {i["label"]: i for i in parse_check(built.pdf, shuffled)["items"]}
    assert not items["Bullets read top to bottom"]["ok"]


@pytest.mark.skipif(not shutil.which("pdflatex"), reason="needs TeX")
def test_a_bullet_missing_from_the_pdf_is_named_as_missing():
    built = asyncio.run(compile_pdf(editor.render(_with_bullets(THREE[0]), editor.tailored_style())))
    assert built.ok
    items = {i["label"]: i for i in parse_check(built.pdf, _with_bullets(THREE[0], OTHER))["items"]}
    assert not items["Bullets read top to bottom"]["ok"]
    assert "1 bullet(s) not found" in items["Bullets read top to bottom"]["detail"]


def test_the_picker_shows_aggressive_as_coming_soon_until_it_registers():
    rows = {r["id"]: r for r in modes.listing()}
    assert rows["normal"]["available"]
    if modes.get("aggressive") is None:
        assert rows["aggressive"]["available"] is False


def test_a_registered_mode_is_available():
    async def run(job, source_, profile, ctx):  # pragma: no cover
        raise NotImplementedError

    modes.register(modes.ModeSpec("test-mode", "Test", "A test mode.", run))
    try:
        assert {r["id"]: r for r in modes.listing()}["test-mode"]["available"]
        assert modes.get("test-mode").run is run
        assert modes.is_tailored_policy(modes.policy_for("test-mode"))
    finally:
        modes._REGISTRY.pop("test-mode")
