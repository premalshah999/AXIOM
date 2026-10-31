"""Applications that ask for a photo ("Photo Choose file", Bask 2026-10-29) took the owner's time for a file the profile can
hold. Setup › Profile keeps one photo (API: /api/profile/photo); a photo upload gets it, an optional one without it is left
empty, a required one becomes one clear question."""

import pytest

from jobpilot import profile_photo
from jobpilot.answers import Resolver
from jobpilot.forms import FormSession
from jobpilot.schemas import Profile

PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic image bytes" * 4
JPEG = b"\xff\xd8\xff\xe0" + b"synthetic image bytes" * 4


def test_which_file_fields_ask_for_a_photo():
    ask = lambda label, **more: profile_photo.wants_photo({"type": "file", "label": label, **more})  # noqa: E731
    for label in ("Photo Choose file", "Upload a headshot", "Profile picture", "Photo"):
        assert ask(label), label
    assert ask("Attachment", hint="Please attach a recent photo of yourself")
    for label in ("Photo ID", "Government-issued photo identification", "Picture of your degree certificate",
                  "Upload your resume", "Driver's license photo"):
        assert not ask(label), label
    assert not profile_photo.wants_photo({"type": "text", "label": "Photo"})


def test_save_replace_remove(service):
    db, data = service.db, service.config.data_dir
    assert profile_photo.describe(db) == {"saved": False} and profile_photo.path_for(db, data) is None
    first = profile_photo.save(db, data, "me.png", PNG)
    assert first["saved"] and first["content_type"] == "image/png" and first["size"] == len(PNG)
    old = profile_photo.path_for(db, data)
    assert old.read_bytes() == PNG and old.stat().st_mode & 0o777 == 0o600
    profile_photo.save(db, data, "me.jpg", JPEG)
    assert not old.exists() and profile_photo.path_for(db, data).suffix == ".jpg"
    assert profile_photo.remove(db, data) is True and profile_photo.path_for(db, data) is None
    assert profile_photo.remove(db, data) is False


@pytest.mark.parametrize("content", [b"", b"%PDF-1.4 not an image", b"x" * 20, PNG + b"0" * profile_photo.MAX_BYTES])
def test_only_small_images_are_kept(service, content):
    with pytest.raises(ValueError):
        profile_photo.save(service.db, service.config.data_dir, "p.png", content)


def test_the_api_uploads_shows_and_removes_the_photo(client):
    assert client.get("/api/profile/photo").json() == {"saved": False}
    assert client.get("/api/profile/photo/file").status_code == 404
    sent = client.post("/api/profile/photo", files={"file": ("me.png", PNG, "image/png")})
    assert sent.status_code == 200 and sent.json()["saved"] is True
    assert client.get("/api/profile/photo").json()["name"] == "me.png"
    assert client.get("/api/profile/photo/file").content == PNG
    bad = client.post("/api/profile/photo", files={"file": ("cv.pdf", b"%PDF-1.4 x", "application/pdf")})
    assert bad.status_code == 400
    assert client.delete("/api/profile/photo").json() == {"deleted": True}
    assert client.get("/api/profile/photo").json() == {"saved": False}


def form(page, service, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4")
    return FormSession(page, Resolver(Profile(), service.db, service.config), resume, lambda *a, **k: None)


PAGE = (
    "<label for=r>Resume</label><input id=r type=file required>"
    "<label for=p>Photo <span>Choose file</span></label><input id=p type=file {required}>"
)


async def test_a_saved_photo_goes_to_the_photo_field_not_the_resume(page, service, tmp_path):
    profile_photo.save(service.db, service.config.data_dir, "me.png", PNG)
    await page.set_content(PAGE.format(required=""))
    session = form(page, service, tmp_path)
    await session.scan()
    assert await session.attach_owner_documents() is True
    names = await page.evaluate("[...document.querySelectorAll('input[type=file]')].map(e => e.files[0]?.name || '')")
    assert names[0] == "" and names[1].startswith("photo-") and names[1].endswith(".png")


async def test_a_lone_photo_field_is_not_taken_for_the_resume(page, service, tmp_path):
    await page.set_content("<label for=p>Photo</label><input id=p type=file>")
    session = form(page, service, tmp_path)
    await session.scan()
    assert session.resume_fields() == []


async def test_no_photo_leaves_an_optional_field_alone_and_asks_once_for_a_required_one(page, service, tmp_path):
    await page.set_content(PAGE.format(required=""))
    session = form(page, service, tmp_path)
    await session.scan()
    assert await session.attach_owner_documents() is False
    assert not any(q.get("key") == profile_photo.KEY for q in session.pending)
    await page.set_content(PAGE.format(required="required"))
    session = form(page, service, tmp_path)
    await session.scan()
    report = await session.verify()
    asked = [q for q in report["pending"] if q.get("key") == profile_photo.KEY]
    assert len(asked) == 1 and asked[0]["owner_question"] == "Upload a profile photo in Profile › Identity"
    assert asked[0]["type"] == "file"


async def test_the_owners_file_for_the_photo_question_becomes_the_profile_photo(service):
    from jobpilot.db import Review

    from test_waiting_email_state import seed

    run_id = seed(service, "photo")
    ask = {"question": "Photo Choose file", "options": [], "key": profile_photo.KEY, "reason": "Required profile photo",
           "blocker_type": "missing_fact", "type": "file", "required": True, "field_id": "p", "scope": {},
           "owner_question": profile_photo.QUESTION}
    service.complete(run_id, {"state": "needs_review", "reviews": [ask, dict(ask)]}, 30.0)
    with service.db.session() as s:
        rows = s.query(Review).where(Review.run_id == run_id).all()
        assert [r.question for r in rows] == [profile_photo.QUESTION]
        review_id = rows[0].id
    await service.resolve_review_file(review_id, "me.png", PNG)
    assert profile_photo.describe(service.db)["saved"] is True
    with service.db.session() as s:
        assert s.get(Review, review_id).answer.startswith("File: ")
