"""The five words every screen uses for a job, and the one place a state becomes one of them (owner 2026-11-01: "literally all
the numbers on the frontend are high key confusing").

  applied   the employer has it (api/pipeline.confirmed_jobs)
  applying  running now, queued, or stopped and the app will retry it on its own
  needs     only the user can answer it
  failed    "Couldn't apply": the app has given up (outlook.given_up), with one sentence saying why
  not       "Not applying": closed, removed, not eligible, already applied elsewhere, paused, or waiting for approval

Internal state names (waiting_browser, timed_out, submission_unknown ...) never reach a screen: the dashboard shows LABELS and the
sentence from `describe`. frontend/src/model.ts mirrors RUN_WORD; tests/test_vocabulary.py keeps them in step.
"""

import re

WORDS = ("applied", "applying", "needs", "failed", "not")
LABELS = {
    "applied": "Applied",
    "applying": "Applying",
    "needs": "Needs you",
    "failed": "Couldn't apply",
    "not": "Not applying",
}
# Every run state, exactly one word. A stop the app will retry reads "applying"; outlook.given_up moves the ones it has given up
# on to "failed". "confirmed" is only the ledger's settled confirmations (pipeline.confirmed_jobs); one still being reconciled
# reads "needs".
RUN_WORD = {
    "queued": "applying",
    "running": "applying",
    "submitting": "applying",
    "waiting_email": "applying",
    "waiting_browser": "applying",
    "timed_out": "applying",
    "failed": "applying",
    "waiting_answer": "needs",
    "needs_review": "needs",
    "submission_unknown": "needs",
    "confirmed": "applied",
    "already_applied": "not",
    "dry_run_passed": "not",
    "cancelled": "not",
    "skipped": "not",
    "closed": "not",
    "paused": "not",
}
# Stops that the app may retry, and so give up on (outlook.given_up).
RETRIED_STATES = ("waiting_browser", "timed_out", "failed")
# A job with no run: found, not started. "to apply" is the Discovered list, not a state; these are the ones that are not.
JOB_WORD = {"closed": "not", "skipped": "not", "paused": "not", "archived": "not"}

SENTENCES = {
    "queued": "Waiting for a free worker.",
    "running": "Filling in the application now.",
    "submitting": "Sending it now.",
    "waiting_email": "Waiting for the employer's emailed code or link.",
    "waiting_answer": "The form asks something only you know.",
    "needs_review": "The form asks something only you know.",
    "submission_unknown": "Sent, but no receipt was captured. Check your email, or confirm it yourself.",
    "confirmed": "The employer has your application.",
    "already_applied": "The employer already has an application from you.",
    "dry_run_passed": "A practice run. Nothing was sent.",
    "cancelled": "Stopped before anything was sent.",
    "skipped": "Skipped. Nothing was sent.",
    "closed": "The posting is closed or no longer available.",
    "paused": "You took it out of the queue. It stays out until you put it back.",
    "reserved": "A reserved employer. It applies only after you approve it.",
    "retry": "It stopped on a problem and will try again on its own.",
    "gave_up": "The app has stopped trying.",
}

# The engine's own words become a sentence: its first clause, without a leading label or a trailing detail.
_LABEL = re.compile(r"^(?:Recovery stopped|Not submitted|Owner approved|Applicant approved[^:]*):\s*", re.I)


def plain_sentence(reason, limit=150):
    """One plain sentence from a stored reason: the first clause, cut at a colon-led detail, never over `limit`."""
    text = _LABEL.sub("", (reason or "").strip())
    text = re.split(r"(?<=[.!?])\s|;\s|\s\|\s", text, maxsplit=1)[0].strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text if text[-1] in ".!?…" else text + "."


def describe(word, state, reason="", *, request_reason="", reserved=False, paused=False):
    """The row's one sentence for what is happening, from its word and state."""
    if reserved:
        return SENTENCES["reserved"]
    if paused:
        return SENTENCES["paused"]
    if state == "pending:waiting":
        return plain_sentence(request_reason) or "Waiting to start."
    if word == "failed":
        return plain_sentence(reason) or SENTENCES["gave_up"]
    if word == "applying" and state in RETRIED_STATES:
        return SENTENCES["retry"]
    if word == "not" and state == "closed":
        return plain_sentence(reason) or SENTENCES["closed"]
    return SENTENCES.get(state, plain_sentence(reason))
