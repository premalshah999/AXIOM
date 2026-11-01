"""A sign-up form asks for the address twice. "Email Address:" took the account's address (a plus-address) while "Retype Email
Address:" kept the profile's, and the employer answered "Your email address doesn't match the repeated one" (Farmers, 2026-11-01)."""

from types import SimpleNamespace

from jobpilot.accounts import AccountFlow
from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

URL = "https://careers.example.test/career?action=register"
HTML = """<!doctype html><html><body><h1>Create an Account</h1>
<label for=e>Email Address: *</label><input id=e type=text required>
<label for=r>Retype Email Address: *</label><input id=r type=text required>
<label for=p>Choose Password: *</label><input id=p type=password required>
<label for=q>Retype Password: *</label><input id=q type=password required>
<label for=f>First Name *</label><input id=f type=text required>
<label for=l>Last Name *</label><input id=l type=text required>
<button type=button onclick="window.sent=[e.value,r.value,p.value===q.value&&!!p.value]">Create Account</button>
</body></html>"""


async def test_the_address_is_the_same_both_times(page, service, tmp_path):
    await page.route(URL, lambda route: route.fulfill(content_type="text/html", body=HTML))
    await page.goto(URL)
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    profile = Profile(first_name="Alex", last_name="Rivera", email="alex@example.test", allow_account_creation=True,
                      accept_all_application_terms=True)
    form = FormSession(page, Resolver(profile, service.db, service.config), resume, lambda *a, **k: None)

    async def nothing(*_a, **_k):
        return None

    engine = SimpleNamespace(
        service=service, form=form, page=page, job={"url": URL}, emit=lambda *a, **k: None,
        email_verification=SimpleNamespace(prepare_submission=nothing), browser_challenge=nothing,
    )
    flow = AccountFlow(engine)
    flow.store.save(URL, {"email": "alex+apply@example.test", "password": "Synthetic-Pass1"}, "created_locally")
    assert await flow.handle()
    assert await page.evaluate("window.sent") is not None, getattr(engine, "result", None)
    email, retyped, passwords_match = await page.evaluate("window.sent")
    assert email == retyped == "alex+apply@example.test"
    assert passwords_match
