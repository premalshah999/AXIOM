"""hCaptcha's second page can take a few seconds to replace the answered images. Reading the frame at 1.5 s called an
accepted round "rejected the answer" (Honeywell 10 of 21 same-IP attempts, JPMorgan, 2026-10-18). The solver now waits
for the next page before deciding."""

from types import SimpleNamespace

from jobpilot import captcha_visual
from jobpilot.captcha_visual import CaptchaSolver


class Page:
    url = "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/1/apply/email"

    async def wait_for_timeout(self, ms):
        pass


class Frame:
    def locator(self, selector):
        return SimpleNamespace(filter=lambda **k: SimpleNamespace(count=_zero, is_visible=_false))


async def _zero():
    return 0


async def _false():
    return False


async def test_a_slow_second_round_is_answered_not_rejected(monkeypatch):
    shown = iter(["round-1", "round-1", "round-1", "round-2"])  # the answered images linger for two reads
    state = {"open": True, "answers": 0}

    async def challenge_frame(page):
        return state["open"]

    monkeypatch.setattr(captcha_visual, "challenge_frame", challenge_frame)
    solver = object.__new__(CaptchaSolver)
    solver.config = SimpleNamespace(twocaptcha_api_key="k", captcha_max_attempts=3, captcha_max_rounds=3)
    solver.disabled, solver.attempts, solver.rounds = set(), {}, 0
    solver.emit = lambda *args, **kwargs: None
    current = {"hash": None}

    async def observe_puzzle(frame, owner):
        current["hash"] = next(shown)
        return {"hash": current["hash"], "target": None}

    async def ask_coordinates(puzzle):
        return [(1, 1)]

    async def capture(page, owner, target):
        return current["hash"].encode(), (0, 0, 10, 10)

    async def act(page, puzzle, points):
        state["answers"] += 1
        if state["answers"] == 2:
            state["open"] = False

    async def token_present(page):
        return True

    solver.observe_puzzle, solver.ask_coordinates, solver.act, solver.token_present = (
        observe_puzzle, ask_coordinates, act, token_present)
    monkeypatch.setattr(captcha_visual.hashlib, "sha256", lambda data: SimpleNamespace(hexdigest=lambda: data.decode()))
    solver.capture = capture
    assert await solver.visual(Page(), Frame(), None) is True
    assert state["answers"] == 2 and solver.last_reason == "Website accepted the image challenge"


async def test_a_puzzle_replaced_while_the_solver_worked_gets_another_round(monkeypatch):
    # Honeywell 2026-10-19: 2Captcha answered after 25 s, hCaptcha had rotated the images, and the run gave up.
    observed = iter(["rotated-away", "fresh"])
    captures = iter(["rotated-in", "fresh"])
    state = {"open": True, "answers": 0, "asked": 0}

    async def challenge_frame(page):
        return state["open"]

    monkeypatch.setattr(captcha_visual, "challenge_frame", challenge_frame)
    solver = object.__new__(CaptchaSolver)
    solver.config = SimpleNamespace(twocaptcha_api_key="k", captcha_max_attempts=3, captcha_max_rounds=3)
    solver.disabled, solver.attempts, solver.rounds = set(), {}, 0
    solver.emit = lambda *args, **kwargs: None

    async def observe_puzzle(frame, owner):
        return {"hash": next(observed), "target": None}

    async def ask_coordinates(puzzle):
        state["asked"] += 1
        return [(1, 1)]

    async def capture(page, owner, target):
        return next(captures).encode(), (0, 0, 10, 10)

    async def act(page, puzzle, points):
        state["answers"] += 1
        state["open"] = False

    async def token_present(page):
        return True

    solver.observe_puzzle, solver.ask_coordinates, solver.act, solver.token_present = (
        observe_puzzle, ask_coordinates, act, token_present)
    monkeypatch.setattr(captcha_visual.hashlib, "sha256", lambda data: SimpleNamespace(hexdigest=lambda: data.decode()))
    solver.capture = capture
    assert await solver.visual(Page(), Frame(), None) is True
    assert state["asked"] == 2 and state["answers"] == 1  # The stale answer was never clicked.
