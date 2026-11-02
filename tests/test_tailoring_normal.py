"""Normal mode and the per-job choice, on synthetic data: what the model proposes goes through code, and only the approved file is sent."""

import asyncio
import hashlib
import json
import shutil

import pytest

from jobpilot.db import Job, Resume
from jobpilot.features.resumes import ats, choice, editor, modes, normal, pipeline
from jobpilot.features.resumes.compiler import compile_pdf
from jobpilot.features.resumes.editor import Bullet, Entry, ResumeDoc, Section
from jobpilot.features.resumes.master import Imported
from jobpilot.features.resumes.rules import check_rules, doc_plain, skill_items
from jobpilot.features.resumes.tables import RsArtifact, RsTailorChoice

needs_tex = pytest.mark.skipif(not shutil.which("pdflatex"), reason="needs TeX")
B1 = "Built a reporting pipeline in SQL and Python that replaced a manual spreadsheet process for the finance team"
B2 = "Reviewed monthly close variances with the controller and documented every adjustment for the external audit"
POSTING = """About us
We have a winning culture and a global family.
What you'll do
Build data pipelines in Python and SQL. Own dashboards for finance stakeholders.
Requirements
- Experience with Python, SQL and Tableau
- Experience with Kubernetes
Preferred
- GAAP knowledge is a plus
Benefits
- Dental and a free snack cupboard
"""


def source_doc():
    return ResumeDoc(
        name="Alex Example",
        contact=["alex@example.test", "+1 202 555 0100"],
        sections=[
            Section(id="experience", title="Experience", kind="experience", entries=[
                Entry(id="r1", heading="Data Analyst", subheading="ExampleSoft", dates="06/2023 - Present", location="Sampleville, MD",
                      bullets=[Bullet(id="b1", text=B1), Bullet(id="b2", text=B2)]),
            ]),
            Section(id="education", title="Education", kind="education", entries=[
                Entry(id="e1", heading="Example University", subheading="B.S., Statistics", dates="08/2019 - 05/2023")]),
            Section(id="skills", title="Skills", kind="skills", text="Languages: Python, SQL\nTools: Excel"),
        ],
    )


PROFILE = {
    "name": "Alex Example",
    "email": "alex@example.test",
    "phone": "+1 202 555 0100",
    "skills": ["Python", "SQL", "Excel", "Tableau", "GAAP"],
    "work": [{"company": "ExampleSoft", "title": "Data Analyst", "start": "2023-06", "current": True, "description": "- " + B1 + "\n- " + B2}],
    "education": [{"school": "Example University", "degree": "B.S.", "field_of_study": "Statistics", "start": "2019-08", "end": "2023-05"}],
}


def stub(plan=None, repair=None, summary_retry="Data Analyst who builds SQL and Python reporting for finance teams."):
    calls = []

    async def model(output_type, system, user):
        calls.append((output_type.__name__, system, user))
        if output_type is Imported:
            d = source_doc()
            return Imported(name=d.name, contact=d.contact, sections=[s.model_dump() for s in d.sections])
        if output_type is normal.NormalPlan:
            return plan or normal.NormalPlan()
        if output_type is normal.BulletRepair:
            return repair or normal.BulletRepair()
        if output_type is normal.SummaryRepair:
            return normal.SummaryRepair(summary=summary_retry)
        return output_type()

    model.calls = calls
    return model


def ctx_for(tmp_path, doc=None, profile=PROFILE, model=None):
    from jobpilot.features.resumes.evidence import build
    from jobpilot.features.resumes.select import supported_terms

    doc = doc or source_doc()
    inv = build(profile, doc_plain(doc))
    terms = ats.terms(POSTING, "Data Engineer", supported_terms(inv))
    evidence_text = " ".join([doc_plain(doc), inv.vocabulary])
    source = modes.SourceResume(id="res", sha="sha", text=doc_plain(doc), doc=doc, style=editor.Style())
    ctx = modes.ModeContext(db=None, config=None, model=model or stub(), compile_fn=compile_pdf, work_dir=tmp_path, evidence=inv, ats_terms=terms,
                            ats_source=ats.score(terms, doc_plain(doc), evidence_text))
    return source, ctx


JOB = {"title": "Data Engineer", "company": "Northwind", "description": POSTING, "url": "http://jobs.example.test/1", "location": "Remote"}


# ---------- The keyword check, in any field ----------
def test_culture_prose_and_benefits_never_make_a_keyword():
    found = {t["term"].lower() for t in ats.terms(POSTING, "Data Engineer")}
    assert {"python", "sql", "tableau", "kubernetes", "gaap"} <= found
    assert not {w for w in found if any(x in w for x in ("culture", "family", "snack", "dental"))}


def test_a_term_keeps_the_postings_spelling_and_matches_its_other_spellings():
    rows = ats.terms("Requirements\nExperience with CI/CD, retrieval-augmented generation and Kubernetes.", "")
    by = {r["term"]: r for r in rows}
    assert "CI/CD" in by
    assert ats.matches(by["retrieval-augmented generation"]["pattern"], "Built a RAG service")
    assert ats.matches(by["Kubernetes"]["pattern"], "ran on k8s")
    assert not ats.matches(by["CI/CD"]["pattern"], "CI CD sounds like two words")


@pytest.mark.parametrize(
    "posting,expected",
    [
        ("Responsibilities\nProvide patient care on a telemetry unit. Medication administration using Epic EMR.\nQualifications\nActive RN license. BLS and ACLS certification. Knowledge of infection control and HIPAA.", {"patient care", "epic emr", "hipaa", "infection control"}),
        ("Senior Accountant. Requirements: experience with month-end close, GAAP, account reconciliations and financial reporting; proficiency in NetSuite and Microsoft Excel. Responsibilities: prepare journal entries and support the annual audit.", {"gaap", "month-end close", "financial reporting", "netsuite", "microsoft excel"}),
        ("Welder needed. You will perform MIG and TIG welding on structural steel and read blueprints. Must have AWS D1.1 certification. Forklift operation preferred.", {"welding", "aws d1.1"}),
        ("Teacher. Responsibilities: lesson planning and classroom management for grade 5. Requirements: experience with curriculum development and differentiated instruction.", {"lesson planning", "curriculum development"}),
    ],
)
def test_it_finds_keywords_outside_tech(posting, expected):
    found = {t["term"].lower() for t in ats.terms(posting, "")}
    assert expected <= found, found
    assert not any(" and " in t for t in found), found


def test_a_sentence_saying_preferred_doesnt_make_the_whole_paragraph_optional():
    rows = {t["term"].lower(): t["tier"] for t in ats.terms("You will perform welding on steel. Must have AWS D1.1 certification. Forklift operation preferred.", "")}
    assert rows["welding"] == "duties"


def test_a_posting_flattened_to_one_line_still_splits_at_its_headings():
    flat = (
        "About Us We are a winning culture and a global family of curious people who love snacks and offsites every quarter of the year. "
        "Responsibilities Build dashboards for the finance team and own the monthly reporting calendar with care and attention to detail. "
        "Requirements Experience with Python, SQL and Tableau. Knowledge of GAAP. "
        "Nice to Have Experience with dbt. Benefits Dental cover and a free snack cupboard for everyone who works in the office."
    )
    parts = ats.blocks(flat)
    assert "Python" in parts["required"] and "dashboards" in parts["duties"] and "dbt" in parts["preferred"]
    assert "winning culture" in parts["other"] and "Dental" in parts["other"]
    found = {t["term"].lower(): t["tier"] for t in ats.terms(flat, "")}
    assert found["python"] == "required" and found["dbt"] == "preferred"
    assert not any("snack" in t or "culture" in t for t in found)


def test_a_short_flattened_posting_splits_too_and_a_sentence_isnt_a_name():
    flat = "What you'll do Build reporting in Python. Requirements Experience with Python, SQL and Tableau Knowledge of GAAP and financial reporting Preferred Experience with dbt is a plus About us We have a winning culture"
    found = {t["term"]: t["tier"] for t in ats.terms(flat, "")}
    assert found["GAAP"] == "required" and found["Tableau"] == "required" and found["dbt"] == "preferred"
    assert "Tableau Knowledge of GAAP" not in found


# The shape of a real posting that read as 30 "required" terms such as employment, protected, follow and its own employer, with Python and SQL pushed off the list.
RUN_ON = """Who We Are:
Northwind is the partner for supply chain automation.
What You'll Do:
Build applications and workflows using APIs, data and code.
You May Be a Great Fit If
You can write code in a language such as Python, JavaScript, or TypeScript and want to continue developing your engineering skills.
You have some familiarity with APIs, SQL or data structures, Git, testing, and debugging.
Experience
A typical candidate has one to three years of relevant experience, from full-time employment or internships.
Helpful, but Not Required
Familiarity with vector databases, embedding models, or evaluation tools.
What Success Looks Like
Earn the trust of teammates through follow-through and curiosity, building, leading and follow-up at Northwind and Northwind teams.
Why You'll Love Working at Northwind:
Discretionary Time Off for all employees. Industry leading health, vision, and dental benefits.
Learn more at northwind.example and follow Northwind on LinkedIn.
All qualified applicants will receive consideration for employment without regard to race, color, religion, sex, sexual orientation, gender identity, national origin, age, disability or protected veteran status, or any other legally protected basis.
"""


def test_a_run_on_posting_keeps_its_real_terms_and_drops_the_legal_text_and_the_employer():
    rows = {t["term"].lower(): t for t in ats.terms(RUN_ON, "Builder", company="Northwind")}
    assert {"python", "javascript", "typescript", "sql", "git", "debugging", "data structures"} <= set(rows)
    assert rows["python"]["tier"] == "required" and rows["vector databases"]["tier"] == "preferred"
    assert not set(rows) & {"northwind", "employment", "protected", "follow", "linkedin", "dental", "building", "leading"}
    assert not [t for t in rows if "your" in t or "want" in t]


def test_a_word_only_repetition_made_a_term_is_marked_common():
    rows = {t["term"]: t for t in ats.terms("Requirements\nExperience with Python. Great reasoning, clear reasoning, careful reasoning.", "")}
    assert rows["reasoning"]["common"] is True
    assert not rows["Python"].get("common")


def test_the_same_word_in_the_plural_is_one_term():
    rows = [t["term"].lower() for t in ats.terms("Requirements\nExperience with evaluation.\nDuties\nBuild evaluations and test them.", "")]
    assert rows.count("evaluation") == 1 and "evaluations" not in rows


def test_the_score_is_the_weighted_share_and_says_what_a_missing_term_is():
    rows = [
        {"term": "Python", "pattern": ats.pattern_for("Python"), "tier": "required", "weight": 3},
        {"term": "Tableau", "pattern": ats.pattern_for("Tableau"), "tier": "duties", "weight": 2},
        {"term": "Kubernetes", "pattern": ats.pattern_for("Kubernetes"), "tier": "preferred", "weight": 1},
    ]
    result = ats.score(rows, "Python scripts", "Tableau dashboards")
    assert result["score"] == 50
    assert {k["term"]: k["status"] for k in result["keywords"]} == {"Python": "matched", "Tableau": "available", "Kubernetes": "needed"}
    assert result["tiers"]["required"] == {"found": 1, "total": 1}
    assert "estimates keyword match" in result["disclaimer"]


def test_an_empty_posting_has_no_score_rather_than_a_false_one():
    assert ats.score(ats.terms("", ""), "anything")["score"] is None


# ---------- Normal mode's gates, each with the proposal that must not get through ----------
def run_plan(tmp_path, plan, **kw):
    source, ctx = ctx_for(tmp_path, model=stub(plan, **kw))
    return source, asyncio.run(normal.run(JOB, source, PROFILE, ctx)), ctx


def plan_of(b1=None, summary="Data Analyst who builds SQL and Python reporting for finance teams.", skills=()):
    return normal.NormalPlan(summary=summary, bullets=[normal.PlanBullet(id="b1", text=b1)] if b1 else [], skills_to_add=list(skills))


@needs_tex
def test_normal_adds_a_summary_and_real_skills_and_passes_every_rule(tmp_path):
    good = "Built a reporting pipeline in Python and SQL that replaced a manual spreadsheet process for finance teams"
    assert len(good) <= len(B1)
    source, result, ctx = run_plan(tmp_path, plan_of(good, skills=["Tableau"]))
    assert result.doc.sections[0].title == "Summary"
    assert result.report["changed_bullets"] == 1
    items = skill_items(next(s for s in result.doc.sections if s.kind == "skills").text)
    assert {"Python", "SQL", "Excel", "Tableau", "GAAP"} <= set(items)
    assert "Kubernetes" not in doc_plain(result.doc)
    pdf = result.pdf_path.read_bytes()
    report = check_rules(result.doc, source.doc, pdf=pdf, allowed_skills=result.allowed_skills, vocabulary=ctx.evidence.vocabulary)
    assert report["ok"], report
    now = ats.score(ctx.ats_terms, doc_plain(result.doc), ctx.evidence.vocabulary)
    assert now["score"] > ctx.ats_source["score"]


@needs_tex
@pytest.mark.parametrize(
    "attempt",
    [
        B1 + " and more words to run long",  # longer than the source
        "Built a pipeline",  # more than 10% shorter
        B1[:-12] + " on Kubernetes",  # a term nothing in their history supports
        B1[:-8] + " by 40%",  # a number the bullet doesn't state
        "Managed a global team of analysts across several product lines and regions for the head of finance today",  # says something else
    ],
)
def test_a_bad_bullet_keeps_the_sources_words(tmp_path, attempt):
    _, result, _ = run_plan(tmp_path, plan_of(attempt))
    bullet = next(b for s in result.doc.sections for e in s.entries for b in e.bullets if b.id == "b1")
    assert bullet.text == B1
    assert result.report["changed_bullets"] == 0


@needs_tex
def test_a_refused_bullet_gets_one_repair_call_and_is_held_to_the_same_gates(tmp_path):
    fixed = "Built a reporting pipeline in Python and SQL that replaced a manual spreadsheet process for finance teams"
    source, ctx = ctx_for(tmp_path, model=stub(plan_of(B1 + " and more"), repair=normal.BulletRepair(bullets=[normal.PlanBullet(id="b1", text=fixed)])))
    result = asyncio.run(normal.run(JOB, source, PROFILE, ctx))
    assert [c[0] for c in ctx.model.calls] == ["NormalPlan", "BulletRepair"]
    assert next(b for s in result.doc.sections for e in s.entries for b in e.bullets if b.id == "b1").text == fixed


@needs_tex
@pytest.mark.parametrize(
    "summary",
    ["Data Engineer with 9 years of Kubernetes.", "Data Engineer who builds pipelines.", "I am a passionate analyst at Northwind.", "Led Northwind reporting."],
)
def test_a_summary_that_overclaims_never_reaches_the_resume(tmp_path, summary):
    _, result, _ = run_plan(tmp_path, plan_of(summary=summary), summary_retry="Still a Data Engineer at Northwind with 12 years.")
    text = next(s for s in result.doc.sections if s.title == "Summary").text
    assert "Data Analyst" in text and "Data Engineer" not in text and "Northwind" not in text
    assert "Kubernetes" not in text and "9" not in text and "12" not in text
    assert result.report["summary_origin"] == "plain"


@needs_tex
def test_a_model_skill_nobody_can_back_up_is_ignored(tmp_path):
    _, result, _ = run_plan(tmp_path, plan_of(skills=["Kubernetes", "Rust", "a phrase about stakeholder alignment"]))
    items = skill_items(next(s for s in result.doc.sections if s.kind == "skills").text)
    assert not {"Kubernetes", "Rust"} & set(items)


def test_the_prompt_carries_the_lists_and_never_the_culture_prose(tmp_path):
    source, ctx = ctx_for(tmp_path)
    data = json.loads(normal.plan_prompt(JOB, source, ctx.ats_source))
    assert "Kubernetes" in data["do_not_use"]
    assert "Tableau" in data["summary_terms"]
    assert "winning culture" not in json.dumps(data) and "snack" not in json.dumps(data)
    row = next(b for b in data["bullets"] if b["id"] == "b1")
    assert row["chars"] == row["max_chars"] == len(B1) and row["min_chars"] == int(len(B1) * 0.9 + 0.999)
    assert row["min_chars"] <= row["aim_chars"] <= row["max_chars"]
    assert "Python" in row["lead_with"] and "Tableau" not in row["lead_with"] + row["say_as"]
    assert "@" not in normal.plan_prompt(JOB, source, ctx.ats_source)  # Contact details aren't the model's to see.


# ---------- What makes an edit land, and what stops a false one ----------
NURSE = "Administered medications and documented every dose in Epic for up to 6 patients on a surgical unit"


def one_bullet(text):
    return ResumeDoc(name="Sam Sample", contact=["sam@example.test"], sections=[
        Section(id="experience", title="Experience", kind="experience", entries=[Entry(id="r1", heading="Registered Nurse", subheading="Sample Hospital", dates="07/2020 - Present", bullets=[Bullet(id="b1", text=text)])]),
    ])


def keywords_for(posting, text):
    return ats.score(ats.terms(posting, ""), text, text)["keywords"]


@pytest.mark.parametrize(
    "term,text,says",
    [
        ("medication administration", NURSE, True),
        ("patient care", "Cared for up to six patients per shift", True),
        ("financial reporting", "Built a reporting pipeline in SQL for the finance team", True),
        ("documentation", "Documented every dose", True),
        ("Snowflake", "snowflake", False),  # A name is stated or it isn't.
        ("Excel", "Excelled at the close", False),
        ("Spark", "Sparked a redesign", False),
        ("Kubernetes", NURSE, False),
        ("infection control", NURSE, False),
        ("data engineering", "Cleaned survey data in pandas, wrote a report on response bias, presented it to the board, then answered their many questions and later joined the engineering team", False),
    ],
)
def test_a_term_is_described_only_by_the_lines_own_words(term, text, says):
    assert normal.described(term, text) is says


def test_each_line_is_told_what_it_can_honestly_gain_and_a_line_with_nothing_isnt_offered():
    posting = "Requirements\nExperience with medication administration, patient care, Epic and Kubernetes."
    doc = one_bullet(NURSE)
    doc.sections[0].entries[0].bullets.append(Bullet(id="b2", text="Trained at the front desk during a staff shortage last winter"))
    rows, rest = normal.rewrite_rows(doc, keywords_for(posting, NURSE))
    assert [r["id"] for r in rows] == ["b1"] and rest == ["Trained at the front desk during a staff shortage last winter"]
    assert rows[0]["say_as"] == ["medication administration", "patient care"] and rows[0]["lead_with"] == ["Epic"]
    assert "Kubernetes" not in json.dumps(rows)


def test_a_term_is_offered_on_two_lines_at_most_and_the_reply_stays_under_its_cap():
    text = "Administered medications and documented every dose in the chart on a surgical unit"
    doc = one_bullet(text)
    doc.sections[0].entries[0].bullets += [Bullet(id=f"b{i}", text=text) for i in range(2, 6)]
    rows, _ = normal.rewrite_rows(doc, keywords_for("Requirements\nExperience with medication administration.", ""))
    assert sum("medication administration" in r["say_as"] for r in rows) == 2
    long = "Administered medications and documented every dose in the chart on a surgical unit. " * 12
    doc = one_bullet(long.strip())
    doc.sections[0].entries[0].bullets += [Bullet(id=f"b{i}", text=long.strip() + " again") for i in range(2, 12)]
    rows, rest = normal.rewrite_rows(doc, keywords_for("Requirements\nExperience with documentation and medication administration.", ""))
    assert sum(len(r["text"]) for r in rows) <= normal.MAX_REPLY_CHARS and rest


def test_a_described_term_lands_and_is_what_raises_the_score():
    posting = "Requirements\nExperience with medication administration and Kubernetes."
    good = "Performed medication administration and documented every dose in Epic for up to 6 patients on surgical unit"
    doc = one_bullet(NURSE + " today")
    keywords = keywords_for(posting, doc_plain(doc))
    assert next(k for k in keywords if k["term"] == "medication administration")["status"] == "needed"
    changes, refused = normal.apply_bullets(doc, [normal.PlanBullet(id="b1", text=good)], keywords)
    assert not refused and changes
    assert ats.score(ats.terms(posting, ""), doc_plain(doc), "")["score"] > ats.score(ats.terms(posting, ""), NURSE, "")["score"]


@pytest.mark.parametrize(
    "attempt,why",
    [
        ("Administered medications and documented every dose in Epic with Kubernetes for 6 patients on a unit", "claims Kubernetes"),  # Nothing supports it.
        ("Administered medications and documented every dose in Epic and Tableau for up to 6 patients on a unit", "Tableau"),  # True elsewhere, not on this line.
        ("Administered medications and documented every dose in Epic for up to 9 patients on a surgical unit", "unsupported number"),
        ("Administered medications and documented every dose in the system for up to 6 patients on a surgical unit", "drops Epic"),
        ("Administered medications and documented every dose in Epic for several patients on a surgical floor", "drops 6"),
        ("Cleared the backlog of old invoices and managed the budget in Epic for up to 6 people at the front desk", "loses too much"),
    ],
)
def test_a_rewrite_that_adds_or_loses_a_fact_is_refused_for_that_reason(attempt, why):
    doc = one_bullet(NURSE)
    keywords = ats.score(ats.terms("Requirements\nExperience with Kubernetes and Tableau.", ""), NURSE, NURSE + " Tableau")["keywords"]
    changes, refused = normal.apply_bullets(doc, [normal.PlanBullet(id="b1", text=attempt)], keywords)
    assert not changes and why in refused[0]["reason"], refused
    assert doc.sections[0].entries[0].bullets[0].text == NURSE


@pytest.mark.parametrize(
    "text,high,want",
    [
        ("Built a tool for the team in order to save time", 44, "Built a tool for the team to save time"),
        ("Built a tool for the finance team and the sales team", 49, "Built a tool for the finance team and sales team"),
        ("Saved about 2 hours a week for the team", 35, "Saved about 2 hours a week for team"),  # "a week" keeps its "a".
        ("Ran A/B tests, charts, and reports.", 34, "Ran A/B tests, charts and reports."),
        ("Ran reports.", 11, "Ran reports"),
    ],
)
def test_a_small_overrun_is_tightened_without_touching_a_fact(text, high, want):
    assert normal.tighten(text, high) == want


def test_far_over_isnt_a_slip_so_its_articles_stay_for_the_repair():
    text = "Built a tool for the team and the other team and the third team and a fourth"
    assert normal.tighten(text, len(text) - 40) == text


def test_the_repair_gets_the_real_numbers_and_the_lines_own_terms():
    long = NURSE + " and more"
    _, refused = normal.apply_bullets(one_bullet(NURSE), [normal.PlanBullet(id="b1", text=long)], [])
    assert f"it has {len(long)} characters" in refused[0]["reason"] and "cut at least 9" in refused[0]["reason"]
    _, short = normal.apply_bullets(one_bullet(NURSE), [normal.PlanBullet(id="b1", text=NURSE[:80])], [])
    assert f"add at least {normal.bullet_window(NURSE)[0] - len(NURSE[:80].strip())}" in short[0]["reason"]
    row = json.loads(normal.repair_prompt(refused, keywords_for("Requirements\nExperience with medication administration and Epic.", NURSE)))["bullets"][0]
    assert row["source"] == NURSE and row["attempt"] == long and row["max_chars"] == len(NURSE)
    assert set(row["may_use"]) == {"medication administration", "Epic"}


def test_us_customers_isnt_first_person_but_we_is():
    line = "Moved 300 US customers to the new billing system and answered every support ticket in the first week"
    good = "Moved 300 US customers to new billing system and answered every support ticket during the first week"
    assert normal.apply_bullets(one_bullet(line), [normal.PlanBullet(id="b1", text=good)], [])[0]
    bad = "Moved 300 US customers to the new billing system and we answered every support ticket in first week"
    assert "first person" in normal.apply_bullets(one_bullet(line), [normal.PlanBullet(id="b1", text=bad)], [])[1][0]["reason"]


@pytest.mark.parametrize(
    "skills,want",
    [
        ("Python, SQL\nLanguages: English", "Python, SQL, Docker\nLanguages: English"),
        ("Languages: Python, SQL\nTools: Excel", "Languages: Python, SQL\nTools: Excel, Docker"),
        ("Languages: English", "Languages: English\nSkills: Docker"),
    ],
)
def test_an_added_skill_joins_a_line_it_belongs_on(skills, want):
    doc = ResumeDoc(name="A", sections=[Section(id="skills", title="Skills", kind="skills", text=skills)])
    assert normal.add_skills(doc, ["Docker"], ["Docker"], "") == ["Docker"]
    assert doc.sections[0].text == want


@needs_tex
def test_a_word_the_posting_only_repeats_is_never_added_as_a_skill(tmp_path):
    posting = "Requirements\nExperience with Python. Great models, clear models, careful models."
    doc = source_doc()
    text = doc_plain(doc) + " models"
    source = modes.SourceResume(id="res", sha="sha", text=text, doc=doc, style=editor.Style())
    from jobpilot.features.resumes.evidence import build

    terms = ats.terms(posting, "")
    assert next(t for t in terms if t["term"] == "models")["common"]
    ctx = modes.ModeContext(db=None, config=None, model=stub(plan_of(skills=["Models"])), compile_fn=compile_pdf, work_dir=tmp_path, evidence=build(PROFILE, text), ats_terms=terms, ats_source=ats.score(terms, doc_plain(doc), text))
    result = asyncio.run(normal.run({**JOB, "description": posting}, source, PROFILE, ctx))
    assert result.report["added_skills"] == []
    assert "models" not in {i.lower() for i in skill_items(next(s for s in result.doc.sections if s.kind == "skills").text)}


@needs_tex
def test_a_reply_with_no_bullets_gets_one_repair_call_and_no_more(tmp_path):
    source, ctx = ctx_for(tmp_path, model=stub(plan_of()))
    result = asyncio.run(normal.run(JOB, source, PROFILE, ctx))
    assert [c[0] for c in ctx.model.calls] == ["NormalPlan", "BulletRepair"]
    assert result.report["offered_bullets"] >= 1 and result.report["changed_bullets"] == 0 and result.report["refused"] == []
    assert "no rewrite came back" in ctx.model.calls[1][2]


@needs_tex
def test_a_fixed_bullet_isnt_reported_as_refused(tmp_path):
    fixed = "Built a reporting pipeline in Python and SQL that replaced a manual spreadsheet process for finance teams"
    source, ctx = ctx_for(tmp_path, model=stub(plan_of(B1 + " and more"), repair=normal.BulletRepair(bullets=[normal.PlanBullet(id="b1", text=fixed)])))
    result = asyncio.run(normal.run(JOB, source, PROFILE, ctx))
    assert result.report["refused"] == [] and result.report["repaired_bullets"] == 1 and result.report["changed_bullets"] == 1


def test_the_prompt_stays_small_and_its_example_counts_are_true():
    assert len(normal.SYSTEM) < 6000
    assert f"Good, {len(normal._GOOD)} characters" in normal.SYSTEM
    low, high = normal.bullet_window(normal._EXAMPLE)
    assert low <= len(normal._GOOD) <= high < len(normal._LONG)
    assert normal.described("financial reporting", normal._EXAMPLE) and normal.described("vendor management", "Paid vendor invoices and managed petty cash")


# ---------- The choice, approval, and the send path ----------
def add_job(service, job_id="j1", description=POSTING):
    with service.db.exclusive() as s:
        s.add(Resume(id="r1", name="resume", filename="r1.pdf", sha256="sha1", roles=[], text=doc_plain(source_doc())))
        s.add(Job(id=job_id, identity=job_id, url="https://jobs.example.test/" + job_id, title="Data Engineer", company="Northwind", status="ready", score=90, description=description, resume_id="r1"))
    service.db.set_setting("profile", PROFILE)


def tailored(service, mode="normal", plan=None):
    choice.choose(service.db, "j1", mode)
    return asyncio.run(choice.run(service.db, service.config, "j1", model=stub(plan or plan_of())))


def test_off_by_default(service):
    add_job(service)
    assert choice.get_choice(service.db, "j1") is None
    with service.db.exclusive() as s:
        assert choice.artifact_for_queue(s, s.get(Job, "j1"), "submit") is None


@pytest.mark.parametrize("status", ["queued", "running", "confirmed", "submission_unknown"])
def test_the_choice_cant_change_once_the_application_has_started(client, service, status):
    add_job(service)
    with service.db.exclusive() as s:
        s.get(Job, "j1").status = status
    answer = client.put("/api/v2/resumes/tailor/job/j1", json={"mode": "normal"})
    assert answer.status_code == 409 and "already started" in answer.json()["detail"]
    assert choice.get_choice(service.db, "j1") is None


def test_queue_says_a_job_waits_on_its_tailored_resume(service):
    add_job(service)
    assert choice.waiting_words(service.db, ["j1"]) == {}
    choice.choose(service.db, "j1", "normal")
    assert "Preparing" in choice.waiting_words(service.db, ["j1"])["j1"]
    with service.db.exclusive() as s:
        s.get(RsTailorChoice, "j1").state = "review"
    assert "review" in choice.waiting_words(service.db, ["j1"])["j1"]
    with service.db.exclusive() as s:
        s.get(RsTailorChoice, "j1").state = "approved"
    assert choice.waiting_words(service.db, ["j1"]) == {}


def test_a_mode_that_isnt_registered_cant_be_chosen(service):
    add_job(service)
    if modes.get("aggressive") is None:
        with pytest.raises(LookupError):
            choice.choose(service.db, "j1", "aggressive")


@needs_tex
def test_with_approval_on_nothing_tailored_leaves_until_this_file_is_approved(service):
    from jobpilot.admission import QueueBlocked

    add_job(service)
    assert tailored(service)["state"] == "review"
    row = choice.get_choice(service.db, "j1")
    with service.db.exclusive() as s:
        with pytest.raises(QueueBlocked) as blocked:
            choice.artifact_for_queue(s, s.get(Job, "j1"), "submit")
    assert blocked.value.code == choice.REVIEW and blocked.value.outcome == "deferred"
    done = choice.approve(service.db, service.config, "j1", row.artifact_id)
    with service.db.exclusive() as s:
        assert choice.artifact_for_queue(s, s.get(Job, "j1"), "submit") == row.artifact_id
        artifact = s.get(RsArtifact, row.artifact_id)
    path = service.config.data_dir / artifact.pdf_path
    assert hashlib.sha256(path.read_bytes()).hexdigest() == done["pdf_sha"]


@needs_tex
def test_with_approval_off_it_goes_straight_through_and_the_rules_still_hold(service):
    add_job(service)
    choice.set_approval(service.db, False)
    assert tailored(service)["state"] == "approved"
    row = choice.get_choice(service.db, "j1")
    assert row.approved_how == "setting_off"
    with service.db.exclusive() as s:
        assert choice.artifact_for_queue(s, s.get(Job, "j1"), "submit") == row.artifact_id


@needs_tex
@pytest.mark.parametrize("approval", [True, False])
def test_an_unconfirmed_draft_in_the_file_stops_the_send_approval_on_or_off(service, approval):
    from jobpilot.admission import QueueBlocked

    add_job(service)
    choice.set_approval(service.db, False)
    tailored(service)
    row = choice.get_choice(service.db, "j1")
    with service.db.exclusive() as s:
        artifact = s.get(RsArtifact, row.artifact_id)
        # The experience heading stands in for a draft's title that somehow reached the file.
        artifact.validation = {**artifact.validation, "drafts": [{"id": "d", "title": "Data Analyst", "bullets": [], "confirmed": False}]}
    choice.set_approval(service.db, approval)
    with service.db.exclusive() as s:
        with pytest.raises(QueueBlocked) as blocked:
            choice.artifact_for_queue(s, s.get(Job, "j1"), "submit")
    assert blocked.value.code == choice.UNCONFIRMED
    with pytest.raises(ValueError, match="confirmed"):
        choice.approve(service.db, service.config, "j1", row.artifact_id)


@needs_tex
def test_a_run_cant_take_a_pdf_whose_approval_has_gone(client, service):
    """Queued while approved, then edited: the run stops where it picks up the file, instead of sending what's no longer approved."""
    add_job(service)
    tailored(service)
    row = choice.get_choice(service.db, "j1")
    choice.approve(service.db, service.config, "j1", row.artifact_id)
    packet = {"resume_artifact_id": row.artifact_id}
    assert choice.run_problem(service.db, packet) is None
    assert choice.run_problem(service.db, {}) is None  # Plain Apply has nothing to ask.
    view = client.get("/api/v2/resumes/tailor/job/j1").json()
    doc = view["doc"]
    target = next(b for s in doc["sections"] for e in s["entries"] for b in e["bullets"] if b["id"] == "b1")
    target["text"] = "Built a reporting pipeline in Python and SQL that replaced a manual spreadsheet process for finance teams"
    assert client.post("/api/v2/resumes/tailor/job/j1/save", json={"doc": doc}).status_code == 200
    assert "review" in choice.run_problem(service.db, packet)
    # The new version itself isn't approved either, until it is.
    fresh = choice.get_choice(service.db, "j1")
    assert "review" in choice.run_problem(service.db, {"resume_artifact_id": fresh.artifact_id})
    choice.approve(service.db, service.config, "j1", fresh.artifact_id)
    assert choice.run_problem(service.db, {"resume_artifact_id": fresh.artifact_id}) is None
    assert "review" in choice.run_problem(service.db, packet)  # Approval is for that exact file, so the old one stays out.


@needs_tex
def test_a_changed_file_isnt_the_approved_file(service):
    add_job(service)
    tailored(service)
    row = choice.get_choice(service.db, "j1")
    choice.approve(service.db, service.config, "j1", row.artifact_id)
    with service.db.exclusive() as s:
        s.get(RsTailorChoice, "j1").approved_sha = "0" * 64
    assert "review" in choice.run_problem(service.db, {"resume_artifact_id": row.artifact_id})


@needs_tex
def test_every_registered_mode_goes_through_the_same_approval(client, service, tmp_path):
    """A mode that isn't Normal: same screen, same checks, same switch. Registered only for this test."""

    async def run(job, source, profile, ctx):
        built = await compile_pdf(editor.render(source.doc, editor.tailored_style(source.style)))
        path = ctx.work_dir / "resume.pdf"
        path.write_bytes(built.pdf)
        return modes.ModeResult(latex="", pdf_path=path, drafts=[modes.Draft(id="d1", title="An Idea Not On The Page", bullets=["Something this resume doesn't say at all, written down as an idea only"])], doc=source.doc, style=editor.tailored_style(source.style))

    modes.register(modes.ModeSpec("test-mode", "Test", "A test mode.", run))
    try:
        add_job(service)
        answer = client.put("/api/v2/resumes/tailor/job/j1", json={"mode": "test-mode"})
        assert answer.status_code == 200
        assert asyncio.run(choice.run(service.db, service.config, "j1", model=stub()))["state"] == "review"
        view = client.get("/api/v2/resumes/tailor/job/j1").json()
        assert view["choice"]["mode"] == "test-mode" and view["ats"]["score"] is not None and view["rules"]["ok"]
        assert [d["title"] for d in view["drafts"]] == ["An Idea Not On The Page"]
        artifact = view["artifact"]["id"]
        assert "review" in choice.run_problem(service.db, {"resume_artifact_id": artifact})
        assert client.post("/api/v2/resumes/tailor/job/j1/approve", json={"artifact_id": artifact, "apply": False}).status_code == 200
        assert choice.run_problem(service.db, {"resume_artifact_id": artifact}) is None
        # With the switch off, the next version of this mode goes straight through, as any mode's does.
        choice.set_approval(service.db, False)
        client.post("/api/v2/resumes/tailor/job/j1/retry")
        assert asyncio.run(choice.run(service.db, service.config, "j1", model=stub(), fresh=True))["state"] == "approved"
    finally:
        modes._REGISTRY.pop("test-mode")


@needs_tex
def test_a_failed_rules_check_cant_be_approved_or_sent(service):
    from jobpilot.admission import QueueBlocked

    add_job(service)
    tailored(service)
    row = choice.get_choice(service.db, "j1")
    with service.db.exclusive() as s:
        artifact = s.get(RsArtifact, row.artifact_id)
        artifact.validation = {**artifact.validation, "rules": {"ok": False, "problems": [{"message": "A bullet is too long"}]}}
    with pytest.raises(ValueError, match="too long"):
        choice.approve(service.db, service.config, "j1", row.artifact_id)
    with service.db.exclusive() as s:
        with pytest.raises(QueueBlocked) as blocked:
            choice.artifact_for_queue(s, s.get(Job, "j1"), "submit")
    assert blocked.value.code == choice.BROKEN


@needs_tex
def test_an_edit_after_approval_needs_a_new_approval(client, service):
    add_job(service)
    tailored(service)
    row = choice.get_choice(service.db, "j1")
    assert client.post("/api/v2/resumes/tailor/job/j1/approve", json={"artifact_id": row.artifact_id, "apply": False}).status_code == 200
    view = client.get("/api/v2/resumes/tailor/job/j1").json()
    assert view["choice"]["state"] == "approved"
    doc = view["doc"]
    target = next(b for s in doc["sections"] for e in s["entries"] for b in e["bullets"] if b["id"] == "b1")
    target["text"] = "Built a reporting pipeline in Python and SQL that replaced a manual spreadsheet process for finance teams"
    saved = client.post("/api/v2/resumes/tailor/job/j1/save", json={"doc": doc})
    assert saved.status_code == 200, saved.text
    after = saved.json()
    assert after["choice"]["state"] == "review" and after["choice"]["approved_artifact_id"] is None
    assert after["artifact"]["id"] != row.artifact_id


@needs_tex
def test_saving_refuses_what_the_rules_refuse_and_says_which_bullet(client, service):
    add_job(service)
    tailored(service)
    view = client.get("/api/v2/resumes/tailor/job/j1").json()
    doc = view["doc"]
    next(s for s in doc["sections"] if s["kind"] == "experience")["entries"][0]["bullets"][0]["text"] = B1 + " and then some"
    answer = client.post("/api/v2/resumes/tailor/job/j1/save", json={"doc": doc})
    assert answer.status_code == 422
    assert answer.json()["problems"][0]["code"] == "bullet_too_long"
    assert answer.json()["problems"][0]["where"] == "b1"


@needs_tex
def test_the_server_score_is_the_number_the_screen_starts_from(client, service):
    add_job(service)
    tailored(service)
    view = client.get("/api/v2/resumes/tailor/job/j1").json()
    again = client.post("/api/v2/resumes/ats/score", json={"job_id": "j1", "doc": view["doc"]}).json()
    assert again["score"] == view["ats"]["score"]
    assert view["ats_source"] < view["ats"]["score"]
    statuses = {k["term"]: k["status"] for k in view["ats"]["keywords"]}
    assert statuses["Kubernetes"] == "needed"


def test_the_pickers_contract_for_batch_rows(client, service):
    add_job(service)
    answer = client.get("/api/v2/resumes/tailor/choices?job_ids=j1").json()
    assert answer["choices"] == {}
    assert {m["id"] for m in answer["modes"]} >= {"normal", "aggressive"}
    assert client.get("/api/v2/resumes/tailor/settings").json() == {"approval": True}
    assert client.put("/api/v2/resumes/tailor/settings", json={"approval": False}).json() == {"approval": False}
    assert client.get("/api/v2/resumes/tailor/settings").json() == {"approval": False}


@needs_tex
def test_a_new_user_with_no_master_still_gets_a_tailored_resume(service):
    add_job(service)
    result = asyncio.run(pipeline.prepare_resume(service.db, service.config, "j1", mode="normal", model=stub(plan_of())))
    assert result.status == "ready", result.error
    assert result.artifact.validation["origin"] in {"import", "profile"}
    assert result.artifact.validation["rules"]["ok"]


@needs_tex
def test_choosing_plain_apply_again_goes_back_to_the_uploaded_resume(service):
    add_job(service)
    tailored(service)
    assert choice.choose(service.db, "j1", "off") is None
    with service.db.session() as s:
        assert s.get(RsTailorChoice, "j1") is None
    with service.db.exclusive() as s:
        assert choice.artifact_for_queue(s, s.get(Job, "j1"), "submit") is None
