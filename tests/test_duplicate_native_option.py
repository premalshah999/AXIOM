"""ZS (iCIMS 2026-10-26) lists "Job Board" twice in "How did you hear about us?"; the native select refused the choice
as ambiguous on every pass and the required question stayed empty. Identical visible options are one choice."""

from jobpilot import widgets

OPTIONS = ["Company Website", "Job Board", "Job Board", "Others"]


class Select:
    def __init__(self):
        self.chosen = None

    async def evaluate(self, script, arg=None):
        if "flatMap" in script:
            return [i for i, text in enumerate(OPTIONS) if text == arg]
        if "map(o => o.text.trim())" in script:
            return OPTIONS
        return None

    async def select_option(self, index, timeout=None):
        self.chosen = index


async def test_identical_options_choose_the_first():
    el = Select()
    assert await widgets.select(el, "Job Board") == "Job Board" and el.chosen == 1
