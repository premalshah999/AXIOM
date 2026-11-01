"""One vocabulary (owner 2026-11-01: "literally all the numbers on the frontend are high key confusing").

Every backend state is exactly one of five words, a stopped job is "Applying" only while the app will retry it itself, and the
counts that name the words open exactly that many rows. Synthetic data only.
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import insert

from jobpilot import vocabulary
from jobpilot.application_budget import SETTING
from jobpilot.api import pipeline
from jobpilot.db import Job, Run, Setting
from jobpilot.service.states import ACTIVE, TERMINAL

from test_pipeline_counts import at, job, run

pytestmark = pytest.mark.integration

MODEL_TS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "model.ts"


def test_every_backend_state_is_exactly_one_of_the_five_words():
    states = {*ACTIVE, *TERMINAL, *(s for group in pipeline.RUN_GROUPS.values() for s in group)}
    assert states <= set(vocabulary.RUN_WORD), states - set(vocabulary.RUN_WORD)
    # "failed" is never a state's word: only outlook.given_up moves a stopped job there.
    assert set(vocabulary.RUN_WORD.values()) == set(vocabulary.WORDS) - {"failed"}
    assert set(vocabulary.LABELS) == set(vocabulary.WORDS)


def test_the_dashboard_labels_every_state_with_its_word_and_never_an_internal_name():
    text = MODEL_TS.read_text()
    block = text[text.index("export const STATES") : text.index("export const GROUPS")]
    labels = dict(re.findall(r'^  "?([\w:]+)"?: \{\s*label: "([^"]+)"', block, re.M))
    allowed = set(vocabulary.LABELS.values())
    for state, word in vocabulary.RUN_WORD.items():
        assert state in labels, state
        # A state the app retries reads Applying; one it gave up on is its own entry ("gave_up").
        assert labels[state] == vocabulary.LABELS[word], (state, labels[state])
    assert labels["gave_up"] == "Couldn't apply"
    assert {label for state, label in labels.items() if not state.startswith("job:")} <= allowed | {"Waiting to start", "Applying"}
    for banned in ("Repairing", "Recovering", "Started", "Retrying", "Waiting browser", "Timed out", "Not submitted", "Unconfirmed"):
        assert f'label: "{banned}"' not in block


def seed(service):
    jobs = [job(n) for n in range(1, 11)]
    jobs[8]["ats"] = "custom"  # a platform the daily retry does not cover
    jobs[9]["ats"] = "custom"
    runs = [
        run(1, "job00001", "confirmed", created=at(90), finished=at(80)),
        run(2, "job00002", "running", created=at(5)),
        run(3, "job00003", "waiting_answer", created=at(5)),
        run(4, "job00004", "closed", created=at(70), finished=at(60)),
        run(5, "job00005", "waiting_browser", created=at(70), finished=at(60)),  # Greenhouse: the daily retry covers it
        run(6, "job00006", "failed", created=at(70), finished=at(60)),  # nothing retries a failed run
        run(7, "job00007", "submission_unknown", created=at(70), finished=at(60)),
        run(8, "job00008", "already_applied", created=at(70), finished=at(60)),
        run(9, "job00009", "waiting_browser", created=at(70), finished=at(60)),  # custom site, nothing scheduled: given up
        run(10, "job00010", "waiting_browser", created=at(70), finished=at(60)),  # custom site, recovery scheduled: retrying
    ]
    with service.db.exclusive() as s:
        s.execute(insert(Job), jobs)
        s.execute(insert(Run), runs)
        s.add(Setting(key="draft_recovery:job00010", value={"scheduled": True, "source_run_id": "run00010", "status": "scheduled"}))


def test_a_stopped_job_is_applying_only_while_a_retry_is_coming(client, service):
    seed(service)
    words = {r["job"]["id"]: r["word"] for g in vocabulary.WORDS for r in client.get(f"/api/jobs?group={g}&size=50").json()["rows"]}
    assert words == {
        "job00001": "applied",
        "job00002": "applying",
        "job00003": "needs",
        "job00004": "not",
        "job00005": "applying",
        "job00006": "failed",
        "job00007": "needs",
        "job00008": "not",
        "job00009": "failed",
        "job00010": "applying",
    }
    rows = {r["job"]["id"]: r for r in client.get("/api/jobs?group=failed&size=50").json()["rows"]}
    assert rows["job00009"]["sentence"] and "machine" not in rows["job00009"]["sentence"]


def test_an_application_that_used_all_its_attempts_is_couldnt_apply(client, service):
    service.db.set_setting(SETTING, {"max_seconds": 0})  # production runs without the eight-minute cap
    with service.db.exclusive() as s:
        s.execute(insert(Job), [job(1)])
        s.execute(insert(Run), [
            {**run(n, "job00001", "waiting_browser", created=at(100 - n), finished=at(90 - n)), "started_at": at(100 - n)} for n in (1, 2, 3)
        ])
    counts = client.get("/api/snapshot?include_sources=false").json()["counts"]["words"]
    assert counts["failed"] == 1 and counts["applying"] == 0
    # The same job with one start spent still has retries coming.
    with service.db.exclusive() as s:
        s.query(Run).where(Run.id.in_(["run00002", "run00003"])).delete()
    counts = client.get("/api/snapshot?include_sources=false").json()["counts"]["words"]
    assert counts["failed"] == 0 and counts["applying"] == 1


def test_the_words_add_up_and_each_opens_exactly_that_many_rows(client, service):
    seed(service)
    snap = client.get("/api/snapshot?include_sources=false").json()
    words, groups = snap["counts"]["words"], snap["counts"]["groups"]
    assert sum(words.values()) == snap["counts"]["pipeline"] == groups["all"] == 10
    assert words["applied"] == snap["counts"]["confirmed"] == snap["stats"]["confirmed_total"] == groups["done"]
    assert words["applied"] == len(client.get("/api/successful-applications").json()["applications"])
    for word in vocabulary.WORDS:
        page = client.get(f"/api/jobs?group={word}&size=50").json()
        assert page["total"] == len(page["rows"]) == words[word], word
        assert page["counts"]["words"][word] == words[word]


def test_a_job_found_but_not_started_is_in_no_word_and_a_reserved_one_is_not_applying(client, service):
    """The Not applying list held every discovered job (Discovered's Ready and Held jobs) until "found, not started" had no word."""
    with service.db.exclusive() as s:
        s.execute(insert(Job), [job(1), job(2, status="skipped"), job(3, status="closed")])
        s.execute(insert(Run), [run(1, "job00003", "closed", created=at(70), finished=at(60))])
    words = client.get("/api/snapshot?include_sources=false").json()["counts"]["words"]
    assert words == {"applied": 0, "applying": 0, "needs": 0, "failed": 0, "not": 1}
    page = client.get("/api/jobs?group=not&size=50").json()
    assert [r["job"]["id"] for r in page["rows"]] == ["job00003"] and page["total"] == 1
