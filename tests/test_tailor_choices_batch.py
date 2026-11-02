"""A list shows each row's tailoring choice from one request for the page, not one per row (the owner saw lag on the Job board and review batch, 2026-11-01)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jobpilot.db import Job
from jobpilot.features.resumes.tables import RsTailorChoice

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="needs node (22.18+ reads TypeScript)")


def run_node(script: str) -> dict:
    """tailorbatch.ts has no imports, so Node runs it as it is with a counting stand-in for the request."""
    out = subprocess.run(["node", "--input-type=module", "-e", script.replace("BATCH", (SRC / "tailorbatch.ts").as_uri())], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-800:]
    return json.loads(out.stdout.strip().splitlines()[-1])


@needs_node
def test_a_page_of_25_rows_makes_one_state_request():
    got = run_node("""
import { loadChoices } from "BATCH";
const calls = [];
const ids = Array.from({ length: 25 }, (_, i) => "job-" + i);
const r = await loadChoices(ids, async (path) => { calls.push(path); return { choices: { "job-3": { mode: "normal", state: "review" } }, modes: [{ id: "normal" }] }; });
console.log(JSON.stringify({ calls: calls.length, asked: decodeURIComponent(calls[0]).split("=")[1].split(",").length, state: r.choices["job-3"].state, modes: r.modes.length }));
""")
    assert got == {"calls": 1, "asked": 25, "state": "review", "modes": 1}


@needs_node
def test_only_a_page_past_the_servers_limit_splits_and_a_repeated_id_is_asked_once():
    got = run_node("""
import { loadChoices, PER_REQUEST } from "BATCH";
const calls = [];
const ids = Array.from({ length: PER_REQUEST + 1 }, (_, i) => "job-" + i).concat(["job-0"]);
await loadChoices(ids, async (path) => { calls.push(path.split("=")[1].split(",").length); return { choices: {}, modes: [] }; });
console.log(JSON.stringify({ calls }));
""")
    assert got == {"calls": [200, 1]}


@needs_node
def test_a_listed_row_never_asks_for_itself_and_a_row_alone_does():
    got = run_node("""
import { rowFetchesItself } from "BATCH";
console.log(JSON.stringify({ listed_unloaded: rowFetchesItself(true, null, undefined), listed_loaded: rowFetchesItself(true, null, []), alone: rowFetchesItself(false, undefined, undefined), alone_given: rowFetchesItself(false, null, []) }));
""")
    assert got == {"listed_unloaded": False, "listed_loaded": False, "alone": True, "alone_given": False}


@needs_node
def test_a_row_changing_updates_its_own_entry_and_the_rest_stay():
    got = run_node("""
import { mergeChoices } from "BATCH";
const was = { a: { mode: "normal", state: "preparing" }, b: { mode: "normal", state: "approved" } };
const one = mergeChoices(was, ["a"], { a: { mode: "normal", state: "review" } });
const gone = mergeChoices(was, ["a"], {});
console.log(JSON.stringify({ a: one.a.state, b: one.b.state, gone: Object.keys(gone), untouched: was.a.state }));
""")
    assert got == {"a": "review", "b": "approved", "gone": ["b"], "untouched": "preparing"}


def test_both_lists_hand_their_rows_to_one_provider_and_the_row_control_is_listed():
    board, review, choice = ((SRC / n).read_text() for n in ("jobboard.tsx", "review.tsx", "applychoice.tsx"))
    for page in (board, review):
        assert re.search(r"<ApplyChoices ids=\{shown\}>\s*<Table", page) and "</ApplyChoices>" in page
    assert "<TailorChoice jobId={jobId} compact listed" in choice
    # The only request a list makes for state is the provider's; the row component never calls the endpoint itself in list mode.
    assert "/tailor/choices" not in choice


def test_the_endpoint_answers_25_rows_in_one_call(client, service):
    ids = [f"j{i}" for i in range(25)]
    with service.db.exclusive() as s:
        for i in ids:
            s.add(Job(id=i, identity=i, url="https://jobs.example.test/" + i, title="Data Engineer", company="ExampleSoft", status="ready", score=90, description="x"))
        for i in ids[:10]:
            s.add(RsTailorChoice(job_id=i, mode="normal", state="review"))
    answer = client.get("/api/v2/resumes/tailor/choices?job_ids=" + ",".join(ids)).json()
    assert set(answer["choices"]) == set(ids[:10])
    assert answer["choices"]["j0"] == {"mode": "normal", "state": "review"}
    assert answer["modes"]
