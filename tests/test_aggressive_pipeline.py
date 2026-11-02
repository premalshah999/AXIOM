"""Synthetic, compiled Aggressive path. No dotenv, production state or network."""

import asyncio
import hashlib
import io
import json
import shutil
from pathlib import Path

import pytest
from pypdf import PdfReader

from jobpilot.db import Job, Resume
from jobpilot.features.resumes import ats, choice, editor, evidence, modes
from jobpilot.features.resumes.aggressive import quality, store
from jobpilot.features.resumes.aggressive.pipeline import run, with_projects
from jobpilot.features.resumes.aggressive.schema import (
    Brief,
    BulletFix,
    Category,
    Choice,
    Confirmation,
    Critique,
    Plan,
    Proposal,
    Revision,
    Rewrite,
    ShapeFix,
)
from jobpilot.features.resumes.compiler import compile_pdf
from jobpilot.features.resumes.editor import Bullet, Entry, ResumeDoc, Section
from jobpilot.features.resumes.layout import fit
from jobpilot.features.resumes.master import Imported
from jobpilot.features.resumes.rules import bullet_window, check_rules, doc_plain, live_bullets, skill_items
from jobpilot.features.resumes.tables import RsArtifact
from jobpilot.features.resumes.validate import ITALIC, check_typography, font_runs, parse_check, squash

needs_tex = pytest.mark.skipif(not shutil.which("pdflatex"), reason="TeX required")
FULL_PAGE_ROLES = (20, 20, 8)  # Bullets per role: enough that this resume's real page has room for nothing more.
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
# The resume's own projects, each the size its generated draft below must keep: a draft takes the place of the one at its position.
ORIGINALS = {
    "original": ("Public Report Review", [REAL1, REAL2]),
    "budget": (
        "Budget Table Comparison",
        [
            "Compared quarterly budget tables in Excel and documented each category change for a repeatable public records review",
            "Kept a SQL query log beside the budget tables so each reviewer could quickly see which source record changed a reported total",
        ],
    ),
    "transit": (
        "Transit Feed Checks",
        [
            "Checked a public transit feed against published schedules in Python and recorded every stale record in a shared weekly review sheet",
            "Wrote a short documentation page explaining how the feed checks run, which fields they skip, and why a stale record gets flagged for review",
        ],
    ),
}


def original_projects():
    return [
        Entry(
            id=key,
            heading=heading,
            subheading="Personal project",
            bullets=[Bullet(id=f"{key}{i + 1}" if key != "original" else f"p{i + 1}", text=t) for i, t in enumerate(texts)],
        )
        for key, (heading, texts) in ORIGINALS.items()
    ]


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
                entries=original_projects(),
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
                "Compare local query results with the downloaded records before adding filters, keeping missing fields visible.",
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


def stub(initial=None, repair=None, revision=None, fixes=None, metered=False, shape_fix=None):
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
        if kind is ShapeFix:
            if isinstance(shape_fix, Exception):
                raise shape_fix
            if isinstance(shape_fix, list):  # One reply per round.
                return shape_fix.pop(0) if shape_fix else ShapeFix()
            return shape_fix or ShapeFix()
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
    # One project, so of the three drafts the model writes only the first takes a place on the page.
    doc.sections[2].entries = doc.sections[2].entries[:1]
    doc.sections[2].entries[0].heading = "Lesson Review Toolkit"
    doc.sections[2].entries[0].bullets = [
        Bullet(
            id="p1",
            text="Designed lesson plans using public library materials and classroom review notes, documenting all changes for repeatable weekly preparation",
        ),
        Bullet(
            id="p2",
            text="Compared classroom activities with curriculum goals and kept a teaching checklist to explain each revision made before the next planned lesson",
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
    assert "Lesson Review Toolkit" not in text and "Smithsonian" not in text
    assert result.report["preview_rules"]["ok"]
    assert [d.title for d in result.drafts] == ["Library Lesson Pack"]


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
    # The drafts took their projects' places, and the shared rules accept that: it is the workspace's own choice.
    assert all(heading not in text["text"] for heading, _ in ORIGINALS.values())
    assert result.report["removed_projects"] == list(ORIGINALS)
    # The writer got the exact numbers for every bullet, in project order.
    written = next(c for c in model.calls if c[0] == "Plan")[2]["project_shapes"]
    assert [x["replaces"] for x in written] == [heading for heading, _ in ORIGINALS.values()]
    assert written[0]["bullets"][0]["max_chars"] == len(REAL1)
    assert written[0]["bullets"][0]["min_chars"] == bullet_window(REAL1)[0]


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
    # With no project of the resume's to size it, a draft still holds three bullets at most.
    ps = proposals()
    ps[0].bullets = [*ps[0].bullets[:2], *["Check the export against the source."] * 4]
    assert len(quality.normalize_proposals(ps)[0].bullets) == 3


@pytest.mark.parametrize(
    "written, reads",
    [
        ("Will build a FastAPI identity service from OWASP data.", "Builds a FastAPI identity service from OWASP data."),
        ("Will weigh token validation, and will report test coverage.", "Weighs token validation, and reports test coverage."),
        ("Build a Python report browser using NYC Open Data.", "Builds a Python report browser using NYC Open Data."),
        ("Compare query results, then will also apply a fix.", "Compares query results, then applies a fix."),
        ("FastAPI identity service that issues session cookies.", "FastAPI identity service that issues session cookies."),
        ("Handles partial failures with bounded retries.", "Handles partial failures with bounded retries."),
        ("Will need source records, and will embed a viewer.", "Needs source records, and embeds a viewer."),
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


def test_a_draft_takes_its_original_projects_bullet_count_and_length():
    ps = quality.normalize_proposals(proposals())
    lengths = [len(b) for b in ps[0].bullets]
    assert quality.shape_problems(ps[0], [(n, n) for n in lengths]) == []
    assert quality.shape_problems(ps[0], [(int(n * 0.9), n) for n in lengths]) == []
    assert "write exactly 3" in quality.shape_problems(ps[0], [(90, 100)] * 3)[0]
    too_long = quality.shape_problems(ps[0], [(lengths[0] - 12, lengths[0] - 2), (lengths[1], lengths[1])])
    assert too_long == [
        f"{ps[0].title}: bullet 1 has {lengths[0]} characters, cut 2 to 12 so it has {lengths[0] - 12} to {lengths[0] - 2}"
    ]
    too_short = quality.shape_problems(ps[0], [(lengths[0] + 3, lengths[0] + 9), (lengths[1], lengths[1])])
    assert too_short == [
        f"{ps[0].title}: bullet 1 has {lengths[0]} characters, add 3 to 9 so it has {lengths[0] + 3} to {lengths[0] + 9}"
    ]


def test_the_fixture_drafts_fit_the_projects_they_replace():
    assert quality.shape_issues(quality.normalize_proposals(proposals(), quality.project_shapes(source_doc())), quality.project_shapes(source_doc())) == []


def test_only_as_many_drafts_as_the_resume_has_projects():
    fixed = quality.normalize_proposals(proposals(), [[(90, 100), (90, 100)], [(90, 100), (90, 100)]])
    assert [p.proof_point for p in fixed] == [0, 1]
    assert [p.title for p in fixed] == [p.title for p in proposals()[:2]]


def test_a_draft_loses_bullets_past_the_count_and_the_gates_ask_for_the_missing_ones():
    ps = proposals()
    ps[0].bullets = [*ps[0].bullets, "Checks each export against the source."]
    ps[1].bullets = ps[1].bullets[:1]
    fixed = quality.normalize_proposals(ps, quality.project_shapes(source_doc()))
    assert len(fixed[0].bullets) == 2
    assert len(fixed[1].bullets) == 1
    issues = quality.proposal_issues(fixed, [], POSTING, quality.project_shapes(source_doc()))
    assert any("has 1 bullets, write exactly 2" in i for i in issues)


def test_project_slots_skip_what_has_no_size_and_stop_at_three():
    doc = source_doc()
    projects = doc.sections[2]
    assert [e.id for e in quality.project_slots(doc)] == list(ORIGINALS)
    projects.entries[0].bullets = []
    projects.entries.append(projects.entries[1].model_copy(update={"id": "fourth"}))
    projects.entries.append(projects.entries[1].model_copy(update={"id": "fifth"}))
    assert [e.id for e in quality.project_slots(doc)] == ["budget", "transit", "fourth"]
    doc.sections.remove(projects)
    assert quality.project_slots(doc) == [] and quality.project_shapes(doc) == []


@pytest.mark.parametrize(
    "text, window, reads",
    [
        ("Compares records.", (10, 16), "Compares records"),
        ("Compares records in order to flag gaps.", (25, 34), "Compares records to flag gaps"),
        ("Compares records, and flags gaps", (20, 31), "Compares records and flags gaps"),
        # Already fits, or can't land in the window: left word for word for the model.
        ("Compares records.", (10, 17), "Compares records."),
        ("Compares records against the source.", (10, 20), "Compares records against the source."),
        ("Short.", (20, 30), "Short."),
    ],
)
def test_a_few_characters_over_is_settled_without_a_model(text, window, reads):
    assert quality.tighten(text, *window) == reads


def test_the_fix_asks_only_for_what_missed_with_exact_numbers():
    ps = quality.normalize_proposals(proposals())
    shapes = [[(len(b), len(b)) for b in p.bullets] for p in ps]
    fine = len(ps[0].bullets[0])
    shapes[0][0] = (fine - 12, fine - 2)
    ps[2].bullets = ps[2].bullets[:1]
    ps[1].bullets[0] = "Built and shipped " + ps[1].bullets[0]  # Going to be left out: nothing is spent on it.
    shapes[1][0] = (10, 20)
    asked = quality.shape_request(ps, shapes, [], POSTING)
    assert [(x["draft"], x["bullet"]) for x in asked["bullets"]] == [(0, 0), (2, 1)]
    first, missing = asked["bullets"]
    assert (first["chars"], first["min_chars"], first["max_chars"]) == (fine, fine - 12, fine - 2)
    assert first["change"] == "cut 2 to 12 characters"
    assert missing["text"] == "" and missing["chars"] == 0 and missing["change"].startswith("add ")
    assert asked["wordings_per_bullet"] == 3


def test_a_big_fix_asks_for_fewer_wordings_then_fewer_bullets_within_the_reply_cap():
    ps = quality.normalize_proposals(proposals())
    for p in ps:
        p.bullets = [p.bullets[0]] * 8
    shapes = [[(1100, 1200)] * 8] * 3
    asked = quality.shape_request(ps, shapes, [], POSTING)
    assert sum(x["max_chars"] for x in asked["bullets"]) * asked["wordings_per_bullet"] <= quality.FIX_REPLY_CHARS
    assert asked["wordings_per_bullet"] == 1 and len(asked["bullets"]) == 4
    shapes = [[(190, 200)] * 2] * 3
    ps = [p.model_copy(update={"bullets": p.bullets[:2]}) for p in ps]
    assert quality.shape_request(ps, shapes, [], POSTING)["wordings_per_bullet"] == 3
    # The reply stays under the default cap: the fix is never given a long reply of its own.
    from jobpilot.features.knowledge.writing import LONG_REPLIES

    assert "ShapeFix" not in LONG_REPLIES


def test_a_fix_takes_the_first_wording_that_fits_and_breaks_no_truth_check():
    ps = quality.normalize_proposals(proposals())
    low, high = len(ps[0].bullets[1]) - 10, len(ps[0].bullets[1]) - 1
    shapes = [[(len(ps[0].bullets[0]), len(ps[0].bullets[0])), (low, high)]]
    good = "Compares local query results with the records downloaded before adding filters, showing missing fields."
    assert low <= len(good) <= high
    claims_a_result = "Compares local query results with the downloaded records and cuts the review time by 45% in one week."
    assert low <= len(claims_a_result) <= high
    fix = ShapeFix(
        bullets=[
            BulletFix(draft=0, bullet=1, wordings=["Far too short.", claims_a_result, good]),
            BulletFix(draft=2, bullet=0, wordings=[good]),  # No such draft on this resume.
            BulletFix(draft=0, bullet=5, wordings=[good]),  # No such bullet.
        ]
    )
    ps[0].bullets[1] = ps[0].bullets[1] + " Extra"
    quality.apply_shape_fix(ps[:1], shapes, fix, [], POSTING)
    assert ps[0].bullets[1] == good
    # Nothing that fits: the bullet stays as it was, and the draft is left out with the numbers.
    ps = quality.normalize_proposals(proposals())
    before = list(ps[0].bullets)
    shapes = [[(len(before[0]), len(before[0])), (300, 320)]]
    quality.apply_shape_fix(ps[:1], shapes, ShapeFix(bullets=[BulletFix(draft=0, bullet=1, wordings=[good])]), [], POSTING)
    assert ps[0].bullets == before
    kept, dropped = quality.keep_sound(ps[:1], [], POSTING, shapes)
    assert kept == [] and "bullet 2 has" in dropped[0]["problems"][0]


def test_real_experience_and_project_bullets_keep_the_rule_with_drafts_on_the_page():
    source = source_doc()
    entry = store.entry({"id": "ws:p0", "title": "Public Record Explorer", "descriptor": "Research", "bullets": ["A draft bullet of some length."]})

    def problems(change):
        doc = source.model_copy(deep=True)
        doc.sections[2].entries.insert(0, entry)
        doc.sections[2].entries[1].include = False
        change(doc)
        return {p["code"] for p in check_rules(doc, source, added_projects=[entry], removed_projects=["original"])["problems"]}

    assert problems(lambda d: None) == set()
    work = lambda d: d.sections[0].entries[0].bullets  # noqa: E731
    assert "bullet_too_long" in problems(lambda d: setattr(work(d)[0], "text", work(d)[0].text + " again"))
    assert "bullet_too_short" in problems(lambda d: setattr(work(d)[0], "text", work(d)[0].text[:60]))
    assert "bullet_count" in problems(lambda d: work(d).pop())
    kept = lambda d: d.sections[2].entries[2].bullets  # noqa: E731  (the budget project, which no draft replaced)
    assert "bullet_too_long" in problems(lambda d: setattr(kept(d)[0], "text", kept(d)[0].text + " again"))
    assert "bullet_count" in problems(lambda d: kept(d).pop())
    # A project with no draft and no choice to remove it is still never dropped.
    assert "entry_dropped" in problems(lambda d: setattr(d.sections[2].entries[2], "include", False))


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
    ps[0].bullets[0] = "Public record explorer on NYC Open Data for readers [target: p95 under 200 ms]."
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


@pytest.mark.parametrize(
    "bullet",
    [
        "Fact checker on NYC Open Data reaches 95 percent accuracy on 40000 claims.",
        "Fact checker on NYC Open Data reaches ninety-five percent accuracy.",
        "Fact checker on NYC Open Data reaches 95% accuracy.",
        "Fact checker on NYC Open Data that covers 40000 claims.",
        "Fact checker on NYC Open Data that covers 40,000 claims.",
        "Fact checker on NYC Open Data that covers forty thousand claims.",
        "Fact checker on NYC Open Data that runs 2x faster than a spreadsheet.",
        "Fact checker on NYC Open Data that runs x10 faster than a spreadsheet.",
        "Fact checker on NYC Open Data that runs twice as fast as a spreadsheet.",
        "Fact checker on NYC Open Data that doubles reader throughput.",
        "Fact checker on NYC Open Data that costs $5 a month.",
        "Fact checker on NYC Open Data that answers in under200ms.",
        "Fact checker on NYC Open Data with sub-200 ms lookups.",
        "Fact checker on NYC Open Data with a 30-day window.",
        "Fact checker on NYC Open Data that checks 3 sources.",
        "Fact checker on NYC Open Data from 2024.",
        "Fact checker on NYC Open Data that finishes in five minutes.",
        "Fact checker on NYC Open Data with GPT-4 at 90 percent recall.",
        "Fact checker on NYC Open Data with Python 95 accuracy.",
        "Fact checker on NYC Open Data [p95 under 200 ms].",
        "Fact checker on NYC Open Data with recall95 on each batch.",
        "Fact checker on NYC Open Data with a GPT 95 accuracy score.",
    ],
)
def test_no_figure_stands_outside_a_target_bracket(bullet):
    ps = proposals()
    ps[0].bullets[0] = bullet
    fixed = quality.normalize_proposals(ps)  # describe() runs first, as it does in every real run.
    assert any("unmeasured" in w for w in quality.proposal_problems(fixed[0], [], POSTING)), bullet


@pytest.mark.parametrize(
    "bullet",
    [
        "Log explorer on NYC Open Data stored in S3 for readers [target: p95 under 200 ms].",
        "Summarizer on NYC Open Data that compares GPT-4 with GPT-3.5 and Llama 3.",
        "Service in Python 3 and Java 17 on NYC Open Data with an EC2 host and K8s manifests.",
        "Parser on NYC Open Data with SHA-256 hashes, UTF-8 text, Neo4j storage and OAuth 2.0.",
        "Filing review on NYC Open Data for SEC 10-K forms, comparing three methods at one desk.",
        "Reader on NYC Open Data that flags double counting and scaffolds a second service.",
    ],
)
def test_a_name_holding_a_digit_is_not_a_figure(bullet):
    ps = proposals()
    ps[0].bullets[0] = bullet
    fixed = quality.normalize_proposals(ps)
    assert quality.proposal_problems(fixed[0], [], POSTING) == [], bullet


@pytest.mark.parametrize(
    "bullet",
    [
        "Developed a public explorer from NYC Open Data for readers.",
        "Public explorer from NYC Open Data, developed for readers.",
        "Public explorer from NYC Open Data that was deployed for readers.",
        "Public explorer from NYC Open Data and shipped to readers.",
        "Public explorer from NYC Open Data, launched for readers.",
        "Public explorer from NYC Open Data that achieved a clear review.",
        "Public explorer from NYC Open Data that reduced review time.",
        "Public explorer from NYC Open Data that increased reader trust.",
        "Public explorer from NYC Open Data that improved review quality.",
        "Public explorer from NYC Open Data that saved review time.",
        "Public explorer from NYC Open Data, delivered to readers.",
        "Public explorer built on NYC Open Data for readers.",
        "Public explorer on NYC Open Data that has been used by readers.",
        "Will have built a public explorer from NYC Open Data for readers.",
        "Would be shipped as a public explorer from NYC Open Data.",
        "Will shipped a public explorer from NYC Open Data for readers.",
        "Public explorer from NYC Open Data that should delivered readers a summary.",
        "Will parsed the NYC Open Data records for a public explorer.",
        "Leveraged NYC Open Data for a public explorer for readers.",  # sanitize() says "used".
        "Parsed the NYC Open Data records for a public explorer.",
        "Refactored a public explorer from NYC Open Data for readers.",
        "Public explorer from NYC Open Data that parsed the records.",
        "Automated the review of NYC Open Data for a public explorer.",
    ],
)
def test_a_draft_never_claims_finished_work_anywhere_in_a_bullet(bullet):
    from jobpilot.features.resumes import gates

    ps = proposals()
    ps[0].bullets[0] = gates.sanitize(bullet)
    fixed = quality.normalize_proposals(ps)  # describe() must not turn any of these into a passing bullet.
    assert any("completed work" in w for w in quality.proposal_problems(fixed[0], [], POSTING)), bullet


@pytest.mark.parametrize(
    "bullet",
    [
        "Public explorer from NYC Open Data that builds a report and deploys it locally for one reader.",
        "Compares NYC Open Data records and reduces each to a short summary for readers.",
        "Distributed rate limiter on NYC Open Data that handles partial failures with bounded retries.",
        "Automated review checklist on NYC Open Data, designed so a reader can replay one failed record.",
        "Compares published schedules from NYC Open Data with structured logs, using built-in checks.",
        "Feed parser on NYC Open Data that keeps rejected rows beside established methods.",
        "Embeddings viewer on NYC Open Data that won't drop a row and can embed the source.",
    ],
)
def test_a_present_tense_description_stays_allowed(bullet):
    ps = proposals()
    ps[0].bullets[0] = bullet
    fixed = quality.normalize_proposals(ps)
    assert quality.proposal_problems(fixed[0], [], POSTING) == [], bullet


def test_a_title_holds_no_figure_or_finished_work_and_two_targets_are_two_metrics():
    ps = proposals()
    ps[0].title = "Explorer With 40000 Claims"
    assert any("unmeasured" in w for w in quality.proposal_problems(ps[0], [], POSTING))
    ps = proposals()
    ps[0].title = "Deployed Claims Explorer"
    assert any("completed work" in w for w in quality.proposal_problems(ps[0], [], POSTING))
    ps = proposals()
    ps[0].title = "Tiered Claims Explorer"  # An adjective, not a claim.
    assert quality.proposal_problems(ps[0], [], POSTING) == []
    ps[0].bullets = [
        "Public record explorer on NYC Open Data [target: ninety percent parsed].",
        "Handles missing fields without dropping rows [target: few rows lost].",
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
    bad.proposals[1].bullets[0] = "Built and shipped a public explorer from SEC EDGAR for review teams, comparing categories across documents."
    assert 105 <= len(bad.proposals[1].bullets[0]) <= 116  # Only the truth check fails.
    model = stub(initial=bad, repair=bad)
    source, ctx = context(tmp_path, model=model)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    title = bad.proposals[1].title
    assert [d["title"] for d in result.report["drafts_left_out"]] == [title]
    text = parse_check(result.pdf_path.read_bytes(), result.doc)["text"]
    assert title not in text
    assert len(result.preview_projects) == 2
    # That slot's own project stays where it was, between the two drafts that replaced its neighbours.
    assert result.report["removed_projects"] == ["original", "transit"]
    assert text.index(proposals()[0].title) < text.index("Budget Table Comparison") < text.index(proposals()[2].title)


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
    assert result.report["removed_projects"] == []
    assert all(heading in parse_check(result.pdf_path.read_bytes(), result.doc)["text"] for heading, _ in ORIGINALS.values())
    # Nothing was spent fixing the length of drafts a truth check had already ruled out.
    assert "ShapeFix" not in [c[0] for c in model.calls]


def fix_for(bullets):
    """The bounded length fix a model would return: {(draft, bullet): wording}."""
    return ShapeFix(bullets=[BulletFix(draft=d, bullet=b, wordings=[text]) for (d, b), text in bullets.items()])


def too_long_plan():
    """A real model's first try: every draft right but for one bullet, far enough over that only a model can shorten it."""
    missed = plan()
    missed.proposals[0].bullets[0] += " It groups each record by source year for quick reading."
    missed.proposals[2].bullets[1] += " It also keeps a dated copy of each export for later review."
    return missed


FIXED_0 = "Builds a Python report browser using NYC Open Data so a reader can check each source record beside the summary."
FIXED_2 = "Keeps the source export beside the checklist and documents how a stale feed changes the review, with no claim of live integration."


@needs_tex
def test_a_length_miss_costs_one_short_fix_and_never_a_new_plan(tmp_path):
    model = stub(initial=too_long_plan(), shape_fix=fix_for({(0, 0): FIXED_0, (2, 1): FIXED_2}), metered=True)
    source, ctx = context(tmp_path, model=model)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert [c[0] for c in model.calls] == ["Brief", "Plan", "ShapeFix", "Critique"]
    asked = next(c for c in model.calls if c[0] == "ShapeFix")[2]["bullets"]
    assert [(x["draft"], x["bullet"]) for x in asked] == [(0, 0), (2, 1)]
    assert (asked[0]["min_chars"], asked[0]["max_chars"]) == bullet_window(REAL1)
    assert asked[0]["chars"] == len(too_long_plan().proposals[0].bullets[0]) + 1  # "Build" reads "Builds".
    assert asked[0]["change"].startswith("cut ")
    # The fix is told the rules, and carries no plan, posting or resume: it is the short call.
    system = next(c for c in model.calls if c[0] == "ShapeFix")[1]
    assert "[target: ...]" in system and "never past tense" in system
    assert len(json.dumps(asked)) < 1500
    assert result.report["drafts_left_out"] == []
    assert [d.bullets[0] for d in result.drafts][0] == FIXED_0 and result.drafts[2].bullets[1] == FIXED_2
    assert result.report["removed_projects"] == list(ORIGINALS)
    assert result.report["deterministic_repairs"][0]["left"] == []


@needs_tex
def test_a_draft_still_missing_after_the_fix_keeps_that_slots_project(service, tmp_path):
    model = stub(initial=too_long_plan(), shape_fix=fix_for({(0, 0): FIXED_0, (2, 1): "Still far too short."}))
    source, ctx = context(tmp_path, db=service.db, model=model)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert [c[0] for c in model.calls] == ["Brief", "Plan", "ShapeFix", "Critique"]
    gone = proposals()[2].title
    assert [d["title"] for d in result.report["drafts_left_out"]] == [gone]
    assert "bullet 2 has" in result.report["drafts_left_out"][0]["problems"][0]
    assert result.report["removed_projects"] == ["original", "budget"]
    text = parse_check(result.pdf_path.read_bytes(), result.doc)["text"]
    assert gone not in text and "Transit Feed Checks" in text
    assert "Public Report Review" not in text and "Budget Table Comparison" not in text
    # What's on the page is the applicant's own wording for that slot, whole.
    transit = next(e for s in result.doc.sections for e in s.entries if e.id == "transit")
    assert transit.include and [b.text for b in transit.bullets] == ORIGINALS["transit"][1]
    stored = store.get(service.db, result.report["workspace_id"])
    assert [d["replaces"] for d in stored["drafts"]] == ["original", "budget"]
    assert result.report["preview_rules"]["ok"]


@needs_tex
def test_a_repair_gets_the_exact_numbers_and_the_fix_still_follows(tmp_path):
    first = too_long_plan()
    first.proposals[1].bullets[0] = "Built and shipped a public explorer from SEC EDGAR for review teams, comparing categories across documents."
    still = too_long_plan()
    model = stub(initial=first, repair=still, shape_fix=fix_for({(0, 0): FIXED_0, (2, 1): FIXED_2}))
    source, ctx = context(tmp_path, model=model)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert [c[0] for c in model.calls] == ["Brief", "Plan", "Plan", "ShapeFix", "Critique"]
    issues = [c for c in model.calls if c[0] == "Plan"][1][2]["issues"]
    assert any("completed work" in i for i in issues)
    n = len(first.proposals[0].bullets[0]) + 1
    low, high = bullet_window(REAL1)
    assert f"{proposals()[0].title}: bullet 1 has {n} characters, cut {n - high} to {n - low} so it has {low} to {high}" in issues
    assert result.report["drafts_left_out"] == []


@needs_tex
def test_a_fix_that_comes_nearer_gets_another_round_from_there(tmp_path):
    """A real model miscounts: on 2026-11-02 one round left every draft out. Each round starts from the nearest wording so far."""
    nearer = FIXED_2 + " It is checked against each dated export."  # Still over its window, but nearer than the first try.
    model = stub(initial=too_long_plan(), shape_fix=[fix_for({(0, 0): FIXED_0, (2, 1): nearer}), fix_for({(2, 1): FIXED_2})])
    source, ctx = context(tmp_path, model=model)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert [c[0] for c in model.calls] == ["Brief", "Plan", "ShapeFix", "ShapeFix", "Critique"]
    second = [c for c in model.calls if c[0] == "ShapeFix"][1][2]["bullets"]
    assert [(x["draft"], x["bullet"], x["chars"]) for x in second] == [(2, 1, len(nearer))]
    assert result.report["drafts_left_out"] == [] and result.drafts[2].bullets[1] == FIXED_2
    assert [s.get("round") for s in result.report["deterministic_repairs"] if s.get("stage") == "shape_repair"] == [1, 2]


@needs_tex
def test_a_failed_length_fix_only_leaves_those_drafts_out(tmp_path):
    model = stub(initial=too_long_plan(), shape_fix=RuntimeError("provider down"))
    source, ctx = context(tmp_path, model=model)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert result.report["deterministic_repairs"][0]["error"] == "RuntimeError"
    assert [d.title for d in result.drafts] == [proposals()[1].title]
    assert result.report["removed_projects"] == ["budget"]
    assert result.report["preview_rules"]["ok"]


def await_fit(doc, style):
    return asyncio.run(fit(doc, editor.tailored_style(style), compile_pdf))


def printed_projects(pdf, doc):
    """The compiled page as a reader gets it: each project on it, with the bullets printed under it."""
    lines = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages).splitlines()
    live = [e for s in doc.sections if s.include and s.kind == "projects" for e in s.entries if e.include]
    others = {s.title for s in doc.sections if s.kind != "projects"}
    printed, current = {}, None
    for line in lines[lines.index("Projects") + 1 :]:
        if line in others:
            break
        heading = next((e.heading for e in live if line.startswith(e.heading)), None)
        if heading:
            current = printed.setdefault(heading, [])
        elif line.startswith("•"):
            current.append(line[1:].strip())
        else:
            current[-1] += " " + line  # A long bullet wrapped onto its next line.
    return printed


def assert_the_owners_page(result, compiled_doc, source):
    """Everything the owner set, read back from the compiled file: never from the document it was made from."""
    pdf = result.pdf_path.read_bytes()
    page = PdfReader(io.BytesIO(pdf)).pages
    assert len(page) == 1
    # Type sizes and italic dates, from the PDF's own drawing operators.
    typography = check_typography(pdf, compiled_doc)
    assert typography["ok"], typography["problems"]
    assert typography["sizes"]["name"] == [16.0]
    assert typography["sizes"]["section"] == [10.5]
    assert typography["sizes"]["sub"] == [10.0]
    assert typography["sizes"]["bullet"] == [9.0]
    runs = font_runs(pdf)
    dated = [e.dates for s in compiled_doc.sections for e in s.entries if e.include and e.dates]
    for dates in dated:
        assert any(dates in r["text"] and ITALIC.search(r["font"]) for r in runs), dates
    # Margins: the style, what TeX was told, and where the ink starts. 0.25 inch is 18 pt.
    style = result.style
    assert {style.margin_top, style.margin_bottom, style.margin_left, style.margin_right} == {0.25}
    assert "left=0.25in" in result.latex and "right=0.25in" in result.latex
    assert "top=0.25in" in result.latex and "bottom=0.25in" in result.latex
    spots = []

    def visit(text, cm, tm, *_):
        if text.strip():
            spots.append((tm[4] * cm[0] + cm[4], tm[5] * cm[3] + cm[5]))

    page[0].extract_text(visitor_text=visit)
    assert min(x for x, _ in spots) == pytest.approx(18, abs=0.5)
    assert min(y for _, y in spots) >= 18 - 3  # A baseline may sit a descender above the bottom margin.
    assert 792 - max(y for _, y in spots) < 18 + 16 * 1.2  # The name's top is at the margin.
    # Same bullet counts, characters in the window, for each draft against the project it replaced.
    printed = printed_projects(pdf, compiled_doc)
    originals = {e.id: e for s in source.doc.sections if s.kind == "projects" for e in s.entries}
    drafts = [d for d in result.report.get("drafts", [])]
    for entry in (e for s in compiled_doc.sections if s.kind == "projects" for e in s.entries if e.include):
        assert [squash(x) for x in printed[entry.heading]] == [squash(b.text) for b in live_bullets(entry)], entry.heading
    return printed, originals, drafts


@needs_tex
def test_on_a_compiled_pdf_each_draft_keeps_the_size_of_the_project_it_replaced(service, tmp_path):
    # A resume whose real page is already full: a draft added beside its project ran it over, which is what the owner saw.
    doc = source_doc()
    doc.sections[0].entries = [
        Entry(
            id=f"role{r}",
            heading="Data Analyst",
            subheading=f"Sample Company {r}",
            dates=f"06/20{20 + r} - 05/20{21 + r}",
            bullets=[
                Bullet(id=f"full{r}x{i}", text=f"Reviewed monthly report {i} with the finance lead and documented every adjustment made for the annual audit")
                for i in range(count)
            ],
        )
        for r, count in enumerate(FULL_PAGE_ROLES)
    ]
    source, ctx = context(tmp_path, db=service.db, doc=doc)
    tight = await_fit(source.doc, source.style)
    assert tight["compiled"].ok and not tight["overflow"], "the real resume fits one page"
    model = stub(initial=too_long_plan(), shape_fix=fix_for({(0, 0): FIXED_0, (2, 1): FIXED_2}))
    ctx.model = model
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert result.report["drafts_left_out"] == [] and len(result.drafts) == 3
    printed, originals, _ = assert_the_owners_page(result, result.doc, source)
    assert len(printed) == 3
    assert all(heading not in printed for heading, _ in ORIGINALS.values())
    stored = store.get(service.db, result.report["workspace_id"])
    for draft in stored["drafts"]:
        own = live_bullets(originals[draft["replaces"]])
        assert len(printed[draft["title"]]) == len(own) == len(draft["bullets"])
        for words, original in zip(draft["bullets"], own):
            assert 0.9 * len(original.text) <= len(words) <= len(original.text)
    # What the owner reported: the same drafts beside the originals push this very resume over a page.
    beside, _ = with_projects(source.doc, {**stored, "removed_projects": []})
    over = await_fit(beside, source.style)
    assert over["overflow"], "this resume must be full enough to show the old behaviour going over"

    # "Use only my original projects" brings back everything, at the same sizes and margins.
    store.choose(service.db, stored["id"], Choice(revision=stored["revision"]))
    back = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert back.drafts == [] and back.report["removed_projects"] == [] and back.report["send_rules"]["ok"]
    printed, _, _ = assert_the_owners_page(back, back.doc, source)
    assert list(printed) == [heading for heading, _ in ORIGINALS.values()]
    for heading, texts in ORIGINALS.values():
        assert [squash(x) for x in printed[heading]] == [squash(t) for t in texts]


@needs_tex
def test_a_resume_with_no_projects_gets_its_drafts_and_switches_back_to_none(service, tmp_path):
    doc = source_doc()
    doc.sections = [s for s in doc.sections if s.kind != "projects"]
    assert quality.project_shapes(doc) == []
    source, ctx = context(tmp_path, db=service.db, doc=doc)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    stored = store.get(service.db, result.report["workspace_id"])
    assert len(result.preview_projects) == 3 and result.report["removed_projects"] == []
    assert [d["replaces"] for d in stored["drafts"]] == [None, None, None]
    assert "project_shapes" not in next(c for c in ctx.model.calls if c[0] == "Plan")[2]
    assert result.report["preview_rules"]["ok"]
    assert_the_owners_page(result, result.doc, source)
    assert all(p.title in parse_check(result.pdf_path.read_bytes(), result.doc)["text"] for p in proposals())
    store.choose(service.db, stored["id"], Choice(revision=stored["revision"]))
    back = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert back.drafts == [] and back.report["send_rules"]["ok"]
    assert all(s.kind != "projects" or not s.entries for s in back.doc.sections)


@needs_tex
def test_one_project_takes_one_draft_and_a_fourth_project_stays(tmp_path):
    doc = source_doc()
    doc.sections[2].entries = doc.sections[2].entries[:1]
    model = stub(fixes=["Tighten the draft"], revision=Revision(proposals=[Plan(proposals=proposals()).proposals[0]]))
    source, ctx = context(tmp_path, doc=doc, model=model)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert [d.title for d in result.drafts] == [proposals()[0].title]
    assert result.report["removed_projects"] == ["original"]
    assert result.report["deterministic_repairs"] == [{"stage": "critique_fixes", "refused_edits": [], "coverage_regressed": False}]
    # The critique's rewrite sees this resume's one draft, and its size.
    asked = next(c for c in model.calls if c[0] == "Revision")[2]
    assert len(asked["project_shapes"]) == 1 and asked["project_shapes"][0]["replaces"] == "Public Report Review"

    doc = source_doc()
    doc.sections[2].entries.append(doc.sections[2].entries[1].model_copy(update={"id": "fourth", "heading": "Fourth Own Project"}))
    source, ctx = context(tmp_path, doc=doc)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert result.report["removed_projects"] == list(ORIGINALS)
    assert "Fourth Own Project" in parse_check(result.pdf_path.read_bytes(), result.doc)["text"]


def test_a_critique_rewrite_off_its_size_changes_nothing():
    ps = quality.normalize_proposals(proposals(), quality.project_shapes(source_doc()))
    ps[0].bullets[0] += " And a lot more wording that no longer fits the place on the page."
    errors = quality.proposal_issues(ps, [], POSTING, quality.project_shapes(source_doc()))
    assert any("bullet 1 has" in e and "cut" in e for e in errors)


def test_a_draft_sits_where_the_project_it_replaces_sat():
    doc = source_doc()
    drafts = [
        {"id": "ws:p0", "title": "Draft Zero", "descriptor": "Research", "bullets": ["One bullet."], "replaces": "original"},
        {"id": "ws:p2", "title": "Draft Two", "descriptor": "Research", "bullets": ["One bullet."], "replaces": "transit"},
    ]
    workspace = {"drafts": drafts, "selected_drafts": ["ws:p0", "ws:p2"], "removed_projects": ["original", "transit"]}
    placed, selected = with_projects(doc, workspace)
    assert selected == drafts
    entries = placed.sections[2].entries
    assert [e.id for e in entries if e.include] == ["ws:p0", "budget", "ws:p2"]
    # Switched back, nothing is left of the drafts.
    placed, selected = with_projects(doc, {**workspace, "selected_drafts": [], "removed_projects": []})
    assert selected == [] and [e.id for e in placed.sections[2].entries if e.include] == list(ORIGINALS)
    # A draft that replaces nothing, on a resume with no projects, makes the section.
    doc.sections = [s for s in doc.sections if s.kind != "projects"]
    placed, _ = with_projects(doc, {"drafts": [{**drafts[0], "replaces": None}], "selected_drafts": ["ws:p0"], "removed_projects": []})
    assert [e.id for s in placed.sections if s.kind == "projects" for e in s.entries] == ["ws:p0"]


@needs_tex
def test_a_page_fit_never_widens_the_owners_margins(tmp_path):
    doc = source_doc()
    doc.sections[0].entries[0].bullets = [Bullet(id=f"long{i}", text="Reviewed a report and documented its source assumptions " * 3) for i in range(60)]
    style = editor.tailored_style(editor.Style())
    tried = []

    async def compile_recording(tex, **kwargs):
        tried.append(tex)
        return await compile_pdf(tex, **kwargs)

    fitted = asyncio.run(fit(doc, style, compile_recording))
    assert fitted["overflow"]
    assert fitted["style"].margin_left == 0.25 and fitted["style"].margin_top == 0.25
    assert fitted["style"].line_spacing == 0.9 and fitted["style"].section_gap == 2  # It did tighten what there is to tighten.
    assert all("left=0.25in" in tex for tex in tried)


@needs_tex
def test_critique_can_fix_pending_generated_projects(tmp_path):
    revised = proposals()
    revised[0].bullets[1] = (
        "Compare missing-field behavior with the source export before adding filters, using a repeatable local checklist."
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
    revised[0].bullets[0] = "Build a Python report browser using NYC Open Data with a Kubernetes deployment exercise for readers to inspect."
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
        for i in range(70)
    ]
    source, ctx = context(tmp_path, doc=doc)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert len(result.doc.sections[0].entries[0].bullets) == 70
    assert result.report["page_fill"]["overflow"]
    # However long it runs, tightening never gives back the owner's margins or a type size.
    assert {result.style.margin_top, result.style.margin_bottom, result.style.margin_left, result.style.margin_right} == {0.25}
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
