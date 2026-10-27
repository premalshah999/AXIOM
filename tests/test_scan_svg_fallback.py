"""Workable (JeffreyM Consulting 2026-10-27): each checkbox option carried its icon's SVG <desc> fallback text, so the
options read "SVGs not supported by this browser. Python" while the fill read "Python", and the group never committed."""

from jobpilot.forms import SCAN

ICON = '<svg viewBox="0 0 20 20"><desc aria-hidden="true"><p>SVGs not supported by this browser.</p></desc></svg>'


async def test_svg_fallback_text_is_not_an_option_label(page):
    await page.set_content(f"""<form><div role=group aria-labelledby=q>
      <span id=q>Which programming languages have you used? (Select all that apply.)</span>
      <label><input type=checkbox name=a value=1><span>{ICON}</span>Python</label>
      <label><input type=checkbox name=b value=2><span>{ICON}</span>Java</label></div></form>""")
    data = await page.main_frame.evaluate(SCAN)
    group = next(f for f in data["fields"] if f["type"] == "checkboxgroup")
    assert group["options"] == ["Python", "Java"]
