"""PASS/FAIL for Take over (2026-11-01): the real engine, a real Chrome and a local employer page.

A run stops at a question no saved fact answers. The owner presses Take over; the application opens in the worker Chrome at the point
it stopped, with the draft filled so far, and no run is attached to that tab. A Playwright client acting as the owner fills the
question by hand. Then:
- "Done, continue" resumes the application from that tab and the employer receives exactly one application;
- "I submitted it myself" (the owner pressed Submit in the tab) records the application as sent, and a retry is refused;
- "Done, continue" on a page that already confirms the application is refused, because continuing could send it twice.
The employer's own record of what it received is the evidence.
"""

import asyncio
import hashlib
import os
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy import select

from jobpilot import browser as browser_module, steer
from jobpilot.admission import QueueBlocked
from jobpilot.db import Event, Job, Resume, Review, Run, Setting
from jobpilot.discovery import add_job
from jobpilot.answers import Resolver
from jobpilot.schemas import Answer, JobInput

pytestmark = [pytest.mark.browser, pytest.mark.integration]

ME = "alex.rivera@example.test"
FORM = """<!doctype html><html><head><title>Apply: Analyst</title></head><body><h1>Analyst</h1>
<form method=post action="/applications" enctype="multipart/form-data">
<label for=f>First Name</label><input id=f name=first_name required>
<label for=l>Last Name</label><input id=l name=last_name required>
<label for=m>Email</label><input id=m name=email type=email required>
<label for=q>What is your favourite programming paradigm?</label><input id=q name=paradigm required>
<label for=r>Resume</label><input id=r name=resume type=file required>
<button type=submit>Submit Application</button></form></body></html>"""
DONE = "<!doctype html><html><head><title>Thank you</title></head><body><h1>Thank you for applying</h1><p>Your application has been received.</p></body></html>"


class Employer:
    """A local employer. `received` is its own record of every application it took, with the answer to the question."""

    def __init__(self):
        self.received, self.hits = [], []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def send(self, body, status=200):
                page = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)

            def do_GET(self):
                outer.hits.append(urlsplit(self.path).path)
                self.send(DONE if urlsplit(self.path).path == "/thanks" else FORM)

            def do_POST(self):
                size = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(size)
                outer.received.append({"path": urlsplit(self.path).path, "paradigm": b"functional" in raw or b"by hand" in raw})
                self.send(DONE)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/jobs/1/apply"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def blank_pdf():
    from io import BytesIO

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


async def start_chrome(temp):
    """The worker Chrome the app attaches to: its own profile, the mock keychain, visible-capable but headless here."""
    executable = os.environ.get("CHROMIUM_PATH") or ""
    if not executable:
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        executable = pw.chromium.executable_path
        await pw.stop()
    argv = browser_module.throwaway_chrome(executable, temp, True)
    process = await asyncio.create_subprocess_exec(*argv, "about:blank", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    portfile = os.path.join(temp, "DevToolsActivePort")
    for _ in range(100):
        if os.path.exists(portfile) and os.path.getsize(portfile):
            break
        await asyncio.sleep(0.1)
    with open(portfile) as f:
        return process, f"http://127.0.0.1:{f.read().splitlines()[0]}"


async def until(check, seconds, step=0.5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        await asyncio.sleep(step)
    return None


class World:
    """A Service wired to a real Chrome and one local employer, with an application stopped on its question."""

    def __init__(self, service):
        self.service = service
        self.tasks = set()

    def runs(self, job_id):
        with self.service.db.session() as s:
            return [(r.state, r.mode, r.reason) for r in s.scalars(select(Run).where(Run.job_id == job_id).order_by(Run.created_at))]

    def trail(self, n=25):
        with self.service.db.session() as s:
            return [f"{e.kind}: {e.message[:110]} {str(e.data)[:200] if e.kind.startswith(('model', 'option')) else ''}" for e in s.scalars(select(Event).order_by(Event.id.desc()).limit(n))][::-1]

    def live(self):
        return any(not t.done() for t in self.tasks)

    async def setup(self):
        config = self.service.config
        config.repair_agent_enabled = False
        config.mimo_api_key = "no-model"
        config.mimo_base_url = "http://127.0.0.1:9/v1"
        config.headless = True
        pdf = blank_pdf()
        (config.data_dir / "resumes").mkdir(exist_ok=True)
        (config.data_dir / "resumes" / "alex.pdf").write_bytes(pdf)
        with self.service.db.exclusive() as s:
            s.add(Resume(name="Alex", filename="alex.pdf", sha256=hashlib.sha256(pdf).hexdigest(), text="Alex Rivera, analyst."))
        self.service.db.set_setting("profile", {"name": "Alex Rivera", "email": ME, "first_name": "Alex", "last_name": "Rivera",
                                                "autonomous": False, "facts": {"first_name": "Alex", "last_name": "Rivera"}})
        self.service.db.set_setting("control", {"paused": False, "auto_submit": True})

        async def dispatch(run_id):
            task = asyncio.create_task(self.service.execute(run_id))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

        self.service.dispatch = dispatch
        self.site = Employer()
        self.temp = tempfile.mkdtemp(prefix="steer-chrome-")
        self.process, config.browser_cdp_url = await start_chrome(self.temp)
        job, _ = add_job(self.service.db, JobInput(url=self.site.url, title="Analyst", company="ExampleSoft", location="Remote", description="A synthetic posting."))
        self.job_id = job["id"]
        with self.service.db.exclusive() as s:
            row = s.get(Job, self.job_id)
            row.status, row.ats = "ready", os.environ.get("STEER_ATS", "greenhouse")
        return self

    async def stop_at_question(self):
        """Press Apply and wait until the application stops on the question nothing saved answers."""
        from jobpilot.owner_apply import apply

        await apply(self.service, self.job_id)
        stopped = await until(lambda: self.runs(self.job_id) and self.runs(self.job_id)[-1][0] in {"needs_review", "waiting_answer", "waiting_browser", "failed"} and not self.live(), 150)
        assert stopped, self.runs(self.job_id)
        return self.runs(self.job_id)

    async def tab(self, expect=True):
        """The application's tab in the worker Chrome, read from Chrome's HTTP endpoint (attaches nothing)."""
        endpoint = self.service.config.browser_cdp_url

        def find():
            with httpx.Client(timeout=2, trust_env=False) as client:
                tabs = [t for t in client.get(endpoint + "/json/list").json() if t.get("type") == "page" and "/jobs/1/" in t.get("url", "")]
            return tabs[0] if tabs else None

        return await until(find, 25) if expect else find()

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.site.close()
        self.process.terminate()
        await self.process.wait()
        shutil.rmtree(self.temp, ignore_errors=True)


async def as_the_owner(world, action):
    """A Playwright client acting as the person at the keyboard, on the tab the app left open."""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(world.service.config.browser_cdp_url)
        try:
            page = next(p for c in browser.contexts for p in c.pages if "/jobs/1/" in p.url)
            return await action(page)
        finally:
            await browser.close()


@pytest.fixture(autouse=True)
def no_saved_fact(monkeypatch):
    """The model is the only thing between a question and the owner: here it finds no saved fact, as for a question about the
    applicant that nothing in the profile covers."""

    async def none_known(self, pending, *args, **kwargs):
        return {
            f["id"]: Answer(field_id=f["id"], disposition="review", reason="The answer is not in the saved profile",
                            blocker_type="missing_fact")
            for f in pending
        }

    async def not_answerable(resolver, field):
        return None, "not_answerable"

    monkeypatch.setattr(Resolver, "_answer_batch", none_known)
    monkeypatch.setattr("jobpilot.inference_agent.infer", not_answerable)


async def taken_over(service):
    world = await World(service).setup()
    try:
        before = await world.stop_at_question()
        assert before[-1][0] in {"needs_review", "waiting_answer"}, (before, world.trail())
        assert not world.site.received
        return world, before
    except BaseException:
        await world.close()
        raise


async def test_done_continue_resumes_from_the_tab_the_owner_filled_and_sends_once(service):
    world, before = await taken_over(service)
    try:
        job_id = world.job_id
        with service.db.session() as s:
            asked = list(s.scalars(select(Review).where(Review.answer.is_(None))))
        assert asked, "the application did not stop on a question"
        result = await steer.start(service, job_id)
        assert result["phase"] == "opening"
        held = await until(lambda: steer.record(service.db, job_id).get("phase") == "yours" and not world.live(), 150)
        assert held, (steer.record(service.db, job_id), world.runs(job_id))
        # Nothing about the stop reached the owner a second time: no new question, no repair, the job is held.
        with service.db.session() as s:
            newest = s.scalars(select(Run).where(Run.job_id == job_id).order_by(Run.created_at.desc())).first()
            assert newest.state == "paused" and newest.mode == "dry_run" and newest.packet.get("steer")
            assert not list(s.scalars(select(Review).where(Review.run_id == newest.id)))
            assert s.get(Job, job_id).status == "paused"
        # The tab is open at the application, with the draft filled so far (name and email), and nothing is attached to it.
        tab = await world.tab()
        assert tab, "the application's tab was closed"
        view = steer.status(service, job_id)
        assert view["phase"] == "yours" and view["tab_open"], view
        filled = await as_the_owner(world, lambda page: page.evaluate("[document.getElementById('f').value, document.getElementById('m').value, document.getElementById('q').value]"))
        assert filled[0] == "Alex" and filled[1] == ME and filled[2] == "", filled
        # No automatic path starts it while the owner has it.
        with pytest.raises(QueueBlocked):
            await service.queue(job_id, "submit", retry=True)
        # The owner answers the question by hand.
        async def by_hand(page):
            await page.fill("#q", "functional, by hand")
            return page.url

        await as_the_owner(world, by_hand)
        assert not world.site.received
        done = await steer.finish(service, job_id, "done")
        assert done["state"] == "queued"
        sent = await until(lambda: world.site.received and not world.live(), 150)
        assert sent, (world.runs(job_id), world.site.hits)
        await asyncio.sleep(3)
        assert len(world.site.received) == 1 and world.site.received[0]["paradigm"], world.site.received
        states = world.runs(job_id)
        assert states[-1][0] == "confirmed", states
        assert steer.record(service.db, job_id)["phase"] == "released"
    finally:
        await world.close()


async def test_i_submitted_it_myself_is_recorded_as_sent_and_a_retry_is_refused(service):
    world, before = await taken_over(service)
    try:
        job_id = world.job_id
        await steer.start(service, job_id)
        assert await until(lambda: steer.record(service.db, job_id).get("phase") == "yours" and not world.live(), 150), world.runs(job_id)

        async def submit_by_hand(page):
            await page.fill("#q", "functional, by hand")
            state = await page.evaluate("[...document.querySelectorAll('input')].map(i => [i.id, i.type === 'file' ? i.files.length : i.value, i.validity.valid])")
            assert all(v[2] for v in state), state
            await page.click("button[type=submit]", no_wait_after=True)
            for _ in range(50):
                if await page.title() == "Thank you":
                    break
                await asyncio.sleep(0.2)
            return await page.title()

        assert await as_the_owner(world, submit_by_hand) == "Thank you"
        assert len(world.site.received) == 1
        # Done on a page that already confirms the application would send it again: refused.
        with pytest.raises(ValueError, match="confirmation"):
            await steer.finish(service, job_id, "done")
        answer = steer.submitted(service, job_id)
        assert answer["state"] == "confirmed"
        with service.db.session() as s:
            newest = s.scalars(select(Run).where(Run.job_id == job_id).order_by(Run.created_at.desc())).first()
            assert newest.state == "confirmed" and newest.receipt["type"] == "owner_submitted"
            assert s.get(Job, job_id).status == "confirmed"
        # A retry, from the queue, the Apply button or the hand-back, is refused; the employer still has one application.
        from jobpilot.owner_apply import apply

        with pytest.raises(Exception):
            await service.queue(job_id, "submit", retry=True)
        runs_before = len(world.runs(job_id))
        pressed = await apply(service, job_id)  # The Apply button on a job the owner recorded as sent: answered, nothing queued.
        assert len(world.runs(job_id)) == runs_before and not world.live(), (pressed, world.runs(job_id))
        with pytest.raises(ValueError):
            await steer.finish(service, job_id, "back")
        await asyncio.sleep(2)
        assert len(world.site.received) == 1 and not world.live()
    finally:
        await world.close()


async def test_the_tab_cleanup_leaves_a_held_tab_alone_and_closes_it_once_released(service):
    from jobpilot.operations.close_tabs import close_inactive_tabs

    world, before = await taken_over(service)
    try:
        job_id = world.job_id
        await steer.start(service, job_id)
        assert await until(lambda: steer.record(service.db, job_id).get("phase") == "yours" and not world.live(), 150)
        endpoint = service.config.browser_cdp_url
        with httpx.Client(timeout=3, trust_env=False) as client:
            await asyncio.to_thread(close_inactive_tabs, service.db, endpoint, client)
        assert await world.tab(expect=False), "the cleanup closed a tab the owner is working in"
        steer.release(service, job_id)
        with httpx.Client(timeout=3, trust_env=False) as client:
            result = await asyncio.to_thread(close_inactive_tabs, service.db, endpoint, client)
        assert result["closed"] >= 1 and not await world.tab(expect=False), result
    finally:
        await world.close()


async def test_done_after_the_tab_was_closed_keeps_nothing_by_hand(service):
    """Done, continue hands back only the page the person saw. With the tab gone, what they typed there is gone too: the run opens the
    application again and asks, rather than treating whatever is on the new page as their answer."""
    world, before = await taken_over(service)
    try:
        job_id = world.job_id
        await steer.start(service, job_id)
        assert await until(lambda: steer.record(service.db, job_id).get("phase") == "yours" and not world.live(), 150), world.runs(job_id)

        async def by_hand_then_close(page):
            await page.fill("#q", "functional, by hand")
            await page.close()

        await as_the_owner(world, by_hand_then_close)
        assert not await world.tab(expect=False)
        await steer.finish(service, job_id, "done")
        stopped = await until(lambda: world.runs(job_id)[-1][0] not in {"queued", "running", "submitting"} and not world.live(), 150)
        assert stopped, world.runs(job_id)
        assert not world.site.received, world.site.received
        with service.db.session() as s:
            kept = [e.message for e in s.scalars(select(Event).where(Event.kind == "owner_values")) if e.message.startswith("Kept")]
        assert not kept, kept
    finally:
        await world.close()
