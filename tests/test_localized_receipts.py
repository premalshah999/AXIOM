"""Acknowledgement emails in German, French and Spanish are receipts too (Sopra Steria's SmartRecruiters 2026-10-27)."""

import pytest

from jobpilot.receipts import RECEIPT_SEARCH, RECEIPT_WORDING


@pytest.mark.parametrize("body", [
    "vielen dank für ihre bewerbung als ai platform engineer",
    "ihre bewerbung ist bei uns eingegangen",
    "merci pour votre candidature",
    "gracias por tu postulación",
])
def test_localized_acknowledgements_are_receipts(body):
    assert RECEIPT_WORDING.search(body)


def test_the_mailbox_search_asks_for_them():
    assert "Bewerbung" in RECEIPT_SEARCH and "candidature" in RECEIPT_SEARCH
