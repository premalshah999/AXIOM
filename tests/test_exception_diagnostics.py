"""Failures after the commit keep their cause (Greenhouse emailed-code runs, 2026-10-18/19).

The controller used to turn every exception raised after the final click into one fixed sentence, so eight
"Submission was attempted but its outcome could not be verified" runs left no class, message or location behind.
"""

import pytest

from jobpilot.browser import exception_diagnostics
from jobpilot.network import public_url


async def test_names_the_exception_and_the_jobpilot_frame_that_raised_it():
    with pytest.raises(ValueError) as caught:
        await public_url("ftp://careers.example.test/x")
    d = exception_diagnostics(caught.value)
    assert d["exception"] == "ValueError"
    assert d["message"] == "Only public http(s) URLs are supported"
    assert any(loc.startswith("network.py:") and loc.endswith(":public_url") for loc in d["locations"]), d


def test_urls_and_addresses_are_masked_because_verification_links_carry_tokens():
    exc = ValueError("net::ERR_FAILED at https://acme.example.test/verify?token=SECRET123 for alex@example.test")
    d = exception_diagnostics(exc)
    assert "SECRET123" not in d["message"] and "acme.example.test" not in d["message"]
    assert "alex@example.test" not in d["message"]
    assert "<url>" in d["message"] and "<email>" in d["message"]


@pytest.mark.parametrize("text", ['Locator.fill("hunter2"): timeout', "Locator.type('abc') failed", "keyboard.press(x)"])
def test_messages_from_typing_calls_are_dropped_because_they_can_echo_typed_values(text):
    assert exception_diagnostics(RuntimeError(text))["message"] == ""


def test_only_the_first_line_is_kept_and_it_is_bounded():
    d = exception_diagnostics(ValueError("first line\nsecond line with detail " + "x" * 400))
    assert d["message"] == "first line"
    assert len(exception_diagnostics(ValueError("y" * 500))["message"]) == 200
    assert d["locations"] == []  # raised outside jobpilot: no frames to report
