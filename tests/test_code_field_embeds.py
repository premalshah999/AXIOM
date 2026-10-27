"""Wipro's SuccessFactors job page (2026-10-26) embeds a YouTube player; a field in it was taken for the emailed-code
box and the code was refused as "on an unexpected website"."""

from types import SimpleNamespace

from support_icims import adapter

PAGE = "https://careers-acme.icims.com/jobs/42/x/login"


def obs():
    return {"url": PAGE, "text": "Enter the verification code we sent you", "errors": [], "headings": [], "controls": [],
            "fields": [{"id": "yt", "type": "text", "label": "Verification code", "maxlength": -1},
                       {"id": "own", "type": "text", "label": "Verification code", "maxlength": -1}]}


def test_a_field_in_a_video_frame_is_never_the_code_box(service):
    value = adapter(service, obs())
    value.form.frames = {"yt": SimpleNamespace(url="https://www.youtube-nocookie.com/embed/abc"),
                         "own": SimpleNamespace(url=PAGE)}
    assert [f["id"] for f in value.code_fields(obs())] == ["own"]
