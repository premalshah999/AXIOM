"""Evidence inventory: the only material a tailored resume may contain.

Units come from the applicant's structured profile (work, education, project
notes, skills) and, for roles without a description, from the bullets under
that employer in the uploaded source resume. Every unit carries a stable ID and
its source row so the rendered wording stays traceable.
"""

import re
from dataclasses import dataclass, field

from .._store import digest, norm
from ..knowledge.migration import row_id

BULLET = re.compile(r"^\s*(?:[•▪●◦‣*·\-–]\s+)(.+)$")
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


@dataclass(frozen=True)
class Unit:
    id: str
    kind: str  # experience | project | education
    source_row: str
    text: str
    order: int = 0


@dataclass(frozen=True)
class Role:
    row: str
    company: str
    title: str
    location: str
    start: str
    end: str
    current: bool


@dataclass(frozen=True)
class Project:
    row: str
    title: str


@dataclass
class Inventory:
    name: str
    contact: list[str]
    roles: list[Role]
    education: list[dict]
    projects: list[Project]
    units: list[Unit]
    skills: list[str]
    languages: list[str]
    vocabulary: str = ""  # all supported text, normalized, for claim validation
    by_id: dict = field(default_factory=dict)


def _bullets(text: str) -> list[str]:
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = BULLET.match(line)
        line = m.group(1) if m else line
        out += [s.strip() for s in SENTENCE.split(line) if len(s.strip()) >= 12]
    return out


def resume_bullets_for(resume_text: str, company: str) -> list[str]:
    """Bullets that follow the employer's line in the source resume."""
    lines = (resume_text or "").splitlines()
    want = norm(company)
    for i, line in enumerate(lines):
        if want and want in norm(line):
            found = []
            for nxt in lines[i + 1 : i + 14]:
                m = BULLET.match(nxt)
                if m:
                    found.append(m.group(1).strip())
                elif found and nxt.strip():
                    break
            return found
    return []


def build(profile: dict, resume_text: str = "") -> Inventory:
    contact = [
        profile.get(k, "")
        for k in ("email", "phone", "location", "linkedin", "github", "website")
        if profile.get(k)
    ]
    name = profile.get("name") or " ".join(
        x for x in (profile.get("first_name"), profile.get("last_name")) if x
    )
    units, roles, projects = [], [], []
    for row in profile.get("work") or []:
        rid = row_id("exp", row, ("company", "title", "start"))
        roles.append(
            Role(
                rid,
                row.get("company", ""),
                row.get("title", ""),
                row.get("location", ""),
                row.get("start", ""),
                row.get("end", ""),
                bool(row.get("current")),
            )
        )
        bullets = _bullets(row.get("description", "")) or resume_bullets_for(
            resume_text, row.get("company", "")
        )
        units += [Unit(f"{rid}:b{n}", "experience", rid, b, n) for n, b in enumerate(bullets)]
    education = []
    for row in profile.get("education") or []:
        rid = row_id("edu", row, ("school", "degree", "field_of_study", "start"))
        education.append({**row, "row": rid})
    for note in profile.get("evidence") or []:
        text = (note or {}).get("text", "")
        m = re.match(r"\s*project\s*[:\-]\s*([^.\n]+)[.\n]?\s*(.*)$", text, re.I | re.S)
        if not m:
            continue
        nid = note.get("id") or digest(text)[:10]
        projects.append(Project(nid, m.group(1).strip()))
        units += [Unit(f"{nid}:b{n}", "project", nid, b, n) for n, b in enumerate(_bullets(m.group(2)))]
    skills = [s for s in profile.get("skills") or [] if s]
    languages = [s for s in profile.get("languages") or [] if s]
    vocabulary = norm(
        " ".join(
            [u.text for u in units]
            + skills
            + languages
            + [f"{r.company} {r.title} {r.location}" for r in roles]
            + [" ".join(str(v) for v in e.values()) for e in education]
            + [p.title for p in projects]
            + [resume_text or ""]
        )
    )
    inv = Inventory(name, contact, roles, education, projects, units, skills, languages, vocabulary)
    inv.by_id = {u.id: u for u in units}
    return inv


# Fields that change the rendered resume. Saved screening answers do not, so
# answering a question never invalidates a validated artifact.
RESUME_FIELDS = (
    "name",
    "first_name",
    "last_name",
    "email",
    "phone",
    "location",
    "linkedin",
    "github",
    "website",
    "work",
    "education",
    "skills",
    "languages",
    "evidence",
)


def profile_digest(profile: dict) -> str:
    return digest({k: profile.get(k) for k in RESUME_FIELDS})[:20]
