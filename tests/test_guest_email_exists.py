"""Textron's Taleo (2026-11-01): the guest application answered that the email already exists, with Send Email and
Cancel. It read as an unknown page and the model spent 30 decisions on the site's job search."""

import asyncio
from collections import Counter
from unittest.mock import MagicMock

from jobpilot.adapters.base import Adapter

TEXT = (
    "Create a User Name and Password\nThe email address provided (alex@example.test) already exists and cannot be used "
    "to create a guest submission.\nTo apply using this email address, create a username and password."
)


def adapter():
    value = Adapter.__new__(Adapter)
    value.attempts = Counter()
    value.e = MagicMock()
    value.emit = MagicMock()
    value.clicked = []

    async def click(button, auth=False, step=True):
        value.clicked.append(button["label"])

    value.click = click
    return value


def page(text, labels):
    return {"text": text, "controls": [{"id": x, "label": x, "aria_label": "", "automation_id": "", "href": ""} for x in labels]}


def test_the_closed_guest_route_goes_back_to_the_sign_in():
    a = adapter()
    assert asyncio.run(a.on_unknown(page(TEXT, ["Send Email", "Cancel"]))) is None
    assert a.clicked == ["Cancel"] and a.attempts["guest"] == 1


def test_an_unrelated_unknown_page_still_falls_back():
    a = adapter()
    assert asyncio.run(a.on_unknown(page("Welcome to our careers site", ["Cancel"])))["fallback"]
    assert a.clicked == []
