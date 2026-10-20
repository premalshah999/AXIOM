"""Fixed LaTeX template. Every string is escaped; nothing is spliced as LaTeX.

Standard section headings, contact details in the body (never a header/footer),
MM/YYYY dates, Type 1 fonts with ToUnicode maps so extraction stays readable.
"""

import re
import unicodedata

PREAMBLE = r"""\documentclass[10pt,letterpaper]{article}
\usepackage[letterpaper,top=0.45in,bottom=0.45in,left=0.55in,right=0.55in]{geometry}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{mathptmx}
\usepackage{tabularx}
\input{glyphtounicode}
\pdfgentounicode=1
\setlength{\parindent}{0pt}
\setlength{\parskip}{0pt}
\pagestyle{empty}
\renewcommand{\baselinestretch}{0.96}
\newcommand{\ressection}[1]{\vspace{6pt}{\large\bfseries #1}\par\vspace{1pt}\hrule\vspace{3pt}}
\newcommand{\resrow}[2]{\noindent\begin{tabularx}{\textwidth}{@{}X@{}r@{}}#1 & #2\end{tabularx}\par}
\newenvironment{resbullets}{\begin{list}{\textbullet}{\setlength{\leftmargin}{1.1em}\setlength{\itemsep}{1pt}%
\setlength{\topsep}{1pt}\setlength{\parsep}{0pt}}}{\end{list}\vspace{2pt}}
"""

SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    "<": r"\textless{}",
    ">": r"\textgreater{}",
    "|": r"\textbar{}",
}
PUNCT = {"‘": "'", "’": "'", "“": '"', "”": '"', "…": "...", " ": " ", "—": ", ", "–": "-", "•": "-"}


def plain(text) -> str:
    """Normalize to characters the T1 font covers; drop control characters."""
    value = "".join(PUNCT.get(c, c) for c in str(text or ""))
    value = unicodedata.normalize("NFKC", value)
    out = []
    for ch in value:
        if ch in "\n\t":
            out.append(" ")
        elif unicodedata.category(ch).startswith("C"):
            continue
        elif ord(ch) < 256:
            out.append(ch)
        else:
            out.append(unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode())
    return re.sub(r"\s+", " ", "".join(out)).strip()


def esc(text) -> str:
    return "".join(SPECIAL.get(c, c) for c in plain(text))


def month(value: str, current=False) -> str:
    if current and not value:
        return "Present"
    m = re.fullmatch(r"\s*(\d{4})-(\d{1,2})(?:-\d{1,2})?\s*", value or "")
    if m:
        return f"{int(m.group(2)):02d}/{m.group(1)}"
    m = re.fullmatch(r"\s*(\d{4})\s*", value or "")
    return m.group(1) if m else plain(value)


def span(start, end, current=False) -> str:
    a, b = month(start), ("Present" if current else month(end))
    return f"{a} - {b}" if a and b else a or b


def render(inv, sel, wording: dict) -> str:
    """wording: unit_id -> final plain text (already validated)."""
    lines = [PREAMBLE, r"\begin{document}", r"\begin{center}"]
    lines.append(r"{\fontsize{17}{20}\selectfont\bfseries " + esc(inv.name) + r"}\par\vspace{2pt}")
    lines.append(r"{\small " + r" \textbar{} ".join(esc(c) for c in inv.contact) + r"}\par")
    lines.append(r"\end{center}")

    def bullets(units):
        if not units:
            return []
        out = [r"\begin{resbullets}"]
        out += [r"\item " + esc(wording.get(u.id, u.text)) for u in units]
        return out + [r"\end{resbullets}"]

    if sel.roles:
        lines.append(r"\ressection{Experience}")
        for role in inv.roles:
            if role.row not in sel.roles:
                continue
            left = r"\textbf{" + esc(role.title) + "}, " + esc(role.company)
            right = esc(span(role.start, role.end, role.current))
            lines.append(r"\resrow{" + left + "}{" + right + "}")
            if role.location:
                lines.append(r"{\small\itshape " + esc(role.location) + r"}\par")
            lines += bullets(sel.roles[role.row])
    if inv.education:
        lines.append(r"\ressection{Education}")
        for e in inv.education:
            degree = ", ".join(x for x in (e.get("degree"), e.get("field_of_study")) if x)
            left = r"\textbf{" + esc(e.get("school", "")) + "}" + (", " + esc(degree) if degree else "")
            if e.get("gpa"):
                left += ", GPA " + esc(e["gpa"])
            lines.append(r"\resrow{" + left + "}{" + esc(span(e.get("start", ""), e.get("end", ""))) + "}")
    if sel.projects:
        lines.append(r"\ressection{Projects}")
        for p in inv.projects:
            if p.row in sel.projects:
                lines.append(r"\textbf{" + esc(p.title) + r"}\par")
                lines += bullets(sel.projects[p.row])
    if sel.skills or inv.languages:
        lines.append(r"\ressection{Skills}")
        if sel.skills:
            lines.append(r"\textbf{Technical:}\ " + esc(", ".join(sel.skills)) + r"\par")
        if inv.languages:
            lines.append(r"\textbf{Languages:}\ " + esc(", ".join(inv.languages)) + r"\par")
    lines.append(r"\end{document}")
    return "\n".join(lines) + "\n"
