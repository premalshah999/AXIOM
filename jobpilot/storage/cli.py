"""`jobpilot database ...`: the Database screen's work from a terminal. Connection strings are asked for without echo or read from the
environment, never taken from the command line, where `ps` would show them."""

import getpass
import os
import sys

from . import check, choice, mode, switch

MARK = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL", "skip": "    "}


def show(report, say=print):
    for item in report["checks"]:
        say(f"  {MARK.get(item['status'], '    ')} {item['label']}: {item['detail']}")
    workers = report.get("workers")
    if workers:
        say(f"  Workers: {workers['text']}")
    say("The connection can be used." if report["ok"] else "This database cannot be used yet. Nothing was changed.")


def command(args, config_factory, say=print) -> int:
    """`config_factory()` returns the prepared settings; use-local avoids it, because a broken remote choice is why it is run."""
    action = args.action
    if action == "use-local":
        from ..config import Settings

        config = Settings()
        config.data_dir = config.data_dir.resolve()
        config.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        choice.save(config, "local")
        say("The database on this computer is chosen. It takes effect when AXIOM next starts (nothing was copied or deleted).")
        if "database_mode" in config.model_fields_set and config.database_mode != "local":
            say(f"But DATABASE_MODE={config.database_mode} is set in the environment or .env, and that wins: remove it.")
        return 0
    config = config_factory()
    if action == "status":
        view = choice.view(config)
        say(f"In use: {view['mode']}" + (f" (restart to use {view['chosen']})" if view["restart_needed"] else ""))
        for name, option in view["options"].items():
            if name != "local":
                say(f"  {name}: {option['target'] or 'not set'}")
        return 0
    if action == "set":
        if config.database_mode != "local" and args.target == config.database_mode:
            say("That database is in use; switch to another first.")
            return 1
        raw = os.environ.get("AXIOM_DATABASE_URL") or (getpass.getpass("Connection string (not shown): ") if sys.stdin.isatty() else sys.stdin.readline())
        report = check.run(args.target, raw)
        show(report, say)
        if not report["ok"]:
            return 1
        choice.keep_url(config, args.target, raw)
        say(f"Saved. Switch to it with: jobpilot database switch {args.target}")
        return 0
    if action == "test":
        target = args.target or config.database_mode
        raw = choice.local_urls(config)[0] if target == "local" else choice.url_for(config, target)
        if not raw:
            say(f"No connection string is saved for {target}. Add one with: jobpilot database set {target}")
            return 1
        report = check.run(target, raw)
        show(report, say)
        return 0 if report["ok"] else 1
    if action == "switch":
        try:
            report = switch.run(config, args.target, copy=args.copy, replace=args.replace, verify=args.verify, say=say)
        except switch.SwitchError as exc:
            say(f"Not switched: {exc}")
            return 1
        say(report["message"])
        return 0
    return 2


def add_parser(sub):
    database = sub.add_parser("database", help="Choose where AXIOM keeps its data: this computer, Supabase or a server")
    database.add_argument("action", choices=["status", "set", "test", "switch", "use-local"])
    database.add_argument("target", nargs="?", choices=list(mode.MODES))
    database.add_argument("--copy", dest="copy", action="store_true", default=True, help="switch: bring the data along (default)")
    database.add_argument("--no-copy", dest="copy", action="store_false", help="switch: use the other database as it is")
    database.add_argument("--replace", action="store_true", help="switch: overwrite AXIOM data already in the other database")
    database.add_argument("--verify", choices=["counts", "full"], default="counts", help="switch: also compare a hash of every row")
    return database
