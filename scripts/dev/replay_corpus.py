#!/usr/bin/env python3
"""Replay every page classifier and scan rule over real saved pages, and say what a code change would change, before it ships.

    scripts/dev/replay_corpus.py collect             copy saved pages into the private corpus (a few of each platform and outcome)
    scripts/dev/replay_corpus.py baseline            record what the code in this checkout reads on every page in it (--code PATH: another's)
    ... change the code ...
    scripts/dev/replay_corpus.py compare             what this checkout reads differently, grouped, with the run and page to go and look at
    scripts/dev/replay_corpus.py compare --scan 300  also run scan.js itself over 300 saved pages in a headless browser (slower)

Saved pages hold personal data, so the corpus never enters this public repository. It lives in AXIOM_CORPUS_DIR (default
~/.cache/axiom-corpus, private to this user), as compressed copies, because a run's pages are deleted after 7 days. Only this runner is
in the repo, and it contains no page, name or answer. Read-only towards the app: it copies files and runs SELECTs through `axiom psql`.
The code replayed makes no network request: rules are pure functions, and --scan aborts everything the saved page asks for.

Compare exits 3 when anything changed, 0 when nothing did. A change is not a failure, it is the thing to read: replay old against new and
read every change before committing (a rule that passes its test can still flip hundreds of real pages).

Rules on each saved page: the adapter's page classification and Apply choice, the fields it would treat as the application, the posting
widgets, confirmation wording. On each saved answers.json: the work-status decision for each question, with this installation's own facts.
"""

import argparse
import collections
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
# --code PATH reads another checkout's rules (the last release, or main) into the baseline. Parsed before anything is imported.
sys.path.insert(0, sys.argv[sys.argv.index("--code") + 1] if "--code" in sys.argv else str(ROOT))
RUNS = Path(os.environ.get("AXIOM_RUNS_DIR", "~/Library/Application Support/AXIOM/data/runs")).expanduser()
AXIOM = os.environ.get("AXIOM_BIN", "/Applications/AXIOM.app/Contents/Resources/bin/axiom")
CORPUS = Path(os.environ.get("AXIOM_CORPUS_DIR", "~/.cache/axiom-corpus")).expanduser()
WORK_WORDS = re.compile(r"sponsor|visa|authori[sz]|citizen|employ|work", re.I)


class Fields(dict):
    """A saved observation keeps fewer keys than a live one: a rule reading a missing one sees it empty, as it does for a page without it."""

    def __missing__(self, _key):
        return ""


def select(sql):
    done = subprocess.run([AXIOM, "psql", "-XAt", "-F", "|", "-c", sql], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
    if done.returncode:
        raise SystemExit(f"axiom psql: {done.stderr[-300:]}")
    return [line.split("|") for line in done.stdout.splitlines() if line]


def private(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


# ---- collect ---------------------------------------------------------------------------------------------------------


def collect(options):
    """A few of the newest runs of each platform and outcome that still have their pages. Adds to what is there; nothing is removed."""
    pages = private(CORPUS / "pages")
    index_file = CORPUS / "index.json"
    index = json.loads(index_file.read_text()) if index_file.exists() else {}
    rows = select("select r.id, j.ats, r.state, j.company from runs r join jobs j on j.id = r.job_id "
                  "where j.demo = false and r.started_at is not null order by r.created_at desc limit 8000")
    taken, added = collections.Counter(), 0
    for run_id, ats, state, company in rows:
        if taken[ats, state] >= options.per_group or not (RUNS / run_id / "steps").is_dir():
            continue
        taken[ats, state] += 1
        if run_id in index:
            continue
        target = private(pages / run_id)
        files = [p for p in (RUNS / run_id / "steps").iterdir() if p.suffix in {".json", ".html"} and not p.name.endswith(".form.json")]
        answers = RUNS / run_id / "answers.json"
        for path in [*files, *([answers] if answers.exists() else [])]:
            with path.open("rb") as source, gzip.open(target / (path.name + ".gz"), "wb", compresslevel=6) as sink:
                shutil.copyfileobj(source, sink)
        index[run_id] = {"ats": ats, "state": state, "company": company}
        added += 1
    index_file.write_text(json.dumps(index))
    index_file.chmod(0o600)
    size = sum(p.stat().st_size for p in pages.rglob("*") if p.is_file()) / 1024**2
    print(f"{added} runs added; {len(index)} in the corpus, {size:.0f} MB at {CORPUS}")


# ---- rules -----------------------------------------------------------------------------------------------------------


def read(path):
    with gzip.open(path, "rt", errors="replace") as stream:
        return stream.read()


def adapter_for(ats, url, service, profile):
    from jobpilot.adapters import for_ats

    engine = SimpleNamespace(service=service, config=service.config, job={"id": "corpus", "url": url, "ats": ats, "company": "", "demo": False},
                             run_record={"id": "corpus", "mode": "submit", "packet": {}}, profile=profile, form=None, armed=False, run_id="corpus",
                             page=SimpleNamespace(url=url), db=service.db, emit=lambda *a, **k: None)
    return for_ats(ats, engine)


def observation(saved, html):
    from bs4 import BeautifulSoup

    obs = Fields(saved)
    obs["fields"] = [Fields(f, required=f.get("required") in (True, "True", "true")) for f in saved.get("fields") or [] if isinstance(f, dict)]
    obs["controls"] = [Fields(c) for c in saved.get("controls") or [] if isinstance(c, dict)]
    obs["text"] = " ".join(BeautifulSoup(html, "html.parser").get_text(" ").split()) if html else " ".join(saved.get("headings") or [])
    obs["dialogs"] = saved.get("dialogs") or []
    return obs


def page_rules(adapter, obs):
    """What the code reads on this page. A rule that raises is a verdict too: it was, or it stopped being, able to read it."""
    from jobpilot.adapters.base import posting_widget

    verdict = {}

    def rule(name, fn):
        try:
            verdict[name] = fn()
        except Exception as exc:  # noqa: BLE001 - see the docstring
            verdict[name] = f"error: {type(exc).__name__}"

    rule("page", lambda: str(adapter.classify_page(obs)))
    rule("apply", lambda: (adapter.find_apply(obs) or {}).get("label", ""))
    rule("form_fields", lambda: sorted(str(f.get("label"))[:60] for f in adapter.app_fields(obs)))
    rule("posting_widget", lambda: posting_widget(obs["fields"]))
    return verdict


def answer_rules(answers, facts):
    from jobpilot.answers.work_status import decide

    verdicts = {}
    for item in answers.values():
        if not isinstance(item, dict):
            continue
        question = " ".join(str(item.get("label", "")).split())
        if len(question) < 12 or not WORK_WORDS.search(question):
            continue
        options = [str(o) for o in (item.get("options") or [])]
        try:
            decided = decide({"label": question, "options": options if len(options) > 1 else ["Yes", "No"], "type": "select"}, facts)
            verdicts[question[:120]] = [decided.kind, decided.value] if decided else None
        except Exception as exc:  # noqa: BLE001
            verdicts[question[:120]] = f"error: {type(exc).__name__}"
    return verdicts


def scan_rules(run_pages):
    """scan.js itself, on saved HTML, in a browser that fetches nothing. Saved pages have no stylesheets or scripts of their own here, so this
    answers one question only: did the scan's reading of the same document change?"""
    import asyncio

    from playwright.async_api import async_playwright

    from jobpilot.forms import SCAN

    async def go():
        out = {}
        async with async_playwright() as driver:
            browser = await driver.chromium.launch(headless=True)
            context = await browser.new_context(java_script_enabled=True, service_workers="block")
            await context.route("**/*", lambda route: route.abort())
            page = await context.new_page()
            for key, html in run_pages:
                try:
                    no_scripts = re.sub(r"(?is)<script\b.*?</script\s*>", "", html)
                    await page.set_content(no_scripts, wait_until="commit", timeout=8000)
                    data = await asyncio.wait_for(page.evaluate(SCAN), timeout=8)
                    out[key] = {"fields": sorted(str(f.get("label"))[:60] for f in data.get("fields") or []),
                                "controls": sorted(str(c.get("label"))[:60] for c in (data.get("buttons") or data.get("controls") or []))}
                except Exception as exc:  # noqa: BLE001
                    out[key] = f"error: {type(exc).__name__}"
            await browser.close()
        return out

    return asyncio.run(go())


def replay(options):
    """{key: verdict} over the whole corpus, with the code in this checkout."""
    from jobpilot.config import Settings
    from jobpilot.db import Database
    from jobpilot.schemas import Profile
    from jobpilot.service import Service

    index = json.loads((CORPUS / "index.json").read_text())
    row = select("select value from settings where key = 'profile'")
    raw = json.loads("|".join(row[0])) if row else {}
    with tempfile.TemporaryDirectory(prefix="axiom-corpus-") as scratch:
        config = Settings(_env_file=None, database_url="", dbos_database_url="", data_dir=Path(scratch), app_token="corpus", enable_workers=False,
                          allow_private_urls=True, browser_cdp_url="", llm_provider="mimo", mimo_api_key="").prepare()
        service = Service(Database(config.database_url), config)
        profile = Profile.model_validate(raw)
        facts = profile.facts if isinstance(getattr(profile, "facts", None), dict) else {}
        verdicts, htmls, adapters = {}, [], {}
        for run_id, meta in sorted(index.items()):
            folder = CORPUS / "pages" / run_id
            for path in sorted(folder.glob("*.json.gz")):
                if path.name == "answers.json.gz":
                    for question, verdict in answer_rules(json.loads(read(path)), facts).items():
                        verdicts[f"answer|{run_id}|{question}"] = verdict
                    continue
                saved = json.loads(read(path))
                if not isinstance(saved, dict) or "fields" not in saved:
                    continue
                html_path = folder / path.name.replace(".json.gz", ".html.gz")
                html = read(html_path) if html_path.exists() else ""
                adapter = adapters.setdefault(meta["ats"], adapter_for(meta["ats"], saved.get("url", ""), service, profile))
                adapter.job = {**adapter.job, "url": saved.get("url", "")}
                obs = observation(saved, html)
                for rule, verdict in page_rules(adapter, obs).items():
                    verdicts[f"{rule}|{run_id}|{path.name[:-8]}"] = verdict
                if html and options.scan:
                    htmls.append((f"scan|{run_id}|{path.name[:-8]}", html))
        if options.scan:
            step = max(1, len(htmls) // options.scan)  # An even spread, the same pages each time for the same corpus.
            verdicts.update(scan_rules(htmls[::step][: options.scan]))
    return verdicts


def baseline(options):
    verdicts = replay(options)
    path = CORPUS / "baseline.json"
    path.write_text(json.dumps(verdicts))
    path.chmod(0o600)
    counts = collections.Counter(key.split("|")[0] for key in verdicts)
    print(f"baseline of {len(verdicts)} readings: {dict(counts)}")


def compare(options):
    old = json.loads((CORPUS / "baseline.json").read_text())
    new = replay(options)
    index = json.loads((CORPUS / "index.json").read_text())
    changed = collections.defaultdict(list)
    same = 0
    for key, verdict in new.items():
        if key not in old:
            continue
        if old[key] == verdict:
            same += 1
            continue
        rule, run_id, where = key.split("|", 2)
        before, after = json.dumps(old[key])[:90], json.dumps(verdict)[:90]
        changed[rule, before, after].append((run_id, where))
    lines = [f"{same} readings unchanged, {sum(len(v) for v in changed.values())} changed, on {len(index)} real runs"]
    for (rule, before, after), found in sorted(changed.items(), key=lambda item: -len(item[1])):
        ended = collections.Counter(index[run]["state"] for run, _ in found)
        lines.append(f"\n{len(found):5}  {rule}: {before}  ->  {after}\n       runs ended {dict(ended)}")
        for run, where in found[: options.examples]:
            lines.append(f"       {run} {where}  ({index[run]['ats']}, {index[run]['company'][:24]}): {RUNS / run / 'steps'}")
    report = "\n".join(lines)
    print(report)
    path = CORPUS / "last-compare.txt"
    path.write_text(report + "\n")
    path.chmod(0o600)
    return 3 if changed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["collect", "baseline", "compare"])
    parser.add_argument("--per-group", type=int, default=8, help="collect: runs of each platform and outcome")
    parser.add_argument("--scan", type=int, default=0, help="also run scan.js over this many saved pages in a browser")
    parser.add_argument("--examples", type=int, default=4)
    parser.add_argument("--code", help="Read with the rules of this checkout instead of the one this script is in")
    options = parser.parse_args(argv)
    private(CORPUS)
    return {"collect": collect, "baseline": baseline, "compare": compare}[options.command](options) or 0


if __name__ == "__main__":
    raise SystemExit(main())
