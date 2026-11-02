"""20 synthetic postings on normal resumes, against a model trying its best to lie and one that slips the way real ones do. Zero invented claims may survive, by claims.py and by a read
that shares no code with it, and the slips must not cost the honest edits."""

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


def own_words(term: str, seen: str) -> bool:
    """Every word of a posting's term is in the source in some form: the same opening letters, for words long enough to have any. Apart from normal.described on purpose, and a product's name must be there whole."""
    words = re.findall(r"[a-z]+", term.lower())
    if term != term.lower() and (any(w[1:] != w[1:].lower() for w in term.split()) or len(term.split()) == 1):
        return False
    return all(re.search(r"\b" + w[: max(4, len(w) - 5)], seen) for w in words if len(w) > 3 or w not in {"of", "and", "for"})


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
        if ats.matches(term["pattern"], page) and not ats.matches(term["pattern"], seen) and not term.get("common") and not own_words(term["term"], seen):
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


def _trim(text: str, until: int) -> str:
    """How a model pays for a term: little words first, last ones first, then words that say little. It never cuts a figure or a name, so it can stay over."""
    while len(text) > until:
        hits = list(normal._SMALL_WORD.finditer(text))
        if not hits:
            break
        text = text[: hits[-1].start()] + text[hits[-1].end() :]
    words = text.split()
    for i in range(len(words) - 1, 1, -1):
        if len(" ".join(words)) <= until:
            break
        if words[i] in {"during", "also", "very", "really", "just", "short", "small", "new", "every", "each", "about", "average", "annual", "weekly", "monthly"}:
            del words[i]
    return " ".join(words)


def _worked_in(text: str, say_as: list[str], lead_with: list[str]) -> str:
    """A line as a fast model rewrites it: the job's term near the start, the words it replaces gone, every figure and name kept, and usually too long."""
    first, rest = text.split(" ", 1)
    if say_as:
        term = say_as[0]
        roots = {normal.stem(w) for w in re.findall(r"[a-z]+", term)}
        kept = []
        for word in rest.split():
            root = normal.stem(re.sub(r"\W", "", word))
            if root in roots and word == word.lower():
                roots.discard(root)
                continue
            kept.append(word)
        if normal.stem(first) in roots:
            return f"Handled {term} {' '.join(kept)}"
        return f"For {term}, {first.lower()} {' '.join(kept)}"
    for term in lead_with:
        found = re.search(ats.pattern_for(term), rest, re.I | re.A)
        if found:
            before = re.sub(r"\b(?:in|with|using|on|and|via)\s*$", "", rest[: found.start()])
            body = re.sub(r"\s+([,;.])", r"\1", re.sub(r"\s{2,}", " ", f"{before}{rest[found.end() :]}")).strip()
            return f"Using {found.group(0)}, {first.lower()} {body}"
    return text


def sloppy(posting):
    """A real model on a real resume: mostly honest, near its character limits rather than inside them, and now and then it reaches for something the line doesn't say."""
    turn = sum(map(ord, posting["title"]))  # Which line slips differs by posting, because most postings offer only a line or two.
    seen = {}

    def lie(row, i, data):
        other = [t for t in data["summary_terms"] if t not in row["say_as"] + row["lead_with"]]
        wanted = data["do_not_use"][:1] or ["Kubernetes"]
        says = (f" with {wanted[0]}", ", up 37%", f" using {other[0]}" if other else " using COBOL", f" at {posting['company']}")[i % 4]
        return row["text"][: row["max_chars"] - len(says)].rstrip(" ,.;") + says

    async def model(output_type, system, user):
        data = json.loads(user)
        if output_type is normal.NormalPlan:
            lines = []
            for i, row in enumerate(data["bullets"]):
                text = _worked_in(row["text"], row["say_as"], row["lead_with"])
                slip = (i + turn) % 6
                if slip == 4:
                    text = lie(row, turn, data)
                elif slip == 2:
                    text = _trim(text, row["min_chars"] - 3)  # Cut too hard.
                else:
                    text = _trim(text, row["max_chars"] + (3, 9, 0, -2, 0, 18)[slip])
                lines.append(normal.PlanBullet(id=row["id"], text=text))
            title = seen["title"] = data["roles"][0].split(" at ")[0]
            terms = data["summary_terms"][:3]
            evidence = (data["bullets"] and data["bullets"][0]["text"]) or (data["also_on_resume"] or [""])[0]
            summary = (f"{title} working with {', '.join(terms)}. " if terms else f"{title}. ") + _trim(evidence, 150).rstrip(".") + "."
            if turn % 3 == 0:
                summary = summary.replace(title, posting["title"], 1)  # It flatters them with the job's own title.
            return normal.NormalPlan(summary=summary, bullets=lines, skills_to_add=[*terms, "Kubernetes"])
        if output_type is normal.BulletRepair:
            lines = []
            for i, row in enumerate(data["bullets"]):
                source, why = row["source"], row["refused_because"]
                say = [t for t in row["may_use"] if not ats.matches(ats.pattern_for(t), source)]
                if (i + turn) % 7 == 6:
                    continue  # It gives up on some, and their originals stay.
                if "cut at least" in why:
                    text = _trim(row["attempt"], row["max_chars"] + (2 if (i + turn) % 4 == 3 else -1))  # One in four is still a little over.
                elif (i + turn) % 5 == 4:
                    text = source[:-14] + " with COBOL"  # It lies again, and that's the end of it.
                else:
                    text = _trim(_worked_in(source, say, row["may_use"]), row["max_chars"] - 1)
                lines.append(normal.PlanBullet(id=row["id"], text=text))
            return normal.BulletRepair(bullets=lines)
        if output_type is normal.SummaryRepair:
            return normal.SummaryRepair(summary=f"{seen['title']} with experience in {', '.join(data['summary_terms'][:2]) or 'this field'}.")
        return output_type()

    return model


def run_one(posting, model, tmp_path):
    doc = RESUMES[posting["resume"]]
    profile = profile_of(doc)
    inv = build(profile, doc_plain(doc))
    terms = ats.terms(posting["description"], posting["title"], supported_terms(inv), company=posting["company"])
    evidence_text = " ".join([doc_plain(doc), inv.vocabulary])
    source = modes.SourceResume(id="r", sha="s", text=doc_plain(doc), doc=doc, style=editor.Style())
    ctx = modes.ModeContext(db=None, config=None, model=model, compile_fn=compile_pdf, work_dir=tmp_path, evidence=inv, ats_terms=terms, ats_source=ats.score(terms, doc_plain(doc), evidence_text))
    return doc, profile, ctx, asyncio.run(normal.run(posting, source, profile, ctx))


def assert_truthful(posting, doc, profile, ctx, result):
    pdf = result.pdf_path.read_bytes()
    report = check_rules(result.doc, doc, pdf=pdf, allowed_skills=[*ctx.evidence.skills, *result.allowed_skills], vocabulary=ctx.evidence.vocabulary)
    assert report["ok"], report["problems"]
    # claims.py, on every bullet against its own source.
    originals = {b.id: b.text for s in doc.sections for e in s.entries for b in e.bullets}
    for s in result.doc.sections:
        for e in s.entries:
            own = e.heading + " " + " ".join(b.text for b in doc_entry(doc, e.id).bullets) if e.id in {x.id for sec in doc.sections for x in sec.entries} else e.heading
            for b in e.bullets:
                if b.id in originals and b.text != originals[b.id]:
                    assert fits_window(b.text, originals[b.id])
                    bad = [p for p in claims.check(b.text, originals[b.id], own) if p.startswith("unsupported")]
                    assert not bad, bad
                    assert not claims.dropped(b.text, originals[b.id])
    # The independent read.
    assert independent_read(doc, result.doc, profile, posting) == []
    # Keywords it can't back up are named, and left out.
    seen = re.sub(r"\s+", " ", doc_plain(doc)).lower()
    needed = [k["term"] for k in ctx.ats_source["keywords"] if k["status"] == "needed" and not k.get("common") and not own_words(k["term"], seen)]
    text = doc_plain(result.doc)
    assert not [t for t in needed if ats.matches(ats.pattern_for(t), text)]
    # Skills only ever grew, and only with theirs.
    before = {i.lower() for s in doc.sections if s.kind == "skills" for i in skill_items(s.text)}
    after = {i.lower() for s in result.doc.sections if s.kind == "skills" for i in skill_items(s.text)}
    assert before <= after
    assert not {"quantum computing", "cobol", "kubernetes"} & (after - before)
    # Every posting still gets a summary, from their own words.
    assert any(s.title == "Summary" and s.text.strip() for s in result.doc.sections)


def doc_entry(doc, entry_id):
    return next(e for s in doc.sections for e in s.entries if e.id == entry_id)


@pytest.mark.parametrize("posting", POSTINGS, ids=[f"{p['resume']}-{p['title']}" for p in POSTINGS])
def test_nothing_a_lying_model_says_reaches_the_resume(posting, tmp_path):
    doc, profile, ctx, result = run_one(posting, hostile(posting), tmp_path)
    assert_truthful(posting, doc, profile, ctx, result)


def test_a_model_that_misses_limits_by_a_little_still_lands_its_honest_edits(tmp_path):
    """The real failure wasn't lying. It was edits that never landed. Every line here is offered as a real model would answer it: the term worked in, a length near the limit, a lie now and then."""
    offered = changed = risen = 0
    for posting in POSTINGS:
        doc, profile, ctx, result = run_one(posting, sloppy(posting), tmp_path)
        assert_truthful(posting, doc, profile, ctx, result)
        offered += result.report["offered_bullets"]
        changed += result.report["changed_bullets"]
        before = ctx.ats_source["score"]
        after = ats.score(ctx.ats_terms, doc_plain(result.doc), " ".join([doc_plain(doc), ctx.evidence.vocabulary]))["score"]
        assert after >= before
        risen += after > before
    assert offered >= 12, offered  # What these short postings can honestly offer on these resumes. If it falls, bullets stopped being offered.
    assert changed >= offered * 0.6, (changed, offered)
    assert risen >= 5, risen


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
