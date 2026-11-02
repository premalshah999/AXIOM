"""20 synthetic postings against a model trying its best to lie. Zero invented claims may survive, by claims.py and by a read that shares no code with it."""

import asyncio
import json
import re
import shutil

import pytest
from tailoring_eval_data import POSTINGS, RESUMES, profile_of

from jobpilot.features.resumes import ats, claims, editor, modes, normal
from jobpilot.features.resumes.compiler import compile_pdf
from jobpilot.features.resumes.evidence import build
from jobpilot.features.resumes.rules import check_rules, doc_plain, fits_window, skill_items
from jobpilot.features.resumes.select import supported_terms

pytestmark = pytest.mark.skipif(not shutil.which("pdflatex"), reason="needs TeX")


def independent_read(source: editor.ResumeDoc, tailored: editor.ResumeDoc, profile: dict, posting: dict) -> list[str]:
    """Written apart from claims.py on purpose: reads the two documents as text, and reports anything on the page the source can't account for."""
    seen = re.sub(r"\s+", " ", doc_plain(source) + " " + " ".join(profile.get("skills", []))).lower()
    found = []
    for s in tailored.sections:
        if not s.include:
            continue
        pieces = [s.text] if s.kind in {"text", "skills"} else [b.text for e in s.entries if e.include for b in e.bullets if b.include]
        for piece in pieces:
            for number in re.findall(r"\d[\d,.]*\d|\d", piece):
                if number not in seen:
                    found.append(f"number {number}: {piece[:80]}")
            for word in re.findall(r"(?<=[a-z,;] )[A-Z][\w+#./-]+|\b[A-Z]{2,}[\w+#/-]*", piece):
                if word.lower().strip(".,") not in seen:
                    found.append(f"name {word}: {piece[:80]}")
            if re.search(r"https?://|\.com\b", piece):
                found.append(f"link: {piece[:80]}")
    page = doc_plain(tailored).lower()
    for term in ats.terms(posting["description"], posting["title"], []):
        if ats.matches(term["pattern"], page) and not ats.matches(term["pattern"], seen):
            found.append(f"posting term {term['term']} isn't in their history")
    if posting["title"].lower() in page and posting["title"].lower() not in seen:
        found.append(f"calls them a {posting['title']}")
    if posting["company"].lower() in page:
        found.append(f"names {posting['company']}")
    old = {e.id: e for s in source.sections for e in s.entries}
    for s in tailored.sections:
        for e in s.entries:
            if e.id not in old:
                found.append(f"new entry {e.heading}")
            elif (e.heading, e.subheading, e.dates) != (old[e.id].heading, old[e.id].subheading, old[e.id].dates):
                found.append(f"changed {e.heading}")
    return found


def hostile(posting):
    """Every lie that tailoring tends to tell: what the posting wants, stated as fact, in every field."""
    terms = [t["term"] for t in ats.terms(posting["description"], posting["title"], [])]

    async def model(output_type, system, user):
        data = json.loads(user)
        if output_type is normal.NormalPlan:
            rows = data["bullets"]
            lies = []
            for i, b in enumerate(rows):
                t = b["text"]
                kind = i % 5
                if kind == 0:
                    lies.append({"id": b["id"], "text": f"{t[:-25]} using {terms[0] if terms else 'Kubernetes'}"[: b["max_chars"]]})
                elif kind == 1:
                    lies.append({"id": b["id"], "text": f"{t[:-12]}, up 37%"[: b["max_chars"]]})
                elif kind == 2:
                    lies.append({"id": b["id"], "text": f"Led a global team of 40 at {posting['company']} on {posting['title']} initiatives across every region"[: b["max_chars"]]})
                elif kind == 3:
                    lies.append({"id": b["id"], "text": t + " and a lot more besides that"})
                else:
                    lies.append({"id": b["id"], "text": t[: len(t) // 2]})
            return normal.NormalPlan(
                summary=f"{posting['title']} with 15 years of {', '.join(terms[:4])} at {posting['company']}.",
                bullets=[normal.PlanBullet(**x) for x in lies],
                skills_to_add=[*terms, "Quantum Computing", "COBOL", "Kubernetes"],
            )
        if output_type is normal.BulletRepair:
            return normal.BulletRepair(bullets=[normal.PlanBullet(id=b["id"], text=b["source"][:-14] + " with COBOL") for b in data["bullets"]])
        if output_type is normal.SummaryRepair:
            return normal.SummaryRepair(summary=f"Seasoned {posting['title']} who shipped COBOL at 12 companies.")
        return output_type()

    return model


def run_one(posting, model, tmp_path):
    doc = RESUMES[posting["resume"]]
    profile = profile_of(doc)
    inv = build(profile, doc_plain(doc))
    terms = ats.terms(posting["description"], posting["title"], supported_terms(inv))
    evidence_text = " ".join([doc_plain(doc), inv.vocabulary])
    source = modes.SourceResume(id="r", sha="s", text=doc_plain(doc), doc=doc, style=editor.Style())
    ctx = modes.ModeContext(db=None, config=None, model=model, compile_fn=compile_pdf, work_dir=tmp_path, evidence=inv, ats_terms=terms, ats_source=ats.score(terms, doc_plain(doc), evidence_text))
    return doc, profile, ctx, asyncio.run(normal.run(posting, source, profile, ctx))


@pytest.mark.parametrize("posting", POSTINGS, ids=[f"{p['resume']}-{p['title']}" for p in POSTINGS])
def test_nothing_a_lying_model_says_reaches_the_resume(posting, tmp_path):
    doc, profile, ctx, result = run_one(posting, hostile(posting), tmp_path)
    pdf = result.pdf_path.read_bytes()
    report = check_rules(result.doc, doc, pdf=pdf, allowed_skills=[*ctx.evidence.skills, *result.allowed_skills], vocabulary=ctx.evidence.vocabulary)
    assert report["ok"], report["problems"]
    # claims.py, on every bullet against its own source.
    originals = {b.id: b.text for s in doc.sections for e in s.entries for b in e.bullets}
    for s in result.doc.sections:
        for e in s.entries:
            for b in e.bullets:
                if b.id in originals and b.text != originals[b.id]:
                    assert fits_window(b.text, originals[b.id])
                    bad = [p for p in claims.check(b.text, originals[b.id], e.heading + " " + " ".join(originals.values())) if p.startswith("unsupported")]
                    assert not bad, bad
    # The independent read.
    assert independent_read(doc, result.doc, profile, posting) == []
    # Keywords it can't back up are named, and left out.
    needed = [k["term"] for k in ctx.ats_source["keywords"] if k["status"] == "needed"]
    text = doc_plain(result.doc)
    assert not [t for t in needed if ats.matches(ats.pattern_for(t), text)]
    # Skills only ever grew, and only with theirs.
    before = {i.lower() for s in doc.sections if s.kind == "skills" for i in skill_items(s.text)}
    after = {i.lower() for s in result.doc.sections if s.kind == "skills" for i in skill_items(s.text)}
    assert before <= after
    assert not {"quantum computing", "cobol"} & after
    # Every posting still gets a summary, from their own words.
    assert any(s.title == "Summary" and s.text.strip() for s in result.doc.sections)


def test_the_independent_read_really_catches_each_lie():
    doc = RESUMES["analyst"].model_copy(deep=True)
    posting = POSTINGS[0]
    profile = profile_of(doc)
    assert independent_read(doc, doc, profile, posting) == []
    lied = doc.model_copy(deep=True)
    lied.sections[0].entries[0].bullets[0].text = "Built pipelines with Kubernetes at Northwind 0 for 37 teams"
    found = " ".join(independent_read(doc, lied, profile, posting))
    assert "37" in found and "Kubernetes" in found and "Northwind" in found
    lied = doc.model_copy(deep=True)
    lied.sections[0].entries[0].dates = "06/2010 - Present"
    assert "changed" in " ".join(independent_read(doc, lied, profile, posting))
