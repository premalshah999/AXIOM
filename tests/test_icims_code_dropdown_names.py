"""iCIMS's select-less Country Code dropdown names a row with its dial code before or after the country. Planview's
email step (2026-10-26) stopped at "No matching option is offered" on every pass; only "(+1) United States" was read."""

import pytest

from jobpilot import widgets


class Node:
    def __init__(self, page, key):
        self.page, self.key = page, key

    def locator(self, selector):
        return Node(self.page, selector)

    async def is_visible(self):
        return self.key != "input.dropdown-search"

    async def count(self):
        return 0

    async def click(self, timeout=None):
        if self.key.startswith('[id="row'):
            self.page.chosen = self.key[5:-2]

    async def press(self, key):
        pass

    async def evaluate_all(self, script):
        return self.page.rows

    async def inner_text(self):
        return dict(self.page.rows).get(self.page.chosen, "— Make a Selection —")


class Frame:
    def __init__(self, rows):
        self.rows, self.chosen = rows, None

    def locator(self, selector):
        return Node(self, selector)


class Anchor:
    async def evaluate(self, script):
        return ["anchor", "container"]


@pytest.mark.parametrize("rows", [
    [("row1", "Canada (+1)"), ("row2", "United States (+1)"), ("row3", "India (+91)")],
    [("row1", "(+1) Canada"), ("row2", "(+1) United States"), ("row3", "(+91) India")],
    [("row1", "Canada +1"), ("row2", "United States of America +1"), ("row3", "India +91")],
])
@pytest.mark.parametrize("value", ["United States", "+1"])
async def test_the_country_is_found_wherever_its_code_is_written(rows, value):
    frame = Frame(rows)
    assert await widgets.icims_code_dropdown_commit(frame, Anchor(), value) == rows[1][1]
    assert frame.chosen == "row2"
