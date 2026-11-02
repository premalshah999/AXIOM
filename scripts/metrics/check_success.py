"""Compare success_core.py with scripts/metrics/success.sql on one saved copy of the runs (a read-only cross-check).

  1. dump (axiom psql -XAt -c "select json_build_object(...) from runs ...", the columns DUMP names) to runs.jsonl, and the
     commit events to commits.txt (job|run|created_at|kinds), then run success.sql on the same moment into sql.txt;
  2. python3 scripts/metrics/check_success.py runs.jsonl commits.txt sql.txt <cutoff>

Prints every line where the two disagree, and "agree" when none does. Standard library only."""

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "jobpilot" / "api" / "success_core.py"
INTERRUPTED = "Worker interrupted before submission; draft recovery scheduled"
DUMP = "run job ats state created reason type ean res mea web sel mail trusted auth blocker"


def load_core():
    spec = importlib.util.spec_from_file_location("success_core", CORE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows_from(path, cutoff):
    runs = [json.loads(line) for line in open(path)]
    inside = {r["job"] for r in runs if r["created"] >= cutoff}
    runs = sorted((r for r in runs if r["job"] in inside), key=lambda r: (r["job"], r["created"], r["run"]))
    return [
        dict(job=r["job"], ats=r["ats"], state=r["state"], created=r["created"], blocker=r["blocker"],
             interrupted=(r["reason"] or "") == INTERRUPTED,
             receipt={k: r[k] for k in ("type", "ean", "res", "mea", "web", "sel", "mail", "trusted", "auth")})
        for r in runs
    ]


def python_figures(core, rows, commits, cutoff):
    jobs = core.classify_jobs(rows, commits, cutoff)
    t = core.tally(jobs)
    out = {
        "funnel|attempted": t["attempted"], "funnel|confirmed": t["confirmed"], "funnel|failed": t["failed"],
        "funnel|eligible": t["eligible"], "funnel|raw_pct": t["raw_pct"], "funnel|filtered_pct": t["final_pct"],
        "funnel|first_try_pct": t["first_try_pct"], "funnel|strict_pct": t["strict_pct"], "funnel|strong_pct": t["strong_pct"],
        "funnel|evidence_strong": t["evidence"]["strong"], "funnel|evidence_medium": t["evidence"]["medium"],
        "funnel|evidence_weak": t["evidence"]["weak"],
    }
    out.update({f"excluded|{k}": v for k, v in t["excluded"].items() if v})
    out.update({f"failures|{k}": v for k, v in t["failures"].items()})
    grades = Counter(f"{j['ev'][0]}|{j['ev'][1]}" for j in jobs if j["outcome"] == "confirmed")
    out.update({f"evidence|{k}": v for k, v in grades.items()})
    return out


def sql_figures(path):
    out = {}
    for line in open(path):
        parts = line.rstrip("\n").split("|")
        if parts[0] == "platform":
            continue
        out["|".join(parts[:-1])] = float(parts[-1]) if "." in parts[-1] else int(parts[-1])
    return out


def main(runs, commits_path, sql_path, cutoff):
    core = load_core()
    commits = {}
    for line in open(commits_path):
        job, _run, created, _kinds = line.rstrip("\n").split("|")
        commits[job] = min(commits.get(job, "9"), created)
    mine = python_figures(core, rows_from(runs, cutoff), commits, cutoff)
    theirs = sql_figures(sql_path)
    bad = [(k, mine.get(k), theirs.get(k)) for k in sorted(set(mine) | set(theirs)) if mine.get(k) != theirs.get(k)]
    for k, a, b in bad:
        print(f"DIFF {k}: python={a} sql={b}")
    print("agree" if not bad else f"{len(bad)} lines differ")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
