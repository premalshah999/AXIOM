"""Answer patterns: text."""

import re
from ..answer_context import CACHE_VERSION
from ..answer_context import digest
from ..answer_context import question_identity
from ..answer_context import semantic_text
from ..knowledge import relevant_fact
from .intents import INTENTS


def normalize(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def strip_catalog_code(text):
    """An option's name without the catalog code an employer's list appends: "Computer Science (00123)"."""
    return re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", str(text))


def answer_key(field):
    return CACHE_VERSION + ":" + digest(question_identity(field))


def option_value(value, options):
    from ..widgets import best_option, placeholder_option

    if placeholder_option(value):
        return None
    if not options:
        return str(value)
    options = [o for o in options if not placeholder_option(o)]
    if not options:
        return None
    matches = [x for x in options if semantic_text(x) == semantic_text(str(value))]
    if len(set(matches)) == 1:
        # One choice listed twice is one choice to a person (ADP's "Cell" in its light and rendered lists, 2026-10-28).
        return matches[0]
    if matches:
        return None
    return best_option(options, str(value))


def first_matching(options, patterns):
    for pattern in patterns:
        if hit := next((o for o in options if pattern.search(str(o).strip())), None):
            return hit
    return None


def yes_no(value):
    if isinstance(value, bool):
        return "Yes" if value else "No"
    text = str(value).strip()
    if text.lower() in {"true", "yes", "y"}:
        return "Yes"
    if text.lower() in {"false", "no", "n"}:
        return "No"
    return text


def normalize_label_text(label):
    return re.sub(r"\s+", " ", re.sub(r"[*:✱]+", " ", str(label or ""))).strip()


def boolean_detail(field):
    """A numeric or explanatory follow-up cannot be answered by a willingness boolean."""
    return (
        field.get("type") in {"text", "textarea", "number", "contenteditable", "url"}
        and not field.get("options")
        and (
            field.get("type") == "number"
            or bool(
                re.search(
                    r"\b(?:how long|duration|length|reason|why|explain|describe|details|amount|percentage)\b",
                    field.get("label", ""),
                    re.I,
                )
            )
        )
    )


JOB_SEARCH_BOX = re.compile(r"search(?: for)?\s+(?:jobs?|titles?|keywords?|roles?|positions?)\b", re.I)


def job_catalog_search_box(field):
    """A job board's own search box (Eightfold: "Search for job title or keywords"), not an application question.

    Only optional text boxes qualify; a required field always goes to the answer path.
    """
    # Eightfold's search is a role=combobox whose suggestions are job titles.
    if field.get("required") or field.get("type") not in {"text", "search", "combobox"}:
        return False
    return any(
        JOB_SEARCH_BOX.match(str(field.get(key) or "").strip())
        for key in ("label", "placeholder", "aria_label")
    )


def jev_fact_keys(label, facts, limit=8):
    """The few saved facts a pick-one question is about: subject rules, intents, then name overlap."""
    keys = [
        k for k in facts if k.startswith(("fact:", "profile:")) and relevant_fact(label, k.split(":", 1)[1])
    ]
    for pattern, names in INTENTS:
        if pattern.search(label):
            keys += [k for k in ("fact:" + n for n in names) if k in facts]
    words = {w[:5] for w in re.findall(r"[a-z]{4,}", label.lower())} - {
        "would",
        "pleas",
        "which",
        "about",
        "curre",
    }
    for k in facts:
        if k.startswith("fact:") and not k.startswith("fact:kb_"):
            parts = {w[:5] for w in re.findall(r"[a-z]{4,}", k[5:].lower())}
            if parts and len(parts & words) >= min(2, len(parts)):
                keys.append(k)
    return list(dict.fromkeys(k for k in keys if facts.get(k) not in (None, "", [], {})))[:limit]
