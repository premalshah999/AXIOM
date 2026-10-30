import argparse
import asyncio
import hashlib
from urllib.parse import urlsplit

from .config import settings


async def capture_session(config, url):
    from playwright.async_api import async_playwright

    from .network import public_url

    await public_url(url, config.allow_private_urls)
    host = urlsplit(url).hostname
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, executable_path=config.chromium_path or None)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(url)
        print("Sign in in the browser. Return here and press Enter when the application is visible.")
        await asyncio.to_thread(input)
        state = await context.storage_state()
        state["cookies"] = [
            c
            for c in state["cookies"]
            if host == c["domain"].lstrip(".") or host.endswith("." + c["domain"].lstrip("."))
        ]
        state["origins"] = [o for o in state["origins"] if urlsplit(o["origin"]).hostname == host]
        import json

        path = config.data_dir / "sessions" / (hashlib.sha256(host.encode()).hexdigest() + ".json")
        path.write_text(json.dumps(state))
        path.chmod(0o600)
        await browser.close()
        print(f"Saved employer session for {host}. Requeue the application in Studio.")


async def probe(config, url, company=""):
    """Run one dry run outside the queue and print what each step saw. Nothing is submitted."""
    from .db import Database, Resume, Run
    from .discovery import add_job
    from .schemas import JobInput
    from .service import Service

    db = Database(config.database_url)
    service = Service(db, config)
    with db.session() as s:
        resume = s.query(Resume).filter(Resume.demo.is_(False)).first()
        if not resume:
            raise SystemExit("Upload a resume in the dashboard first")
        resume_id = resume.id
    job, _ = add_job(db, JobInput(url=url, company=company), origin="manual")
    try:
        run = await service.queue(job["id"], "dry_run", resume_id, retry=True)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    print(f"Run {run['id']} (dry run) on {job['ats']} ...")
    await service.execute(run["id"])
    with db.session() as s:
        row = s.get(Run, run["id"])
        print(f"Result: {row.state} - {row.reason} ({row.elapsed:.0f}s, {row.model_calls} model calls)")
    print_trace(config, run["id"])


def print_trace(config, run_id):
    import json

    folder = config.data_dir / "runs" / run_id / "steps"
    if not folder.exists():
        print("No step trace for this run (TRACE_STEPS=false or it ended before the first step).")
        return
    for path in sorted(folder.glob("*.json")):
        step = json.loads(path.read_text())
        if not isinstance(step, dict):
            continue  # A frame-structure record (*.form.json, 2026-10-21 runs), not a step summary.
        print(f"\n[{step['n']:02d}] {step['step']}  {step['url']}")
        if step["headings"]:
            print("     headings: " + " | ".join(step["headings"][:4]))
        for error in step["errors"][:4]:
            print("     error:    " + error[:160])
        for f in step["fields"][:40]:
            mark = "*" if f["required"] else " "
            state = "filled" if f["filled"] else "empty "
            print(
                f"     {mark} {state} {f['type']:<10} {f['label'][:70]}"
                + (f"  [{f['group']}]" if f.get("group") else "")
            )
        print("     controls: " + ", ".join(c["label"] for c in step["controls"][:14]))
    print(f"\nSnapshots (HTML + screenshots): {folder}")


def _sizes(db):
    """Tables that shrink, in MB as PostgreSQL counts them; {} on SQLite."""
    from sqlalchemy import text

    if db.engine.dialect.name != "postgresql":
        return {}
    with db.engine.connect() as c:
        return {
            "runs": round(c.execute(text("select pg_total_relation_size('runs')")).scalar() / 1e6),
            "profile_snapshots": round(c.execute(text("select pg_total_relation_size('profile_snapshots')")).scalar() / 1e6),
            "database": round(c.execute(text("select pg_database_size(current_database())")).scalar() / 1e6),
        }


def compact_runs(config, apply, say=print) -> int:
    """The report goes to stdout as JSON. Safe beside a live application."""
    import json

    from .db import Database
    from .run_profile import compact_runs as compact

    db = Database(config.database_url)
    before = _sizes(db)
    report = compact(
        db,
        apply=apply,
        progress=lambda r: say(f"{r['runs_scanned']} runs read, {r['runs_changed']} to change", flush=True),
    )
    say(json.dumps({**report, "sizes_before_mb": before, "sizes_after_mb": _sizes(db)}, indent=1))
    if apply and before:
        say("PostgreSQL reuses the freed space but its files keep their size until: VACUUM (FULL, ANALYZE) runs;")
        say("That locks the table while it runs, so stop the app first. It needs free disk equal to the new size.")
    elif not apply:
        say("Nothing changed. Run again with --apply to write this.")
    return 0


def prune_workflows(config, days, apply, say=print) -> int:
    import json

    from .retention import has_history, open_system_database, prune

    days = config.dbos_retention_days if days is None else days
    if days <= 0:
        say("Retention is off (DBOS_RETENTION_DAYS=0); pass --days to choose an age.")
        return 1
    database = open_system_database(config)
    try:
        say(json.dumps(prune(database, days, apply=apply), indent=1) if has_history(database) else "No DBOS history yet.")
    finally:
        database.destroy()
    return 0


def reset_password(config, ask=None, say=print) -> int:
    """Set the owner's dashboard password from the host, without echoing it, and sign every browser out.

    The last of three ways back in for an owner who forgot the password (2026-10-22): this installation cannot send
    email, so whoever can run commands on the host is the owner. Returns the process exit code.
    """
    import getpass

    from .db import Database
    from .owner_account import account, password_problem, set_password

    ask = ask or getpass.getpass
    db = Database(config.prepare().database_url)
    stored = account(db)
    if not stored.get("password_hash"):
        say("No email and password are set on this installation yet.")
        say(
            "Sign in to the dashboard with the access token (`jobpilot token`), then add them in Setup → System."
        )
        return 1
    say(f"Setting a new dashboard password for {stored.get('email') or 'the owner account'}.")
    for _ in range(3):
        password = ask("New password: ")
        if problem := password_problem(password):
            say(problem + ".")
            continue
        if ask("Type it again: ") != password:
            say("The two passwords differ. Try again.")
            continue
        set_password(db, password, reason="reset_cli")
        say(
            "Password changed. Every browser that was signed in must sign in again; recovery codes still work."
        )
        return 0
    say("Password not changed.")
    return 1


def main():
    parser = argparse.ArgumentParser(description="ApplyPilot Studio")
    sub = parser.add_subparsers(dest="command", required=True)
    server = sub.add_parser("serve")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8080)
    sub.add_parser("token", help="Print this installation's dashboard access token")
    sub.add_parser(
        "reset-password",
        help="Set a new dashboard password (asks without echoing) and sign every browser out",
    )
    doctor = sub.add_parser("doctor", help="Check local configuration")
    doctor.add_argument("--check-mail", action="store_true", help="Log in to Gmail over IMAP once")
    probe = sub.add_parser("probe", help="Dry-run one job URL and print the step trace (never submits)")
    probe.add_argument("url")
    probe.add_argument("--headed", action="store_true", help="Show the browser window (needs a display)")
    probe.add_argument("--company", default="")
    trace = sub.add_parser("trace", help="Summarize the step trace of a run")
    trace.add_argument("run_id")
    sub.add_parser("mcp", help="Serve read-only application tools over MCP stdio")
    compact = sub.add_parser("compact-runs", help="Drop duplicate profiles and unneeded checkpoints from runs not in flight")
    compact.add_argument("--apply", action="store_true", help="Write the changes; without it this only reports")
    prune = sub.add_parser("prune-workflows", help="Delete finished DBOS workflows past the retention age")
    prune.add_argument("--days", type=int, help="Keep this many days (default DBOS_RETENTION_DAYS)")
    prune.add_argument("--apply", action="store_true", help="Delete them; without it this only counts")
    verify = sub.add_parser(
        "verify", help="Exercise the running installation with two synthetic browser runs"
    )
    verify.add_argument("--report", help="Write evidence manifest (default: DATA_DIR/verification.json)")
    verify.add_argument(
        "--recheck", help="Verify an existing manifest after restart; creates no applications"
    )
    verify.add_argument("--timeout", type=int, default=420, help="Total queue wait budget in seconds")
    login = sub.add_parser("login", help="Capture an employer session using your own local browser")
    login.add_argument("url")
    desktop = sub.add_parser("browser", help="Open the local application browser for Docker workers")
    desktop.add_argument("--port", type=int, default=9224)
    twin = sub.add_parser(
        "twin-browser", help="Open the clean twin Chrome that mints hCaptcha tokens (BROWSER_TWIN_CDP_URL)"
    )
    twin.add_argument("--port", type=int, default=9225)
    args = parser.parse_args()
    config = settings()
    if args.command == "token":
        print(config.app_token)
    elif args.command == "reset-password":
        raise SystemExit(reset_password(config))
    elif args.command in {"compact-runs", "prune-workflows"}:
        import os

        os.nice(10)  # Beside live browsers: both are background work.
        if args.command == "compact-runs":
            raise SystemExit(compact_runs(config.prepare(), args.apply))
        raise SystemExit(prune_workflows(config.prepare(), args.days, args.apply))
    elif args.command == "mcp":
        from .mcp_server import create_server

        create_server(config).run(transport="stdio")
    elif args.command == "serve":
        import uvicorn

        from .api import create_app

        # A hung request must not hold the stop up: the drain runs in the lifespan shutdown, after this wait.
        uvicorn.run(
            create_app(config),
            host=args.host,
            port=args.port,
            log_level="warning",
            timeout_graceful_shutdown=15,
        )
    elif args.command == "login":
        asyncio.run(capture_session(config, args.url))
    elif args.command == "probe":
        if args.headed:
            config.headless = False
        asyncio.run(probe(config, args.url, args.company))
    elif args.command == "trace":
        print_trace(config, args.run_id)
    elif args.command == "browser":
        import subprocess
        from pathlib import Path
        from playwright.sync_api import sync_playwright

        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        with sync_playwright() as pw:
            executable = config.chromium_path or (
                str(chrome) if chrome.exists() else pw.chromium.executable_path
            )
        profile = config.data_dir / "desktop-browser"
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        subprocess.Popen(
            [
                executable,
                f"--remote-debugging-port={args.port}",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
                # Bounded renderers: two profiles reached 47 GB each on a 16 GB Mac (2026-10-26).
                "--renderer-process-limit=8",
                # A capped disk cache: an unbounded one grew to ~1 GB and slowed page loads.
                "--disk-cache-size=268435456",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print("Application browser opened. Leave this Chrome window open while applications run.")
    elif args.command == "twin-browser":
        import subprocess
        from pathlib import Path

        # Never connect Playwright to this Chrome: an attached session is what hCaptcha distrusts (captcha_twin).
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        profile = config.data_dir / "twin-browser"
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        subprocess.Popen(
            [
                config.chromium_path or str(chrome),
                f"--remote-debugging-port={args.port}",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile}",
                "--no-startup-window",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
                # Bounded renderers: two profiles reached 47 GB each on a 16 GB Mac (2026-10-26).
                "--renderer-process-limit=8",
                "--disk-cache-size=134217728",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print(f"Twin browser started on port {args.port} with no window; it opens a minimized one per token.")
    elif args.command == "verify":
        import sys

        import httpx

        from .verify import VerificationFailed, verify_deployment, write_report

        if args.timeout < 1:
            parser.error("--timeout must be positive")
        try:
            report = verify_deployment(config, recheck=args.recheck, timeout=args.timeout)
            path = args.report or config.data_dir / "verification.json"
            write_report(path, report)
        except (VerificationFailed, httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
            # Do not print transport errors, which can contain request credentials.
            reason = str(exc) if isinstance(exc, VerificationFailed) else type(exc).__name__
            print(f"FAIL: {reason}", file=sys.stderr)
            raise SystemExit(1) from None
        print(f"PASS: {', '.join(report['checks'])}")
        print(f"Evidence: {path}")
        print(
            "This verifies the owned demo. Live ATS flows and provider integrations remain separate checks."
        )
    else:
        from pathlib import Path

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = Path(config.chromium_path or pw.chromium.executable_path).exists()
        for label, ready in [
            ("Chromium", browser),
            (config.llm_model, bool(config.llm_api_key)),
            ("Telegram", bool(config.telegram_bot_token and config.telegram_user_id)),
            ("Gmail app password (OTP/links)", bool(config.gmail_address and config.gmail_app_password)),
            ("Gmail OAuth (optional)", bool(config.google_client_id and config.google_client_secret)),
            ("TWOCAPTCHA token solver", bool(config.twocaptcha_api_key)),
            ("Employer account password (else generated once)", bool(config.application_password)),
        ]:
            print(f"{'READY' if ready else 'MISSING':7} {label}")
        from .config import password_problems

        if config.application_password and (problems := password_problems(config.application_password)):
            print("WARN    APPLICATION_PASSWORD: " + "; ".join(problems))
        print(f"        Account email: {config.account_email or '(profile email)'} · engine: {config.engine}")
        if args.check_mail and config.gmail_address and config.gmail_app_password:
            from .gmail_imap import request

            try:
                request(
                    config.gmail_address.strip().lower(),
                    "".join(config.gmail_app_password.split()),
                    "/profile",
                )
                print("READY   Gmail IMAP login (read-only)")
            except Exception as exc:
                print(
                    f"FAIL    Gmail IMAP login: {type(exc).__name__}. Check the app password and that IMAP is enabled."
                )
        print(
            f"{config.normal_workers} ATS workers + {config.specialist_workers} specialists · {config.application_timeout}s budget · {config.daily_application_limit} submissions/day"
        )
        print("Run `jobpilot token` to view your dashboard access token.")


if __name__ == "__main__":
    main()
