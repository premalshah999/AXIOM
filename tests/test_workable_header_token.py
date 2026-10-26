"""Workable's apply POST carries its Turnstile token in X-TURNSTILE-TOKEN. The widget fails in the worker browser, the
page swallows the error and posts without the header, and Workable answered 412 to every application from 2026-10-23.
The clean twin Chrome's token is sent instead (owner-approved 2026-10-26)."""

from types import SimpleNamespace
from urllib.parse import urlsplit

from jobpilot.browser import BrowserEngine, header_token_endpoint


def test_only_workables_apply_endpoint_carries_a_header_token():
    assert header_token_endpoint(urlsplit("https://apply.workable.com/api/v1/jobs/1B817A4D38/apply")) == "x-turnstile-token"
    assert header_token_endpoint(urlsplit("https://apply.workable.com/api/v1/jobs/1B817A4D38/questions")) is None
    assert header_token_endpoint(urlsplit("https://jobs.example.test/api/v1/jobs/1/apply")) is None


async def test_a_prepared_token_is_used_once_and_a_real_one_is_never_replaced():
    engine = object.__new__(BrowserEngine)
    minted = []

    async def mint_header_token():
        minted.append(1)
        return "fresh-token"

    engine.mint_header_token = mint_header_token
    import time

    engine.prepared_header_token = ("prepared-token", time.monotonic())
    empty = SimpleNamespace(headers={})
    assert await engine.header_turnstile_token(empty, "x-turnstile-token") == "prepared-token"
    assert await engine.header_turnstile_token(empty, "x-turnstile-token") == "fresh-token" and minted == [1]
    failed = SimpleNamespace(headers={"x-turnstile-token": "error_code_600010"})
    assert await engine.header_turnstile_token(failed, "x-turnstile-token") == "fresh-token"
    valid = SimpleNamespace(headers={"x-turnstile-token": "0.real-token"})
    assert await engine.header_turnstile_token(valid, "x-turnstile-token") is None
