"""WCAG AA for every text and background pair the visual identity defines, read from frontend/src/styles/tokens.css, so a
palette change that breaks legibility fails here instead of on the owner's screen. Translucent tokens are composited over
the surface they sit on. Text needs 4.5:1; display text (24px and up, or the sign) and non-text marks such as focus rings
and control edges need 3:1."""

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
TOKENS = (SRC / "styles" / "tokens.css").read_text()


def block(selector):
    match = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", TOKENS, re.S)
    assert match, selector
    return dict(re.findall(r"--([\w-]+):\s*([^;]+);", match.group(1)))


BUILDING = block("/* ---------- the building: constant across hours ---------- */\n:root")
THEMES = {"night": block(':root[data-theme="dark"]'), "dawn": block(':root[data-theme="light"]')}


def rgba(value):
    value = value.strip()
    if value.startswith("#"):
        digits = value[1:]
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        return tuple(int(digits[i : i + 2], 16) for i in (0, 2, 4)) + (1.0,)
    parts = re.match(r"rgba?\(([^)]+)\)", value)
    assert parts, value
    nums = [float(x) for x in parts.group(1).split(",")]
    return (nums[0], nums[1], nums[2], nums[3] if len(nums) > 3 else 1.0)


def over(top, bottom):
    a = top[3]
    return tuple(top[i] * a + bottom[i] * (1 - a) for i in range(3)) + (1.0,)


def luminance(c):
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(c[0]) + 0.7152 * channel(c[1]) + 0.0722 * channel(c[2])


def ratio(a, b):
    a, b = luminance(a), luminance(b)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def resolve(theme, name):
    tokens = {**BUILDING, **THEMES[theme]}
    value = tokens[name]
    while value.startswith("var("):
        value = tokens[value[6:-1]]
    return rgba(value)


def measure(theme, fg, *layers):
    """`fg` on the given surfaces, topmost first; the last must be opaque."""
    background = resolve(theme, layers[-1])
    for layer in reversed(layers[:-1]):
        background = over(resolve(theme, layer), background)
    return ratio(over(resolve(theme, fg), background), background)


TEXT = 4.5
LARGE = 3.0
SURFACES = [
    ("ground",),
    ("ground-2",),
    ("raised",),
    ("tint", "ground"),
    ("tint", "raised"),
]
INKS = ["ink", "ink-2", "ink-3", "accent-text", "st-work", "st-ask", "st-fix", "st-unc", "st-done", "st-stop", "warn", "bad"]

PAIRS = [(fg, surface, TEXT) for fg in INKS for surface in SURFACES]
PAIRS += [
    # Controls
    ("brand-ink", ("brand",), TEXT),
    ("ink", ("field",), TEXT),
    ("ink-2", ("field",), TEXT),
    ("ink-3", ("field",), TEXT),  # placeholders
    ("field-edge", ("ground",), LARGE),
    ("field-edge", ("raised",), LARGE),
    ("focus", ("ground",), LARGE),
    ("focus", ("ground-2",), LARGE),
    ("focus", ("raised",), LARGE),
    ("focus", ("field",), LARGE),
    ("focus", ("sky-0",), LARGE),
    ("focus", ("sky-3",), LARGE),
    # The sky: small captions and axis labels at their lowest stop, and the display sentence at every stop.
    ("ink", ("sky-0",), TEXT),
    ("ink", ("sky-1",), TEXT),
    ("ink", ("sky-2",), TEXT),
    ("ink", ("sky-3",), TEXT),
    ("ink-2", ("sky-0",), TEXT),
    ("accent-text", ("sky-1",), LARGE),
    ("accent-text", ("sky-2",), LARGE),
    ("dot", ("sky-1",), LARGE),
    ("dot", ("sky-2",), LARGE),
    ("dot", ("sky-3",), LARGE),
    # A lit window: dark text at the gradient's lightest (with its sheen) and darkest stops.
    ("lit-ink", ("lamp-sheen", "lamp-1"), TEXT),
    ("lit-ink", ("lamp-1",), TEXT),
    ("lit-ink", ("lamp-2",), TEXT),
    ("lit-ink", ("lamp-3",), TEXT),
    ("lit-ink-2", ("lamp-3",), TEXT),
    ("lit-ink", ("hot-1",), TEXT),
    # An idle or held window (its one line of text sits below where the sky fades out)
    ("idle-text", ("idle-1",), TEXT),
    ("idle-text", ("idle-2",), TEXT),
    # The façade, in both themes
    ("cream", ("facade",), TEXT),
    ("cream", ("facade-lo",), TEXT),
    ("cream", ("facade-hi",), TEXT),
    ("cream-2", ("facade",), TEXT),
    ("cream-2", ("facade-lo",), TEXT),
    # The phone menu sits on the frame colour.
    ("cream", ("frame",), TEXT),
    ("cream-2", ("frame",), TEXT),
    ("cream-3", ("frame",), TEXT),
    ("on-green-work", ("frame",), TEXT),
    ("on-green-ask", ("frame",), TEXT),
    ("on-green-focus", ("frame",), LARGE),
    # The sign, in both of its words
    ("sign-ink", ("oxblood",), LARGE),
    ("sign-ink", ("oxblood-2",), LARGE),
    ("sign-off", ("oxblood",), LARGE),
    ("sign-off", ("oxblood-2",), LARGE),
    # A fanlight's lit, dim and unreached panes must read apart.
    ("fan-on", ("fan-off",), LARGE),
    ("fan-now", ("fan-off",), LARGE),
]


@pytest.mark.parametrize("theme", ["night", "dawn"])
@pytest.mark.parametrize("fg,layers,need", PAIRS, ids=[f"{p[0]}-on-{'+'.join(p[1])}" for p in PAIRS])
def test_pair_meets_aa(theme, fg, layers, need):
    got = measure(theme, fg, *layers)
    assert got >= need, f"{theme}: {fg} on {'+'.join(layers)} is {got:.2f}:1, needs {need}:1"


def test_literal_colours_live_only_in_tokens():
    """Stylesheets and the identity's components name tokens. The grain data URIs live in tokens.css too."""
    literal = re.compile(r"#[0-9a-fA-F]{3,8}\b(?![^{]*\{)|\brgba?\(")
    offenders = []
    for path in [*(SRC / "styles").glob("*.css"), *(SRC / "identity").rglob("*")]:
        if path.name == "tokens.css" or path.name.startswith("harness") or not path.is_file():
            continue
        offenders += [f"{path.name}:{n}" for n, line in enumerate(path.read_text().splitlines(), 1) if literal.search(line)]
    assert not offenders, offenders


if __name__ == "__main__":
    worst = {}
    for theme in THEMES:
        rows = sorted((measure(theme, fg, *layers), fg, "+".join(layers), need) for fg, layers, need in PAIRS)
        print(theme, f"{len(rows)} pairs, {sum(r[0] < r[3] for r in rows)} under")
        for got, fg, layers, need in rows[:12]:
            print(f"  {got:5.2f} (needs {need}) {fg} on {layers}")
