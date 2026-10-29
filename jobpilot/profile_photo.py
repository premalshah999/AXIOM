"""The applicant's profile photo, for the applications that ask for one ("Photo  Choose file", Bask 2026-10-29).

Kept like a resume: a file under the data directory (`profile/`), described by the `profile_photo` setting. A form's photo
upload gets it when one is saved; without one an optional field is left empty and a required one becomes one clear question
("Upload a profile photo in Setup › Profile"). An ID document is not a profile photo and never takes it.
"""

import hashlib
import re
from pathlib import Path

from .db import now

SETTING = "profile_photo"
MAX_BYTES = 5 * 1024 * 1024
QUESTION = "Upload a profile photo in Setup › Profile"
KEY = "profile:photo"

# Magic numbers of the image types employers accept in a photo field.
KINDS = (
    (b"\xff\xd8\xff", ".jpg", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"GIF87a", ".gif", "image/gif"),
    (b"GIF89a", ".gif", "image/gif"),
)
# A field asking for the applicant's photo, by its label or hint ("Photo", "Upload a headshot", "Profile picture").
WANTS_PHOTO = re.compile(r"\b(?:photos?|pictures?|headshots?|portraits?|profilbild|foto)\b", re.I)
# ... and not a document: "Photo ID", "government-issued photo identification", a "picture of your degree".
NOT_PROFILE_PHOTO = re.compile(
    r"photo[- ]?id\b|\bid\b.{0,12}\bphoto|photo identification|government|passport|driver|licen[cs]e|"
    r"(?:picture|photo|copy|image) of (?:your |the |a )?(?:id|degree|diploma|certificate|transcript|resume|cv)",
    re.I,
)


def wants_photo(field):
    """True when a file field asks for the applicant's profile photo."""
    if field.get("type") != "file":
        return False
    text = " ".join(str(field.get(k) or "") for k in ("label", "hint", "name"))
    return bool(WANTS_PHOTO.search(text)) and not NOT_PROFILE_PHOTO.search(text)


def sniff(content):
    """(extension, content type) of an accepted image, else None."""
    for magic, suffix, media in KINDS:
        if content.startswith(magic):
            return suffix, media
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


def folder(data_dir):
    path = Path(data_dir) / "profile"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def describe(db):
    """The saved photo's description (no path), or {"saved": False}."""
    entry = db.get_setting(SETTING, {}) or {}
    if not entry.get("file"):
        return {"saved": False}
    return {"saved": True, **{k: entry[k] for k in ("name", "content_type", "size", "sha256", "saved_at") if k in entry}}


def path_for(db, data_dir):
    """The saved photo's file, or None."""
    entry = db.get_setting(SETTING, {}) or {}
    name = Path(str(entry.get("file") or "")).name
    if not name or name in {".", ".."}:
        return None
    path = Path(data_dir) / "profile" / name
    return path if path.is_file() else None


def save(db, data_dir, filename, content):
    """Keep `content` as the profile photo (replacing an earlier one). Raises ValueError for anything but a small image."""
    if not content or len(content) > MAX_BYTES:
        raise ValueError("Upload an image no larger than 5 MB")
    found = sniff(content)
    if not found:
        raise ValueError("Upload a JPEG, PNG, GIF or WebP image")
    suffix, media = found
    sha = hashlib.sha256(content).hexdigest()
    stored = f"photo-{sha[:16]}{suffix}"
    target = folder(data_dir) / stored
    target.write_bytes(content)
    target.chmod(0o600)
    old = (db.get_setting(SETTING, {}) or {}).get("file")
    entry = {
        "file": stored,
        "name": re.sub(r"[^\w.-]+", "_", Path(str(filename or "photo")).name).strip("._")[-80:] or "photo" + suffix,
        "content_type": media,
        "size": len(content),
        "sha256": sha,
        "saved_at": now(),
    }
    db.set_setting(SETTING, entry)
    if old and Path(old).name != stored:
        (folder(data_dir) / Path(old).name).unlink(missing_ok=True)
    return describe(db)


def remove(db, data_dir):
    """Forget the profile photo and delete its file. True when one was saved."""
    entry = db.get_setting(SETTING, {}) or {}
    db.set_setting(SETTING, {})
    name = Path(str(entry.get("file") or "")).name
    if name:
        (folder(data_dir) / name).unlink(missing_ok=True)
    return bool(name)
