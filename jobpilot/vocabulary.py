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


# The engine's notes about its own browser, which name source files and internal steps. Runs keep them; a person reads these.
_BROWSER_TROUBLE = (
    (re.compile(r"^(?:Browser control failed|Browser observation stopped)", re.I), "The browser stopped responding on this page."),
    (re.compile(r"^(?:Open the ApplyPilot Chrome|The local Chrome debugging|Chrome worker tab)", re.I), "AXIOM's browser wasn't ready, so this application didn't start."),
)
# The engine's own words for its own trouble. "Browser control failed at browser.py:441" reached the first-time user's screen as
# the reason; so did "Worker error: KeyError" (read over every production reason, 2026-11-01: only these match).
_ENGINE = (
    (re.compile(r"\.py:\d+"), "AXIOM got stuck on this site's pages."),
    (re.compile(r"^Worker interrupted", re.I), "This attempt was interrupted before anything was sent."),
    (re.compile(r"^Worker error\b", re.I), "AXIOM hit a problem of its own."),
    (re.compile(r"debugging endpoint|automation connection|Chromium (?:missing|did not|exited)", re.I), "AXIOM couldn't use its browser."),
)


# The stop reasons the engine writes most, as a person would say them (counted over production, 2026-11-01). `{q}` is the question the
# reason names, cut short: "failed 3 results in a row (Value not accepted: Location)" says nothing to someone who has not read the code.
_STOPS = (
    (re.compile(r"^'(?P<q>.+?)' failed \d+ results in a row\b", re.I | re.S), "AXIOM could not complete \u201c{q}\u201d on the form."),
    (re.compile(r"^Value not accepted: (?P<q>[^;]+)", re.I), "The site would not take the answer to \u201c{q}\u201d."),
    (re.compile(r"^Required field incomplete: (?P<q>[^;]+)", re.I), "\u201c{q}\u201d stayed empty on the form."),
    (re.compile(r"^The menu of '(?P<q>.+?)' refused", re.I | re.S), "The menu for \u201c{q}\u201d would not take the choice."),
    (re.compile(r"^The website did not accept known answers: (?P<q>.+)", re.I | re.S), "The site would not take the saved answer to \u201c{q}\u201d."),
    (re.compile(r"^Required (?:profile information|application facts) (?:is|are) missing: (?P<q>.+)", re.I | re.S), "The form asks \u201c{q}\u201d and your profile has no answer."),
    (re.compile(r"^No observed action advanced the application", re.I), "Nothing AXIOM tried moved the application forward."),
    (re.compile(r"^Employer field errors stayed on the form", re.I), "The site showed errors on the form after Submit and did not accept it."),
    (re.compile(r"^Only an observed dismiss control can close a dialog", re.I), "A pop-up covered the form and AXIOM could not close it."),
    (re.compile(r"^Answer evidence did not match the question scope", re.I), "AXIOM could not back an answer with your saved facts, so it stopped before sending."),
    (re.compile(r"^The applicant's saved application source is not offered", re.I), "This form does not offer the way you said you find jobs. Add one of its choices in your profile's answers, or allow any other plain channel there."),
    (re.compile(r"^No final submit control is on this page", re.I), "AXIOM could not find the Submit button on this page."),
    (re.compile(r"^Application already used its initial attempt and two retries", re.I), "It has used all three of its attempts."),
    (re.compile(r"^Employer sign-in must complete", re.I), "The employer's sign-in did not finish."),
    (re.compile(r"^(?:2Captcha|Captcha)\b.*unconfirmed", re.I | re.S), "The site's picture check could not be solved, so AXIOM can't tell whether it was sent."),
    (re.compile(r"^Unrecognized page", re.I), "AXIOM did not recognise this page."),
)
_QUESTION_SHOWN = 90


def _short_question(text):
    text = " ".join(str(text).split()).rstrip(" ;:.")
    # A question number and the employer's aside in brackets only make it longer.
    text = re.sub(r"^\d+[.)]\s*", "", text)
    if len(text) <= _QUESTION_SHOWN:
        return text
    return text[: _QUESTION_SHOWN - 1].rsplit(" ", 1)[0] + "\u2026"


def plain_sentence(reason, limit=150):
    """One plain sentence from a stored reason: the first clause, cut at a colon-led detail, never over `limit`."""
    text = _LABEL.sub("", (reason or "").strip())
    for note, sentence in _BROWSER_TROUBLE:
        if note.search(text):
            return sentence
    for engine, words in _ENGINE:
        if engine.search(text):
            return words
    for stop, words in _STOPS:
        if found := stop.search(text):
            groups = found.groupdict()
            return words.format(q=_short_question(groups["q"])) if "q" in groups else words
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
