"""The owner's rules for every tailored resume, in every mode (2026-11-01). One function, checked before a PDF can be approved or sent.

    Each rewritten bullet: the same character count as its source or up to 10% shorter, never longer.
    The same number of bullets per experience and per project as the source.
    No education, skill or experience entry dropped. Skills may be added, never removed; an added skill must be the applicant's own.
    Name 16 pt, section headings 10.5, sub-headings 10, bullets 9, dates in italics: read from the compiled PDF, not the LaTeX.
    Nothing the applicant hasn't confirmed reaches the PDF: an unconfirmed draft's title or bullets are a failure wherever they appear.

Projects, which the owner's "never remove" doesn't name: a source project stays, with its own bullet count, unless it's in `removed_projects`: the
applicant removed it on the review screen, or an Aggressive draft of the same size takes its place until they switch back. A project never drops out by itself.

A page overflow is a warning and never a failure: the rules forbid dropping content to fit, so the app says so plainly instead.
"""

import math
import re
from typing import Iterable

from .._store import norm
from . import claims
from .editor import Entry, ResumeDoc
from .validate import check_typography, squash

LENGTH_FLOOR = 0.9
SUMMARY_TITLE = re.compile(r"^(?:professional\s+)?(?:summary|profile|objective)\b", re.I)
TRUTH = ("unsupported number", "unsupported name or tool", "link not in source")
# Longer than this and a shared phrase is a copy, not a coincidence.
LEAK_CHARS = 28
SLOT = re.compile(r"\[[^\]\n]{0,60}\]")


def bullet_window(source_text: str) -> tuple[int, int]:
    """(fewest, most) characters a rewrite of this bullet may have."""
    n = len(source_text.strip())
    return math.ceil(n * LENGTH_FLOOR), n


def fits_window(text: str, source_text: str) -> bool:
    low, high = bullet_window(source_text)
    return low <= len(text.strip()) <= high


def skill_items(text: str) -> list[str]:
    """Every item of a skills section, one line per category: `Label: a, b, c`."""
    items = []
    for line in (text or "").splitlines():
        body = line.split(":", 1)[1] if ":" in line and len(line.split(":", 1)[0]) <= 40 else line
        items += [x.strip() for x in body.split(",") if x.strip()]
    return items


def live_entries(section) -> list[Entry]:
    return [e for e in section.entries if e.include]


def live_bullets(entry: Entry) -> list:
    return [b for b in entry.bullets if b.include and b.text.strip()]


def doc_plain(doc: ResumeDoc) -> str:
    """The resume's text as a parser would read it. The ATS score reads the same text, in the browser too."""
    parts = [doc.name, *doc.contact]
    for s in doc.sections:
        if not s.include:
            continue
        parts += [s.title, s.text]
        for e in live_entries(s):
            parts += [e.heading, e.subheading, e.dates, e.location]
            parts += [b.text for b in live_bullets(e)]
    return "\n".join(p for p in parts if p)


def draft_leaks(text: str, drafts: Iterable) -> list[str]:
    """THE GUARD: no draft a mode marked unconfirmed may be in a resume that's approved or sent. Every check calls this one function (check_rules, choice.send_problem, choice.approve).

    Returns the titles of unconfirmed drafts whose title or any bullet appears in this text. A draft is {"title", "bullets", "confirmed"} or the same as attributes.
    """
    flat = squash(text)
    leaks = []
    for d in drafts or []:
        get = d.get if isinstance(d, dict) else lambda k, default=None, d=d: getattr(d, k, default)
        if get("confirmed", False):
            continue
        title = get("title", "") or ""
        needles = [squash(title)] if len(squash(title)) >= 6 else []
        for bullet in get("bullets", []) or []:
            # A slot is where a real result goes, so compare the longest stretch the draft actually wrote.
            pieces = sorted((squash(p) for p in SLOT.split(bullet)), key=len, reverse=True)
            if pieces and len(pieces[0]) >= LEAK_CHARS:
                needles.append(pieces[0][:80])
        if any(n in flat for n in needles):
            leaks.append(title or "an unconfirmed draft")
    return leaks


def check_rules(
    tailored: ResumeDoc,
    source: ResumeDoc,
    *,
    pdf: bytes | None = None,
    allowed_skills: Iterable[str] = (),
    added_projects: Iterable[Entry] = (),
    removed_projects: Iterable[str] = (),
    unconfirmed: Iterable = (),
    vocabulary: str = "",
    edited: Iterable[str] = (),
    pages_limit: int = 1,
) -> dict:
    """{"ok", "problems": [{code, where, message}], "warnings": [...], "typography"}. `ok` False means this can't be approved or sent.

    allowed_skills:   skills an added item may be: the applicant's real ones and those of confirmed projects.
    added_projects:   entries that may join Projects (confirmed by the applicant), holding the bullets they confirmed.
    removed_projects: ids the applicant chose to take off this resume.
    unconfirmed:      drafts that must not appear anywhere.
    edited:           bullet and section ids the applicant typed themselves. A number or name they added is their own statement, so it warns.
    """
    problems: list[dict] = []
    warnings: list[dict] = []

    def fail(code, where, message):
        problems.append({"code": code, "where": where, "message": message})

    def warn(code, where, message):
        warnings.append({"code": code, "where": where, "message": message})

    edited = set(edited)
    removed = set(removed_projects)
    joined = {e.id: e for e in added_projects}
    now_sections = {s.id: s for s in tailored.sections if s.include}
    by_kind: dict[str, list] = {}
    for s in tailored.sections:
        if s.include:
            by_kind.setdefault(s.kind, []).append(s)

    if norm(tailored.name) != norm(source.name):
        fail("name_changed", "name", "Your name must stay as it is on your resume.")
    for item in source.contact:
        if item not in tailored.contact:
            fail("contact_changed", "contact", f'Contact detail removed: "{item}".')

    def compare_entry(new: Entry, old: Entry, label: str, vocab: str):
        olds, news = live_bullets(old), live_bullets(new)
        if len(olds) != len(news):
            fail("bullet_count", new.id, f'"{label}" has {len(news)} bullets; your source has {len(olds)}. Keep the same number.')
        by_id = {b.id: b for b in olds}
        for b in news:
            original = by_id.get(b.id)
            if original is None:
                fail("unknown_bullet", b.id, f'A bullet under "{label}" has no source.')
                continue
            if b.text == original.text:
                continue
            low, high = bullet_window(original.text)
            n = len(b.text.strip())
            if n > high:
                fail("bullet_too_long", b.id, f'A bullet under "{label}" has {n} characters; it can have at most {high}.')
            elif n < low:
                fail("bullet_too_short", b.id, f'A bullet under "{label}" has {n} characters; it needs at least {low}.')
            found = claims.check(b.text, original.text, vocab + " " + label, max_growth=1.0)
            for problem in found:
                if problem.startswith(TRUTH):
                    if b.id in edited:
                        warn("your_statement", b.id, f'Not in your source, so keep it only if true ({problem}): "{b.text[:70]}"')
                    else:
                        fail("unsupported_claim", b.id, f'{problem}: "{b.text[:70]}"')

    for s in source.sections:
        if not s.include:
            continue
        new_section = now_sections.get(s.id) or next(
            (x for x in by_kind.get(s.kind, []) if norm(x.title) == norm(s.title)), None
        )
        if s.kind in {"experience", "education", "projects"}:
            if new_section is None:
                if s.kind != "projects" or not {e.id for e in live_entries(s)} <= removed:
                    fail("section_dropped", s.id, f'The "{s.title}" section is missing.')
                continue
            entries = {e.id: e for e in live_entries(new_section)}
            for old in live_entries(s):
                new = entries.get(old.id)
                label = old.heading or old.subheading or old.id
                if new is None:
                    if s.kind == "projects" and old.id in removed:
                        continue
                    kind = {"experience": "experience", "education": "education", "projects": "project"}[s.kind]
                    fail("entry_dropped", old.id, f'The {kind} "{label}" is missing. Nothing may be dropped.')
                    continue
                for field in ("heading", "subheading", "dates", "location"):
                    if (getattr(new, field) or "").strip() != (getattr(old, field) or "").strip():
                        fail("entry_changed", old.id, f'The {field} of "{label}" changed. Titles, employers, schools and dates stay as written.')
                vocab = " ".join(
                    [old.heading, old.subheading, old.location, *(b.text for b in live_bullets(old))]
                )
                compare_entry(new, old, label, vocab)
            if s.kind == "projects":
                known = {e.id for e in live_entries(s)}
                for new in live_entries(new_section):
                    if new.id in known:
                        continue
                    if new.id in joined:
                        compare_entry(new, joined[new.id], new.heading, " ".join(b.text for b in live_bullets(joined[new.id])))
                    else:
                        fail("unknown_project", new.id, f'The project "{new.heading}" isn\'t in your source and hasn\'t been confirmed by you.')
        elif s.kind == "skills":
            wanted = skill_items(s.text)
            now = skill_items(new_section.text) if new_section else []
            have = {norm(x) for x in now}
            lost = [x for x in wanted if norm(x) not in have]
            if lost:
                fail("skill_removed", s.id, "Skills removed: " + ", ".join(lost[:8]) + ". Skills may be added, never removed.")
            ok = {norm(x) for x in allowed_skills} | {norm(x) for x in wanted}
            added = [x for x in now if norm(x) not in ok]
            if added:
                fail("unsupported_skill", s.id, "Not in your history: " + ", ".join(added[:8]) + ". An added skill has to be one you really have.")
        elif not SUMMARY_TITLE.match(s.title):
            if new_section is None or norm(new_section.text) != norm(s.text):
                fail("section_changed", s.id, f'The "{s.title}" section must stay as written.')
    source_ids = {s.id for s in source.sections}
    source_titles = {norm(s.title) for s in source.sections}
    source_kinds = {s.kind for s in source.sections if s.include}
    whole_source = doc_plain(source) + " " + vocabulary
    for s in tailored.sections:
        if not s.include or s.id in source_ids or norm(s.title) in source_titles:
            if s.include and s.kind == "text" and SUMMARY_TITLE.match(s.title):
                _check_summary(s, whole_source, warn if s.id in edited else fail)
            continue
        if s.kind == "text" and SUMMARY_TITLE.match(s.title):
            _check_summary(s, whole_source, warn if s.id in edited else fail)
        elif s.kind == "skills" and "skills" not in source_kinds:
            ok = {norm(x) for x in allowed_skills}
            added = [x for x in skill_items(s.text) if norm(x) not in ok]
            if added:
                fail("unsupported_skill", s.id, "Not in your history: " + ", ".join(added[:8]) + ".")
        elif s.kind == "projects" and "projects" not in source_kinds:
            for new in live_entries(s):
                if new.id in joined:
                    compare_entry(new, joined[new.id], new.heading, " ".join(b.text for b in live_bullets(joined[new.id])))
                else:
                    fail("unknown_project", new.id, f'The project "{new.heading}" isn\'t in your source and hasn\'t been confirmed by you.')
        else:
            fail("unknown_section", s.id, f'The "{s.title}" section isn\'t in your source.')
    for title in draft_leaks(doc_plain(tailored), unconfirmed):
        fail("unconfirmed_draft", title, f'"{title}" is a draft you haven\'t confirmed, so it can\'t be on a resume.')
    typography = None
    pages = None
    if pdf is not None:
        typography = check_typography(pdf, tailored)
        for problem in typography["problems"]:
            fail("typography", "pdf", problem)
        from .validate import parse_check

        parsed = parse_check(pdf, tailored)
        pages = parsed["pages"]
        for title in draft_leaks(parsed["text"], unconfirmed):
            fail("unconfirmed_draft", title, f'"{title}" is a draft you haven\'t confirmed, so it can\'t be in the PDF.')
        if pages > pages_limit:
            warn("overflow", "pdf", f"This resume runs to {pages} pages, because nothing may be dropped to fit. Shorten something if you want one page.")
    return {"ok": not problems, "problems": problems[:60], "warnings": warnings[:40], "typography": typography, "pages": pages}


def _check_summary(section, whole_source: str, report):
    """`report` is fail for what a mode wrote, and warn for what the applicant typed: their own statement, which only they can vouch for."""
    if section.text.strip():
        for problem in claims.check(section.text, whole_source, "", max_growth=1000):
            if problem.startswith(TRUTH):
                report("unsupported_claim", section.id, f"{problem}: in the summary. Keep it only if it's true.")
