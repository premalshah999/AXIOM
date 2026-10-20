"""Taleo postings reach ranking with their description (2026-10-17): Enterprise pages keep it in the hidden
"initialHistory" input (Bealls had no text and was never scored); Business Edition pages are plain HTML (Costco)."""

from pathlib import Path

from jobpilot.features.discovery.postings import TALEO_BE, TALEO_EE, taleo_text

FIXTURES = Path(__file__).parent / "fixtures"


def test_enterprise_description_comes_from_initial_history():
    text = taleo_text((FIXTURES / "taleo_ee_bealls.html").read_text())
    assert "WHY JOIN BEALLS" in text and len(text) > 2000
    assert text.count("WHY JOIN BEALLS") == 1  # the page repeats the description; it is kept once


def test_business_edition_description_follows_apply_now():
    text = taleo_text((FIXTURES / "taleo_be_costco.html").read_text())
    assert text.startswith("Costco Travel IT is responsible") and len(text) > 2000


def test_posting_urls_are_recognized():
    assert TALEO_EE.match("https://beallsinc.taleo.net/careersection/bealls+outlet+mobile/jobdetail.ftl?job=260000GI")
    assert TALEO_BE.match("https://phf.tbe.taleo.net/phf02/ats/careers/v2/viewRequisition?org=COSTCO&cws=41&rid=10973")
    assert TALEO_BE.match("https://phh.tbe.taleo.net/phh01/ats/careers/requisition.jsp?org=BASESOLU&cws=37&rid=3920")
    assert not TALEO_EE.match("https://beallsinc.taleo.net/careersection/bealls+outlet+mobile/jobsearch.ftl")
