"""Picture puzzles drawn by the website itself (no hCaptcha/reCAPTCHA frame): "Select 2 objects that are the same shape".

TikTok's careers site (ByteDance, 2026-10-27) shows one after Continue on its password reset form, in a dialog with a
scene image, Refresh and Confirm. The token and hCaptcha paths never saw it: nine runs stopped on it and four more read
the reset page as "did not open". The puzzle is answered the way captcha_same_ip answers hCaptcha's images: only the
puzzle image goes to 2Captcha's CoordinatesTask with the printed instruction, the returned points are clicked on the
image in this browser, then Confirm. Success means the website closed the puzzle. Click puzzles only; a slider or
rotation puzzle is reported, never guessed.
"""

import hashlib
from types import SimpleNamespace

from playwright.async_api import Error as PlaywrightError

from .captcha_visual import CaptchaSolver, jpeg_size

# Marks the puzzle image and its Confirm button and returns the instruction; null when no click puzzle is shown.
# The instruction is a short line that asks to select/click some objects; the image is the largest visible picture
# within a few ancestors of it, beside a Confirm/Verify/Submit control.
FIND = r"""() => {
 const host = location.hostname;
 if (/(^|\.)(hcaptcha\.com|google\.com|recaptcha\.net|cloudflare\.com)$/.test(host)) return null;
 const shown = e => { if (!e) return false; const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
   return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity !== 0; };
 const text = e => (e.textContent || '').replace(/\s+/g, ' ').trim();
 document.querySelectorAll('[data-jp-inpage-puzzle],[data-jp-inpage-confirm]').forEach(e => {
   delete e.dataset.jpInpagePuzzle; delete e.dataset.jpInpageConfirm; });
 const ASK = /^(?:please )?(?:select|click(?: on)?|tap(?: on)?|choose|pick)\b.{0,80}?\b(?:objects?|images?|pictures?|shapes?|icons?)\b/i;
 const SLIDE = /\b(?:drag|slide|rotate|turn)\b.{0,60}\b(?:slider|puzzle|piece)\b/i;
 const DONE = /^(?:confirm|verify|submit|done)$/i;
 if (!/\b(?:select|click|tap|choose|pick|drag|slide|rotate|turn)\b/i.test(document.body?.innerText || '')) return null;
 for (const el of document.querySelectorAll('body *')) {
   if (el.children.length > 3) continue;
   const said = text(el);
   if (!said || said.length > 160) continue;
   const slider = SLIDE.test(said);
   if (!(slider || ASK.test(said)) || !shown(el)) continue;
   // The puzzle's own card: a picture and its Confirm within a small box, never a page section with a banner image.
   let box = el;
   for (let depth = 0; depth < 6 && box.parentElement; depth++) {
     box = box.parentElement;
     const card = box.getBoundingClientRect();
     if (card.width > 800 || card.height > 800) break;
     const pictures = [...box.querySelectorAll('img,canvas')].filter(shown).filter(p => {
       const r = p.getBoundingClientRect(); return r.width >= 150 && r.height >= 100; });
     if (!pictures.length) continue;
     if (slider) return {kind: 'slider', prompt: said};
     const confirm = [...box.querySelectorAll('button,[role=button],div,span,a')].filter(b => b.children.length <= 2)
       .find(b => DONE.test(text(b)) && shown(b));
     if (!confirm) continue;
     const area = p => { const r = p.getBoundingClientRect(); return r.width * r.height; };
     const picture = pictures.sort((a, b) => area(b) - area(a))[0];
     picture.dataset.jpInpagePuzzle = '1';
     confirm.dataset.jpInpageConfirm = '1';
     return {kind: 'click', prompt: said, confirm: true};
   }
 }
 return null;
}"""


async def find_puzzle(page):
    """(frame, info) of a website-drawn picture puzzle on the page, else None."""
    for frame in page.frames:
        try:
            info = await frame.evaluate(FIND)
        except PlaywrightError:
            continue
        if isinstance(info, dict) and info.get("kind") in {"click", "slider"}:
            return frame, info
    return None


async def solve_inpage(config, page, emit):
    """(solved, reason). Solved only when the website closed the puzzle after the answer."""
    found = await find_puzzle(page)
    if not found:
        return True, "No picture puzzle is shown"
    frame, info = found
    if info["kind"] != "click":
        return False, "The website shows a slider puzzle, which the solver does not answer"
    if not config.twocaptcha_api_key:
        return False, "The website's picture puzzle needs TWOCAPTCHA_API_KEY"
    settings = SimpleNamespace(
        twocaptcha_api_key=config.twocaptcha_api_key,
        captcha_timeout=min(config.captcha_timeout, 180),
        captcha_max_attempts=config.captcha_max_attempts,
        captcha_max_rounds=config.captcha_image_rounds,
        capsolver_api_key="",
    )
    solver = CaptchaSolver(settings, emit)
    answered = set()
    for _ in range(settings.captcha_max_rounds):
        picture = frame.locator("[data-jp-inpage-puzzle]").first
        try:
            await picture.scroll_into_view_if_needed(timeout=2000)
            image = await picture.screenshot(type="jpeg", quality=80, scale="css", timeout=3000)
            region = await picture.bounding_box()
        except PlaywrightError:
            return False, "The website's picture puzzle could not be captured"
        if not region or len(image) > 600_000:
            return False, "The website's picture puzzle could not be captured"
        digest = hashlib.sha256(image).hexdigest()
        if digest in answered:
            return False, "The website kept the same picture puzzle after the answer"
        puzzle = {
            "image": image,
            "region": region,
            "size": jpeg_size(image) or (region["width"], region["height"]),
            "prompt": info["prompt"],
            "dragging": False,
            "example": None,
        }
        points = await solver.ask_coordinates(puzzle)
        if points is None:
            return False, solver.last_reason or "The solver returned no answer for the picture puzzle"
        answered.add(digest)
        try:
            await solver.act(page, puzzle, points)
            confirm = frame.locator("[data-jp-inpage-confirm]").first
            if info.get("confirm") and await confirm.count():
                await page.wait_for_timeout(300)
                await confirm.click(timeout=3000)
        except PlaywrightError:
            return False, "The website's picture puzzle changed during the answer"
        await page.wait_for_timeout(2000)
        found = await find_puzzle(page)
        if not found:
            emit("captcha", "Website accepted the picture puzzle", {"rounds": len(answered)})
            return True, "Website accepted the picture puzzle"
        frame, info = found
        if info["kind"] != "click":
            return False, "The website shows a slider puzzle, which the solver does not answer"
        emit("captcha", "Picture puzzle needs another round", {"rounds": len(answered)})
    return False, "The website's picture puzzle needs more rounds than CAPTCHA_MAX_ROUNDS allows"
