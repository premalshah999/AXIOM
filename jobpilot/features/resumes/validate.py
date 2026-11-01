"""PDF checks with pypdf: page count, readable extraction, contact in the body,
standard headings, ToUnicode maps, no replacement characters or em dashes, and
every selected bullet present in the extracted text."""

import io
import re

from pypdf import PdfReader

from .._store import norm
from .latex import plain

HEADINGS = ("EXPERIENCE", "EDUCATION", "SKILLS")


def squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", norm(text))


def check_pdf(
    pdf: bytes, *, name: str, email: str, bullets: list[str], keywords: list[str], max_pages=1, projects=False
) -> dict:
    reader = PdfReader(io.BytesIO(pdf))
    pages = len(reader.pages)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    flat = squash(text)
    problems, warnings = [], []
    if pages > max_pages:
        problems.append(f"{pages} pages (limit {max_pages})")
    head = squash(text[:400])
    if name and squash(name) not in head:
        problems.append("name not in the first lines of extracted text")
    if email and squash(email) not in head:
        problems.append("email not in the first lines of extracted text")
    for heading in HEADINGS + (("PROJECTS",) if projects else ()):
        if heading.lower() not in norm(text):
            problems.append(f"heading missing: {heading.title()}")
    if "�" in text:
        problems.append("replacement characters in extracted text")
    if "—" in text:
        problems.append("em dash in extracted text")
    missing = [b for b in bullets if squash(plain(b))[:40] not in flat]
    if missing:
        problems.append(f"{len(missing)} selected bullet(s) not extractable")
    fonts_without_unicode = set()
    for page in reader.pages:
        fonts = (page.get("/Resources") or {}).get("/Font") or {}
        for ref in fonts.values():
            font = ref.get_object()
            if "/ToUnicode" not in font and font.get("/Subtype") != "/Type3":
                fonts_without_unicode.add(str(font.get("/BaseFont")))
    if fonts_without_unicode:
        warnings.append("fonts without ToUnicode: " + ", ".join(sorted(fonts_without_unicode)))
    found = [k for k in keywords if squash(k) and squash(k) in flat]
    return {
        "ok": not problems,
        "pages": pages,
        "problems": problems,
        "warnings": warnings,
        "keyword_coverage": round(len(found) / len(keywords), 3) if keywords else None,
        "keywords_found": found,
        "keywords_missing": [k for k in keywords if k not in found],
        "text": text,
    }


# ---------- Reading the compiled PDF back: the sizes it actually prints ----------
def font_runs(pdf: bytes) -> list[dict]:
    """Every run of text with its real size and font, read from the PDF's own drawing operators (what `pdffonts` can't tell you)."""
    out = []
    for number, page in enumerate(PdfReader(io.BytesIO(pdf)).pages):

        def visit(text, cm, tm, font, size, number=number):
            if text and text.strip():
                out.append(
                    {
                        "page": number,
                        "text": text.strip(),
                        "size": round(size * abs(tm[3]) * abs(cm[3]), 2),
                        "font": str((font or {}).get("/BaseFont", "")),
                    }
                )

        page.extract_text(visitor_text=visit)
    return out


ITALIC = re.compile(r"ital|obli", re.I)
SIZE_TOLERANCE = 0.06  # pdfTeX rounds a Tf operand; anything further off is a different size.


def _in_order(doc) -> list[tuple[str, str]]:
    """(kind, squashed text) for everything on the page, top to bottom: the order a one-column PDF draws it."""
    seq = [("name", squash(doc.name)), ("body", squash(" ".join(doc.contact)))]
    for s in doc.sections:
        if not s.include:
            continue
        entries = [e for e in s.entries if e.include]
        if s.kind in {"skills", "text"} and not s.text.strip():
            continue
        if s.kind not in {"skills", "text"} and not entries:
            continue
        seq.append(("section", squash(s.title)))
        if s.kind in {"skills", "text"}:
            seq.append(("body", squash(plain(s.text))))
        for e in entries:
            seq += [("sub", squash(x)) for x in (e.heading, e.subheading) if x.strip()]
            if e.dates.strip():
                seq.append(("dates", squash(e.dates)))
            if e.location.strip():
                seq.append(("body", squash(e.location)))
            seq += [("bullet", squash(plain(b.text))) for b in e.bullets if b.include and b.text.strip()]
    return [x for x in seq if x[1]]


def check_typography(pdf: bytes, doc) -> dict:
    """The owner's type rules on the compiled file: name 16, section headings 10.5, sub-headings 10, bullets 9, dates in italics.

    Each run of text is matched at the exact spot the document says comes next, so a summary line that says "Data Analyst" isn't taken for the job title below it."""
    from .editor import TAILORED_TYPE

    sizes = {"name": TAILORED_TYPE["name"], "section": TAILORED_TYPE["section"], "sub": TAILORED_TYPE["sub"], "bullet": TAILORED_TYPE["bullet"]}
    problems: list[str] = []
    seen: dict[str, set] = {"name": set(), "section": set(), "sub": set(), "bullet": set(), "dates": set()}
    seq = _in_order(doc)
    at, used = 0, 0
    found_dates = 0
    for run in font_runs(pdf):
        t = squash(run["text"])
        if not t:
            continue
        for j in range(at, min(at + 8, len(seq))):
            kind, text = seq[j]
            start = used if j == at else 0
            if text.startswith(t, start):
                at, used = j, start + len(t)
                if kind == "dates":
                    seen["dates"].add(run["size"])
                    found_dates += 1
                    if not ITALIC.search(run["font"]):
                        problems.append(f'dates "{run["text"][:30]}" are not in italics ({run["font"]})')
                elif kind != "body":
                    seen[kind].add(run["size"])
                    if abs(run["size"] - sizes[kind]) > SIZE_TOLERANCE:
                        problems.append(f'{kind} "{run["text"][:40]}" prints at {run["size"]} pt, not {sizes[kind]}')
                elif abs(run["size"] - sizes["bullet"]) > SIZE_TOLERANCE:
                    problems.append(f'"{run["text"][:40]}" prints at {run["size"]} pt, not {sizes["bullet"]}')
                break
    kinds = {k for k, _ in seq}
    for kind in ("name", "section", "sub", "bullet"):
        if kind in kinds and not seen[kind]:
            problems.append(f"no {kind} text could be read from the PDF")
    wanted = sum(1 for k, _ in seq if k == "dates")
    if found_dates < wanted:
        problems.append(f"{wanted - found_dates} date(s) could not be read from the PDF")
    return {"ok": not problems, "problems": problems[:12], "sizes": {k: sorted(v) for k, v in seen.items()}}


# ---------- Reference Step 5b: a resume that compiles can still extract as garbage ----------
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")


def parse_check(pdf: bytes, doc) -> dict:
    """What a parser gets: contact details first and in the body, each heading on its own line, bullets in reading order, real Unicode."""
    reader = PdfReader(io.BytesIO(pdf))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    flat = squash(text)
    items = []

    def item(label, ok, detail=""):
        items.append({"label": label, "ok": bool(ok), "detail": detail})

    head = text[:400]
    item("Your name comes first", squash(doc.name)[:24] in squash(head[:200]) if doc.name else True)
    contact = " ".join(doc.contact)
    if EMAIL.search(contact):
        item("Email in the first lines", EMAIL.search(head))
    if PHONE.search(contact):
        item("Phone in the first lines", PHONE.search(head))
    lines = {squash(line) for line in text.splitlines()}
    for s in doc.sections:
        if s.include and (s.entries or s.text.strip()):
            item(f'"{s.title}" reads as its own heading', squash(s.title) in lines)
    positions, in_order = [], True
    for s in doc.sections:
        if not s.include:
            continue
        for e in s.entries:
            if not e.include:
                continue
            for b in e.bullets:
                if b.include and b.text.strip():
                    at = flat.find(squash(plain(b.text))[:32])
                    positions.append(at)
    missing = sum(1 for p in positions if p < 0)
    found = [p for p in positions if p >= 0]
    in_order = found == sorted(found)
    item("Bullets read top to bottom", in_order and not missing, f"{missing} bullet(s) not found" if missing else "")
    dates = [e.dates for s in doc.sections if s.include for e in s.entries if e.include and e.dates.strip()]
    item("Dates are intact", all(squash(d) in flat for d in dates))
    no_unicode = set()
    for page in reader.pages:
        fonts = (page.get("/Resources") or {}).get("/Font") or {}
        for ref in fonts.values():
            font = ref.get_object()
            if "/ToUnicode" not in font and font.get("/Subtype") != "/Type3":
                no_unicode.add(str(font.get("/BaseFont")))
    item("Every font maps to real characters", not no_unicode, ", ".join(sorted(no_unicode)))
    item("No unreadable characters", "�" not in text)
    return {"ok": all(i["ok"] for i in items), "items": items, "pages": len(reader.pages), "text": text}
