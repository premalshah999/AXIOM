"""Owned failure viewports with secret redaction and evidence required before delivery.

Ordinary application fields remain readable; secrets never appear in the image.
No page text, field values, authentication URLs or questions enter the metadata.
The caller persists the returned metadata in the same run's packet. Legacy images
without that binding are deliberately unsuitable for Telegram delivery.
"""

import asyncio
import hashlib
import json
import re
import secrets
import time
from pathlib import Path


HELP_STATES = frozenset(
    {
        "waiting_browser",
        "waiting_answer",
        "needs_review",
        "timed_out",
        "failed",
        "submission_unknown",
    }
)
MAX_IMAGE_BYTES = 9_000_000
MAX_AGE_SECONDS = 7 * 24 * 3600
_SECRET_FIELDS = (
    'input[type=password],input[autocomplete="one-time-code"],input[autocomplete=username],'
    "input[name*=otp i],input[name*=passcode i],input[name*=token i],"
    'input[name*=password i],input[name*=verification i],input[inputmode=numeric][maxlength="1"],'
    "textarea[name*=captcha-response i],"
    "[data-private],[data-sensitive]"
)
_AUTH_LABEL = re.compile(
    r"password|verification code|security code|one[ -]time|passcode|\botp\b|api[ _-]?key|^(?:code|pin)$", re.I
)
_SECRET_TEXT = re.compile(
    r"\b(?:password|verification\s+code|security\s+code|one[ -]time\s+code|otp|token|api[ _-]?key)\s*[:=]\s*\S+",
    re.I,
)


def _digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def _question(value):
    return _digest(" ".join(str(value).split()).casefold())


def _discard(directory):
    for name in ("help.jpg", "help.json", *(f"help-q{n}.jpg" for n in range(1, 10))):
        (directory / name).unlink(missing_ok=True)


def _focus_field(form, result):
    pending = result.get("reviews") or getattr(form, "pending", [])
    for item in pending:
        scope = item.get("scope", {})

        def matches_item(field, item=item, scope=scope):
            return (
                _question(field.get("label", "")) == _question(item.get("question", ""))
                and all(field.get(k, "") == v for k, v in scope.items() if v)
                and (not item.get("type") or item["type"] == field.get("type"))
            )

        if item.get("field_id") in form.fields:
            field = form.fields[item["field_id"]]
            if matches_item(field):
                return field
        matches = [f for f in form.fields.values() if matches_item(f)]
        if len(matches) == 1:
            return matches[0]
    if pending:
        raise ValueError("Failure field no longer identifies a unique observed control")
    return next((f for f in form.fields.values() if f.get("error") or not f.get("valid", True)), None)


# The question a field belongs to: the nearest ancestor that shows its label as well as its control, never more than
# most of the viewport. A hidden file input has no box of its own; its upload widget does.
_CONTAINER_JS = """(el, label) => {
  const want = String(label || '').toLowerCase().replace(/\\s+/g, ' ').trim().slice(0, 40);
  const limit = (window.innerHeight || 800) * 0.7;
  let best = el;
  for (let node = el, depth = 0; node && node !== document.body && depth < 10; depth++) {
    const r = node.getBoundingClientRect();
    if (r.height > limit) break;
    if (r.width > 0 && r.height > 0) {
      best = node;
      const text = (node.innerText || '').toLowerCase().replace(/\\s+/g, ' ');
      if (want && text.includes(want)) return node;
    }
    node = node.parentElement || (node.getRootNode && node.getRootNode().host) || null;
  }
  return best;
}"""
MAX_CROPS = 5
CROP_BUDGET_SECONDS = 4.5


def _highlight(form, field):
    """A screenshot-only outline around the asked control (Playwright applies it to every frame, then removes it)."""
    dom_id = str((form.fields.get(field["id"]) or field).get("dom_id") or "")
    if not re.fullmatch(r"[\w:.-]{1,80}", dom_id):
        return None
    return f'[data-jp-id="{dom_id}"]{{outline:3px solid #f76707 !important;outline-offset:2px !important}}'


async def _center(form, field):
    """Scroll the field's frame and its question (label and control together) to the middle of the viewport.

    scroll_into_view_if_needed() left a control that was already partly visible at the viewport's edge, and a hidden
    file input's box is elsewhere, so the screenshot showed other questions (2026-10-23). Returns the container."""
    frame = form.frames[field["id"]]
    ancestors = []
    while frame.parent_frame:
        ancestors.append(await frame.frame_element())
        frame = frame.parent_frame
    for owner in reversed(ancestors):
        await owner.scroll_into_view_if_needed(timeout=1000)
    handle = await form.locator(field["id"]).element_handle(timeout=1000)
    if not handle:
        return None, None
    container = (await handle.evaluate_handle(_CONTAINER_JS, field.get("label", ""))).as_element() or handle
    await container.evaluate("e => e.scrollIntoView({block: 'center', inline: 'nearest'})")
    return handle, container


async def _clip(form, handle, container):
    """The question's box in viewport coordinates, padded, at least a readable size, within the viewport."""
    boxes = [b for b in (await container.bounding_box(), await handle.bounding_box()) if b and b["width"] > 0]
    viewport = form.page.viewport_size or {"width": 1280, "height": 800}
    if not boxes:
        return None
    left = min(b["x"] for b in boxes) - 24
    top = min(b["y"] for b in boxes) - 32
    right = max(b["x"] + b["width"] for b in boxes) + 24
    bottom = max(b["y"] + b["height"] for b in boxes) + 24
    if right - left < 520:
        left, right = left - (520 - (right - left)) / 2, right + (520 - (right - left)) / 2
    if bottom - top < 200:
        top, bottom = top - (200 - (bottom - top)) / 2, bottom + (200 - (bottom - top)) / 2
    left, top = max(0.0, left), max(0.0, top)
    right, bottom = min(float(viewport["width"]), right), min(float(viewport["height"]), bottom)
    if right - left < 40 or bottom - top < 40:
        return None
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def _crop_items(form, result):
    """Missing facts asked of the owner that still identify one observed control each."""
    items = []
    for item in result.get("reviews") or getattr(form, "pending", []):
        if item.get("blocker_type", "missing_fact") != "missing_fact" or item.get("key") in {"manual", "session"}:
            continue
        field = form.fields.get(item.get("field_id") or "")
        if field and _question(field.get("label", "")) == _question(item.get("question", "")):
            items.append((item, field))
    return items[:MAX_CROPS]


async def _capture(form, directory, run_id, job_id, result, secret_values):
    started = time.monotonic()
    await form.settle(timeout=1, quiet=0.15)
    observation = await form.scan()
    if not observation.get("scan_success") or observation.get("busy"):
        return None
    field = _focus_field(form, result)
    target = None
    if field:
        locator = form.locator(field["id"])
        handle, _container = await _center(form, field)
        if handle is None:
            return None
        await form.settle(timeout=1, quiet=0.15)
        observation = await form.scan()
        # A remount may reuse the visible question but requires fresh targeting.
        current = form.fields.get(field["id"])
        if not current or form.field_key(current) != form.field_key(field):
            return None
        target = await locator.element_handle()
        if not target:
            return None
        box = await locator.bounding_box()
        viewport = form.page.viewport_size
        if not box or (
            viewport
            and not (
                box["x"] < viewport["width"]
                and box["x"] + box["width"] > 0
                and box["y"] < viewport["height"]
                and box["y"] + box["height"] > 0
            )
        ):
            return None
    masks = []
    secrets_to_mask = {str(value) for value in secret_values if value and len(str(value)) >= 4}
    # Include every document, even excluded provider frames: the screenshot can
    # still contain their pixels. An unreadable document makes capture fail closed.
    for frame in form.page.frames:
        secret_fields = frame.locator(_SECRET_FIELDS)
        named_secrets = frame.get_by_role("textbox", name=_AUTH_LABEL)
        masks += [secret_fields, named_secrets, frame.get_by_text(_SECRET_TEXT)]
        for fields in (secret_fields, named_secrets):
            for item in await fields.all():
                # Reflected codes elsewhere on the page must be hidden too. The
                # values stay in this capture's memory and never enter metadata.
                value = await item.evaluate("e => e.value || ''")
                if value and len(value) >= 4:
                    secrets_to_mask.add(value)
        masks.append(frame.locator('a[href*="token=" i],a[href*="code=" i],a[href*="resetpassword" i]'))
    for frame in form.page.frames:
        for value in secrets_to_mask:
            masks.append(frame.get_by_text(re.compile(re.escape(value))))
        indices = await frame.locator("input,textarea").evaluate_all(
            "(items, secrets) => items.flatMap((e,i) => secrets.includes(e.value) ? [i] : [])",
            list(secrets_to_mask),
        )
        masks.extend(frame.locator("input,textarea").nth(index) for index in indices)
    style = _highlight(form, field) if field else None
    image = await form.page.screenshot(
        type="jpeg",
        quality=65,
        full_page=False,
        mask=masks,
        mask_color="#111111",
        timeout=3000,
        **({"style": style} if style else {}),
    )
    def unchanged(after):
        return (
            (after.get("owner_id"), after.get("revision"), after.get("progress_fingerprint"))
            == (observation.get("owner_id"), observation.get("revision"), observation.get("progress_fingerprint"))
            and after.get("scan_success")
            and not after.get("busy")
        )

    after = await form.scan()
    # cloneNode can retain data-jp-id and every semantic value. The old element
    # must still be the document's actual failure control, not a detached clone.
    if target and not await target.evaluate("element => element.isConnected"):
        return None
    if not unchanged(after):
        return None
    if not image or len(image) > MAX_IMAGE_BYTES:
        return None
    # One crop per question asked of the owner, each scrolled to the middle and outlined, so the screenshot sent with
    # a question shows that question (owner 2026-10-23: "the screenshots don't show the right questions"). Crops
    # are kept only when the page is still the one observed; the viewport capture above stands either way.
    crops = []
    for number, (item, crop_field) in enumerate(_crop_items(form, result), 1):
        if time.monotonic() - started > CROP_BUDGET_SECONDS:
            break
        try:
            handle, container = await _center(form, crop_field)
            clip = await _clip(form, handle, container) if handle else None
            if not clip:
                continue
            highlight = _highlight(form, crop_field)
            crop = await form.page.screenshot(
                type="jpeg",
                quality=70,
                clip=clip,
                mask=masks,
                mask_color="#111111",
                timeout=2000,
                **({"style": highlight} if highlight else {}),
            )
        except Exception:
            continue  # This question is sent with the viewport capture's check, or as text.
        if crop and len(crop) <= MAX_IMAGE_BYTES:
            crops.append(
                {
                    "file": f"help-q{number}.jpg",
                    "question": _question(crop_field.get("label", "")),
                    "key": _digest(item["key"]) if item.get("key") else "",
                    "field": _digest(crop_field["id"]),
                    "image_sha256": hashlib.sha256(crop).hexdigest(),
                    "bytes": crop,
                }
            )
    if crops and not unchanged(await form.scan()):
        crops = []
    metadata = {
        "schema": 1,
        "run_id": run_id,
        "job_id": job_id,
        "owner_id": observation["owner_id"],
        "revision": observation["revision"],
        "capture_id": secrets.token_hex(16),
        "captured_at": time.time(),
        "page_identity": _digest(form.page.url),
        "failure_identity": _digest(
            json.dumps(
                {k: result.get(k) for k in ("state", "reason", "blocker_type", "recovery_cause")},
                sort_keys=True,
            )
        ),
        "focus_questions": [_question(field["label"])] if field else [],
        "focus_keys": [
            _digest(item["key"])
            for item in (result.get("reviews") or getattr(form, "pending", []))
            if field and item.get("key") and item.get("field_id") == field["id"]
        ],
        "image_sha256": hashlib.sha256(image).hexdigest(),
        "redacted": True,
        "viewport": dict(form.page.viewport_size or {}),
        "crops": [{k: v for k, v in crop.items() if k != "bytes"} for crop in crops],
    }
    # Publish metadata last. A reader racing publication either sees the matching
    # bytes or rejects their hash; never falls back to an unbound old screenshot.
    files = [("help.jpg", image), *((crop["file"], crop["bytes"]) for crop in crops)]
    for name, content in (*files, ("help.json", json.dumps(metadata).encode())):
        temporary = directory / ("." + name + "." + metadata["capture_id"])
        try:
            temporary.write_bytes(content)
            temporary.chmod(0o600)
            temporary.replace(directory / name)
        finally:
            temporary.unlink(missing_ok=True)
    return metadata


async def capture_help(form, directory, *, run_id, job_id, result, secret_values=()):
    """Capture a stable current failure; failures leave no deliverable old image."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    _discard(directory)
    if not result or result.get("state") not in HELP_STATES or not run_id or not job_id:
        return None
    try:
        metadata = await asyncio.wait_for(
            _capture(form, directory, run_id, job_id, result, secret_values),
            timeout=8,
        )
        if not metadata:
            _discard(directory)
        return metadata
    except Exception:
        # Exception strings and Playwright call logs can contain typed secrets.
        _discard(directory)
        return None


def _matching_crop(metadata, question, review_key, field_id):
    """The crop made for this question: its own field when known, else the only crop with its wording and key."""
    crops = [
        crop
        for crop in metadata.get("crops") or []
        if isinstance(crop, dict)
        and re.fullmatch(r"help-q\d\.jpg", str(crop.get("file", "")))
        and crop.get("question") == _question(question)
        and (review_key is None or not crop.get("key") or crop.get("key") == _digest(review_key))
    ]
    if field_id:
        own = [crop for crop in crops if crop.get("field") == _digest(field_id)]
        if own:
            return own[0]
    return crops[0] if len(crops) == 1 else None


def _jpeg(path, digest):
    if path.is_symlink() or not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
        return None
    image = path.read_bytes()
    if hashlib.sha256(image).hexdigest() != digest or not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9"):
        return None
    return image


def validated_help_bytes(
    directory, *, run_id, job_id, state, expected, question=None, review_key=None, field_id=None, now=None
):
    """Return bound bytes, not a path that could change between checking and upload.

    Caller must additionally verify this is the latest run and the job is active.
    Persisted expected metadata is required; a sidecar alone is not authority.
    A question gets its own crop when the capture made one; otherwise the viewport capture, only when it was
    centred on that question.
    """
    if state not in HELP_STATES or not expected or not isinstance(expected, dict):
        return None
    try:
        directory = Path(directory)
        picture, sidecar = directory / "help.jpg", directory / "help.json"
        if picture.is_symlink() or sidecar.is_symlink() or sidecar.stat().st_size > 16_384:
            return None
        metadata = json.loads(sidecar.read_text())
        if metadata != expected or metadata.get("schema") != 1 or metadata.get("redacted") is not True:
            return None
        if metadata.get("run_id") != run_id or metadata.get("job_id") != job_id:
            return None
        if not metadata.get("owner_id") or not isinstance(metadata.get("revision"), int):
            return None
        age = (time.time() if now is None else now) - metadata["captured_at"]
        if age < -5 or age > MAX_AGE_SECONDS:
            return None
        if question is not None and (crop := _matching_crop(metadata, question, review_key, field_id)):
            return _jpeg(directory / crop["file"], crop.get("image_sha256"))
        if question is not None and _question(question) not in metadata.get("focus_questions", []):
            return None
        if review_key is not None and _digest(review_key) not in metadata.get("focus_keys", []):
            return None
        if not 0 < picture.stat().st_size <= MAX_IMAGE_BYTES:
            return None
        image = picture.read_bytes()
        if len(image) > MAX_IMAGE_BYTES or hashlib.sha256(image).hexdigest() != metadata.get("image_sha256"):
            return None
        if not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9"):
            return None
        return image
    except (OSError, ValueError, TypeError, KeyError):
        return None


def current_help_bytes(service, run_id, *, question=None, review_key=None, field_id=None):
    """Read-only delivery gate: superseded, paused and archived runs get text only."""
    from sqlalchemy import select

    from .db import Job, Run

    with service.db.session() as session:
        run = session.get(Run, run_id)
        job = session.get(Job, run.job_id) if run else None
        if not job or job.status in {"archived", "paused", "confirmed"}:
            return None
        if service.db.get_setting("job_control:" + job.id, {}).get("state") in {"paused", "deleted"}:
            return None
        if session.scalar(
            select(Run.id)
            .where(
                Run.job_id == job.id,
                Run.id != run.id,
                Run.created_at >= run.created_at,
            )
            .limit(1)
        ):
            return None
        return validated_help_bytes(
            service.config.data_dir / "runs" / run_id,
            run_id=run.id,
            job_id=job.id,
            state=run.state,
            expected=(run.packet or {}).get("diagnostic_capture"),
            question=question,
            review_key=review_key,
            field_id=field_id,
        )
