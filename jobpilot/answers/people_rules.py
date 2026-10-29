"""Resolver methods: the supervisor of a work-history row, and references.

Both come only from what the applicant saved in Knowledge › Supervisors and references (facts `work_supervisors`,
keyed by work-history row, and `references`, in order). A field with nothing saved is not answered here: it stays a
question for the applicant, never a guess. Supervisor boxes in work row N use row N's supervisor.
"""

import re

from ..schemas import Answer
from .text import normalize

SUPERVISOR = re.compile(
    r"supervisor|\bsup(?:name|title|phone|email|contact)\b|(?:reporting|direct|immediate) manager|"
    r"may we contact (?:your )?(?:current |present |previous |former )?employer|employer contact permission",
    re.I,
)
REFERENCE = re.compile(r"\breference\b|\bref ?\d|\breferee\b", re.I)
GROUP_ROW = re.compile(
    r"(?:work experience|experience|employment(?: history)?|work history|employer|job|position)\s*#?\s*(\d+)\b", re.I
)
PREVIOUS = re.compile(r"\b(?:prev(?:ious)?|prior|former|past|last)\b", re.I)


def part_of(label):
    """What a supervisor/reference box asks for: name, first, last, title, phone, email, company, relationship,
    name_title or may_contact."""
    text = str(label or "").lower()
    if re.search(r"\bmay we\b|\bcan we\b|permission|okay to|\bok to|allow us|authori[sz]e us|contact (?:your|this)", text):
        return "may_contact" if re.search(r"contact", text) else None
    if re.search(r"e-?mail", text):
        return "email"
    if re.search(r"phone|telephone|mobile|\bcell\b|supphone", text):
        return "phone"
    if re.search(r"name\s*(?:&|and|/)\s*title|title\s*(?:&|and|/)\s*name", text):
        return "name_title"
    if re.search(r"first name|given name|firstname", text):
        return "first"
    if re.search(r"last name|surname|family name|lastname", text):
        return "last"
    if re.search(r"relationship|how do you know|how you know", text):
        return "relationship"
    if re.search(r"company|employer|organi[sz]ation", text) and not re.search(r"supervisor", text):
        return "company"
    if re.search(r"title|position|suptitle", text):
        return "title"
    if re.search(r"name|supname", text):
        return "name"
    return None


class PeopleRulesMixin:
    def people_answer(self, f):
        label = str(f.get("own_label") or f.get("label") or "")
        if f.get("type") == "file":
            return None
        if REFERENCE.search(label) and not SUPERVISOR.search(label):
            return self.reference_answer(f, label)
        if SUPERVISOR.search(label):
            return self.supervisor_answer(f, label)
        return None

    def _contact_value(self, f, entry, part, evidence, label):
        """The saved entry's value for one box, or None."""
        if part in {"first", "last"}:
            name = str(entry.get("name") or "").split()
            if not name:
                return None
            value = name[0] if part == "first" else " ".join(name[1:])
            return self._ans(f, value, evidence) if value else None
        if part == "name_title":
            name, title = entry.get("name"), entry.get("title")
            return self._ans(f, f"{name}, {title}", evidence) if name and title else None
        if part == "may_contact":
            choice = entry.get("may_contact")
            if not choice:
                return None
            return self._contact_consent(f, choice, evidence)
        value = entry.get(part)
        return self._ans(f, value, evidence) if value else None

    def _contact_consent(self, f, choice, evidence):
        options = [o for o in f.get("options") or [] if str(o).strip()]
        if f.get("type") == "checkbox":
            return Answer(
                field_id=f["id"],
                value="true" if choice == "yes" else "false",
                disposition="answer",
                evidence_ids=evidence,
            )
        if choice == "after_offer":
            later = next((o for o in options if re.search(r"after|upon|offer|conditional|later", str(o), re.I)), None)
            if later:
                return self._ans(f, later, evidence)
            # Not before an offer: the truthful answer to a plain Yes/No is No; free text says so.
            return self._ans(f, "No" if options else "After an offer only", evidence)
        return self._ans(f, "Yes" if choice == "yes" else "No", evidence)

    def work_row_index(self, f, label):
        """The work-history row a supervisor box belongs to, or None when the form does not say."""
        work = self.profile.work
        if not work:
            return None
        row = f.get("history_row") or {}
        if row.get("kind") == "work" and isinstance(row.get("index"), int):
            return row["index"] if row["index"] < len(work) else None
        group = " ".join(str(f.get(k) or "") for k in ("group", "section", "context") if isinstance(f.get(k), str))
        if m := GROUP_ROW.search(group):
            index = int(m[1]) - 1
            return index if 0 <= index < len(work) else None
        if re.search(r"education|school", group, re.I) or PREVIOUS.search(label):
            return None
        # A form without repeating rows asks about the current or most recent job (the same convention as _single_entry).
        return next((i for i, w in enumerate(work) if w.current), 0)

    def supervisor_answer(self, f, label):
        part = part_of(label)
        saved = (self.profile.facts or {}).get("work_supervisors")
        if not part or not isinstance(saved, dict) or not saved:
            return None
        index = self.work_row_index(f, label)
        if index is None:
            return None
        from ..features.knowledge.questionnaires import work_row_id

        entry = saved.get(work_row_id(self.profile.work[index].model_dump()))
        if not isinstance(entry, dict) or not entry:
            return None
        return self._contact_value(f, entry, part, ["fact:work_supervisors", f"profile:work.{index}"], label)

    def reference_answer(self, f, label):
        saved = [r for r in ((self.profile.facts or {}).get("references") or []) if isinstance(r, dict) and r]
        part = part_of(label)
        if not saved or not part or part == "may_contact":
            return None
        group = " ".join(str(f.get(k) or "") for k in ("group", "section") if isinstance(f.get(k), str))
        number = re.search(r"\bref(?:erence)?\s*#?\s*(\d)", normalize(label) + " " + group.lower(), re.I) or re.search(
            r"(?:reference|referee|ref)\D{0,3}(\d)", label + " " + group, re.I
        )
        if number:
            index = int(number[1]) - 1
        elif len(saved) == 1:
            index = 0  # A single saved reference cannot be confused with another.
        else:
            return None  # Which of several references a box belongs to is unknown; ask rather than repeat one.
        if not 0 <= index < len(saved):
            return None
        return self._contact_value(f, saved[index], part, ["fact:references"], label)
