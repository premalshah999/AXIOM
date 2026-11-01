"""What a decision sees of a long form."""

from jobpilot.browser import model_fields


def test_the_cap_keeps_what_blocks_the_page_and_the_page_order():
    filled = [{"label": f"f{i}", "required": True, "value": "x", "valid": True} for i in range(120)]
    empty = {"label": "last row's Country", "required": True, "value": "", "valid": True}
    refused = {"label": "last row's State", "required": True, "value": "x", "valid": False}
    fields = [*filled, empty, refused]
    shown = model_fields(fields)
    assert len(shown) == 100 and empty in shown and refused in shown
    assert [f["label"] for f in shown] == [f["label"] for f in fields if f in shown]
    assert model_fields(filled[:50]) == filled[:50]
