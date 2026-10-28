"""TikTok's careers site (ByteDance, 2026-10-27): after Continue on its password reset form the website draws its own
picture puzzle ("Select 2 objects that are the same shape:", a scene image, Refresh, Confirm) with no hCaptcha or
reCAPTCHA frame. Nine runs stopped on it. Only the puzzle image goes to the solver; its points are clicked on the image,
then Confirm; the puzzle counts as solved only when the website closes it."""

from types import SimpleNamespace

from jobpilot import captcha_visual
from jobpilot.captcha_inpage import find_puzzle, solve_inpage

PUZZLE = """
<form><input id=email placeholder="Enter your email"><input type=password><button type=button>Continue</button></form>
<div id=mask style="position:fixed;inset:0;background:rgba(0,0,0,.4)">
 <div id=card style="position:absolute;left:100px;top:60px;width:380px;height:380px;background:#fff;padding:16px">
  <div class=title>Select 2 objects that are the same shape:</div>
  <canvas id=scene width=340 height=212></canvas>
  <div class=bar><span>Refresh</span><span>Report a problem</span><button id=confirm type=button>Confirm</button></div>
 </div>
</div>
<script>
 const scene = document.getElementById('scene'); let clicks = 0;
 const draw = n => { const c = scene.getContext('2d'); c.fillStyle = n ? '#cfc' : '#fcf'; c.fillRect(0, 0, 340, 212); };
 draw(0);
 scene.addEventListener('click', () => clicks++);
 document.getElementById('confirm').addEventListener('click', () => {
  if (clicks === 2) document.getElementById('mask').remove(); else { clicks = 0; draw(1); }
 });
</script>"""

CONFIG = SimpleNamespace(twocaptcha_api_key="synthetic", captcha_timeout=30, captcha_max_attempts=2, captcha_image_rounds=3)


def solver_answers(points):
    asked = []

    async def task_result(base, key, task, timeout, emit):
        asked.append(task)
        return {"coordinates": points}, None

    return task_result, asked


async def test_the_puzzle_is_found_and_answered_on_its_own_image(page, monkeypatch):
    await page.set_content(PUZZLE)
    frame, info = await find_puzzle(page)
    assert info == {"kind": "click", "prompt": "Select 2 objects that are the same shape:", "confirm": True}
    task_result, asked = solver_answers([{"x": 60, "y": 50}, {"x": 250, "y": 150}])
    monkeypatch.setattr(captcha_visual, "task_result", task_result)
    solved, reason = await solve_inpage(CONFIG, page, lambda *a: None)
    assert solved, reason
    assert not await page.locator("#mask").count()
    # One CoordinatesTask with the printed instruction; the image is the scene alone.
    assert len(asked) == 1 and asked[0]["type"] == "CoordinatesTask"
    assert "Select 2 objects that are the same shape" in asked[0]["comment"]
    assert await find_puzzle(page) is None


async def test_a_puzzle_the_website_keeps_is_not_solved(page, monkeypatch):
    await page.set_content(PUZZLE)
    task_result, asked = solver_answers([{"x": 60, "y": 50}])  # one point: the website draws a new puzzle
    monkeypatch.setattr(captcha_visual, "task_result", task_result)
    solved, _ = await solve_inpage(CONFIG, page, lambda *a: None)
    assert not solved
    assert await page.locator("#mask").count() == 1
    assert len(asked) <= CONFIG.captcha_image_rounds


async def test_a_page_section_with_a_banner_is_not_a_puzzle(page):
    await page.set_content(
        """<section style="width:1000px"><h2>Select the images you like for your profile</h2>
        <img src="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='900' height='300'/>"
        width=900 height=300><button>Submit</button></section>"""
    )
    assert await find_puzzle(page) is None


async def test_a_slider_puzzle_is_reported_not_guessed(page, monkeypatch):
    await page.set_content(PUZZLE.replace("Select 2 objects that are the same shape:", "Drag the slider to fit the puzzle"))
    task_result, asked = solver_answers([])
    monkeypatch.setattr(captcha_visual, "task_result", task_result)
    solved, reason = await solve_inpage(CONFIG, page, lambda *a: None)
    assert not solved and "slider" in reason and not asked
