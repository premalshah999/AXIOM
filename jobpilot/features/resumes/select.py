"""Deterministic evidence selection for a job description.

Keywords are the applicant's own supported terms (skills and tools that appear
in their evidence) that the posting also mentions, weighted double inside the
requirements block. Keyword coverage is advisory; it never adds a claim.
"""

import re
from dataclasses import dataclass

from .._store import norm
from .evidence import Inventory

REQ_HEAD = re.compile(
    r"^\s*(?:requirements|qualifications|minimum qualifications|basic qualifications|required|"
    r"what you(?:'| wi)ll need|what we(?:'re| are) looking for|you have|who you are|must have)\b",
    re.I | re.M,
)
NEXT_HEAD = re.compile(
    r"^\s*(?:preferred|nice to have|bonus|benefits|about us|compensation|perks|what we offer)\b", re.I | re.M
)
TERM = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*(?:\s[A-Z][A-Za-z0-9+#.]*)?")


def requirements_block(description: str) -> str:
    m = REQ_HEAD.search(description or "")
    if not m:
        return ""
    rest = description[m.end() :]
    stop = NEXT_HEAD.search(rest)
    return rest[: stop.start()] if stop else rest[:4000]


def contains(text_norm: str, term: str) -> bool:
    t = norm(term)
    return bool(t) and re.search(r"(?<![\w+#])" + re.escape(t) + r"(?![\w+#])", text_norm) is not None


def supported_terms(inv: Inventory) -> list[str]:
    terms = list(dict.fromkeys(inv.skills + inv.languages))
    # Tool-like tokens the applicant actually wrote in their own evidence.
    for unit in inv.units:
        for token in re.findall(
            r"\b[A-Z][A-Za-z0-9+#.]*[A-Z0-9+#.][A-Za-z0-9+#.]*\b|\b[A-Z][a-z]+[A-Z]\w*\b", unit.text
        ):
            if len(token) >= 2 and token not in terms:
                terms.append(token)
    return terms


@dataclass
class Keywords:
    required: list[str]
    other: list[str]

    def weight(self, text: str) -> float:
        t = norm(text)
        return 2.0 * sum(contains(t, k) for k in self.required) + sum(contains(t, k) for k in self.other)

    @property
    def all(self) -> list[str]:
        return self.required + self.other


def keywords(inv: Inventory, description: str) -> Keywords:
    req, full = norm(requirements_block(description)), norm(description)
    terms = supported_terms(inv)
    required = [t for t in terms if contains(req, t)]
    other = [t for t in terms if t not in required and contains(full, t)]
    return Keywords(required, other)


@dataclass
class Selection:
    roles: dict  # role row -> [Unit]
    projects: dict  # project row -> [Unit]
    skills: list[str]
    keywords: Keywords
    optional: list[str]  # unit ids that may be dropped to fit the page, least relevant first


def select(
    inv: Inventory, description: str, *, max_projects=3, bullets_recent=4, bullets_other=3
) -> Selection:
    kw = keywords(inv, description)
    score = {u.id: kw.weight(u.text) - 0.01 * u.order for u in inv.units}
    roles, optional = {}, []
    for i, role in enumerate(inv.roles):
        units = [u for u in inv.units if u.source_row == role.row]
        units.sort(key=lambda u: -score[u.id])
        keep = units[: bullets_recent if i == 0 else bullets_other]
        # Keep the applicant's own order inside a role for readability.
        roles[role.row] = sorted(keep, key=lambda u: u.order)
        optional += [u.id for u in keep[1:]]  # every role keeps at least its best bullet
    project_rank = []
    for p in inv.projects:
        units = [u for u in inv.units if u.source_row == p.row]
        project_rank.append((sum(score[u.id] for u in units), p, units))
    project_rank.sort(key=lambda x: -x[0])
    projects = {}
    for _total, p, units in project_rank[:max_projects]:
        projects[p.row] = sorted(units[:3], key=lambda u: u.order)
        optional += [u.id for u in units[1:3]]
    optional.sort(key=lambda uid: score.get(uid, 0))
    t = norm(description)
    skills = sorted(inv.skills, key=lambda s: (not contains(t, s), inv.skills.index(s)))
    return Selection(roles, projects, skills, kw, optional)
