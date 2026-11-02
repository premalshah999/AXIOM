"""Compare the success number with scripts/metrics/success.sql (a read-only cross-check, standard library only). Prints every line
where the two disagree, and "agree" when none does.

  Against the route: save GET /api/success as report.json, run success.sql with its `since` as the cutoff into sql.txt, then
    python3 scripts/metrics/check_success.py report.json sql.txt

  Against a saved copy of the runs, with success_core.py and no database:
    1. dump (axiom psql -XAt -c "select json_build_object(...) from runs ...", the columns DUMP names) to runs.jsonl, and the
       commit events to commits.txt (job|run|created_at|kinds), then run success.sql on the same moment into sql.txt;
    2. python3 scripts/metrics/check_success.py runs.jsonl commits.txt sql.txt <cutoff>
"""

import importlib.util
import json
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "jobpilot" / "api" / "success_core.py"
INTERRUPTED = "Worker interrupted before submission; draft recovery scheduled"
DUMP = "run job ats state created reason type ean res mea web sel mail trusted auth rec blocker"


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
        dict(run=r["run"], job=r["job"], ats=r["ats"], state=r["state"], created=r["created"], blocker=r["blocker"],
             interrupted=(r["reason"] or "") == INTERRUPTED,
             receipt={**{k: r[k] for k in ("type", "ean", "res", "mea", "web", "sel", "mail", "trusted", "auth")}, "pending": r["rec"] == "pending"})
        for r in runs
    ]


def figures(report):
    """The lines success.sql prints, from a report shaped like GET /api/success (`evidence` lines by kind: a kind has one grade)."""
    t = report["overall"]
    out = {
        "funnel|attempted": t["attempted"], "funnel|confirmed": t["confirmed"], "funnel|failed": t["failed"],
        "funnel|eligible": t["eligible"], "funnel|raw_pct": t["raw_pct"], "funnel|filtered_pct": t["final_pct"],
        "funnel|first_try_pct": t["first_try_pct"], "funnel|strict_pct": t["strict_pct"], "funnel|strong_pct": t["strong_pct"],
        "funnel|evidence_strong": t["evidence"]["strong"], "funnel|evidence_medium": t["evidence"]["medium"],
        "funnel|evidence_weak": t["evidence"]["weak"],
    }
    out.update({f"excluded|{k}": v for k, v in t["excluded"].items() if v})
    out.update({f"failures|{k}": v for k, v in t["failures"].items()})
    out.update({f"evidence|{k}": v for k, v in t["evidence"]["kinds"].items()})
    for p in report["platforms"]:
        final = "" if p["final_pct"] is None else f"{p['final_pct']:.1f}"
        out[f"platform|{p['ats']}"] = f"{p['attempted']}|{p['confirmed']}|{p['eligible']}|{final}"
    return out


def sql_figures(lines):
    out = {}
    for line in lines:
        parts = line.rstrip("\n").split("|")
        if len(parts) < 3:
            continue
        if parts[0] == "platform":
            out[f"platform|{parts[1]}"] = "|".join(parts[2:])
        elif parts[0] == "evidence":
            out[f"evidence|{parts[2]}"] = int(parts[3])
        elif parts[-1] == "":
            out["|".join(parts[:-1])] = None
        else:
            out["|".join(parts[:-1])] = float(parts[-1]) if "." in parts[-1] else int(parts[-1])
    return out


def differences(mine, theirs):
    return [(k, mine.get(k), theirs.get(k)) for k in sorted(set(mine) | set(theirs)) if mine.get(k) != theirs.get(k)]


def report_from_dump(core, runs, commits_path, cutoff):
    commits = {}
    for line in open(commits_path):
        job, _run, created, _kinds = line.rstrip("\n").split("|")
        commits[job] = min(commits.get(job, "9"), created)
    jobs = core.classify_jobs(rows_from(runs, cutoff), commits, cutoff)
    by_platform = {}
    for j in jobs:
        by_platform.setdefault(j["ats"], []).append(j)
    return {"overall": core.tally(jobs), "platforms": [{"ats": ats, **core.tally(group)} for ats, group in by_platform.items()]}


def main(argv):
    if len(argv) == 2:
        report = json.load(open(argv[0]))
        sql_path = argv[1]
    else:
        runs, commits_path, sql_path, cutoff = argv
        report = report_from_dump(load_core(), runs, commits_path, cutoff)
    bad = differences(figures(report), sql_figures(open(sql_path)))
    for k, a, b in bad:
        print(f"DIFF {k}: python={a} sql={b}")
    print("agree" if not bad else f"{len(bad)} lines differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
