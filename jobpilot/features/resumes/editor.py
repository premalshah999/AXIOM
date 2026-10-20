"""Resume editor: a structured document + style that render to LaTeX, live.

Two ways to edit, both compiled by the same isolated compiler:
- Structured: sections, entries and bullets (each bullet keeps its evidence
  unit id when it came from the applicant's history) plus a style object for
  template, font, size, margins and spacing. Every string is escaped.
- Source: the generated LaTeX, edited directly. It runs under the same
  sandbox (no shell escape, paranoid file access, time/size limits).

Saving creates a new immutable artifact; the original tailored version stays.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

from .evidence import Inventory
from .latex import esc, span

EDITOR_VERSION = "editor-v1"
MAX_SOURCE = 200_000

FONTS = {
    "times": ("Times", r"\usepackage{mathptmx}"),
    "charter": ("Charter", r"\usepackage{charter}"),
    "palatino": ("Palatino", r"\usepackage{mathpazo}"),
    "helvetica": ("Helvetica", r"\usepackage[scaled=0.95]{helvet}\renewcommand{\familydefault}{\sfdefault}"),
    "latin-modern": ("Latin Modern", r"\usepackage{lmodern}"),
}
# The metric file each font needs; a font is offered only when TeX can load it.
FONT_METRICS = {
    "times": "ptmr8t.tfm",
    "charter": "bchr8t.tfm",
    "palatino": "pplr8t.tfm",
    "helvetica": "phvr8t.tfm",
    "latin-modern": "ec-lmr10.tfm",
}
_AVAILABLE: set | None = None


def available_fonts() -> set:
    global _AVAILABLE
    if _AVAILABLE is None:
        import shutil
        import subprocess

        found = set()
        tool = shutil.which("kpsewhich")
        for font, metric in FONT_METRICS.items():
            try:
                ok = (
                    bool(tool)
                    and subprocess.run(
                        [tool, metric], capture_output=True, text=True, timeout=5
                    ).stdout.strip()
                )
            except Exception:
                ok = False
            if ok:
                found.add(font)
        _AVAILABLE = found or {"times"}
    return _AVAILABLE


def usable_font(font: str) -> str:
    fonts = available_fonts()
    return font if font in fonts else ("times" if "times" in fonts else sorted(fonts)[0])


TEMPLATES = {
    "classic": {"label": "Classic", "font": "times", "header": "center", "headings": "rule"},
    "modern": {"label": "Modern", "font": "helvetica", "header": "left", "headings": "rule"},
    "compact": {"label": "Compact", "font": "latin-modern", "header": "left", "headings": "plain"},
}


class Style(BaseModel):
    template: Literal["classic", "modern", "compact"] = "classic"
    font: Literal["times", "charter", "palatino", "helvetica", "latin-modern"] = "times"
    font_size: float = Field(default=10.5, ge=9, le=12.5)
    line_spacing: float = Field(default=1.0, ge=0.85, le=1.4)
    margin_top: float = Field(default=0.5, ge=0.25, le=1.25)
    margin_bottom: float = Field(default=0.5, ge=0.25, le=1.25)
    margin_left: float = Field(default=0.6, ge=0.25, le=1.25)
    margin_right: float = Field(default=0.6, ge=0.25, le=1.25)
    section_gap: float = Field(default=7, ge=0, le=20)  # points before a section heading
    entry_gap: float = Field(default=3, ge=0, le=14)  # points between entries
    bullet_gap: float = Field(default=1, ge=0, le=8)  # points between bullets
    header: Literal["center", "left"] = "center"
    headings: Literal["rule", "plain"] = "rule"
    name_size: float = Field(default=17, ge=12, le=26)


class Bullet(BaseModel):
    id: str
    text: str = Field(max_length=1200)
    unit_id: str | None = None  # evidence unit this bullet came from
    source_text: str = ""
    include: bool = True


class Entry(BaseModel):
    id: str
    heading: str = Field(default="", max_length=300)
    subheading: str = Field(default="", max_length=300)
    dates: str = Field(default="", max_length=80)
    location: str = Field(default="", max_length=160)
    bullets: list[Bullet] = Field(default_factory=list, max_length=20)
    include: bool = True


class Section(BaseModel):
    id: str
    title: str = Field(max_length=60)
    kind: Literal["experience", "education", "projects", "skills", "text"]
    entries: list[Entry] = Field(default_factory=list, max_length=30)
    text: str = Field(default="", max_length=2000)  # skills/summary line
    include: bool = True


class ResumeDoc(BaseModel):
    name: str = Field(default="", max_length=120)
    contact: list[str] = Field(default_factory=list, max_length=8)
    sections: list[Section] = Field(default_factory=list, max_length=12)


def from_inventory(
    inv: Inventory, selection: list[dict] | None = None, skills: list[str] | None = None
) -> ResumeDoc:
    """A document from the applicant's history; with a selection, exactly that artifact's rows."""
    chosen = {x["unit_id"]: x for x in selection or []}

    def bullets_for(row):
        units = [u for u in inv.units if u.source_row == row]
        out = []
        for u in units:
            picked = chosen.get(u.id)
            out.append(
                Bullet(
                    id=u.id,
                    text=picked["text"] if picked else u.text,
                    unit_id=u.id,
                    source_text=u.text,
                    include=bool(picked) if selection else True,
                )
            )
        return out

    sections = []
    if inv.roles:
        sections.append(
            Section(
                id="experience",
                title="Experience",
                kind="experience",
                entries=[
                    Entry(
                        id=r.row,
                        heading=r.title,
                        subheading=r.company,
                        dates=span(r.start, r.end, r.current),
                        location=r.location,
                        bullets=bullets_for(r.row),
                        include=any(b.include for b in bullets_for(r.row)) or not selection,
                    )
                    for r in inv.roles
                ],
            )
        )
    if inv.education:
        sections.append(
            Section(
                id="education",
                title="Education",
                kind="education",
                entries=[
                    Entry(
                        id=e["row"],
                        heading=e.get("school", ""),
                        subheading=", ".join(x for x in (e.get("degree"), e.get("field_of_study")) if x)
                        + (f", GPA {e['gpa']}" if e.get("gpa") else ""),
                        dates=span(e.get("start", ""), e.get("end", "")),
                    )
                    for e in inv.education
                ],
            )
        )
    if inv.projects:
        sections.append(
            Section(
                id="projects",
                title="Projects",
                kind="projects",
                entries=[
                    Entry(
                        id=p.row,
                        heading=p.title,
                        bullets=bullets_for(p.row),
                        include=any(b.include for b in bullets_for(p.row)) or not selection,
                    )
                    for p in inv.projects
                ],
            )
        )
    skill_list = skills if skills is not None else inv.skills
    if skill_list or inv.languages:
        text = ", ".join(skill_list)
        if inv.languages:
            text += ("\n" if text else "") + "Languages: " + ", ".join(inv.languages)
        sections.append(Section(id="skills", title="Skills", kind="skills", text=text))
    return ResumeDoc(name=inv.name, contact=inv.contact, sections=sections)


def render(doc: ResumeDoc, style: Style) -> str:
    base = 10 if style.font_size < 10.75 else 11 if style.font_size < 11.75 else 12
    lead = round(style.font_size * 1.2, 2)
    rule = r"\vspace{1pt}\hrule" if style.headings == "rule" else ""
    lines = [
        rf"\documentclass[{base}pt,letterpaper]{{article}}",
        rf"\usepackage[letterpaper,top={style.margin_top}in,bottom={style.margin_bottom}in,"
        rf"left={style.margin_left}in,right={style.margin_right}in]{{geometry}}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage[utf8]{inputenc}",
        FONTS[usable_font(style.font)][1],
        r"\usepackage{tabularx}",
        r"\input{glyphtounicode}",
        r"\pdfgentounicode=1",
        r"\setlength{\parindent}{0pt}",
        r"\setlength{\parskip}{0pt}",
        r"\pagestyle{empty}",
        rf"\linespread{{{style.line_spacing}}}",
        rf"\renewcommand{{\normalsize}}{{\fontsize{{{style.font_size}}}{{{lead}}}\selectfont}}",
        rf"\newcommand{{\ressection}}[1]{{\vspace{{{style.section_gap}pt}}{{\large\bfseries #1}}\par{rule}\vspace{{3pt}}}}",
        r"\newcommand{\resrow}[2]{\noindent\begin{tabularx}{\textwidth}{@{}X@{}r@{}}#1 & #2\end{tabularx}\par}",
        r"\newenvironment{resbullets}{\begin{list}{\textbullet}{\setlength{\leftmargin}{1.1em}"
        rf"\setlength{{\itemsep}}{{{style.bullet_gap}pt}}\setlength{{\topsep}}{{1pt}}\setlength{{\parsep}}{{0pt}}}}}}"
        r"{\end{list}}",
        r"\begin{document}",
        r"\normalsize",
    ]
    align = "center" if style.header == "center" else "flushleft"
    lines += [
        rf"\begin{{{align}}}",
        rf"{{\fontsize{{{style.name_size}}}{{{round(style.name_size * 1.2, 1)}}}\selectfont\bfseries {esc(doc.name)}}}\par\vspace{{2pt}}",
        r"{\small " + r" \textbar{} ".join(esc(c) for c in doc.contact if c) + r"}\par",
        rf"\end{{{align}}}",
    ]
    for section in doc.sections:
        if not section.include:
            continue
        entries = [e for e in section.entries if e.include]
        if section.kind in {"skills", "text"}:
            if not section.text.strip():
                continue
            lines.append(r"\ressection{" + esc(section.title) + "}")
            for line in section.text.splitlines():
                if ":" in line and len(line.split(":", 1)[0]) <= 30:
                    label, rest = line.split(":", 1)
                    lines.append(r"\textbf{" + esc(label) + r":}\ " + esc(rest.strip()) + r"\par")
                elif line.strip():
                    lines.append(esc(line) + r"\par")
            continue
        if not entries:
            continue
        lines.append(r"\ressection{" + esc(section.title) + "}")
        for i, e in enumerate(entries):
            if i:
                lines.append(rf"\vspace{{{style.entry_gap}pt}}")
            left = r"\textbf{" + esc(e.heading) + "}"
            if e.subheading:
                left += ", " + esc(e.subheading)
            lines.append(r"\resrow{" + left + "}{" + esc(e.dates) + "}")
            if e.location:
                lines.append(r"{\small\itshape " + esc(e.location) + r"}\par")
            bullets = [b for b in e.bullets if b.include and b.text.strip()]
            if bullets:
                lines.append(r"\begin{resbullets}")
                lines += [r"\item " + esc(b.text) for b in bullets]
                lines.append(r"\end{resbullets}")
    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"


FORBIDDEN = re.compile(
    r"\\(?:write18|immediate\s*\\write|openout|openin|read\b|input\s*\{\s*/|include\s*\{\s*/|"
    r"special\s*\{|pdfobj|directlua|catcode|usepackage\s*(?:\[[^\]]*\])?\s*\{[^}]*(?:shellesc|"
    r"minted|pythontex|verbatimout)[^}]*\})",
    re.I,
)


def check_source(tex: str) -> list[str]:
    """Defence in depth for hand-edited source; the compiler sandbox is the real boundary."""
    problems = []
    if len(tex) > MAX_SOURCE:
        problems.append(f"Source is longer than {MAX_SOURCE // 1000} KB")
    for m in FORBIDDEN.finditer(tex):
        line = tex.count("\n", 0, m.start()) + 1
        problems.append(f"Line {line}: {m.group(0)!r} is not allowed in the editor")
    if r"\begin{document}" not in tex or r"\end{document}" not in tex:
        problems.append("The source needs \\begin{document} and \\end{document}")
    return problems


def selection_of(doc: ResumeDoc) -> list[dict]:
    """Rows saved with an edited artifact: wording plus the unit it came from, if any."""
    out = []
    for section in doc.sections:
        if not section.include:
            continue
        for e in section.entries:
            if not e.include:
                continue
            for b in e.bullets:
                if b.include and b.text.strip():
                    out.append(
                        {
                            "unit_id": b.unit_id or b.id,
                            "kind": section.kind,
                            "source_row": e.id,
                            "text": b.text,
                            "source_text": b.source_text,
                            "edited": b.text != b.source_text,
                        }
                    )
    return out
