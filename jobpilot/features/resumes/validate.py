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
