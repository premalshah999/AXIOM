"""A page the employer's site would not serve is the site's outcome, not a broken control. West Monroe's career site
served an expired certificate and Amazon's dropped the connection (2026-10-25): every run read "Browser control failed
at browser.py:1408" and sat in repair."""

import pytest

from jobpilot.browser import NET_RETRY, net_failure


class NavError(Exception):
    pass


@pytest.mark.parametrize("code", ["ERR_CERT_DATE_INVALID", "ERR_CERT_AUTHORITY_INVALID", "ERR_SSL_PROTOCOL_ERROR"])
def test_an_invalid_certificate_waits_and_never_closes_the_job(code):
    # West Monroe (2026-10-25): six jobs were closed on an expired certificate; three siblings had confirmed the day before.
    result = net_failure(NavError(f"Page.goto: net::{code} at <url>"))
    assert result["state"] == "waiting_browser" and result["recovery_cause"] == "site_temporary"
    assert code in result["reason"] and "no application data was sent" in result["reason"]


@pytest.mark.parametrize("code", ["ERR_FAILED", "ERR_NETWORK_CHANGED", "ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION_REFUSED",
                                  "ERR_TIMED_OUT", "ERR_INTERNET_DISCONNECTED", "ERR_TUNNEL_CONNECTION_FAILED"])
def test_a_site_that_did_not_load_is_retried_later(code):
    result = net_failure(NavError(f"Page.goto: net::{code} at <url>"))
    assert result["state"] == "waiting_browser" and result["recovery_cause"] == "site_temporary"


def test_aborted_and_ordinary_errors_stay_control_failures():
    assert net_failure(NavError("Page.goto: net::ERR_ABORTED at <url>")) is None
    assert net_failure(ValueError("Locator is stale")) is None


def test_a_dropped_or_changed_network_is_retried_once_in_place():
    assert NET_RETRY.search("net::ERR_NETWORK_CHANGED at <url>") and NET_RETRY.search("net::ERR_FAILED at <url>")
    assert not NET_RETRY.search("net::ERR_CERT_DATE_INVALID at <url>")
