"""Reference-specific gates. Resume invariants stay in the shared rules module."""

import re
from collections import Counter

from ..._store import norm
from .. import ats, claims, gates
from ..editor import Section
from ..normal import refuse_reason
from ..rules import live_bullets, live_entries, skill_items

DESCRIPTORS = {"Personal project", "Open source", "Internal tool", "Research"}
VENDORS = {
    x.casefold(): x
    for x in "pandas|scikit-learn|spaCy|matplotlib|seaborn|ggplot2|dbt|npm|pytest|nginx|jQuery|iOS|macOS|eBPF|NumPy|PyTorch|TensorFlow|FastAPI|OpenAPI|MySQL|PostgreSQL|GitHub Actions".split(
        "|"
    )
}
VENDORS.update(
    {x.casefold(): x for x in "SQL API APIs UAT SDLC CI/CD ETL RAG LLM AWS NLP GAAP HIPAA".split()}
)
VENDORS.update({x.casefold(): x for x in ats.KNOWN if any(c.isupper() for c in x[1:])})


def casing(value):
    return VENDORS.get(value.casefold(), gates.skill_case(value))


def literal_match(text, token):
    pattern = re.escape(token).replace(r"\ ", r"[\s\-]+")
    return re.search(r"(?<!\w)" + pattern + r"(?!\w)", text, re.I)


def contains(text, token):
    return bool(literal_match(text, token))


def coverage(text, keywords):
    missing = [kw for kw in keywords if not contains(text, kw)]
    score = (len(keywords) - len(missing)) / len(keywords) if keywords else None
    return {
        "keyword_coverage": score,
        "pillars_missing": missing,
        "advisory": True,
        "warning": "Below 60% of hard requirements; keep real gaps instead of stuffing."
        if score is not None and score < 0.6
        else "",
    }


def proposal_issues(proposals, history, hard_text):
    problems = []
    if len({p.archetype for p in proposals}) != 3:
        problems.append("Choose three distinct project archetypes")
    if [p.proof_point for p in proposals] != [0, 1, 2]:
        problems.append("Order proposals by proof point 0, 1, 2, exactly once each")
    counts = [len(p.bullets) for p in proposals]
    if sum(counts) not in (6, 7) or counts.count(3) > 1:
        problems.append("Use 2,2,2 or 2,2,3 proposal bullets")
    inputs = Counter(norm(p["data_source"]) for app in history for p in app)
    for p in proposals:
        if p.descriptor not in DESCRIPTORS and not re.fullmatch(r"[A-Za-z]+", p.descriptor):
            problems.append(f"{p.title}: descriptor must state provenance or one domain word")
        if not any(contains(hard_text, t) for t in p.tools):
            problems.append(f"{p.title}: name an actual posting tool or method")
        if not any(contains(b, p.data_source) for b in p.bullets):
            problems.append(f"{p.title}: name its real public input in a bullet")
        combined = " ".join([p.title, *p.bullets, *p.build_plan])
        if claims.URL.search(combined):
            problems.append(f"{p.title}: remove proposed links")
        if gates.first_person(combined):
            problems.append(f"{p.title}: remove first person")
        if any(re.search(r"\b" + re.escape(w) + r"\b", combined, re.I) for w in gates.BANNED):
            problems.append(f"{p.title}: remove filler")
        if re.search(r"independent deploys|team velocity|onboarding friction", combined, re.I):
            problems.append(f"{p.title}: a solo project needs solo obstacles")
        if sum(bool(gates.ANY_METRIC.search(b)) for b in p.bullets) > 1:
            problems.append(f"{p.title}: at most one headline metric")
        if any(gates.ANY_METRIC.search(b) and "[measure after building]" not in b for b in p.bullets):
            problems.append(f"{p.title}: mark unmeasured results [measure after building]")
        if any(re.match(r"Built|Deployed|Achieved|Reduced|Shipped|Saved", b) for b in p.bullets):
            problems.append(f"{p.title}: proposed work must not claim completed work")
        if inputs[norm(p.data_source)] >= 3 and any(
            norm(d["data_source"]) == norm(p.data_source)
            and (d["archetype"] == p.archetype or d["metric_shape"] == p.metric_shape)
            for app in history
            for d in app
        ):
            problems.append(f"{p.title}: repeated input + archetype + metric shape in the last 15 jobs")
    return problems


def inventory(source, profile, inv):
    """Base skills survive even when orphaned. Only demonstrated additions join."""
    base = [v for s in source.sections if s.include and s.kind == "skills" for v in skill_items(s.text)]
    evidence = " ".join(
        [
            e.heading + " " + e.subheading + " " + " ".join(b.text for b in live_bullets(e))
            for s in source.sections
            if s.include and s.kind in {"experience", "education", "projects"}
            for e in live_entries(s)
        ]
    )
    evidence += (
        " "
        + " ".join(u.text for u in inv.units)
        + " "
        + " ".join(str(v) for e in inv.education for v in e.values())
    )
    candidates = [*inv.skills, *(profile.get("skills") or []), *ats.KNOWN]
    extra = [c for c in candidates if contains(evidence, c) and norm(c) not in {norm(x) for x in base}]
    return base, list(dict.fromkeys(extra))


def apply_revision(source, revision, allowed, hard_text, forbidden):
    """Return a complete preserved document plus refused edits for one repair call."""
    doc = source.model_copy(deep=True)
    refused = []
    index = {
        b.id: (e, b)
        for s in doc.sections
        if s.include and s.kind == "projects"
        for e in live_entries(s)
        for b in live_bullets(e)
    }
    seen = set()
    for edit in revision.bullets:
        if edit.id not in index or edit.id in seen:
            refused.append({"id": edit.id, "reason": "Unknown, duplicate or immutable bullet"})
            continue
        seen.add(edit.id)
        e, b = index[edit.id]
        text = gates.sanitize(edit.text)
        # Own-bullet vocabulary is stricter than sharing tools across projects.
        reason = refuse_reason(text, b.text, b.text, forbidden)
        if reason:
            refused.append({"id": edit.id, "reason": reason})
        else:
            b.source_text, b.text = b.text, text
    order = revision.project_order
    originals = [e.id for s in doc.sections if s.include and s.kind == "projects" for e in live_entries(s)]
    if order and (len(order) != len(set(order)) or set(order) != set(originals)):
        refused.append({"id": "project_order", "reason": "Keep every original project exactly once"})
    elif order:
        rank = {v: i for i, v in enumerate(order)}
        for s in doc.sections:
            if s.kind == "projects":
                s.entries.sort(key=lambda e: rank.get(e.id, len(rank)))
    base = [v for s in source.sections if s.include and s.kind == "skills" for v in skill_items(s.text)]
    permitted = {norm(x): x for x in [*base, *allowed]}
    items, lines = set(), []
    for cat in revision.categories:
        held = []
        for raw in cat.skills:
            key = norm(raw)
            if key not in permitted:
                refused.append({"id": "skills", "reason": f"Unsupported skill: {raw}"})
                continue
            # A bad base item still survives; it was supplied by the applicant.
            why = gates.skill_problem(raw, hard_text) if key not in {norm(x) for x in base} else ""
            if why:
                refused.append({"id": "skills", "reason": f"{raw}: {why}"})
                continue
            if key not in items:
                held.append(casing(permitted[key]))
                items.add(key)
        if held:
            lines.append(casing(cat.label).replace(":", "") + ": " + ", ".join(held))
    # A model omission can never delete a held skill.
    missing = [casing(x) for x in base if norm(x) not in items]
    if missing:
        lines.append("Additional Skills: " + ", ".join(missing))
    sections = [s for s in doc.sections if s.include and s.kind == "skills"]
    if revision.categories and lines:
        if sections:
            # Preserve skills split across several source sections, too.
            sections[0].text = "\n".join(lines)
            for s in sections[1:]:
                s.text = next(old.text for old in source.sections if old.id == s.id)
        else:
            doc.sections.append(
                Section(id="aggressive-skills", title="Skills", kind="skills", text="\n".join(lines))
            )
    return doc, refused
