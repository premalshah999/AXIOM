"""Synthetic, compiled Aggressive path. No dotenv, production state or network."""

import asyncio
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from jobpilot.db import Job, Resume
from jobpilot.features.resumes import ats, choice, editor, evidence, modes
from jobpilot.features.resumes.aggressive import quality, store
from jobpilot.features.resumes.aggressive.pipeline import run
from jobpilot.features.resumes.aggressive.schema import (
    Brief,
    Category,
    Choice,
    Confirmation,
    Critique,
    Plan,
    Proposal,
    Revision,
    Rewrite,
)
from jobpilot.features.resumes.compiler import compile_pdf
from jobpilot.features.resumes.editor import Bullet, Entry, ResumeDoc, Section
from jobpilot.features.resumes.master import Imported
from jobpilot.features.resumes.rules import check_rules, doc_plain, skill_items
from jobpilot.features.resumes.tables import RsArtifact
from jobpilot.features.resumes.validate import parse_check

needs_tex = pytest.mark.skipif(not shutil.which("pdflatex"), reason="TeX required")
POSTING = """About us
We have a winning culture and a global family.
Responsibilities
Build reporting tools in Python, SQL and Excel using public data. Document review decisions.
Requirements
Experience with Python, SQL, Excel, testing and documentation.
Experience with Kubernetes.
Benefits
Dental coverage and snacks.
"""
JOB = {
    "title": "Reporting Analyst",
    "company": "ExampleWorks",
    "url": "https://jobs.example.test/aggressive",
    "description": POSTING,
}
REAL1 = "Built a Python report using SQL queries and public records, then documented query assumptions for repeatable local reviews"
REAL2 = "Chose local SQL queries over a spreadsheet copy to preserve source records and compare report changes before each release"


def source_doc():
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
                        id="role",
                        heading="Data Analyst",
                        subheading="Sample Company",
                        dates="06/2023 - Present",
                        bullets=[
                            Bullet(
                                id="work1",
                                text="Reviewed monthly reports with the finance lead and documented every adjustment for the annual audit",
                            ),
                            Bullet(
                                id="work2",
                                text="Used Python and SQL to reconcile public records and kept Excel notes for repeatable report reviews",
                            ),
                        ],
                    )
                ],
            ),
            Section(
                id="education",
                title="Education",
                kind="education",
                entries=[
                    Entry(
                        id="school",
                        heading="Example College",
                        subheading="B.S., Statistics",
                        dates="08/2019 - 05/2023",
                    )
                ],
            ),
            Section(
                id="projects",
                title="Projects",
                kind="projects",
                entries=[
                    Entry(
                        id="original",
                        heading="Public Report Review",
                        subheading="Personal project",
                        bullets=[Bullet(id="p1", text=REAL1), Bullet(id="p2", text=REAL2)],
                    )
                ],
            ),
            Section(
                id="skills",
                title="Skills",
                kind="skills",
                text="Tools: Python, SQL, Excel\nPractices: Documentation",
            ),
        ],
    )


PROFILE = {
    "name": "Alex Example",
    "email": "alex@example.test",
    "phone": "+1 202 555 0100",
    "skills": ["Python", "SQL", "Excel", "Documentation"],
    "work": [
        {
            "company": "Sample Company",
            "title": "Data Analyst",
            "description": "Used Python and SQL with Excel for documentation",
            "start": "2023-06",
            "current": True,
        }
    ],
    "education": [
        {
            "school": "Example College",
            "degree": "B.S.",
            "field_of_study": "Statistics",
            "start": "2019-08",
            "end": "2023-05",
        }
    ],
}


def proposals():
    return [
        Proposal(
            title="Public Record Explorer",
            archetype="shipped product",
            proof_point=0,
            descriptor="Personal project",
            data_source="NYC Open Data",
            tools=["Python"],
            bullets=[
                "Build a Python report browser using NYC Open Data so a reader can inspect source records alongside the summary.",
                "Compare local query results with the downloaded records before adding filters, keeping missing fields visible to the reader.",
            ],
            build_plan=[
                "Download public records and define the report fields",
                "Build a local browser and document review checks",
            ],
        ),
        Proposal(
            title="Filing Review Notebook",
            archetype="messy data analysis",
            proof_point=1,
            descriptor="Research",
            data_source="SEC EDGAR",
            tools=["SQL"],
            bullets=[
                "Compare SEC EDGAR public filings with SQL to show which reporting categories change across source documents.",
                "Keep inconsistent filing labels in a review table rather than silently merging them, and document the mapping decisions.",
            ],
            build_plan=[
                "Download a small public filing sample",
                "Inspect mismatched labels and document the query decisions",
            ],
        ),
        Proposal(
            title="Public Feed Review Checklist",
            archetype="internal tool",
            proof_point=2,
            descriptor="Internal tool",
            data_source="USGS Earthquake Catalog",
            tools=["Excel"],
            bullets=[
                "Build an Excel review checklist for USGS Earthquake Catalog records so a reader can identify missing or inconsistent fields.",
                "Keep the source export beside the checklist and document how a stale feed changes the review rather than claiming a live integration.",
            ],
            build_plan=[
                "Export a small public feed sample",
                "Define the review checks and write instructions for a solo walkthrough",
            ],
        ),
    ]


def plan(**kwargs):
    return Plan(proposals=proposals(), **kwargs)


def stub(initial=None, repair=None, revision=None, fixes=None, metered=False):
    calls = []

    async def model(kind, system, user):
        calls.append((kind.__name__, system, json.loads(user)))
        if metered:
            from jobpilot.models import COST_METER

            COST_METER.get().append({"usd": 0.00123, "prompt_tokens": 1000, "completion_tokens": 500})
        if kind is Imported:
            d = source_doc()
            return Imported(name=d.name, contact=d.contact, sections=[s.model_dump() for s in d.sections])
        if kind is Brief:
            return Brief(
                product="Public reporting tools",
                proof_points=[
                    "Make records inspectable",
                    "Explain query tradeoffs",
                    "Document repeatable review",
                ],
                real_stack=["Python", "SQL", "Excel"],
                seniority="Junior: a solo weekend deliverable",
                hard_keywords=["Python", "SQL", "Excel", "Kubernetes", "winning culture"],
            )
        if kind is Plan:
            return (
                (repair or plan()) if len([c for c in calls if c[0] == "Plan"]) > 1 else (initial or plan())
            )
        if kind is Critique:
            return Critique(
                proof_points=["Inspect records", "Explain decisions", "Repeat reviews"],
                evidence=["Report browser", "SQL labels", "Excel checklist"],
                missing_requirements=["Kubernetes"],
                distinctness="Product, analysis, internal checklist",
                grade="B",
                passed=True,
                line_fixes=fixes or [],
                what_would_change_the_verdict=fixes or [],
            )
        if kind is Revision:
            return revision or Revision()
        raise AssertionError(kind)

    model.calls = calls
    return model


def context(tmp_path, db=None, model=None, doc=None, profile=PROFILE, job=JOB):
    doc = doc or source_doc()
    inv = evidence.build(profile, doc_plain(doc))
    terms = ats.terms(job["description"], job["title"], inv.skills)
    source = modes.SourceResume(
        id="source", sha="source-sha", text=doc_plain(doc), doc=doc, style=editor.Style()
    )
    ctx = modes.ModeContext(
        db=db,
        config=None,
        model=model or stub(),
        compile_fn=compile_pdf,
        work_dir=tmp_path,
        evidence=inv,
        ats_terms=terms,
        ats_source=ats.score(terms, doc_plain(doc), inv.vocabulary),
    )
    return source, ctx


def test_registered_and_off_by_default(service):
    assert modes.get("aggressive").run is run
    assert service.db.get_setting("tailor_settings") is None
    assert choice.get_choice(service.db, "job") is None


def test_invalid_rewrites_and_missing_skills_cannot_change_source(tmp_path):
    source, ctx = context(tmp_path)
    revision = Revision(
        bullets=[
            Rewrite(id="work1", text="Invent an employer"),
            Rewrite(id="p1", text=REAL1.replace("Python", "Rust")),
            Rewrite(id="p2", text=REAL2 + " and 94.7%"),
        ],
        project_order=["missing"],
        categories=[Category(label="Tools", skills=["Python", "Kubernetes"])],
    )
    doc, refused = quality.apply_revision(
        source.doc, revision, ["Python", "SQL", "Excel", "Documentation"], POSTING, []
    )
    assert len(refused) == 5
    assert doc.sections[0] == source.doc.sections[0]
    assert next(s for s in doc.sections if s.kind == "projects") == next(
        s for s in source.doc.sections if s.kind == "projects"
    )
    assert {x.casefold() for x in skill_items(next(s for s in doc.sections if s.kind == "skills").text)} == {
        "python",
        "sql",
        "excel",
        "documentation",
    }
    assert check_rules(doc, source.doc)["ok"]


def test_proposals_obey_distinctness_repeat_metric_and_link_gates():
    ps = proposals()
    assert quality.proposal_issues(ps, [], POSTING) == []
    ps[1].archetype = ps[0].archetype
    ps[0].bullets[0] += " Saved 94.7% at https://fake.example.test"
    history = [
        [{"data_source": ps[2].data_source, "archetype": ps[2].archetype, "metric_shape": ps[2].metric_shape}]
    ] * 3
    errors = quality.proposal_issues(ps, history, POSTING)
    assert any("distinct" in e for e in errors)
    assert any("unmeasured" in e for e in errors)
    assert any("links" in e for e in errors)
    assert any("last 15" in e for e in errors)


def test_valid_rewrite_keeps_count_and_source_character_budget(tmp_path):
    source, _ = context(tmp_path)
    text = REAL1.replace(", then documented", "; documented")
    rewritten, refused = quality.apply_revision(
        source.doc, Revision(bullets=[Rewrite(id="p1", text=text)]), [], POSTING, []
    )
    assert not refused
    assert check_rules(rewritten, source.doc)["ok"]
    project = next(s for s in rewritten.sections if s.kind == "projects").entries[0]
    assert len(project.bullets) == 2
    assert project.bullets[0].text == text
    assert 0.9 * len(REAL1) <= len(text) <= len(REAL1)


@needs_tex
def test_nonsoftware_field_uses_its_own_methods_and_profile(tmp_path):
    doc = source_doc()
    doc.name = "Jordan Example"
    doc.sections[0].entries[0].heading = "Teacher"
    doc.sections[0].entries[0].bullets = [
        Bullet(
            id="work1",
            text="Designed lesson plans and documented classroom activities for repeatable weekly preparation",
        )
    ]
    doc.sections[1].entries[0].subheading = "B.Ed., Education"
    doc.sections[2].entries[0].heading = "Lesson Review Toolkit"
    doc.sections[2].entries[0].bullets = [
        Bullet(
            id="p1",
            text="Designed lesson plans using public library materials and classroom review notes, documenting changes for repeatable weekly preparation",
        ),
        Bullet(
            id="p2",
            text="Compared classroom activities with curriculum goals and kept a teaching checklist to explain each revision before the next lesson",
        ),
    ]
    doc.sections[
        3
    ].text = (
        "Teaching: Lesson Planning, Curriculum Development\nPractice: Classroom Management, Documentation"
    )
    job = {
        **JOB,
        "title": "Teacher",
        "description": "Requirements\nLesson Planning, Curriculum Development, Classroom Management and Documentation.\nResponsibilities\nPrepare activities using public teaching materials.",
    }
    profile = {
        "name": doc.name,
        "skills": ["Lesson Planning", "Curriculum Development", "Classroom Management", "Documentation"],
    }
    drafts = proposals()
    for p, name, public_input, tool, tradeoff in zip(
        drafts,
        ["Library Lesson Pack", "Public Resource Comparison", "Classroom Preparation Checklist"],
        ["Library of Congress", "Smithsonian Learning Lab", "National Archives"],
        ["Lesson Planning", "Curriculum Development", "Classroom Management"],
        [
            "Compare activity instructions with the learning goal before adding them to the pack, and explain how accessibility changes the choice.",
            "Inspect the source lesson labels before grouping materials, documenting the curriculum mapping instead of assuming the categories agree.",
            "Keep classroom preparation notes beside the original public activity and explain how a missing material changes the teaching checklist.",
        ],
        strict=True,
    ):
        p.title, p.data_source, p.tools = name, public_input, [tool]
        p.bullets = [
            f"Design a {tool} resource using {public_input} materials so a reader can inspect each activity alongside its public source.",
            tradeoff,
        ]
        p.build_plan = ["Choose public teaching materials", "Write the activity and a review checklist"]

    async def teacher_model(kind, system, user):
        if kind is Brief:
            return Brief(
                product="Classroom teaching",
                proof_points=[
                    "Prepare activities",
                    "Explain curriculum choices",
                    "Document classroom review",
                ],
                real_stack=profile["skills"],
                seniority="Classroom teacher",
                hard_keywords=profile["skills"],
            )
        if kind is Plan:
            return Plan(proposals=drafts)
        if kind is Critique:
            return Critique(
                proof_points=["Prepare", "Explain", "Review"],
                evidence=["Lesson pack", "Resource comparison", "Checklist"],
                distinctness="Product, analysis, internal tool",
                grade="B",
                passed=True,
            )
        raise AssertionError(kind)

    source, ctx = context(tmp_path, doc=doc, profile=profile, job=job, model=teacher_model)
    result = asyncio.run(run(job, source, profile, ctx))
    text = parse_check(result.pdf_path.read_bytes(), result.doc)["text"]
    assert "Python" not in text and "SQL" not in text and "Data Analyst" not in text
    assert "Jordan Example" in text and "Library of Congress" in text
    assert result.report["preview_rules"]["ok"]


@needs_tex
def test_every_step_compiles_generated_projects_and_critic_is_blind(tmp_path):
    model = stub(fixes=["Keep the supported skill inventory"], metered=True)
    source, ctx = context(tmp_path, model=model)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    text = parse_check(result.pdf_path.read_bytes(), result.doc)
    assert text["ok"]
    assert all(p.title in text["text"] for p in proposals())
    assert len(result.drafts) == 3
    assert result.report["preview_rules"]["ok"]
    assert not result.report["send_rules"]["ok"]
    assert result.report["requires_project_approval"]
    assert result.report["preview_rules"]["typography"]["sizes"]["name"] == [16.0]
    assert result.report["preview_rules"]["typography"]["sizes"]["bullet"] == [9.0]
    critic = next(c for c in model.calls if c[0] == "Critique")
    assert set(critic[2]) == {"job_description", "resume_text"}
    assert "brief" not in critic[2] and "archetype" not in critic[2]
    assert "winning culture" not in result.report["brief"]["hard_keywords"]
    assert [c[0] for c in model.calls] == ["Brief", "Plan", "Critique", "Revision"]
    assert result.report["cost"]["actual_usd"] == 0.00492
    assert len(result.report["steps"]) == 8
    assert "Public Report Review" in text["text"]


@needs_tex
def test_one_quality_repair_then_rerun_gates(tmp_path):
    bad = plan()
    bad.proposals[2].archetype = bad.proposals[0].archetype
    model = stub(initial=bad)
    source, ctx = context(tmp_path, model=model)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert [c[0] for c in model.calls] == ["Brief", "Plan", "Plan", "Critique"]
    assert result.report["deterministic_repairs"][0]["proposal_issues"]


def test_a_bullet_names_its_input_however_the_sentence_words_it():
    assert quality.names_input("Build a planner from FCC broadband records for field crews.", "FCC Broadband Data Collection records")
    assert quality.names_input("Compare filings from SEC EDGAR by quarter.", "SEC EDGAR")
    assert not quality.names_input("Build a planner from public records for field crews.", "FCC Broadband Data Collection records")


def test_a_tool_counts_anywhere_in_the_posting_in_its_phrase_or_all_its_words():
    posting = "Preferred: dbt. Build reporting tools. Experience with statistical models."
    assert quality.in_posting(posting, "dbt")
    assert quality.in_posting(posting, "statistical models")
    assert quality.in_posting(posting, "models statistical")
    assert not quality.in_posting(posting, "Kubernetes")
    assert not quality.in_posting(posting, "statistical Kubernetes")


def test_shape_is_settled_by_code_order_proof_points_and_one_third_bullet():
    ps = proposals()
    ps[0].proof_point, ps[1].proof_point, ps[2].proof_point = 2, 2, 0
    ps[0].bullets = [*ps[0].bullets[:2], "Check the export against the source."]
    ps[1].bullets = [*ps[1].bullets[:2], "Check the export against the source."]
    fixed = quality.normalize_proposals(ps)
    assert [p.proof_point for p in fixed] == [0, 1, 2]
    assert sorted(len(p.bullets) for p in fixed) == [2, 2, 3]
    assert quality.proposal_issues(fixed, [], POSTING) == []


@pytest.mark.parametrize(
    "written, reads",
    [
        ("Will build a FastAPI identity service from OWASP data.", "Builds a FastAPI identity service from OWASP data."),
        ("Will weigh token validation, and will report test coverage.", "Weighs token validation, and reports test coverage."),
        ("Build a Python report browser using NYC Open Data.", "Builds a Python report browser using NYC Open Data."),
        ("Compare query results, then will also apply a fix.", "Compares query results, then applies a fix."),
        ("FastAPI identity service that issues session cookies.", "FastAPI identity service that issues session cookies."),
        ("Handles partial failures with bounded retries.", "Handles partial failures with bounded retries."),
    ],
)
def test_a_draft_reads_as_a_description_not_a_to_do_list(written, reads):
    assert quality.describe(written) == reads


def test_rewording_never_turns_a_draft_into_finished_work():
    ps = proposals()
    ps[0].bullets[0] = "Will build a public explorer from NYC Open Data, and will save 94.7% of review time."
    fixed = quality.normalize_proposals(ps)
    problems = quality.proposal_problems(fixed[0], [], POSTING)
    assert any("unmeasured" in w for w in problems)  # an invented number still fails, however it is worded
    ps = proposals()
    ps[0].bullets[0] = "Built and shipped a public explorer from NYC Open Data."
    assert any("completed work" in w for w in quality.proposal_problems(quality.normalize_proposals(ps)[0], [], POSTING))


def test_a_tailored_page_is_tight_at_the_owners_sizes():
    style = editor.tailored_style(editor.Style())
    assert {style.margin_top, style.margin_bottom, style.margin_left, style.margin_right} == {0.25}
    assert (style.section_gap, style.entry_gap, style.bullet_gap) == (3, 1, 0)
    assert style.name_size == 16.0 and style.font_size == 9.0


def test_the_long_replies_get_room_and_others_keep_the_default():
    from jobpilot.features.knowledge.writing import LONG_REPLIES

    assert LONG_REPLIES["Imported"][0] >= 8000 and LONG_REPLIES["Imported"][1] >= 120
    assert "Answer" not in LONG_REPLIES and "Brief" not in LONG_REPLIES


def test_a_draft_figure_is_allowed_only_as_a_stated_target():
    ps = proposals()
    ps[0].bullets[0] = "Public record explorer built on NYC Open Data for readers [target: p95 under 200 ms]."
    assert quality.proposal_problems(ps[0], [], POSTING) == []
    ps[0].bullets[0] = "Public record explorer on NYC Open Data with p95 under 200 ms."  # stated as fact
    assert any("unmeasured" in w for w in quality.proposal_problems(ps[0], [], POSTING))
    ps[0].bullets[0] = "Public record explorer on NYC Open Data [p95 under 200 ms]."  # brackets that don't say target
    assert any("unmeasured" in w for w in quality.proposal_problems(ps[0], [], POSTING))
    ps[0].bullets[0] = "Public record explorer on NYC Open Data [target: 95% parsed], cutting review time by 40%."
    assert any("unmeasured" in w for w in quality.proposal_problems(ps[0], [], POSTING))
    ps[0].bullets = [
        "Public record explorer on NYC Open Data [target: 95% parsed].",
        "Handles missing fields without dropping rows [target: under 1% lost].",
    ]
    assert any("one headline metric" in w for w in quality.proposal_problems(ps[0], [], POSTING))


async def test_choosing_a_mode_again_retries_a_stored_failure(monkeypatch):
    from types import SimpleNamespace

    calls = []

    async def prepare(db, config, job_id, *, mode, model, fresh, **kw):
        calls.append(fresh)
        return SimpleNamespace(status="failed", cached=not fresh, artifact=None, error=None)

    async def read(*a, **k):
        return None

    monkeypatch.setattr(choice.pipeline, "prepare_resume", prepare)
    monkeypatch.setattr(choice, "read_posting", read)
    monkeypatch.setattr(choice, "get_choice", lambda db, job_id: SimpleNamespace(mode="aggressive"))
    monkeypatch.setattr(choice, "_record", lambda *a, **k: None)
    await choice.run(None, None, "job")
    assert calls == [False, True]


def test_keep_sound_never_keeps_a_draft_that_fails_a_truth_check():
    ps = proposals()
    ps[0].bullets[0] = "Built and shipped a public explorer from NYC Open Data."  # claims finished work
    ps[1].bullets[0] += " Saved 94.7% of review time."  # an unmeasured metric
    kept, dropped = quality.keep_sound(ps, [], POSTING)
    assert [p.title for p in kept] == [ps[2].title]
    assert [d["title"] for d in dropped] == [ps[0].title, ps[1].title]
    assert any("completed work" in w for w in dropped[0]["problems"])
    assert any("unmeasured" in w for w in dropped[1]["problems"])


@needs_tex
def test_a_draft_that_still_fails_is_left_out_and_the_tailoring_finishes(tmp_path):
    bad = plan()
    bad.proposals[1].bullets[0] = "Built and shipped a public explorer from SEC EDGAR for review teams."
    model = stub(initial=bad, repair=bad)
    source, ctx = context(tmp_path, model=model)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    title = bad.proposals[1].title
    assert [d["title"] for d in result.report["drafts_left_out"]] == [title]
    assert title not in parse_check(result.pdf_path.read_bytes(), result.doc)["text"]
    assert len(result.preview_projects) == 2


@needs_tex
def test_with_no_sound_draft_the_real_projects_carry_the_resume(tmp_path):
    bad = plan()
    for p in bad.proposals:
        p.bullets[0] = "Built and shipped " + p.bullets[0]
    model = stub(initial=bad, repair=bad)
    source, ctx = context(tmp_path, model=model)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert len(result.report["drafts_left_out"]) == 3
    assert result.preview_projects == []
    assert result.report["requires_project_approval"] is False
    assert Path(result.pdf_path).exists()


@needs_tex
def test_critique_can_fix_pending_generated_projects(tmp_path):
    revised = proposals()
    revised[0].bullets[1] = (
        "Compare missing-field behavior with the source export before adding filters, and document a repeatable local review checklist."
    )
    model = stub(fixes=["Explain missing-field handling"], revision=Revision(proposals=revised))
    source, ctx = context(tmp_path, model=model)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert result.report["critique_fixes_applied"]
    assert quality.describe(revised[0].bullets[1]) in result.drafts[0].bullets
    assert result.report["workspace_revision"] == 1


@needs_tex
def test_critique_cannot_reduce_pdf_keyword_coverage(tmp_path):
    # SQL remains in immutable source; a revised pending project cannot remove
    # the only occurrence of a brief keyword and still replace the preview.
    revised = proposals()
    revised[0].bullets[0] += " Use Kubernetes for a local deployment exercise."
    model = stub(
        initial=Plan(proposals=revised),
        fixes=["Simplify the deployment"],
        revision=Revision(proposals=proposals()),
    )
    source, ctx = context(tmp_path, model=model)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert not result.report["critique_fixes_applied"]
    assert "Kubernetes" in result.drafts[0].bullets[0]
    assert any("regressed" in x for x in result.report["unresolved"])


@needs_tex
def test_overflow_is_reported_without_cutting_source_content(tmp_path):
    doc = source_doc()
    role = doc.sections[0].entries[0]
    role.bullets = [
        Bullet(
            id=f"long-{i}",
            text=f"Reviewed report {i} and documented source assumptions for the annual audit, preserving every original record and explaining each adjustment in a repeatable review checklist.",
        )
        for i in range(28)
    ]
    source, ctx = context(tmp_path, doc=doc)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert len(result.doc.sections[0].entries[0].bullets) == 28
    assert result.report["page_fill"]["overflow"]
    assert "Nothing was cut" in result.report["overflow_message"]
    assert result.report["preview_rules"]["ok"]


def test_project_approval_is_specific_and_stale_choices_are_rejected(service, tmp_path):
    source, ctx = context(tmp_path, db=service.db)
    brief = asyncio.run(ctx.model(Brief, "", "{}"))
    ws = store.create(service.db, JOB, source, PROFILE, proposals(), brief)
    assert len(ws["selected_drafts"]) == 3
    approved = store.confirm(
        service.db, ws["id"], ws["drafts"][0]["id"], Confirmation(approved=True, revision=0)
    )
    assert store.confirmed(approved["drafts"][0])
    approved["drafts"][0]["bullets"][0] += " Changed"
    assert not store.confirmed(approved["drafts"][0])
    with pytest.raises(ValueError, match="changed"):
        store.choose(service.db, ws["id"], Choice(revision=0))
    with pytest.raises(ValueError, match="only projects"):
        store.choose(service.db, ws["id"], Choice(revision=1, removed_projects=["work1"]))
    original = store.choose(service.db, ws["id"], Choice(revision=1))
    assert original["selected_drafts"] == [] and original["removed_projects"] == []


@needs_tex
def test_original_projects_path_needs_no_confirmed_drafts(service, tmp_path):
    source, ctx = context(tmp_path, db=service.db)
    first = asyncio.run(run(JOB, source, PROFILE, ctx))
    ws = store.get(service.db, first.report["workspace_id"])
    store.choose(service.db, ws["id"], Choice(revision=ws["revision"]))
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert not result.drafts
    assert result.report["send_rules"]["ok"]
    assert result.report["history_checked"] == 0
    assert result.report["critique"]
    text = parse_check(result.pdf_path.read_bytes(), result.doc)["text"]
    assert "Public Report Review" in text
    assert all(p.title not in text for p in proposals())


def add_job(service):
    with service.db.exclusive() as s:
        s.add(
            Resume(
                id="source",
                name="Synthetic",
                filename="source.pdf",
                sha256="source-sha",
                text=doc_plain(source_doc()),
                roles=[],
            )
        )
        s.add(
            Job(
                id="ag-job",
                identity="ag-job",
                url=JOB["url"],
                title=JOB["title"],
                company=JOB["company"],
                description=POSTING,
                resume_id="source",
                status="ready",
                score=90,
            )
        )
    service.db.set_setting("profile", PROFILE)


@needs_tex
def test_real_shared_pipeline_accepts_approved_projects_and_pins_exact_pdf(service, tmp_path):
    from jobpilot.execution_services import pin_resume

    add_job(service)
    source, ctx = context(tmp_path, db=service.db)
    first = asyncio.run(run(JOB, source, PROFILE, ctx))
    workspace = store.get(service.db, first.report["workspace_id"])
    for d in workspace["drafts"]:
        workspace = store.confirm(
            service.db, workspace["id"], d["id"], Confirmation(approved=True, revision=workspace["revision"])
        )
    choice.choose(service.db, "ag-job", "aggressive")
    result = asyncio.run(choice.run(service.db, service.config, "ag-job", model=stub()))
    assert result["state"] == "review", result
    approved = choice.approve(service.db, service.config, "ag-job", result["artifact_id"])
    with service.db.exclusive() as s:
        artifact = s.get(RsArtifact, result["artifact_id"])
        packet, _ = pin_resume(
            s, service.config, s.get(Job, "ag-job"), s.get(Resume, "source"), PROFILE, artifact.id
        )
    assert hashlib.sha256(open(packet["resume_path"], "rb").read()).hexdigest() == approved["pdf_sha"]
    assert artifact.validation["rules"]["ok"]
    assert not artifact.validation["drafts"]
    assert all(p.title in artifact.text for p in proposals())
    from jobpilot.features.resumes.aggressive.guards import send_problem

    with service.db.exclusive() as s:
        assert send_problem(s, artifact) is None
    # User-edited artifacts keep the Aggressive provenance under review-v1.
    artifact.policy_version = "tailor-review-v1"
    with service.db.exclusive() as s:
        assert send_problem(s, artifact) is None
    current = store.get(service.db, workspace["id"])
    store.choose(service.db, current["id"], Choice(revision=current["revision"]))
    with service.db.exclusive() as s:
        assert send_problem(s, artifact)[0] == "tailoring_stale"
    # The hook must be called by the shared send guard (see HANDOFF.md).


@needs_tex
@pytest.mark.parametrize("approval", [True, False])
def test_unconfirmed_preview_reaches_review_but_cannot_be_approved_or_sent(service, approval):
    from jobpilot.admission import QueueBlocked

    add_job(service)
    choice.choose(service.db, "ag-job", "aggressive")
    choice.set_approval(service.db, approval)
    result = asyncio.run(choice.run(service.db, service.config, "ag-job", model=stub()))
    assert result["state"] == "review"
    with service.db.session() as s:
        artifact = s.get(RsArtifact, result["artifact_id"])
        assert artifact.validation["preview_rules"]["ok"]
        assert not artifact.validation["rules"]["ok"]
        assert all(p.title in artifact.text for p in proposals())
        with pytest.raises(QueueBlocked) as blocked:
            choice.artifact_for_queue(s, s.get(Job, "ag-job"), "submit")
        assert blocked.value.code == choice.UNCONFIRMED
    with pytest.raises(ValueError, match="Approve"):
        choice.approve(service.db, service.config, "ag-job", result["artifact_id"])
