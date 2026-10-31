"""WCAG AA for every text and background pair the visual identity defines, read from frontend/src/styles/tokens.css, so a
palette change that breaks legibility fails here instead of on the owner's screen. Translucent tokens are composited over
the surface they sit on. Text needs 4.5:1; readouts (56px and up), lamps, focus rings and control edges need 3:1. The grain
tile is so faint it moves no pair by more than a rounding error, so it is not modelled."""

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
TOKENS = (SRC / "styles" / "tokens.css").read_text()


def block(selector):
    match = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", TOKENS, re.S)
    assert match, selector
    return dict(re.findall(r"--([\w-]+):\s*([^;]+);", match.group(1)))


THEMES = {"dark": block(':root[data-theme="dark"]'), "light": block(':root[data-theme="light"]')}


def rgba(value):
    value = value.strip()
    if value.startswith("#"):
        digits = value[1:]
        return tuple(int(digits[i : i + 2], 16) for i in (0, 2, 4)) + (1.0,)
    nums = [float(x) for x in re.match(r"rgba?\(([^)]+)\)", value).group(1).split(",")]
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


def measure(theme, fg, *layers):
    """`fg` on the given surfaces, topmost first; the last must be opaque."""
    tokens = THEMES[theme]
    background = rgba(tokens[layers[-1]])
    for layer in reversed(layers[:-1]):
        background = over(rgba(tokens[layer]), background)
    return ratio(over(rgba(tokens[fg]), background), background)


TEXT = 4.5
LARGE = 3.0
PLATE = [("ground",), ("ground-2",), ("ground-3",), ("raised",), ("tint", "ground"), ("tint", "raised")]
PAIRS = [(fg, surface, TEXT) for fg in ("ink", "ink-2", "ink-3", "accent-text", "signal-ink") for surface in PLATE]
PAIRS += [
    # Keys
    ("key-ink", ("key",), TEXT),
    ("key-ink", ("key-down",), TEXT),
    ("key-ink", ("ground-2",), TEXT),
    ("ink-2", ("key",), TEXT),  # a count on a filter key
    ("ink-2", ("key-down",), TEXT),
    ("brand-ink", ("brand",), TEXT),
    # Controls
    ("ink", ("field",), TEXT),
    ("ink-2", ("field",), TEXT),  # placeholders
    ("line-strong", ("ground",), LARGE),  # a control's edge
    ("line-strong", ("raised",), LARGE),
    ("focus", ("ground",), LARGE),
    ("focus", ("raised",), LARGE),
    ("focus", ("field",), LARGE),
    # The window, which stays dark in both themes
    ("window-ink", ("window",), TEXT),
    ("window-ink-2", ("window",), TEXT),
    ("signal-on-window", ("window",), TEXT),
    ("led", ("window",), LARGE),  # the lit readout and rungs
    # Lamps on the plate
    ("signal", ("ground",), LARGE),
    ("signal", ("ground-2",), LARGE),
    ("ink", ("ground",), LARGE),
]


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("fg,layers,need", PAIRS, ids=[f"{p[0]}-on-{'+'.join(p[1])}" for p in PAIRS])
def test_pair_meets_aa(theme, fg, layers, need):
    got = measure(theme, fg, *layers)
    assert got >= need, f"{theme}: {fg} on {'+'.join(layers)} is {got:.2f}:1, needs {need}:1"


def test_literal_colours_live_only_in_tokens():
    literal = re.compile(r"#[0-9a-fA-F]{3,8}\b(?![^{]*\{)|\brgba?\(")
    offenders = []
    for path in [*(SRC / "styles").glob("*.css"), *(SRC / "identity").glob("*")]:
        if path.name == "tokens.css":
            continue
        offenders += [f"{path.name}:{n}" for n, line in enumerate(path.read_text().splitlines(), 1) if literal.search(line)]
    assert not offenders, offenders


if __name__ == "__main__":
    for theme in THEMES:
        rows = sorted((measure(theme, fg, *layers), fg, "+".join(layers), need) for fg, layers, need in PAIRS)
        print(theme, f"{len(rows)} pairs, {sum(r[0] < r[3] for r in rows)} under")
        for got, fg, layers, need in rows[:8]:
            print(f"  {got:5.2f} (needs {need}) {fg} on {layers}")
