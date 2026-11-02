"""Functional browser journey on an empty install, with a local upload form.

The test explicitly mounts the owned drafts router/component; the main app's
shared screen wiring is Claude Code's responsibility. No visual approval tests.
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request

import pytest


REPO = Path(__file__).resolve().parents[1]


def serve(port, static_root, data_dir):
    import uvicorn
    from fastapi.responses import FileResponse
    from jobpilot.api import create_app
    from jobpilot.config import Settings
    from jobpilot.db import Job, Resume, Run
    from jobpilot.schemas import Profile
    from jobpilot.features.resumes.compiler import compile_pdf
    from jobpilot.features.resumes.editor import render, tailored_style
    from test_aggressive_pipeline import JOB, PROFILE, add_job, context, stub

    config = Settings(
        _env_file=None,
        data_dir=Path(data_dir),
        base_url=f"http://127.0.0.1:{port}",
        app_token="aggressive-test-token",
        enable_workers=False,
        telegram_enabled=False,
        allow_private_urls=True,
        llm_provider="mimo",
        mimo_api_key="aggressive-test-no-model",
        mimo_base_url="http://127.0.0.1:9/v1",
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
    from jobpilot.features.knowledge import writing

    generation_model = stub()
    writing.configured_model = lambda *_args, **_kwargs: generation_model
    # A separate, public local employer: the real application worker needs no
    # authentication to it. This thread inherits the server's empty CWD/env.
    from email.parser import BytesParser
    from email.policy import default
    from gallery import site as site_module

    parse_form = site_module.parse_form

    def recorded_form(content_type, body):
        fields = parse_form(content_type, body)
        if "multipart/form-data" in (content_type or ""):
            message = BytesParser(policy=default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
            )
            for part in message.iter_parts():
                if part.get_filename():
                    fields[part.get_param("name", header="content-disposition") + "__sha256"] = (
                        hashlib.sha256(part.get_payload(decode=True)).hexdigest()
                    )
        return fields

    site_module.parse_form = recorded_form
    employer = site_module.Site(
        {
            "/jobs/1": f'<html lang="en"><title>Reporting Analyst at ExampleWorks</title><body><h1>Reporting Analyst</h1><p>ExampleWorks · Remote</p><pre>{JOB["description"]}</pre><a href="/apply/1">Apply for this job</a></body></html>',
            "/apply/1": '<html lang="en"><title>Apply: Reporting Analyst</title><body><h1>Apply: Reporting Analyst</h1><form method="post" action="/submit/1" enctype="multipart/form-data"><label for="first">First name</label><input id="first" name="first_name" required><label for="last">Last name</label><input id="last" name="last_name" required><label for="email">Email</label><input id="email" name="email" type="email" required><label for="resume">Resume</label><input id="resume" name="resume" type="file" accept=".pdf" required><button type="submit">Submit application</button></form></body></html>',
            "/submit/1": '<html lang="en"><title>Application received</title><body><h1>Thank you for applying!</h1><p>Your application has been received. We will be in touch.</p></body></html>',
        }
    )
    local_job = {**JOB, "url": employer.base + "/jobs/1"}
    profile = Profile.model_validate(
        {**PROFILE, "autonomous": True, "facts": {"first_name": "Alex", "last_name": "Example"}}
    ).model_dump(mode="json")
    test_start = len(app.router.routes)

    @app.post("/api/aggressive-test/generate")
    async def generate():
        add_job(service)
        service.db.set_setting("profile", profile)
        source, ctx = context(Path(data_dir), db=service.db, profile=profile, job=local_job)
        master = await compile_pdf(render(source.doc, tailored_style(source.style)))
        assert master.ok
        source.sha = hashlib.sha256(master.pdf).hexdigest()
        (config.data_dir / "resumes").mkdir(exist_ok=True)
        (config.data_dir / "resumes/source.pdf").write_bytes(master.pdf)
        with service.db.exclusive() as session:
            session.get(Job, "ag-job").url = local_job["url"]
            session.get(Resume, "source").sha256 = source.sha
        service.db.set_setting("control", {"paused": True, "auto_submit": True})
        return {"job_id": "ag-job"}

    @app.post("/api/aggressive-test/execute")
    async def execute():
        async def dispatch(_run_id):
            pass

        service.dispatch = dispatch
        service.db.set_setting("control", {"paused": False, "auto_submit": True})
        from jobpilot.company_sites import allow

        allow(service.db, ["ag-job"])  # A local page reads as a company's own site: the owner's click sends it.
        queued = await service.queue("ag-job", "submit", "source", owner_pick=True)
        await asyncio.wait_for(service.execute(queued["id"]), 90)
        with service.db.session() as session:
            actual = session.get(Run, queued["id"])
            result = {
                "state": actual.state,
                "reason": actual.reason,
                "model_calls": actual.model_calls,
                "packet_sha": actual.packet["resume_sha"],
            }
        return {**result, "applications": employer.applications()}

    @app.get("/api/aggressive-test/health")
    def health():
        return {"ok": True, "generation_calls": len(generation_model.calls)}

    @app.get("/aggressive-test")
    def ui():
        return FileResponse(Path(static_root) / "index.html")

    from fastapi.staticfiles import StaticFiles

    # Feature/test routes mount before the main app's SPA fallback.
    last_routes = app.router.routes[test_start:]
    del app.router.routes[test_start:]
    app.router.routes[0:0] = last_routes
    # Build and serve the real frontend, leaving the checkout's bundled assets
    # untouched. Its production feature router is already mounted by create_app.
    app.router.routes[:] = [route for route in app.router.routes if getattr(route, "path", None) != "/assets"]
    app.mount("/assets", StaticFiles(directory=Path(static_root) / "assets"))
    asset_route = app.router.routes.pop()
    app.router.routes.insert(0, asset_route)
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    finally:
        employer.close()


def build_ui(tmp_path):
    frontend = REPO / "frontend"
    deps = frontend / "node_modules"
    if not deps.is_dir() or not shutil.which("node"):
        pytest.skip("Needs the checkout's frontend dependencies and node")
    harness = tmp_path / "ui"
    harness.mkdir()
    config = harness / "vite.config.mjs"
    config.write_text(
        "import {defineConfig} from "
        + json.dumps(str(deps / "vite/dist/node/index.js"))
        + ";\nimport react from "
        + json.dumps(str(deps / "@vitejs/plugin-react/dist/index.js"))
        + ";\nexport default defineConfig({root:"
        + json.dumps(str(frontend))
        + ", envDir:false, base:'/', plugins:[react()], resolve:{alias:{react:"
        + json.dumps(str(deps / "react"))
        + ",'react-dom':"
        + json.dumps(str(deps / "react-dom"))
        + "}},build:{outDir:"
        + json.dumps(str(harness / "dist"))
        + ",emptyOutDir:true}});"
    )
    clean_env = {"HOME": os.environ["HOME"], "PATH": os.environ["PATH"]}
    result = subprocess.run(
        [shutil.which("node"), str(deps / "vite/bin/vite.js"), "build", "--config", str(config)],
        cwd=tmp_path,
        env=clean_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    return harness / "dist"


@pytest.mark.browser
async def test_drafts_approval_original_switch_and_exact_upload(page, tmp_path):
    if not shutil.which("pdflatex"):
        pytest.skip("Needs TeX")
    static = build_ui(tmp_path)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {
        "HOME": os.environ["HOME"],
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(REPO) + os.pathsep + str(REPO / "tests"),
    }
    # /usr/bin/env -i, empty CWD and a private DATA_DIR are deliberate.
    log = (tmp_path / "server.log").open("w")
    process = subprocess.Popen(
        [
            "/usr/bin/env",
            "-i",
            *(f"{k}={v}" for k, v in env.items()),
            sys.executable,
            str(Path(__file__).resolve()),
            str(port),
            str(static),
            str(tmp_path / "data"),
        ],
        cwd=tmp_path,
        stdout=log,
        stderr=log,
    )
    headers = {"Authorization": "Bearer aggressive-test-token"}
    try:
        for _ in range(200):
            try:
                response = urllib.request.urlopen(
                    urllib.request.Request(url + "/api/aggressive-test/health", headers=headers), timeout=0.3
                )
                if response.status == 200:
                    break
            except (urllib.error.URLError, TimeoutError):
                await asyncio.sleep(0.1)
        else:
            raise AssertionError((tmp_path / "server.log").read_text()[-3000:])
        await page.context.set_extra_http_headers(headers)
        response = await page.request.post(url + "/api/aggressive-test/generate", data={})
        assert response.ok, await response.text()
        legal = await (await page.request.get(url + "/api/legal")).json()
        assert (
            await page.request.post(
                url + "/api/legal/accept", data={"version": legal["version"], "adult": True}
            )
        ).ok
        await page.add_init_script("localStorage.setItem('applypilot:guide-later', '1')")
        chosen = await page.request.put(
            url + "/api/v2/resumes/tailor/job/ag-job", data={"mode": "aggressive"}
        )
        assert chosen.ok, await chosen.text()
        await page.goto(url + "/aggressive-test?view=tailor&tailor=ag-job")
        await page.get_by_role("heading", name="Choose your projects").wait_for()

        async def current():
            return await (await page.request.get(url + "/api/v2/resumes/tailor/job/ag-job")).json()

        initial = await current()
        approval = page.get_by_role("button", name="Approve and apply", exact=True)
        assert await approval.is_disabled()
        assert await page.get_by_role("button", name="Approve project", exact=True).count() == 3
        assert "3 selected projects need" in await page.locator(".ag-status").inner_text()
        warning = await page.locator(".ag-warning").inner_text()
        assert "Please verify every detail on this resume before using it for applications." in warning
        assert await page.get_by_text("Approve only once you've verified every detail of this project is accurate.").count() == 3
        pdf = await page.request.get(url + f"/api/v2/resumes/artifacts/{initial['artifact']['id']}/pdf")
        assert pdf.ok
        blocked = await page.request.post(
            url + "/api/v2/resumes/tailor/job/ag-job/approve",
            data={"artifact_id": initial["artifact"]["id"], "apply": False},
        )
        assert blocked.status == 409
        generation_calls = (await (await page.request.get(url + "/api/aggressive-test/health")).json())[
            "generation_calls"
        ]
        assert generation_calls == 4
        await page.get_by_role("button", name="Use only my original projects").click()
        await page.get_by_role("button", name="Update project PDF", exact=True).click()
        await page.wait_for_function("() => !document.querySelector('.tl-actions button')?.disabled")
        original = await current()
        assert original["rules"]["ok"] and not original["drafts"]
        assert original["cost"]["calls"] == 0
        checks = page.locator(".ag-projects input[type=checkbox]")
        for i in range(3):
            assert not await checks.nth(i).is_checked()
            await checks.nth(i).check()
            await page.wait_for_function(
                "() => document.querySelector('[aria-label=\"Aggressive project drafts\"]')?.getAttribute('aria-busy') === 'false'"
            )
        for _ in range(3):
            await page.get_by_role("button", name="Approve project", exact=True).first.click()
            await page.wait_for_function(
                "() => document.querySelector('[aria-label=\"Aggressive project drafts\"]')?.getAttribute('aria-busy') === 'false'"
            )
        assert await page.get_by_role("button", name="Approve project", exact=True).count() == 0
        await page.get_by_role("button", name="Update project PDF", exact=True).click()
        await page.wait_for_function("() => !document.querySelector('.tl-actions button')?.disabled")
        await approval.click()
        await page.get_by_role("button", name="Approved", exact=True).wait_for()
        result = await current()
        assert result["choice"]["approved_artifact_id"] == result["artifact"]["id"]
        assert (await (await page.request.get(url + "/api/aggressive-test/health")).json())[
            "generation_calls"
        ] == generation_calls
        executed = await page.request.post(url + "/api/aggressive-test/execute", data={}, timeout=120000)
        assert executed.ok, await executed.text()
        outcome = await executed.json()
        assert outcome["state"] == "confirmed", outcome
        assert outcome["model_calls"] == 0, outcome
        assert len(outcome["applications"]) == 1, outcome
        assert (
            outcome["applications"][0]["resume__sha256"]
            == outcome["packet_sha"]
            == result["artifact"]["pdf_sha"]
        )
        assert outcome["applications"][0]["resume"] == "Alex_Example_Resume.pdf"
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
