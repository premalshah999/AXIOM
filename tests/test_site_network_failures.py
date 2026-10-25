"""A page the employer's site would not serve is the site's outcome, not a broken control. West Monroe's career site
served an expired certificate and Amazon's dropped the connection (2026-10-25): every run read "Browser control failed
at browser.py:1408" and sat in repair."""

import pytest

from jobpilot.browser import NET_RETRY, net_failure


class NavError(Exception):
    pass


def test_an_invalid_certificate_closes_the_job_without_sending_anything():
    result = net_failure(NavError("Page.goto: net::ERR_CERT_DATE_INVALID at <url>"))
    assert result["state"] == "closed" and "ERR_CERT_DATE_INVALID" in result["reason"]


@pytest.mark.parametrize("code", ["ERR_FAILED", "ERR_NETWORK_CHANGED", "ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION_REFUSED"])
def test_a_site_that_did_not_load_is_retried_later(code):
    result = net_failure(NavError(f"Page.goto: net::{code} at <url>"))
    assert result["state"] == "waiting_browser" and result["recovery_cause"] == "site_temporary"


def test_aborted_and_ordinary_errors_stay_control_failures():
    assert net_failure(NavError("Page.goto: net::ERR_ABORTED at <url>")) is None
    assert net_failure(ValueError("Locator is stale")) is None


def test_a_dropped_or_changed_network_is_retried_once_in_place():
    assert NET_RETRY.search("net::ERR_NETWORK_CHANGED at <url>") and NET_RETRY.search("net::ERR_FAILED at <url>")
    assert not NET_RETRY.search("net::ERR_CERT_DATE_INVALID at <url>")
