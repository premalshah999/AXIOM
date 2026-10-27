"""ZincFive's ADP sign-in (2026-10-27): the cookie banner, announced as a dialog with a "manage your preferences" link
and an "x", hid the email box and Continue; three runs stopped on "only a privacy dialog with Close"."""

from jobpilot.adapters.base import Adapter

BANNER = ("We use our own and third-party cookies and similar technologies to enhance and personalize your experience. "
          "For more information, please see our Cookie Privacy Statement. To manage your preferences, click here")


def obs(text):
    return {"url": "https://workforcenow.adp.com/x", "text": "Sign In", "headings": ["Sign In"],
            "fields": [{"id": "email", "type": "text", "label": "Email or Mobile Number"}],
            "controls": [{"id": "go", "label": "Continue"}, {"id": "prefs", "label": "To manage your preferences, click here"},
                         {"id": "x", "label": "×"}],
            "dialogs": [{"field_ids": [], "control_ids": ["prefs", "x"], "text": text, "title": ""}]}


def test_a_cookie_banner_is_not_the_step():
    assert Adapter.dialog_view(obs(BANNER)) is None


def test_a_real_dialog_still_is():
    view = Adapter.dialog_view(obs("Your session is about to expire. Stay signed in?"))
    assert view and view["dialog"]
