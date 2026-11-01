"""Deterministic cleanup and quality gates, run in code so they hold whatever a model does (reference pipeline, Step 4).

They work on plain text, not LaTeX: the renderer escapes everything, so there's no brace or math mode left to check. Each gate says
which rule it comes from, and rules about the resume's own facts are warnings only, because the applicant's words aren't ours to change.
"""

import re

from .._store import norm

_CHARS = {
    "‘": "'", "’": "'", "“": '"', "”": '"', "‚": "'", "„": '"', "…": "...", "‑": "-",
    " ": " ", " ": " ", " ": " ", "​": "",
}
_SWAPS = {
    "state-of-the-art": "modern", "cutting-edge": "modern", "delving into": "examining", "delve into": "examine", "myriad of": "many",
    "leveraging": "using", "leveraged": "used", "leverage": "use", "utilizing": "using", "utilized": "used", "utilization": "use", "utilize": "use",
    "spearheading": "leading", "spearheaded": "led", "showcasing": "displaying", "showcased": "displayed", "showcase": "display",
    "seamlessly": "smoothly", "seamless": "smooth", "robust": "reliable", "comprehensive": "thorough", "intricate": "detailed",
    "furthermore": "also", "moreover": "also", "pivotal": "key", "myriad": "many", "realm": "area",
}
BANNED = (
    "leveraged", "utilized", "robust", "spearheaded", "orchestrated", "streamlined", "cross-functional", "results-driven", "comprehensive",
    "seamless", "cutting-edge", "passionate", "dynamic", "state-of-the-art", "delve", "showcase", "underscore", "pivotal", "meticulous",
    "landscape", "furthermore",
)
FIRST_PERSON = re.compile(r"\b(?:I|me|my|we|our|us)\b", re.I)
NOT_A_PRONOUN = re.compile(r"\bU\.?S\.?\b")
ROUND = re.compile(r"(?<!\d)(?:100|95|90|85|80|75|70|65|60|55|50|45|40|35|30|25|20|15|10|5)(?:\.0+)?%(?!\d)")
PRECISE = re.compile(r"\b\d+\.\d+%")
ANY_METRIC = re.compile(r"\d[\d,.]*\s*(?:%|x\b|ms\b|s\b|sec|min|hours?|hrs?|k\b|K\b|M\b|million|billion|users?|customers?|records?|\$)|\$\s?\d")
DATE_ANY = re.compile(r"\b(?:\d{1,2}/\d{4}|[A-Z][a-z]{2,8}\.? \d{4}|\d{4}-\d{2})\b")
DATE_GOOD = re.compile(r"\d{2}/\d{4}")
# Vendor spellings that begin in lower case (reference 3c, rule 1).
VENDOR_LOWER = frozenset(
    "pandas scikit-learn spaCy matplotlib seaborn ggplot2 dbt npm pytest nginx jQuery iOS macOS eBPF pip conda vLex numpy".split()
)
DATASET = re.compile(r"\b(?:dataset|data set|kaggle|corpus)\b|\bAPI data\b", re.I)
def _dashes(text: str) -> str:
    text = re.sub(r"\s*---\s*", ", ", text)
    text = re.sub(r"\s*—\s*", ", ", text)
    text = re.sub(r"(?<=\d)\s*–\s*(?=\d)", "-", text)
    text = re.sub(r"\s*–\s*", ", ", text)
    return re.sub(r"(?<!\d)\s*--\s*(?!\d)", ", ", text)


def _case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    return replacement[:1].upper() + replacement[1:] if original[:1].isupper() else replacement


def sanitize(text: str) -> str:
    """Punctuation and filler tells out, for sure, in code and not by asking a model (reference 4c)."""
    if not text:
        return text
    for src, dst in _CHARS.items():
        text = text.replace(src, dst)
    text = _dashes(text)
    for word in sorted(_SWAPS, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(word)}\b", lambda m, r=_SWAPS[word]: _case(m.group(0), r), text, flags=re.I)
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*,+", ",", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r",\s*\)", ")", text)
    return re.sub(r"\(\s*,\s*", "(", text).strip()


def first_person(text: str) -> set:
    return {m.group(0).lower() for m in FIRST_PERSON.finditer(NOT_A_PRONOUN.sub("", text or "")) if m.group(0) != "US"}


def skill_problem(item: str, requirements_text: str = "") -> str:
    """Why this can't be added as a skill (reference 3b): it's a dataset, or a phrase copied from the posting, not a tool or method."""
    if DATASET.search(item):
        return "a dataset is an input, not a skill"
    if len(item.split()) >= 3 and norm(item) in norm(requirements_text):
        return "copied from the posting"
    return ""


def skill_case(item: str) -> str:
    """Title Case with the vendor's spelling winning (reference 3c). Only ever applied to a skill being added."""
    if item in VENDOR_LOWER or any(c.isupper() for c in item[1:]) or any(ch in item for ch in "+#./"):
        return item
    small = {"and", "or", "of", "for", "to", "with"}
    words = re.split(r"(\s+|-)", item)
    return "".join(w if w in {" ", "-"} or (i and w in small) else w[:1].upper() + w[1:] for i, w in enumerate(words))


def _tense_mixed(bullets: list[str]) -> bool:
    openers = [b.split()[0].lower() for b in bullets if b.split()]
    past = sum(1 for w in openers if w.endswith("ed") or w in {"built", "led", "ran", "wrote", "made", "chose", "cut", "drew", "held", "won", "set"})
    present = sum(1 for w in openers if w.endswith(("ing",)) or (w.endswith("s") and not w.endswith("ss")))
    return past > 0 and present > 0


def quality(doc, source=None) -> dict:
    """Reference 4a on the whole document: tier 0 clean, tier 1 warnings, tier 2 critical. Facts the applicant wrote only warn."""
    critical: list[str] = []
    warnings: list[str] = []
    projects, all_bullets = [], []
    for s in doc.sections:
        if not s.include:
            continue
        for e in s.entries:
            if not e.include:
                continue
            bullets = [b.text for b in e.bullets if b.include and b.text.strip()]
            all_bullets += bullets
            if s.kind == "projects":
                projects.append((e.heading, bullets))
            if bullets and _tense_mixed(bullets):
                warnings.append(f'Mixed tense in "{e.heading[:40]}"')
    for text in all_bullets:
        pronouns = first_person(text)
        if pronouns:
            critical.append(f"First person in a bullet: {', '.join(sorted(pronouns))}")
        filler = [w for w in BANNED if re.search(rf"\b{re.escape(w)}\b", text, re.I)]
        if filler:
            warnings.append("Filler word: " + ", ".join(filler))
    for heading, bullets in projects:
        metrics = [b for b in bullets if ANY_METRIC.search(b)]
        if len(metrics) > 1:
            warnings.append(f'More than one headline metric in "{heading[:40]}"')
    rounds = sum(len(ROUND.findall(b)) for b in all_bullets)
    precise = sum(len(PRECISE.findall(b)) for b in all_bullets)
    if precise >= 4 and precise >= len(all_bullets) * 0.6:
        warnings.append("A fake-precise decimal on most lines reads as generated; these come from your source, so check they're real")
    if rounds >= 4 and rounds >= len(all_bullets) * 0.6:
        warnings.append("Round percentages on most lines; vary how results are stated if you can")
    openers = [b.split()[0].lower() for b in all_bullets if b.split()]
    repeated = sorted({w for w in openers if openers.count(w) >= 4})
    if repeated:
        warnings.append("Many bullets open with the same verb: " + ", ".join(repeated))
    dates = [e.dates for s in doc.sections if s.include for e in s.entries if e.include and e.dates.strip()]
    found = [d for text in dates for d in DATE_ANY.findall(text)]
    if found and not all(DATE_GOOD.fullmatch(d) for d in found) and any(DATE_GOOD.fullmatch(d) for d in found):
        warnings.append("Your source mixes date formats, which can confuse a parser: " + ", ".join(sorted(set(found))[:4]))
    return {"tier": 2 if critical else 1 if warnings else 0, "critical": sorted(set(critical)), "warning": sorted(set(warnings))}
