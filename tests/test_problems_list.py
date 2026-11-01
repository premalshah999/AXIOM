"""Applications, What went wrong (owner, 2026-11-01: "why are the latest set of failures not visible"): a stop the app will retry reads "Applying"
everywhere else, so it must show here, with what happens next, as must everything else that ended without a sent application."""

from datetime import UTC, datetime, timedelta

from jobpilot.db import Job, Review, Run
from jobpilot.problems import recent
from jobpilot.vocabulary import plain_sentence


def at(minutes_ago):
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()


def add(session, job_id, runs, status="waiting_browser", ats="greenhouse"):
    session.add(Job(id=job_id, identity=job_id, url=f"https://example.test/{job_id}", title="Engineer", company=job_id.title(), location="", description="", ats=ats,
                    status=status, score=None, reason="", resume_id=None, demo=False, created_at=at(10000)))
    for i, (state, reason, minutes) in enumerate(runs):
        session.add(Run(id=f"{job_id}-{i}", job_id=job_id, state=state, mode="submit", resume_id=None, packet={}, started_at=at(minutes), finished_at=at(minutes), elapsed=1.0,
                        cost=0.0, model_calls=0, reason=reason, receipt={}, created_at=at(minutes + 1)))


def rows_by_run(session, **kw):
    return {r["run_id"]: r for r in recent(session, **kw)["rows"]}


def test_every_attempt_that_sent_nothing_is_listed_newest_first_with_what_happens_next(service):
    with service.db.exclusive() as s:
        add(s, "retrying", [("waiting_browser", "Recovery stopped: No observed action advanced the application; draft saved.", 5)])
        add(s, "fixed", [("waiting_browser", "Recovery stopped: Value not accepted: Location; draft saved.", 30), ("confirmed", "Website confirmation captured", 20)], status="confirmed")
        add(s, "asking", [("waiting_answer", "Required profile information is missing: Are you a collector?", 10)], status="waiting_answer")
        s.add(Review(id="r1", run_id="asking-0", question="Are you a collector?", options=[], key="k", reason="", created_at=at(9)))
        add(s, "looped", [("waiting_browser", "Application already used its initial attempt and two retries", 50), ("waiting_browser", "Application already used its initial attempt and two retries", 40), ("waiting_browser", "Application already used its initial attempt and two retries", 35)])
        add(s, "gone", [("closed", "The posting is closed or no longer available", 15)], status="closed")
        add(s, "replaced", [("cancelled", "Replaced by a newer attempt; the email wait ended", 7), ("confirmed", "Website confirmation captured", 6)], status="confirmed")
        add(s, "oldnews", [("waiting_browser", "Worker interrupted before submission", 60 * 24 * 9)])
    with service.db.session() as s:
        page = recent(s)
        rows = {r["run_id"]: r for r in page["rows"]}
    assert [r["run_id"] for r in page["rows"]][:3] == ["retrying-0", "asking-0", "gone-0"], "newest first"
    assert "replaced-0" not in rows, "a replaced attempt is not a problem; its newer row says what happened"
    assert "oldnews-0" not in rows, "only the window asked for"
    retry = rows["retrying-0"]
    assert retry["what"] == "Nothing AXIOM tried moved the application forward."
    assert retry["next"]["kind"] == "retry" and retry["next"]["sentence"].startswith("Tries again")
    assert rows["fixed-0"]["next"] == {"kind": "done", "sentence": "Nothing. A later attempt was sent.", "at": ""}
    assert rows["asking-0"]["next"]["kind"] == "you" and "Needs you" in rows["asking-0"]["next"]["sentence"]
    assert "“Are you a collector?”" in rows["asking-0"]["what"]
    assert rows["looped-2"]["next"]["kind"] == "stopped" and "stopped trying" in rows["looped-2"]["next"]["sentence"]
    assert rows["looped-0"]["next"]["kind"] == "done" and "newer row" in rows["looped-0"]["next"]["sentence"]
    assert rows["gone-0"]["next"]["kind"] == "done"
    shot = service.config.data_dir / "runs" / "retrying-0"
    shot.mkdir(parents=True)
    (shot / "final.png").write_bytes(b"x")
    with service.db.session() as s:
        rows = rows_by_run(s, runs_dir=service.config.data_dir / "runs")
    assert rows["retrying-0"]["screenshot"] and not rows["gone-0"]["screenshot"]


def test_a_paused_app_says_it_waits_for_resume_and_kind_narrows_the_list(service):
    with service.db.exclusive() as s:
        add(s, "retrying", [("waiting_browser", "Recovery stopped: No observed action advanced the application; draft saved.", 5)])
        add(s, "gone", [("closed", "The posting is closed or no longer available", 15)], status="closed")
    service.db.set_setting("control", {"paused": True})
    with service.db.session() as s:
        rows = rows_by_run(s)
        assert rows["retrying-0"]["next"]["sentence"] == "Tries again after you press Resume."
        narrowed = recent(s, kind="done")
    assert [r["run_id"] for r in narrowed["rows"]] == ["gone-0"] and narrowed["total"] == 1


def test_the_engines_words_become_a_sentence_a_person_reads():
    assert plain_sentence("Recovery stopped: 'What is 10 times 10?' failed 3 results in a row (Required field incomplete: What is 10 times 10?); draft saved.") == "AXIOM could not complete “What is 10 times 10?” on the form."
    assert plain_sentence("The menu of 'If yes, please enter visa type' refused the chosen option as stale or outside the control 3 times") == "The menu for “If yes, please enter visa type” would not take the choice."
    assert plain_sentence("Recovery stopped: Employer field errors stayed on the form after the commit and no acceptance was captured; draft saved.") == "The site showed errors on the form after Submit and did not accept it."
    assert plain_sentence("Recovery stopped: Only an observed dismiss control can close a dialog; draft saved.") == "A pop-up covered the form and AXIOM could not close it."
    long = plain_sentence("The website did not accept known answers: " + "word " * 80)
    assert len(long) < 150 and long.startswith("The site would not take")
