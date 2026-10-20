"""Owner 2026-10-19: "when I try to use any other browser, there is a complete different icims flow, the normal one,
without central login". The worker Chrome's stored iCIMS state sent the tenant's email step to login.icims.com, whose
Auth0 password step and reset mail never existed for the applicant. Each fresh iCIMS run now starts without iCIMS state;
other sites' sessions are untouched."""

from types import SimpleNamespace

from jobpilot.browser import BrowserEngine


async def test_icims_state_is_cleared_and_other_sites_are_kept(page):
    context = page.context
    await context.add_cookies([
        {"name": "auth0", "value": "x", "domain": "login.icims.com", "path": "/"},
        {"name": "icims_session", "value": "y", "domain": ".icims.com", "path": "/"},
        {"name": "JSESSIONID", "value": "z", "domain": "careers-ice.icims.com", "path": "/"},
        {"name": "_gh_session", "value": "keep", "domain": "boards.greenhouse.io", "path": "/"},
    ])
    e = object.__new__(BrowserEngine)
    e.emitted = []
    e.emit = lambda kind, message, data=None: e.emitted.append(kind)
    await e.clear_platform_identity(page, "https://careers-ice.icims.com/jobs/13825/java-developer/job?in_iframe=1", "icims")
    left = {(c["domain"], c["name"]) for c in await context.cookies()}
    assert left == {("boards.greenhouse.io", "_gh_session")}
    assert e.emitted == ["browser"]


async def test_adp_state_is_cleared_too(page):
    # ADP (Avalon REA 2026-10-19): with stored state, "Another user account already exists"; clean, the code dialog.
    context = page.context
    await context.add_cookies([
        {"name": "SMIDENTITY", "value": "x", "domain": ".adp.com", "path": "/"},
        {"name": "JSESSIONID", "value": "y", "domain": "workforcenow.adp.com", "path": "/"},
        {"name": "_gh_session", "value": "keep", "domain": "boards.greenhouse.io", "path": "/"},
    ])
    e = object.__new__(BrowserEngine)
    e.emit = lambda *a, **k: None
    await e.clear_platform_identity(
        page, "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=x&jobId=1", "adp")
    assert {(c["domain"], c["name"]) for c in await context.cookies()} == {("boards.greenhouse.io", "_gh_session")}
