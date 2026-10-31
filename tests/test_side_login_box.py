"""Lenovo (Avature, 2026-10-29): after the one registration route (resume upload, Continue), the application form
showed Avature's "Already registered?" box beside it: its own small <form> with Username and Password. Read as a
sign-in gate, the form's verification said auth_required, the saved login was posted to /Login, refused, and every
rerun ended "reset the password in your own browser" for an account that never existed. That box is not a sign-in
step; a sign-in page, or a login beside a registration form, still is."""

from collections import Counter
from types import SimpleNamespace

from jobpilot.adapters.base import Adapter, Step
from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

SIDE = """<aside><h3>Already registered?</h3><form action="/en_US/careers/Login" method=post>
  <label>Username <input name=username required></label><label>Password <input type=password name=password required></label>
  <button>LOGIN</button></form></aside>"""
APPLICATION = """<form id=apply action="/en_US/careers/ApplicationForm" method=post><h2>Personal information</h2>
  <label>First name <input name=first required></label><label>Last name <input name=last required></label>
  <label>Email <input type=email name=email required></label><label>Phone <input name=phone required></label>
  <label>City <input name=city required></label><label>Country <select name=country required><option>United States</option></select></label>
  <button>Submit</button></form>"""
REGISTRATION = """<form id=register action="/register" method=post><h2>New user</h2>
  <label>First name <input name=first></label><label>Last name <input name=last></label><label>Email <input type=email name=email></label>
  <label>Phone <input name=phone></label><label>Password <input type=password name=new></label>
  <label>Confirm password <input type=password name=confirm></label><button>Register</button></form>"""


def adapter():
    value = Adapter.__new__(Adapter)
    value.committed, value.attempts = False, Counter()
    value.e = SimpleNamespace(form=SimpleNamespace(frames={}, page=SimpleNamespace(url="")))
    return value


async def scanned(page, tmp_path, html):
    await page.set_content(html)
    form = FormSession(page, Resolver(Profile(), None, None), tmp_path / "r.pdf", lambda *a, **k: None)
    return form, await form.scan()


async def test_the_returning_applicant_box_beside_the_application_is_not_a_sign_in(page, tmp_path):
    form, obs = await scanned(page, tmp_path, "<main>" + APPLICATION + SIDE + "</main>")
    side = {f["label"]: f.get("side_login") for f in obs["fields"]}
    assert side["Username"] and side["Password"] and not side["First name"]
    assert not await form.authentication_required()
    assert adapter().classify_page(obs) != Step.SIGN_IN


async def test_a_sign_in_page_is_still_a_sign_in(page, tmp_path):
    _, obs = await scanned(page, tmp_path, "<main>" + SIDE + "<a href='/register'>Register</a></main>")
    assert not any(f.get("side_login") for f in obs["fields"])
    assert adapter().classify_page(obs) == Step.SIGN_IN


async def test_a_login_beside_a_registration_form_keeps_its_passwords(page, tmp_path):
    _, obs = await scanned(page, tmp_path, "<main>" + REGISTRATION + SIDE + "</main>")
    assert not any(f.get("side_login") for f in obs["fields"])
    assert adapter().classify_page(obs) in {Step.SIGN_IN, Step.CREATE_ACCOUNT}


# City National Bank (iCIMS, 2026-10-31): the employer's own "Online Banking" login drawer opened over its job page.
# Read as the applicant's sign-in, the saved login was typed into the bank's login and five runs ended on a 6 s click
# timeout. A customer login in the site's chrome is not a sign-in step; the drawer is closed to reach Apply.
BANK = """<header><a href="/">Example Bank</a><div id=drawer role=dialog><h2>Log in to your account.</h2>
  <form action="/bank/login"><label>Select Account <select name=product><option>Online Banking</option></select></label>
  <label>Username <input name=user required></label><label>Password <input type=password name=pw required></label>
  <button>Submit</button></form><button aria-label="Close Online Banking" onclick="drawer.remove()">x</button></div></header>"""
JOB = "<main><h1>Data Analyst</h1><p>Overview of the role.</p><a href='/apply' class=apply>Apply for this job online</a></main>"


async def test_a_customer_login_drawer_in_the_site_chrome_is_not_a_sign_in(page, tmp_path):
    form, obs = await scanned(page, tmp_path, BANK + JOB)
    assert all(f.get("side_login") for f in obs["fields"] if f["type"] in {"password", "text"})
    assert not await form.authentication_required()
    assert adapter().classify_page(obs) != Step.SIGN_IN
    assert await page.locator("[data-jp-chrome-close]").count() == 1


async def test_an_applicant_sign_in_that_mentions_banking_roles_stays_a_sign_in(page, tmp_path):
    html = "<main><h2>Candidate sign in for banking careers</h2>" + SIDE.replace("aside", "div") + "</main>"
    _, obs = await scanned(page, tmp_path, html)
    assert not any(f.get("side_login") for f in obs["fields"])
