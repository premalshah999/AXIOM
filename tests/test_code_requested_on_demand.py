"""ByteDance (2026-10-27): "Create account" opened a form with an Email box, an "Enter Email Verification Code" box and
a "Get code" button that stays disabled until an email is typed. The run read the code box as "a code was sent", typed
nothing and pressed nothing, then waited 20 minutes for a code that was never requested. The email is now typed and the
code requested before the wait. Synthetic data only."""

from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from jobpilot.adapters.base import Adapter


def field(fid, label, value=""):
    return {"id": fid, "type": "text", "label": label, "value": value, "automation_id": "", "maxlength": -1}


def control(cid, label):
    return {"id": cid, "label": label, "aria_label": "", "href": "", "automation_id": ""}


class Probe(Adapter):
    mail_rule_known = True


def adapter(pages, disabled=()):
    value = Probe.__new__(Probe)
    log = []
    fill = AsyncMock(side_effect=lambda v, **k: log.append(("fill", v)))
    scans = iter(pages)
    last = {}

    async def scan():
        last["obs"] = next(scans, last.get("obs"))
        return last["obs"]

    form = SimpleNamespace(
        scan=scan, settle=AsyncMock(), meta={"disabled_actions": list(disabled)}, frames={},
        locator=lambda _id: SimpleNamespace(fill=fill, blur=AsyncMock()),
    )
    value.e = SimpleNamespace(form=form, emit=lambda *a, **k: None)
    value.inbox = SimpleNamespace(configured=True, live=lambda _c: True)
    value.accounts = SimpleNamespace(email="alex@example.test", password_allowed=lambda *a: True)
    value.attempts = Counter()
    value.challenge = {"id": "ch"}
    value.arm_mail = AsyncMock(return_value=True)
    value.after_auth_click = AsyncMock(return_value=None)
    value.click = AsyncMock(side_effect=lambda b, **k: log.append(("click", b["label"])))

    async def wait_email(kind):
        log.append(("wait", kind))
        return None

    value.wait_email = wait_email
    value.mail_problem = lambda what: "no " + what
    value.defer = lambda reason, *a, **k: {"reason": reason}
    value.emit = MagicMock()
    return value, log


SIGN_UP = [field("e", "Email"), field("c", "Enter Email Verification Code")]


async def test_the_email_is_typed_and_the_code_requested_before_waiting():
    before = {"fields": SIGN_UP, "controls": [control("go", "Continue")], "text": "Create account"}
    after = {"fields": [field("e", "Email", "alex@example.test"), SIGN_UP[1]],
             "controls": [control("get", "Get code"), control("go", "Continue")], "text": "Create account"}
    value, log = adapter([after], disabled=["Get code"])
    await Adapter.on_email_code(value, before)
    assert log[:3] == [("fill", "alex@example.test"), ("click", "Get code"), ("wait", "code")]
    value.arm_mail.assert_awaited_with("code")


async def test_a_code_step_whose_email_is_already_typed_only_waits():
    page = {"fields": [field("e", "Email", "alex@example.test"), SIGN_UP[1]],
            "controls": [control("get", "Get code"), control("go", "Continue")], "text": "Enter the code we sent"}
    value, log = adapter([page])
    await Adapter.on_email_code(value, page)
    assert log == [("wait", "code")]


async def test_without_a_send_code_control_nothing_is_typed():
    page = {"fields": SIGN_UP, "controls": [control("go", "Continue")], "text": "Enter the code"}
    value, log = adapter([page])
    await Adapter.on_email_code(value, page)
    assert log == [("wait", "code")]
