"""A stalled flow that ends on the employer's acknowledgement is confirmed, and German/French submits and
acknowledgements read like English ones (Sopra Steria, SmartRecruiters 2026-10-26: "Absenden" was clicked as a step
and the run waited on "Bewerbung eingereicht!" until its recovery budget ran out)."""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jobpilot.browser import BrowserEngine as Browser
from jobpilot.forms import FINAL, LOCALIZED_CONFIRMATION


def browser(receipt):
    value = Browser.__new__(Browser)
    value.form = SimpleNamespace(proof=AsyncMock(return_value=receipt))
    value.emit = lambda *args, **kwargs: None
    return value


async def test_a_stall_on_an_acknowledgement_is_confirmed():
    receipt = {"type": "explicit_confirmation_page", "confirmation": "Bewerbung eingereicht!"}
    result = await browser(receipt).page_acknowledged()
    assert result["state"] == "confirmed" and result["receipt"] is receipt


async def test_no_acknowledgement_keeps_the_stall():
    assert await browser(None).page_acknowledged() is None


@pytest.mark.parametrize("label", ["Absenden", "Bewerbung abschicken", "Envoyer ma candidature", "Soumettre"])
def test_localized_submits_are_final(label):
    assert FINAL.search(label)


@pytest.mark.parametrize("label", ["Weiter", "Zurück", "Suivant", "Continuer"])
def test_localized_steps_are_not_final(label):
    assert not FINAL.search(label)


@pytest.mark.parametrize("text", [
    "Bewerbung eingereicht!",
    "Die Bewerbung als AI Platform Engineer bei Sopra Steria wurde erfolgreich eingereicht.",
    "Vielen Dank für Ihre Bewerbung",
    "Votre candidature a bien été envoyée",
    "Gracias por tu postulación",
])
def test_localized_acknowledgements(text):
    assert re.search(LOCALIZED_CONFIRMATION, text, re.I)


async def test_a_stall_on_a_filled_position_is_closed():
    value = Browser.__new__(Browser)
    value.form = SimpleNamespace(scan=AsyncMock(return_value={
        "fields": [{"type": "search", "label": "Search jobs"}], "headings": ["This position has been filled"],
        "errors": [], "text": ""}))
    assert (await value.page_closed())["state"] == "closed"


async def test_a_form_is_never_closed_by_its_text():
    value = Browser.__new__(Browser)
    value.form = SimpleNamespace(scan=AsyncMock(return_value={
        "fields": [{"type": "text", "label": "First name"}], "headings": ["This position has been filled"],
        "errors": [], "text": ""}))
    assert await value.page_closed() is None
