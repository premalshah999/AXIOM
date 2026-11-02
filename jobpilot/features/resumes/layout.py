"""Page fill for a tailored resume: only spacing moves. Type sizes are fixed and no content may be dropped, so a resume that can't fit says so.

Reference pipeline Step 5: compile, measure the gap above the bottom of the text area (not the page edge), and stop at 1.5 lines or less.
"""

from .editor import ResumeDoc, Style, render
from .master import measure_fill

PAGE_PT = 792
TARGET_GAP_PT = 16  # About 1.5 lines of 9 pt text: the target state, not a problem to fix.
# A short resume stays tight (owner, 2026-11-02: "tighten the spacing"): spacing never grows far to fill a page.
MAX_WIDEN = {"section_gap": 5.0, "entry_gap": 2.0, "bullet_gap": 0.5}
LINE_SPACING_STEPS = ()
# Tighter and tighter, sizes untouched. Past the last step the content really doesn't fit one page.
TIGHTER = (
    {"section_gap": 5, "entry_gap": 2, "bullet_gap": 0.5},
    {"section_gap": 4, "entry_gap": 1.5, "bullet_gap": 0, "margin_top": 0.4, "margin_bottom": 0.4, "margin_left": 0.5, "margin_right": 0.5},
    {"section_gap": 3, "entry_gap": 1, "bullet_gap": 0, "margin_top": 0.3, "margin_bottom": 0.3, "margin_left": 0.4, "margin_right": 0.4, "line_spacing": 0.95},
    {"section_gap": 2, "entry_gap": 0.5, "bullet_gap": 0, "margin_top": 0.25, "margin_bottom": 0.25, "margin_left": 0.35, "margin_right": 0.35, "line_spacing": 0.9},
)


def gap_points(fill: dict, style: Style) -> float:
    return (1 - style.margin_bottom * 72 / PAGE_PT - fill["fill"]) * PAGE_PT


def _counts(doc: ResumeDoc) -> tuple[int, int, int]:
    sections = [s for s in doc.sections if s.include]
    entries = sum(1 for s in sections for e in s.entries if e.include)
    bullets = sum(1 for s in sections for e in s.entries if e.include for b in e.bullets if b.include and b.text.strip())
    return len(sections), entries, bullets


async def fit(doc: ResumeDoc, style: Style, compile_fn, timeout: float = 40) -> dict:
    """{"compiled", "style", "fill", "gap_pt", "overflow", "compiles"}. compiled.ok False means it didn't compile at all."""
    compiles = 0

    async def build(st):
        nonlocal compiles
        compiles += 1
        return await compile_fn(render(doc, st), timeout=timeout)

    compiled = await build(style)
    if not compiled.ok:
        return {"compiled": compiled, "style": style, "fill": None, "gap_pt": None, "overflow": False, "compiles": compiles}
    fill = measure_fill(compiled.pdf)
    if fill["pages"] > 1:
        for step in TIGHTER:
            tried = style.model_copy(update=step)
            attempt = await build(tried)
            if not attempt.ok:
                continue
            compiled, style, fill = attempt, tried, measure_fill(attempt.pdf)
            if fill["pages"] == 1:
                break
    if fill["pages"] > 1:
        return {"compiled": compiled, "style": style, "fill": fill, "gap_pt": None, "overflow": True, "compiles": compiles}
    # Room left: spread it over the gaps, so a short resume reads as laid out and doesn't stop halfway. An overshoot goes back to the last that fit.
    sections, entries, bullets = _counts(doc)
    for _ in range(3):
        gap = gap_points(fill, style)
        if gap <= TARGET_GAP_PT:
            break
        units = max(1.0, sections * 1.5 + entries + bullets * 0.6)
        delta = (gap - TARGET_GAP_PT / 2) * 0.9 / units
        tried = style.model_copy(
            update={
                "section_gap": min(MAX_WIDEN["section_gap"], style.section_gap + delta * 1.5),
                "entry_gap": min(MAX_WIDEN["entry_gap"], style.entry_gap + delta),
                "bullet_gap": min(MAX_WIDEN["bullet_gap"], style.bullet_gap + delta * 0.6),
            }
        )
        if (tried.section_gap, tried.entry_gap, tried.bullet_gap) == (style.section_gap, style.entry_gap, style.bullet_gap):
            break  # Every gap is at its limit.
        attempt = await build(tried)
        if not attempt.ok:
            break
        measured = measure_fill(attempt.pdf)
        if measured["pages"] > 1:
            break
        compiled, style, fill = attempt, tried, measured
    for spacing in LINE_SPACING_STEPS:
        if gap_points(fill, style) <= TARGET_GAP_PT or spacing <= style.line_spacing:
            continue
        tried = style.model_copy(update={"line_spacing": spacing})
        attempt = await build(tried)
        if not attempt.ok:
            break
        measured = measure_fill(attempt.pdf)
        if measured["pages"] > 1:
            break
        compiled, style, fill = attempt, tried, measured
    # What's left stays white: no words are added to fill a page.
    return {"compiled": compiled, "style": style, "fill": fill, "gap_pt": round(gap_points(fill, style), 1), "overflow": False, "compiles": compiles}
