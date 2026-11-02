"""Reference-specific gates. Resume invariants stay in the shared rules module."""

import re
from collections import Counter

from ..._store import norm
from .. import ats, claims, gates
from ..editor import Section
from ..normal import refuse_reason
from ..rules import bullet_window, live_bullets, live_entries, skill_items

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


# Words that say what kind of thing a data source is, not which one it is.
SOURCE_FILLER = frozenset(
    "a an and api archive archives by corpus data database dataset datasets export exports feed files for from in "
    "of on open portal public record records repository set sets the via".split()
)


def names_input(bullet, data_source):
    """Whether a bullet names the project's input, however the sentence words it. The real model writes "FCC broadband
    records" for "FCC Broadband Data Collection records": half of the source's distinctive words is the same source."""
    if contains(bullet, data_source):
        return True
    words = [w for w in re.findall(r"[A-Za-z0-9][\w+.-]*", data_source) if w.casefold() not in SOURCE_FILLER]
    return bool(words) and 2 * sum(contains(bullet, w) for w in words) >= len(words)


def in_posting(posting, tool):
    """The posting asks for this tool or method, in that phrase or in all of its words."""
    if contains(posting, tool):
        return True
    words = re.findall(r"[A-Za-z0-9][\w+.-]*", tool)
    return bool(words) and all(contains(posting, w) for w in words)


# A draft's figure is an inspired value, and says so on the resume itself: the employer sees the PDF, not the app's notes.
TARGET = re.compile(r"\[target:[^\]]{1,90}\]", re.I)
FUTURE = re.compile(r"\b(?:will|would|should|shall)\s+(?:also\s+)?([a-z]+)", re.I)
COMMAND = re.compile(
    r"^(Add|Analy[sz]e|Benchmark|Build|Check|Collect|Compare|Create|Design|Develop|Document|Evaluate|Implement|Make|Map|Measure|"
    r"Model|Parse|Run|Test|Train|Track|Use|Validate|Write)\b"
)
IRREGULAR = {"be": "is", "have": "has", "do": "does", "go": "goes"}


def third_person(verb):
    if verb.lower() in IRREGULAR:
        said = IRREGULAR[verb.lower()]
    elif re.search(r"(?:s|x|z|ch|sh)$", verb, re.I):
        said = verb + "es"
    elif re.search(r"[^aeiou]y$", verb, re.I):
        said = verb[:-1] + "ies"
    else:
        said = verb + "s"
    return said.capitalize() if verb[:1].isupper() else said


def describe(bullet):
    """A draft reads as a description of the project, not a to-do list: "Will build X" and "Build X" say "Builds X". The prompt
    asks for this; a bullet that still arrives as an instruction is settled here. It never makes a draft claim finished work."""

    def said(match):
        opening = match.start() == 0 or bullet[: match.start()].rstrip()[-1:] in ".:;"
        word = third_person(match[1])
        return word.capitalize() if opening else word

    text = FUTURE.sub(said, bullet.strip())
    return COMMAND.sub(lambda m: third_person(m[1]), text)


# A plan holds up to three drafts of up to eight bullets (schema.py).
MOST_DRAFTS, MOST_BULLETS = 3, 8
# Roughly what the default 2000-token reply holds. A fix that would run past it asks for fewer wordings, then fewer bullets.
FIX_REPLY_CHARS = 5000
# Shorter words that say the same thing: a bullet a few characters over needs no model, and no draft is lost for it.
TIGHTER = (
    (re.compile(r"\.$"), ""),
    (re.compile(r",(?= and\b| then\b| so\b)"), ""),
    (re.compile(r"\bin order to\b", re.I), "to"),
    (re.compile(r"\bso that\b", re.I), "so"),
    (re.compile(r"\bas well as\b", re.I), "and"),
    (re.compile(r"\bwithout having to\b", re.I), "without"),
)


def project_slots(doc):
    """The resume's own projects a draft can take the place of, in order. A project with no bullets has no size to keep."""
    slots = [
        e
        for s in doc.sections
        if s.include and s.kind == "projects"
        for e in live_entries(s)
        if 1 <= len(live_bullets(e)) <= MOST_BULLETS
    ]
    return slots[:MOST_DRAFTS]


def project_shapes(doc):
    """Each bullet's allowed (fewest, most) characters in those projects: a draft takes one's place, at its size."""
    return [[bullet_window(b.text) for b in live_bullets(e)] for e in project_slots(doc)]


def shape_problems(p, shape):
    """The owner's rule for every bullet: the same count as the project it replaces, and the same length or up to 10% shorter."""
    if len(p.bullets) != len(shape):
        return [f"{p.title}: has {len(p.bullets)} bullets, write exactly {len(shape)}, like the project it replaces"]
    problems = []
    for i, (b, (low, high)) in enumerate(zip(p.bullets, shape)):
        n = len(b.strip())
        if n > high:
            problems.append(f"{p.title}: bullet {i + 1} has {n} characters, cut {n - high} to {n - low} so it has {low} to {high}")
        elif n < low:
            problems.append(f"{p.title}: bullet {i + 1} has {n} characters, add {low - n} to {high - n} so it has {low} to {high}")
    return problems


def shape_issues(proposals, shapes):
    return [problem for p, shape in zip(proposals, shapes) for problem in shape_problems(p, shape)]


def tighten(text, low, high):
    """The same bullet a little shorter when that lands in its window. Otherwise it is left for the repair, word for word."""
    shorter = text.strip()
    for pattern, said in TIGHTER:
        if len(shorter) <= high:
            break
        shorter = pattern.sub(said, shorter)
    return shorter if low <= len(shorter) <= high else text


def normalize_proposals(proposals, shapes=()):
    """Shape is arithmetic, so code settles it. Order by proof point, give each proposal its own, keep one third bullet, and
    word each bullet as a description. A resume with its own projects sets the count instead, and no more drafts than it has."""
    for p in proposals:
        p.bullets = [describe(b) for b in p.bullets]
    if shapes:
        ordered = sorted(proposals, key=lambda p: p.proof_point)[: len(shapes)]
        for i, (p, shape) in enumerate(zip(ordered, shapes)):
            p.proof_point = min(i, 2)
            p.bullets = [tighten(b, *window) for b, window in zip(p.bullets, shape)]
        return ordered
    ordered = sorted(proposals, key=lambda p: p.proof_point)
    free = [i for i in range(len(ordered)) if i not in {p.proof_point for p in ordered}]
    seen = set()
    third = False
    for p in ordered:
        if p.proof_point in seen and free:
            p.proof_point = free.pop(0)
        seen.add(p.proof_point)
        if len(p.bullets) > 2:
            if third:
                p.bullets = p.bullets[:2]
            third = True
    return sorted(ordered, key=lambda p: p.proof_point)


def shape_request(proposals, shapes, history, posting):
    """The short request for one bounded fix: only the bullets that miss, each with its exact numbers. A draft that already
    fails a truth check is going to be left out, so nothing is spent on it."""
    wanted = []
    for d, (p, shape) in enumerate(zip(proposals, shapes)):
        if proposal_problems(p, history, posting):
            continue
        for i, (low, high) in enumerate(shape):
            text = p.bullets[i].strip() if i < len(p.bullets) else ""
            n = len(text)
            if low <= n <= high:
                continue
            change = f"cut {n - high} to {n - low} characters" if n > high else f"add {low - n} to {high - n} characters"
            wanted.append(
                {"draft": d, "title": p.title, "data_source": p.data_source, "tools": p.tools, "bullet": i, "text": text,
                 "chars": n, "min_chars": low, "max_chars": high, "aim_chars": (low + high) // 2, "change": change}
            )
    room = 0
    fits = []
    for item in wanted:
        if room + item["max_chars"] > FIX_REPLY_CHARS:
            break
        room += item["max_chars"]
        fits.append(item)
    return {"bullets": fits, "wordings_per_bullet": max(1, min(3, FIX_REPLY_CHARS // max(room, 1)))}


def apply_shape_fix(proposals, shapes, fix, history, posting):
    """Each bullet takes the first wording that lands in its window and breaks no truth check. Otherwise it stays as it was."""
    for item in sorted(fix.bullets, key=lambda x: (x.draft, x.bullet)):
        if item.draft >= min(len(proposals), len(shapes)):
            continue
        p, shape = proposals[item.draft], shapes[item.draft]
        if item.bullet >= len(shape) or item.bullet > len(p.bullets):
            continue
        low, high = shape[item.bullet]
        for wording in item.wordings:
            text = tighten(describe(gates.sanitize(wording)), low, high)
            trial = p.model_copy(update={"bullets": [*p.bullets[: item.bullet], text, *p.bullets[item.bullet + 1 :]]})
            if low <= len(text) <= high and not proposal_problems(trial, history, posting):
                p.bullets = trial.bullets
                break


def proposal_problems(p, history, posting):
    """What is wrong with one proposal. The truth checks live here: a draft failing any of them never reaches a resume."""
    problems = []
    inputs = Counter(norm(d["data_source"]) for app in history for d in app)
    if p.descriptor not in DESCRIPTORS and not re.fullmatch(r"[A-Za-z]+", p.descriptor):
        problems.append(f"{p.title}: descriptor must state provenance or one domain word")
    if not any(in_posting(posting, t) for t in p.tools):
        problems.append(f"{p.title}: name an actual posting tool or method")
    if not any(names_input(b, p.data_source) for b in p.bullets):
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
    if any(gates.ANY_METRIC.search(TARGET.sub("", b)) for b in p.bullets):
        problems.append(f"{p.title}: mark unmeasured results [target: ...]")
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


def proposal_issues(proposals, history, posting, shapes=()):
    problems = []
    if len({p.archetype for p in proposals}) != len(proposals):
        problems.append("Choose distinct project archetypes")
    if shapes:
        if len(proposals) < len(shapes):
            problems.append(f"Write {len(shapes)} proposals, one for each item of project_shapes")
        problems += shape_issues(proposals, shapes)
    else:
        if len(proposals) != 3:
            problems.append("Choose three distinct project archetypes")
        if [p.proof_point for p in proposals] != [0, 1, 2]:
            problems.append("Order proposals by proof point 0, 1, 2, exactly once each")
        counts = [len(p.bullets) for p in proposals]
        if sum(counts) not in (6, 7) or counts.count(3) > 1:
            problems.append("Use 2,2,2 or 2,2,3 proposal bullets")
    for p in proposals:
        problems += proposal_problems(p, history, posting)
    return problems


def keep_sound(proposals, history, posting, shapes=()):
    """After the repairs: the drafts that pass every check, and why the others were left out. A failed draft never reaches a
    resume, and it no longer costs the person the rest of the tailoring (every real-model run failed that way, 2026-11-01)."""
    kept, dropped, archetypes = [], [], set()
    for i, p in enumerate(proposals):
        problems = proposal_problems(p, history, posting)
        if i < len(shapes):
            problems += shape_problems(p, shapes[i])
        if p.archetype in archetypes:
            problems.append(f"{p.title}: repeats another draft's archetype")
        if problems:
            dropped.append({"title": p.title, "problems": problems})
        else:
            archetypes.add(p.archetype)
            kept.append(p)
    return kept, dropped


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
