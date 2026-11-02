"""A fresh install to a tailored PDF in a real browser, against a stub model over real HTTP and a fake employer, with no .env and an empty database.

The owner's words: tailoring "just tells me that the application uses master resume", and "all resume tailoring fails". So: every step a person takes from
nothing to the PDF on the employer's form, and the file the form received has to be the file they approved, byte for byte.
"""

import asyncio
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
TOKEN = "tailoring-journey-token"
HEADERS = {"Authorization": "Bearer " + TOKEN}
PROOF_TOOLS = ["Python", "SQL", "Excel"]


def replies(kind):
    """What the model answers for each structured request, whichever pipeline asked."""
    from test_aggressive_pipeline import plan, source_doc

    from jobpilot.features.resumes.aggressive.schema import Brief, Critique, Revision

    doc = source_doc()
    if kind == "Imported":
        return {"name": doc.name, "contact": doc.contact, "sections": [s.model_dump() for s in doc.sections]}
    if kind == "NormalPlan":
        return {
            "summary": "Data Analyst with Python, SQL and Excel experience, documenting repeatable report reviews from public records.",
            "bullets": [{"id": "work2", "text": "Used Python and SQL to reconcile public records and kept Excel notes for repeatable report review"}],
            "skills_to_add": [],
        }
    if kind == "Brief":
        return Brief(
            product="Public reporting tools",
            proof_points=["Make records inspectable", "Explain query tradeoffs", "Document repeatable review"],
            real_stack=PROOF_TOOLS,
            seniority="Junior: a solo weekend deliverable",
            hard_keywords=[*PROOF_TOOLS, "Kubernetes"],
        ).model_dump()
    if kind == "Plan":
        return plan().model_dump()
    if kind == "Critique":
        return Critique(
            proof_points=["Inspect records", "Explain decisions", "Repeat reviews"],
            evidence=["Report browser", "SQL labels", "Excel checklist"],
            missing_requirements=["Kubernetes"],
            distinctness="Product, analysis, internal checklist",
            grade="B",
            passed=True,
        ).model_dump()
    if kind == "Revision":
        return Revision().model_dump()
    return {}


class Model(BaseHTTPRequestHandler):
    """An OpenAI-compatible model: the app's real call, retries and parsing all run. `fail` makes it answer with text no schema can read."""

    fail = ""
    calls: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        text = " ".join(str(m.get("content")) for m in body.get("messages", []))
        titles = re.findall(r'"title":\s*"(\w+)"', text)
        known = ("Imported", "NormalPlan", "BulletRepair", "SummaryRepair", "Brief", "Plan", "Critique", "Revision")
        kind = next((t for t in titles if t in known), "?")
        Model.calls.append(kind)
        content = "this is not json" if kind in Model.fail.split(",") else json.dumps(replies(kind))
        out = json.dumps(
            {
                "id": "x",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "stub",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def serve(port, static_root, data_dir):
    import uvicorn
    from email.parser import BytesParser
    from email.policy import default
    from gallery import site as site_module
    from test_aggressive_pipeline import POSTING

    from jobpilot.api import create_app
    from jobpilot.api.routes import frontend
    from jobpilot.config import Settings
    from jobpilot.db import Job, JobListing, Run

    stub = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    frontend.STATIC = Path(static_root)
    config = Settings(
        _env_file=None,
        data_dir=Path(data_dir),
        base_url=f"http://127.0.0.1:{port}",
        app_token=TOKEN,
        enable_workers=False,
        telegram_enabled=False,
        allow_private_urls=True,
        secure_cookies=False,
        llm_provider="deepseek",
        deepseek_api_key="stub-key",
        deepseek_base_url=f"http://127.0.0.1:{stub.server_port}/v1",
        repair_agent_enabled=False,
        answer_quality_enabled=False,
        browser_vision_recovery=False,
        failure_reports_enabled=False,
        trace_steps=False,
        normal_workers=1,
        specialist_workers=0,
    ).prepare()
    app = create_app(config)
    service = app.state.service
    parse_form = site_module.parse_form

    def recorded_form(content_type, body):
        fields = parse_form(content_type, body)
        if "multipart/form-data" in (content_type or ""):
            message = BytesParser(policy=default).parsebytes(f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body)
            for part in message.iter_parts():
                if part.get_filename():
                    fields[part.get_param("name", header="content-disposition") + "__sha256"] = hashlib.sha256(part.get_payload(decode=True)).hexdigest()
        return fields

    site_module.parse_form = recorded_form
    form = (
        '<html lang="en"><title>Apply</title><body><h1>Apply</h1><form method="post" action="/submit" enctype="multipart/form-data">'
        '<label for="first">First name</label><input id="first" name="first_name" required><label for="last">Last name</label><input id="last" name="last_name" required>'
        '<label for="email">Email</label><input id="email" name="email" type="email" required><label for="resume">Resume</label><input id="resume" name="resume" type="file" accept=".pdf" required>'
        '<button type="submit">Submit application</button></form></body></html>'
    )
    pages = {"/submit": '<html lang="en"><title>Application received</title><body><h1>Thank you for applying!</h1><p>Your application has been received.</p></body></html>'}
    for i in range(1, 6):
        pages[f"/jobs/{i}"] = f'<html lang="en"><title>Reporting Analyst {i} at ExampleWorks</title><body><h1>Reporting Analyst {i}</h1><p>ExampleWorks, Remote</p><pre>{POSTING}</pre><a href="/apply/{i}">Apply for this job</a></body></html>'
        pages[f"/apply/{i}"] = form
    employer = site_module.Site(pages)
    start = len(app.router.routes)

    @app.post("/api/journey/seed")
    async def seed(data: dict):
        """Jobs as discovery leaves them: read, ranked, listed on the board, nothing tried. And the profile the setup steps would have saved."""
        from test_aggressive_pipeline import PROFILE

        from jobpilot.schemas import Profile

        service.db.set_setting("profile", Profile.model_validate({**PROFILE, "autonomous": True, "facts": {"first_name": "Alex", "last_name": "Example"}}).model_dump(mode="json"))
        with service.db.exclusive() as s:
            for i in data["ids"]:
                jid = f"job-{i}"
                if not s.get(Job, jid):
                    s.add(Job(id=jid, identity=jid, url=f"{employer.base}/jobs/{i}", title=f"Reporting Analyst {i}", company=f"ExampleWorks {i}", location="Austin, TX", description=POSTING, status="ready", score=90, reason="Good fit", ats="greenhouse"))
                    s.add(JobListing(job_id=jid, source_id="src", posted_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), date_basis="posted", location="Austin, TX", country="US", career_level="", match_reason="x", active=True))
        return {"employer": employer.base}

    @app.post("/api/journey/model")
    async def model_control(data: dict):
        Model.fail = data.get("fail", "")
        return {"calls": Model.calls[-40:]}

    @app.post("/api/journey/key")
    async def key_control(data: dict):
        config.deepseek_api_key = data["key"]
        return {}

    @app.get("/api/journey/health")
    def health():
        return {"ok": True, "calls": Model.calls[-40:]}

    @app.post("/api/journey/execute")
    async def execute(data: dict):
        """The real application worker, on the fake employer's form."""
        job_id = data["job_id"]

        async def dispatch(_run_id):
            pass

        service.dispatch = dispatch
        service.db.set_setting("control", {"paused": False, "auto_submit": True})
        from jobpilot.company_sites import allow

        allow(service.db, [job_id])
        with service.db.exclusive() as s:
            s.get(Job, job_id).ats = "custom"
        queued = await service.queue(job_id, "submit", "source", owner_pick=True)
        await asyncio.wait_for(service.execute(queued["id"]), 90)
        with service.db.session() as s:
            run = s.get(Run, queued["id"])
            result = {"state": run.state, "reason": run.reason, "packet_sha": run.packet.get("resume_sha"), "artifact": run.packet.get("resume_artifact_id")}
        return {**result, "applications": employer.applications()}

    mine = app.router.routes[start:]
    del app.router.routes[start:]
    app.router.routes[0:0] = mine
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    finally:
        employer.close()
        stub.shutdown()


def build_ui(tmp_path):
    frontend = REPO / "frontend"
    deps = frontend / "node_modules"
    if not deps.is_dir() or not shutil.which("node"):
        pytest.skip("Needs the checkout's frontend dependencies and node")
    out = tmp_path / "ui"
    result = subprocess.run(
        [shutil.which("node"), str(deps / "vite/bin/vite.js"), "build", "--outDir", str(out), "--emptyOutDir"],
        cwd=frontend,
        env={"HOME": os.environ["HOME"], "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    return out


async def make_resume_pdf(path):
    from test_aggressive_pipeline import source_doc

    from jobpilot.features.resumes.compiler import compile_pdf
    from jobpilot.features.resumes.editor import Style, render

    compiled = await compile_pdf(render(source_doc(), Style(template="modern", font="helvetica", header="center", font_size=10.5, margin_left=0.55, margin_right=0.55, margin_top=0.45, margin_bottom=0.45)))
    assert compiled.ok
    path.write_bytes(compiled.pdf)


@pytest.mark.browser
async def test_nothing_to_a_tailored_pdf_on_the_employers_form(page, tmp_path):
    if not shutil.which("pdflatex"):
        pytest.skip("Needs TeX")
    static = build_ui(tmp_path)
    resume = tmp_path / "Alex_Example.pdf"
    await make_resume_pdf(resume)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {"HOME": os.environ["HOME"], "PATH": os.environ["PATH"], "PYTHONPATH": str(REPO) + os.pathsep + str(REPO / "tests")}
    empty = tmp_path / "empty"
    empty.mkdir()
    log = (tmp_path / "server.log").open("w")
    process = subprocess.Popen(
        ["/usr/bin/env", "-i", *(f"{k}={v}" for k, v in env.items()), sys.executable, str(Path(__file__).resolve()), str(port), str(static), str(tmp_path / "data")],
        cwd=empty,
        stdout=log,
        stderr=log,
    )
    bad_answers = []
    page.on("response", lambda r: bad_answers.append((r.status, r.url)) if r.status >= 500 and "/api/" in r.url else None)
    posts = []
    page.on("request", lambda r: posts.append(r.url) if r.method != "GET" else None)

    async def view(job):
        return await (await page.request.get(f"{url}/api/v2/resumes/tailor/job/{job}")).json()

    async def until_settled(job, states=("review", "approved", "failed")):
        for _ in range(240):
            current = await view(job)
            if (current["choice"] or {}).get("state") in states:
                return current
            await asyncio.sleep(0.5)
        raise AssertionError(current)

    try:
        for _ in range(300):
            try:
                if urllib.request.urlopen(urllib.request.Request(url + "/api/journey/health", headers=HEADERS), timeout=0.3).status == 200:
                    break
            except (urllib.error.URLError, TimeoutError):
                await asyncio.sleep(0.1)
        else:
            raise AssertionError((tmp_path / "server.log").read_text()[-3000:])
        await page.context.set_extra_http_headers(HEADERS)
        legal = await (await page.request.get(url + "/api/legal")).json()
        assert (await page.request.post(url + "/api/legal/accept", data={"version": legal["version"], "adult": True})).ok
        await page.add_init_script("localStorage.setItem('applypilot:guide-later', '1')")
        assert (await page.request.post(url + "/api/journey/seed", data={"ids": [1, 2, 3]})).ok

        # No resume yet: choosing tailoring says what to do, and links to it, instead of a code.
        await page.goto(url + "/?view=tailor&tailor=job-1")
        await page.locator(".tl-mode select").select_option("normal")
        await page.locator(".tl-failed").wait_for()
        failed = await page.locator(".tl-failed").inner_text()
        assert "Add yours in Resumes" in failed and "no_source_resume" not in failed
        await page.get_by_role("button", name="Open Resumes").click()

        # The resume, through the screen. The sentence names it at once, not half a minute later.
        await page.locator(".rs-add input[type=file]").set_input_files(str(resume))
        await page.get_by_role("button", name="Add resume", exact=True).last.click()
        await page.locator(".rs-sentence b", has_text="Alex_Example").wait_for(timeout=10000)
        said = await page.locator(".rs-state").inner_text()
        assert "Apply with tailored resume" in said, said  # Not only "the application uses your resume".

        # A model that can't answer ends in words, and trying again ends in a resume.
        assert (await page.request.post(url + "/api/journey/model", data={"fail": "NormalPlan"})).ok
        await page.goto(url + "/?view=tailor&tailor=job-1")
        await page.get_by_role("button", name="Try again").click()
        await page.locator(".tl-failed", has_text="couldn't be read").wait_for(timeout=60000)
        text = await page.locator(".tl-failed").inner_text()
        assert "Exceeded" not in text and "validation" not in text, text
        assert (await page.request.post(url + "/api/journey/model", data={"fail": ""})).ok

        # Choosing a mode again after a failure retries it.
        await page.locator(".tl-mode select").select_option("off")
        await page.locator(".tl-score").wait_for()
        await page.locator(".tl-mode select").select_option("normal")
        await page.get_by_role("button", name="Approve and apply").wait_for(timeout=60000)
        tailored = await view("job-1")
        assert tailored["rules"]["ok"] and tailored["stale"] is False

        # The score and the marks move as you type, and typing asks the server nothing.
        score = page.locator('[data-testid="ats-score"]')
        before = await score.inner_text()
        summary = page.get_by_label("Summary", exact=True)
        await summary.click()
        await page.keyboard.press("End")
        posts.clear()
        await page.keyboard.type(" Kubernetes", delay=15)
        assert await score.inner_text() != before
        assert "Kubernetes" in await page.locator(".tl-area-marks mark").all_inner_texts()
        assert posts == []
        # A claim nothing backs up is saved, and said out loud.
        await page.get_by_role("button", name="Save edits").click()
        await page.locator(".tl-claim", has_text="Kubernetes").wait_for(timeout=30000)
        await page.locator(".tl-message", has_text="Saved").wait_for()
        await summary.fill(tailored["doc"]["sections"][0]["text"])
        await page.get_by_role("button", name="Save edits").click()
        await page.locator(".tl-claim").wait_for(state="detached", timeout=30000)

        # The job's only project can be taken off (it used to fail a heading check), and put back.
        keep = page.get_by_label("Keep on this resume")
        await keep.uncheck()
        await page.get_by_role("button", name="Save edits").click()
        await page.locator(".tl-message", has_text="Saved").wait_for(timeout=30000)
        await keep.check()
        await page.get_by_role("button", name="Save edits").click()
        await page.wait_for_function("() => !document.querySelector('.tl-actions button')?.disabled")

        # Resumes now says this version is the job's, not "not in use".
        final = await view("job-1")
        listed = (await (await page.request.get(url + "/api/v2/resumes/artifacts?job_id=job-1&size=1")).json())["items"][0]
        assert listed["choice"] == {"mode": "normal", "state": "review"}

        # Approve: the exact file, and it joins the queue.
        await page.get_by_role("button", name="Approve and apply").click()
        await page.get_by_role("button", name="Approved", exact=True).wait_for(timeout=30000)
        approved = await view("job-1")
        assert approved["choice"]["approved_artifact_id"] == approved["artifact"]["id"] == final["artifact"]["id"]
        assert approved["in_line"] is True
        await page.get_by_text("In the queue", exact=True).wait_for()
        stored = await (await page.request.get(f"{url}/api/v2/resumes/artifacts/{approved['artifact']['id']}/pdf")).body()
        assert hashlib.sha256(stored).hexdigest() == approved["artifact"]["pdf_sha"]

        # Queue, Job board and Review batch all offer the choice, on the same one row each.
        await page.goto(url + "/?view=queue")
        queue_row = page.locator("select[aria-label='How to apply']")
        await queue_row.first.wait_for(timeout=20000)
        assert await queue_row.first.input_value() == "normal"
        await page.get_by_text("Ready to send").first.wait_for()
        await page.goto(url + "/?view=board")
        board_row = page.locator("select[aria-label='How to apply']")
        await board_row.first.wait_for(timeout=20000)
        options = await board_row.first.locator("option").all_inner_texts()
        assert options[:2] == ["Apply", "Apply with tailored resume: Normal"], options

        # The run: the real worker uploads the approved file to the employer's form.
        run = await page.request.post(url + "/api/journey/execute", data={"job_id": "job-1"}, timeout=120000)
        assert run.ok, await run.text()
        outcome = await run.json()
        assert outcome["state"] == "confirmed", outcome
        assert outcome["artifact"] == approved["artifact"]["id"]
        assert len(outcome["applications"]) == 1, outcome
        assert outcome["applications"][0]["resume__sha256"] == outcome["packet_sha"] == approved["artifact"]["pdf_sha"] == hashlib.sha256(stored).hexdigest()

        # Sent: the screen reads the same and nothing can change it.
        await page.goto(url + "/?view=tailor&tailor=job-1")
        await page.get_by_role("button", name="Application started").wait_for(timeout=20000)
        assert await page.get_by_role("button", name="Tailor again").is_disabled()
        assert bad_answers == []
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        log.close()


if __name__ == "__main__":
    serve(int(sys.argv[1]), sys.argv[2], sys.argv[3])
