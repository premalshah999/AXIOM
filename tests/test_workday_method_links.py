from types import SimpleNamespace
import pytest
from jobpilot.workday import WorkdayAuth


@pytest.mark.parametrize("tag", ["a", "button"])
@pytest.mark.parametrize("method,expected", [
    ("Autofill with Resume", "autofillWithResume"),
    ("Apply Manually", "applyManually"),
])
async def test_account_flow_advances_observed_method_regardless_of_tag(page, tag, method, expected):
    await page.set_content(f'''<div role=dialog>
      <{tag} data-automation-id=autofillWithResume onclick="window.chosen='autofillWithResume'">Autofill with Resume</{tag}>
      <{tag} data-automation-id=applyManually onclick="window.chosen='applyManually'">Apply Manually</{tag}>
      </div>''')
    auth = object.__new__(WorkdayAuth)
    auth.engine = SimpleNamespace(page=page, workday_method=method, emit=lambda *args: None)
    async def click(control):
        await control.click()
    auth.click = click
    assert await auth.advance_apply_method()
    assert await page.evaluate("window.chosen") == expected


async def test_account_method_does_not_choose_unrelated_or_ambiguous_controls(page):
    await page.set_content('<a>Apply Manually</a><a data-automation-id=autofillWithResume>One</a><a data-automation-id=autofillWithResume>Two</a>')
    auth = object.__new__(WorkdayAuth)
    auth.engine = SimpleNamespace(page=page, emit=lambda *args: None)
    async def unexpected(control):
        pytest.fail("Ambiguous or unrelated control clicked")
    auth.click = unexpected
    assert not await auth.advance_apply_method()


async def test_a_social_sign_in_chooser_opens_the_email_form_in_any_locale(page):
    # RTX (2026-10-18): Google, LinkedIn or email; the fr-CA labels matched no English button name.
    await page.set_content('''<div data-automation-id=signInContent>
      <button data-automation-id=GoogleSignInButton onclick="window.chosen='google'">Se connecter avec Google</button>
      <button data-automation-id=SignInWithEmailButton onclick="window.chosen='email'">Se connecter avec l'e-mail</button>
      </div>''')
    auth = object.__new__(WorkdayAuth)
    auth.engine = SimpleNamespace(page=page, emit=lambda *args: None)
    async def click(control):
        await control.click()
    auth.click = click
    assert await auth.choose_email_sign_in()
    assert await page.evaluate("window.chosen") == "email"
    await page.set_content("<button>Sign In</button>")
    assert not await auth.choose_email_sign_in()
