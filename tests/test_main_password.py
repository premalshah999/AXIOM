"""The main password and the random fallback, driven through the real Adapter in a real browser against a stateful employer
site this test hosts (wave 13, passwords).

The owner (2026-11-01): one main password is tried first at sign-up and at reset; an employer that refuses it (rules not met,
"different from your previous 3 passwords", too like a name) gets a random password that meets the page's stated rules, kept
for that employer in the account manager; sign-in tries the saved password, then the main one, then a reset, and never more
tries than the employer's lockout allows. Only the mailbox is faked."""

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from jobpilot.accounts import password_rules
from jobpilot.adapters.base import Adapter
from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

MAIN = "Mainpass-2026!x"
EMAIL = "alex@example.test"
RULES = "Password must be 12 to 16 characters, with an uppercase letter, a lowercase letter, a digit and a symbol, and must not contain the word Mainpass."


def page_html(body, title="Careers"):
    return f"<!doctype html><html><head><title>{title}</title></head><body>{body}</body></html>"


class Employer:
    """A career site with an account system. `refuses` names what it turns away; counts everything it is sent."""

    def __init__(self, *, refuses=("rules",), lock_after=None, announce=True, users=None, silent=False):
        self.users = dict(users or {})  # email -> password
        self.history = {}  # email -> passwords it used before
        self.refuses, self.lock_after, self.announce = set(refuses), lock_after, announce
        self.signins, self.signups, self.resets, self.mails, self.failures = [], [], [], 0, 0
        self.locked = False
        self.token = ""
        self.silent = silent  # its reset email never comes
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def reply(self, body, code=200):
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                url = urlsplit(self.path)
                path = url.path
                if path == "/signup":
                    return self.reply(site.signup_page())
                if path == "/forgot":
                    return self.reply(site.forgot_page())
                if path == "/reset":
                    return self.reply(site.reset_page())
                if path == "/home":
                    return self.reply(page_html("<h2>Your applications</h2><p>You are signed in.</p>"))
                return self.reply(site.login_page())

            def do_POST(self):
                size = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(size).decode()).items()}
                path = urlsplit(self.path).path
                if path == "/signup":
                    return self.reply(site.signup(form))
                if path == "/login":
                    return self.reply(site.login(form))
                if path == "/forgot":
                    site.mails += 1
                    site.token = "" if site.silent else f"t{site.mails}"
                    return self.reply(page_html("<h3>Check your email</h3><p>If an account exists, we emailed you a link.</p>"))
                if path == "/reset":
                    return self.reply(site.reset(form))
                self.reply(site.login_page())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # ----- policy ------------------------------------------------------------------------------------------------
    def problem(self, email, password):
        if "rules" in self.refuses and (
            not 12 <= len(password) <= 16
            or "mainpass" in password.lower()
            or not re.search(r"[A-Z]", password) or not re.search(r"[a-z]", password)
            or not re.search(r"\d", password) or not re.search(r"[^A-Za-z0-9]", password)
        ):
            return RULES
        if "history" in self.refuses and password in self.history.get(email, []):
            return "Your new password must be different from your previous 3 passwords."
        return ""

    # ----- pages -------------------------------------------------------------------------------------------------
    def login_page(self, error=""):
        notice = f"<p>Your account will be locked after {self.lock_after} failed attempts.</p>" if self.lock_after and self.announce else ""
        message = f'<p class="error" role="alert">{error}</p>' if error else ""
        return page_html(
            f'<h2>Sign in</h2>{notice}{message}<form method=post action="/login"><label for=e>Email</label><input id=e name=e type=email>'
            '<label for=p>Password</label><input id=p name=p type=password><input type=submit value="Sign in"></form>'
            '<a href="/forgot">Forgot your password?</a> <a href="/signup">Create account</a>'
        )

    def signup_page(self, error=""):
        message = f'<p class="error" role="alert">{error}</p>' if error else ""
        return page_html(
            f'<h2>Create your account</h2>{message}<form method=post action="/signup"><label for=e>Email</label><input id=e name=e type=email>'
            '<label for=p>Password</label><input id=p name=p type=password>'
            '<label for=q>Confirm password</label><input id=q name=q type=password>'
            '<input type=submit value="Create account"></form><a href="/login">Sign in</a>'
        )

    def forgot_page(self):
        return page_html(
            '<h2>Reset your password</h2><form method=post action="/forgot"><label for=e>Email</label><input id=e name=e type=email>'
            '<input type=submit value="Send reset link"></form>'
        )

    def reset_page(self, error=""):
        message = f'<p class="error" role="alert">{error}</p>' if error else ""
        return page_html(
            f'<h2>Choose a new password</h2>{message}<form method=post action="/reset"><label for=p>New password</label><input id=p name=p type=password>'
            '<label for=q>Confirm new password</label><input id=q name=q type=password>'
            '<input type=submit value="Reset password"></form>'
        )

    # ----- actions -----------------------------------------------------------------------------------------------
    def signup(self, form):
        email, password = form.get("e", EMAIL), form.get("p", "")
        self.signups.append(password)
        if error := self.problem(email, password):
            return self.signup_page(error)
        self.users[email] = password
        return page_html("<h2>Account created</h2><p>Welcome. <a href='/home'>Continue</a></p>")

    def login(self, form):
        email, password = form.get("e", ""), form.get("p", "")
        self.signins.append(password)
        if self.locked:
            return self.login_page("Sorry, you can't log in right now due to unsuccessful login attempts. Try again in 30 minutes.")
        if self.users.get(email) == password:
            self.failures = 0
            return page_html("<h2>Your applications</h2><p>You are signed in.</p>")
        self.failures += 1
        if self.lock_after and self.failures >= self.lock_after:
            self.locked = True
            return self.login_page("Your account is locked after too many failed attempts. Try again in 30 minutes.")
        left = f" {self.lock_after - self.failures} attempts remaining." if self.lock_after and self.announce else ""
        return self.login_page("Invalid email or password." + left)

    def reset(self, form):
        password = form.get("p", "")
        self.resets.append(password)
        if error := self.problem(EMAIL, password):
            return self.reset_page(error)
        self.history.setdefault(EMAIL, []).append(self.users.get(EMAIL, ""))
        self.users[EMAIL] = password
        self.failures, self.locked = 0, False
        return page_html("<h2>Password updated</h2><p>Your password was changed. <a href='/login'>Sign in</a></p>")


class FakeInbox:
    """The one thing that cannot be real: a mailbox. It hands back the link the site's reset email would carry."""

    configured = True

    def __init__(self, site):
        self.site, self.armed = site, []

    async def rule(self, *_a):
        return True

    def live(self, challenge):
        return bool(challenge)

    async def arm(self, run_id, job, email, kind, **_k):
        self.armed.append(kind)
        return {"id": "c1", "since": 0}

    async def wait(self, challenge, max_wait_seconds=0):
        return {"kind": "link", "value": self.site.url + "/reset?token=" + self.site.token} if self.site.token else None

    def finish(self, *_a):
        pass

    def reason(self, *_a):
        return ""


@pytest.fixture
def make_site():
    made = []

    def make(**kwargs):
        made.append(Employer(**kwargs))
        return made[-1]

    yield make
    for site in made:
        site.close()


async def build(page, service, tmp_path, site, url):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF")
    profile = Profile(first_name="Alex", last_name="Rivera", email=EMAIL, allow_account_creation=True,
                      accept_all_application_terms=True)
    form = FormSession(page, Resolver(profile, service.db, service.config), resume, lambda *a, **k: None)
    events = []

    async def nothing(*_a, **_k):
        return None

    async def false(*_a, **_k):
        return False

    inbox = FakeInbox(site)
    engine = SimpleNamespace(
        service=SimpleNamespace(db=service.db, config=service.config, inbox=inbox, mail=service.mail),
        config=service.config, form=form, page=page, profile=profile, run_id="run-1",
        job={"id": "j1", "url": url, "ats": "custom"}, run_record={"packet": {}}, result=None,
        emit=lambda kind, message, data=None: events.append((kind, message)),
        redact=lambda u: u, extend=lambda *_: None, trace=nothing, refresh_expired_entry=false, solve_captcha=nothing,
        email_verification=SimpleNamespace(prepare_submission=nothing), browser_challenge=nothing,
    )
    adapter = Adapter(engine)
    adapter.sign_in_page = None
    adapter.max_steps = 8
    return adapter, events


def with_main(service, password=MAIN):
    object.__setattr__(service.config, "application_password", password)


def kind_of(service, url):
    from jobpilot.accounts import AccountStore
    from jobpilot.accounts.overview import password_kind

    return password_kind(service, AccountStore(service, EMAIL, url).record(url))


async def test_sign_up_uses_the_main_password(page, service, tmp_path, make_site):
    site = make_site(refuses=())
    with_main(service)
    await page.goto(site.url + "/signup")
    adapter, events = await build(page, service, tmp_path, site, site.url + "/signup")
    await adapter.drive()
    assert site.signups == [MAIN], events
    record = adapter.accounts.record(site.url + "/signup")
    assert record["state"] == "authenticated"
    assert adapter.accounts.saved(site.url + "/signup")["password"] == MAIN
    assert kind_of(service, site.url + "/signup") == "main"


async def test_an_employer_that_refuses_the_main_password_gets_a_random_one_that_meets_its_rules(page, service, tmp_path, make_site):
    site = make_site(refuses=("rules",))
    with_main(service)
    await page.goto(site.url + "/signup")
    adapter, events = await build(page, service, tmp_path, site, site.url + "/signup")
    await adapter.drive()
    assert site.signups[0] == MAIN and len(site.signups) == 2, (site.signups, events)
    assert site.signups[1] != MAIN and not site.problem(EMAIL, site.signups[1])
    assert site.users[EMAIL] == site.signups[1]
    saved = adapter.accounts.saved(site.url + "/signup")
    assert saved["password"] == site.signups[1], "the account manager must hold the password the site accepted"
    assert adapter.accounts.record(site.url + "/signup")["state"] == "authenticated"
    assert kind_of(service, site.url + "/signup") == "own"
    assert MAIN not in str(events) and site.signups[1] not in str(events)


async def test_without_a_main_password_sign_up_still_works_and_a_refusal_falls_back(page, service, tmp_path, make_site):
    site = make_site(refuses=("rules",))
    with_main(service, "")
    await page.goto(site.url + "/signup")
    adapter, events = await build(page, service, tmp_path, site, site.url + "/signup")
    await adapter.drive()
    assert site.users.get(EMAIL), (site.signups, events)
    assert adapter.accounts.saved(site.url + "/signup")["password"] == site.users[EMAIL]


async def test_sign_in_uses_the_saved_password_first(page, service, tmp_path, make_site):
    site = make_site(refuses=(), users={EMAIL: "Ownpass-7788!zq"})
    with_main(service)
    await page.goto(site.url + "/login")
    adapter, _ = await build(page, service, tmp_path, site, site.url + "/login")
    adapter.accounts.mark(site.url + "/login", "authenticated", {"email": EMAIL, "password": "Ownpass-7788!zq"})
    await adapter.drive()
    assert site.signins == ["Ownpass-7788!zq"], site.signins
    assert site.mails == 0


async def test_sign_in_falls_to_the_main_password_before_any_reset(page, service, tmp_path, make_site):
    site = make_site(refuses=(), users={EMAIL: MAIN})
    with_main(service)
    await page.goto(site.url + "/login")
    adapter, events = await build(page, service, tmp_path, site, site.url + "/login")
    adapter.accounts.mark(site.url + "/login", "authenticated", {"email": EMAIL, "password": "Stale-password-1!"})
    await adapter.drive()
    assert site.signins == ["Stale-password-1!", MAIN], (site.signins, events)
    assert site.mails == 0, "a reset was asked for before the main password was tried"
    assert adapter.accounts.saved(site.url + "/login")["password"] == MAIN


async def test_reset_tries_the_main_password_first_then_a_random_one(page, service, tmp_path, make_site):
    """Lenovo: "must differ from your previous 3 passwords". The main password was used before, so the page refuses it."""
    site = make_site(refuses=("history", "rules"), users={EMAIL: "Forgotten-pw-9!k"})
    site.history[EMAIL] = [MAIN]
    with_main(service)
    await page.goto(site.url + "/login")
    adapter, events = await build(page, service, tmp_path, site, site.url + "/login")
    adapter.accounts.mark(site.url + "/login", "authenticated", {"email": EMAIL, "password": "Stale-password-1!"})
    await adapter.drive()
    assert site.resets[:1] == [MAIN], (site.resets, events)
    assert len(site.resets) == 2 and site.resets[1] != MAIN, (site.resets, events)
    assert site.users[EMAIL] == site.resets[1]
    assert adapter.accounts.saved(site.url + "/login")["password"] == site.resets[1]
    assert kind_of(service, site.url + "/login") == "own"


async def test_a_stated_lockout_stops_the_main_password_from_being_tried(page, service, tmp_path, make_site):
    """The page says two failed attempts lock the account: after the saved password failed, one more could lock it."""
    site = make_site(refuses=(), users={EMAIL: "Forgotten-pw-9!k"}, lock_after=2)
    with_main(service)
    await page.goto(site.url + "/login")
    adapter, events = await build(page, service, tmp_path, site, site.url + "/login")
    adapter.accounts.mark(site.url + "/login", "authenticated", {"email": EMAIL, "password": "Stale-password-1!"})
    await adapter.drive()
    # One failed sign-in, never a second: the reset set the main password (first choice), and the sign-in after it worked.
    assert site.signins == ["Stale-password-1!", MAIN], (site.signins, events)
    assert not site.locked and site.failures == 0
    assert site.mails == 1 and site.resets == [MAIN], "recovery must go to one reset that tries the main password first"


async def test_a_locked_account_is_left_alone(page, service, tmp_path, make_site):
    site = make_site(refuses=(), users={EMAIL: MAIN})
    site.locked = True
    with_main(service)
    await page.goto(site.url + "/login")
    adapter, events = await build(page, service, tmp_path, site, site.url + "/login")
    adapter.accounts.mark(site.url + "/login", "authenticated", {"email": EMAIL, "password": "Stale-password-1!"})
    result = await adapter.drive()
    assert len(site.signins) == 1, (site.signins, events)
    assert result.get("site_cooldown_seconds"), result


async def test_a_refused_password_is_not_typed_again_on_the_next_run(page, service, tmp_path, make_site):
    """Neither the saved password nor the main one works and no reset mail comes: the next run does not type them again."""
    site = make_site(refuses=(), users={EMAIL: "Forgotten-pw-9!k"}, silent=True)
    with_main(service)
    await page.goto(site.url + "/login")
    first, _ = await build(page, service, tmp_path, site, site.url + "/login")
    first.accounts.mark(site.url + "/login", "authenticated", {"email": EMAIL, "password": "Stale-password-1!"})
    await first.drive()
    assert site.signins == ["Stale-password-1!", MAIN], site.signins
    tried, mails = list(site.signins), site.mails
    await page.goto(site.url + "/login")
    second, events = await build(page, service, tmp_path, site, site.url + "/login")
    await second.drive()
    assert site.signins == tried, (site.signins, events)
    assert site.mails <= mails + 1


def test_rules_are_read_from_the_page_and_the_password_meets_them():
    rules = password_rules.read(RULES)
    assert (rules.minimum, rules.maximum) == (12, 16)
    for _ in range(50):
        assert password_rules.meets(password_rules.generate(rules), rules)
    short = password_rules.read("Your password must be at least 8 characters and no more than 10 characters. No special characters.")
    assert short.maximum == 10 and not short.special
    for _ in range(50):
        p = password_rules.generate(short)
        assert password_rules.meets(p, short) and p.isalnum()


def test_a_refused_login_is_not_a_password_rule():
    assert not password_rules.refused("Invalid email or password.")
    assert not password_rules.refused("Your password was incorrect.")
    assert password_rules.refused("Your new password must be different from your previous 3 passwords.")
    assert password_rules.refused("The password is too similar to your name.")


def test_a_lockout_is_read_from_the_page():
    assert password_rules.tries_left("Invalid password. 1 attempt remaining.") == 1
    assert password_rules.lock_threshold("Your account will be locked after 3 failed attempts.") == 3
    assert password_rules.may_try_again(0, "") and not password_rules.may_try_again(2, "locked after 3 failed attempts")
    assert not password_rules.may_try_again(0, "1 attempt remaining")
