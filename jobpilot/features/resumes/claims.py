"""Unsupported-claim checks for rephrased resume text.

A rewrite may reorder and rephrase a source bullet for emphasis. It may not add
a number, tool, employer, credential or link that its source unit (and the
applicant's supported vocabulary) does not contain.
"""

import re

from .._store import norm

BANNED = (
    "leverage",
    "leveraged",
    "leveraging",
    "utilize",
    "utilized",
    "utilizing",
    "spearheaded",
    "robust",
    "seamless",
    "seamlessly",
    "cutting-edge",
    "state-of-the-art",
    "showcase",
    "delve",
    "pivotal",
    "comprehensive",
    "results-driven",
    "passionate",
    "synergy",
    "furthermore",
    "moreover",
)
FIRST_PERSON = re.compile(r"\b(?:i|me|my|we|our|us)\b", re.I)
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Tokens that look like a named tool, product, acronym or proper noun.
NAMED = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9+#.]*[A-Z0-9+#][A-Za-z0-9+#.]*|[A-Z][a-z]+(?:[A-Z][a-z]+)+|"
    r"[A-Za-z]+[0-9][A-Za-z0-9]*|[A-Z]{2,})\b"
)
URL = re.compile(r"https?://|www\.|\.com\b|\.io\b", re.I)
DASH = re.compile(r"[—–]|--")


def numbers(text) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUMBER.findall(text or "")}


def check(candidate: str, source: str, vocabulary: str = "", max_growth: float = 1.5) -> list[str]:
    """Return problems; empty means the rewrite is supported by its source."""
    problems = []
    text = (candidate or "").strip()
    if not text:
        return ["empty"]
    src_norm, vocab = norm(source), norm(vocabulary)
    new_numbers = numbers(text) - numbers(source)
    if new_numbers:
        problems.append("unsupported number: " + ", ".join(sorted(new_numbers)))
    named = {m.group(0) for m in NAMED.finditer(text)}
    # A capitalized word inside a sentence is a name, tool or product.
    named |= {m.group(1) for m in re.finditer(r"(?<![.!?:]\s)(?<!^)\b([A-Z][a-z][A-Za-z0-9+#.]*)", text)}
    first = text.split()[0] if text.split() else ""
    unsupported = sorted(t for t in named if norm(t) not in src_norm and norm(t) not in vocab and t != first)
    if unsupported:
        problems.append("unsupported name or tool: " + ", ".join(unsupported))
    if URL.search(text) and not URL.search(source):
        problems.append("link not in source")
    if DASH.search(text):
        problems.append("dash punctuation")
    if FIRST_PERSON.search(text):
        problems.append("first person")
    banned = [w for w in BANNED if re.search(rf"\b{re.escape(w)}\b", text, re.I)]
    if banned:
        problems.append("filler: " + ", ".join(banned))
    if len(text) > max(len(source) * max_growth, len(source) + 60):
        problems.append("longer than its source allows")
    return problems
