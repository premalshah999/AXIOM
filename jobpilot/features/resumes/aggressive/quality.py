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
# Names that hold a digit and state no result: GPT-4, Python 3, S3. Every other digit outside a target bracket counts as a figure.
# A tight rule can't tell an ordinary number from a result, so this errs on refusing: a refused bullet gets a repair, and then the
# original project keeps its place.
NAMES = re.compile(
    r"\b(?:Python|PHP|Perl|Ruby|Rust|Scala|Swift|Kotlin|Vue|Rails|Django|React|Spark|Hadoop|Redis|MySQL|HTTP|TLS|OAuth|SOC)[ /-]?\d(?:\.\d+)*\b"
    r"|\b(?:Java|Node|Node\.js|Angular|Postgres|PostgreSQL|Ubuntu|Windows|Fedora)[ -]?\d{1,2}(?:\.\d+)*\b"
    r"|\b(?i:gpt|llama|claude|gemini|mistral|phi|qwen|bert|yolo)[ -]?v?\d(?:\.\d+)?[a-z]?\b|\b(?i:resnet)[ -]?\d{2,3}\b"
    r"|\b[A-Z]{2,}[A-Za-z]*-\d[\w.]*"  # SHA-256, ISO-27001, COVID-19, UTF-8
    r"|\b[A-Z]-\d{1,2}\b"  # S-1, W-2, K-12
    # A letter leads the digit: S3, EC2, K8s, Neo4j, OAuth2, x86. "x10", "under200ms" and "recall95" are figures with letters in front.
    r"|\b(?![xX](?!86\b|64\b)\d)"
    r"(?!(?i:under|over|sub|top|upto|above|below|near|less|more|about|around|approx|min|max|recall|score|rate|cost|uptime|latency)\d)"
    r"[A-Za-z][A-Za-z+#]{0,6}\d[A-Za-z0-9]*\b"
    r"|\b(?:10-[KQ]|8-K)\b|\bISO \d{3,5}\b|\b(?:Microsoft|Office) 365\b"
)
FIGURE = re.compile(
    r"\d|[%$€£]"
    r"|\b(?:percent(?:age)?s?|pct|basis points?|bps|zero|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
    r"|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundreds?|thousands?|millions?|billions?|trillions?|dozens?"
    r"|twice|thrice|half|(?:two|three|four|five|six|seven|eight|nine|ten|twenty|hundred|thousand)-?fold"
    r"|doubl(?:e|es|ed|ing)(?![- ](?:count|entr|check))|tripl(?:e|es|ed|ing)|quadrupl(?:e|es|ed|ing))\b"
    r"|\b(?:one|two|three|four|five|six|seven|eight|nine)[ -](?:times|seconds?|minutes?|hours?|days?|weeks?|months?|years?)\b",
    re.I,
)
# Past tense says the work is done, wherever it sits in the bullet. A present-tense description, "Compares...", stays allowed.
COMPLETED = re.compile(
    r"\b(?:(?<!pre-)built(?!-in\b)|developed|deployed|shipped|launched|achieved|reduced|increased|improved|saved|delivered|created"
    r"|implemented|completed|finished|rolled out|migrated|optimi[sz]ed|streamlined|boosted|eliminated|exceeded|grew|won(?!')|earned|led"
    r"|spearheaded|architected|drove|wrote|ran|began|gave|took|analy[sz]ed|used(?! (?:cars?|vehicles?|goods|books?)\b)"
    r"|designed(?! (?:so|to|for|around)\b)|was|were|had|did|(?:has|have) (?:been|[a-z]+ed))\b",
    re.I,
)
# Any other verb in the past tense, by the "the" it acts on: "parsed the filings", though not "parsed filings" further in.
ACTED_ON = re.compile(r"\b[a-z]+(?<!e)(?<!mb)ed (?:a|an|the|each|every|all|its|their)\b", re.I)
OPENED_PAST = re.compile(r"^[A-Z][a-z]+(?<!e)(?<!mb)ed\b")
# What can open a description as a plain adjective: "Distributed rate limiter", though never "Distributed the limiter".
ADJECTIVES = frozenset(
    "aggregated annotated automated cached containerized curated distributed embedded federated guided labeled labelled layered "
    "localized managed normalized randomized scheduled segmented shared simulated stratified structured supervised synchronized "
    "tiered tokenized typed unified unstructured unsupervised versioned weighted".split()
)
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
        # "Will shipped" stays "Shipped", so the check still refuses it. Inflected, it would read "Shippeds" and pass.
        finished = COMPLETED.fullmatch(match[1]) or re.search(r"(?<!e)(?<!mb)ed$", match[1], re.I)
        word = match[1] if finished else third_person(match[1])
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


def stated_figure(text):
    """The first number or figure outside a [target: ...] bracket, if any. Names such as GPT-4 don't count."""
    found = FIGURE.search(NAMES.sub(" ", TARGET.sub(" ", text)))
    return found.group(0) if found else None


def claimed_work(text, opens_a_bullet=True):
    """The first word saying the work is already done, if any. All capitals is a name: "LED display"."""
    for found in COMPLETED.finditer(text):
        if not found.group(0).isupper():
            return found.group(0)
    if not opens_a_bullet:
        return None
    acted = ACTED_ON.search(text)
    if acted:
        return acted.group(0)
    opening = OPENED_PAST.match(text.strip())
    if opening and opening.group(0).lower() not in ADJECTIVES:
        return opening.group(0)
    return None


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
            p.bullets = p.bullets[: 2 if third else 3]
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


def miss(text, low, high):
    """How many characters a bullet is outside its window (0 inside it)."""
    n = len(text.strip())
    return n - high if n > high else low - n if n < low else 0


def apply_shape_fix(proposals, shapes, fix, history, posting):
    """Each bullet takes the first wording that lands in its window and breaks no truth check. Otherwise it takes the one nearest its
    window, when that is nearer than what it has: the next round's numbers start from there (models miscount; one round left
    every draft out on 2026-11-02)."""
    for item in sorted(fix.bullets, key=lambda x: (x.draft, x.bullet)):
        if item.draft >= min(len(proposals), len(shapes)):
            continue
        p, shape = proposals[item.draft], shapes[item.draft]
        if item.bullet >= len(shape) or item.bullet > len(p.bullets):
            continue
        low, high = shape[item.bullet]
        current = p.bullets[item.bullet] if item.bullet < len(p.bullets) else ""
        nearest = None
        for wording in item.wordings:
            text = tighten(describe(gates.sanitize(wording)), low, high)
            trial = p.model_copy(update={"bullets": [*p.bullets[: item.bullet], text, *p.bullets[item.bullet + 1 :]]})
            if proposal_problems(trial, history, posting):
                continue
            if low <= len(text) <= high:
                nearest = trial
                break
            if (current and miss(text, low, high) < miss(current, low, high)) and (
                nearest is None or miss(text, low, high) < miss(nearest.bullets[item.bullet], low, high)
            ):
                nearest = trial
        if nearest is not None:
            p.bullets = nearest.bullets


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
    if sum(len(TARGET.findall(b)) for b in p.bullets) > 1:
        problems.append(f"{p.title}: at most one headline metric")
    figure = next(filter(None, map(stated_figure, [p.title, *p.bullets])), None)
    if figure:
        problems.append(f'{p.title}: mark unmeasured results [target: ...], with no other number or figure ("{figure}")')
    claim = claimed_work(p.title, opens_a_bullet=False) or next(filter(None, map(claimed_work, p.bullets)), None)
    if claim:
        problems.append(f'{p.title}: proposed work must not claim completed work ("{claim}"), write in the present tense')
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
