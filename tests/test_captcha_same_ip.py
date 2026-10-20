"""Owner-authorized 2026-10-14: hCaptcha image challenges are answered in this browser, so the
token is issued to this IP (Honeywell/JPMorgan reject solver-IP tokens)."""

from types import SimpleNamespace

from jobpilot import captcha_same_ip
from jobpilot.captcha_visual import CaptchaSolver

CONFIG = SimpleNamespace(
    twocaptcha_api_key="k", captcha_timeout=120, captcha_max_attempts=2, captcha_image_rounds=3
)


async def no_checkbox(page):
    return None


async def test_visible_challenge_is_answered_in_this_browser(monkeypatch):
    seen = {}

    async def challenge(page):
        return "frame", "owner", "hcaptcha challenge"

    async def visual(self, page, frame, owner):
        seen["rounds"] = self.config.captcha_max_rounds
        self.last_reason = "Website accepted the image challenge"
        return True

    monkeypatch.setattr(captcha_same_ip, "checkbox_frame", no_checkbox)
    monkeypatch.setattr(captcha_same_ip, "challenge_frame", challenge)
    monkeypatch.setattr(CaptchaSolver, "visual", visual)
    assert await captcha_same_ip.solve_same_ip(CONFIG, object(), lambda *a: None) == (
        True,
        "Website accepted the image challenge",
    )
    assert seen["rounds"] == 3


async def test_no_challenge_falls_back_to_the_token_path(monkeypatch):
    async def none(page):
        return None

    monkeypatch.setattr(captcha_same_ip, "checkbox_frame", no_checkbox)
    monkeypatch.setattr(captcha_same_ip, "challenge_frame", none)
    accepted, reason = await captcha_same_ip.solve_same_ip(CONFIG, object(), lambda *a: None)
    assert not accepted and "No hCaptcha image challenge" in reason


async def test_without_a_key_nothing_is_sent():
    config = SimpleNamespace(**{**vars(CONFIG), "twocaptcha_api_key": ""})
    assert await captcha_same_ip.solve_same_ip(config, object(), lambda *a: None) == (
        False,
        "2Captcha is not configured",
    )
