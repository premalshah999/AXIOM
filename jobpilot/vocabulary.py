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
_LABEL = re.compile(r"^(?:Recovery stopped|Not submitted|Owner approved|Applicant approved[^:]*|Repair agent):\s*", re.I)

# What only the code's authors can read: a validator's report and its link, a stack trace, an exception's name, a source file, a
# snake_case name, name=value, a field's internal id, a browser driver's call. Scanned over every production reason, event and failure
# report on 2026-11-01: a person read "Required field f0-ib9l29f2-jp30", "resume_attached=false", "Locator.set_input_files" and
# "browser.py:346:run". frontend/src/format.ts DEVELOPER says the same.
DEVELOPER = re.compile(
    r"errors\.pydantic\.dev|(?i:for further information visit|\bvalidation errors? for\b|\binput should be\b|traceback|stack ?trace)|"
    r"\[type=\w+|\b\w+\.py\b|\b(?:[A-Z][a-z]+)+(?:Error|Exception)\b|\bException\b|\bNoneType\b|(?i:\bpython -m\b|\bjobpilot\b|\bapplypilot\b)|"
    r"\b(?:Locator|Page|BrowserContext|ElementHandle|Frame)\.[a-z_]+|\bpage\.\w+\(|\bTimeout \d+ ?ms\b|\b\d+ ?ms exceeded\b|"
    r"\bf\d-[a-z0-9]+(?:-[a-z0-9]+)*\b|\b[a-z]+(?:_[a-z0-9]+)+\b|\b[a-z]\w*=\S+|\bdata-[a-z-]+\b|\bCDP\b|\bDOM\b|"
    # The repair agent's and engine's notes to themselves.
    r"\b(?:technical|engineering)(?: submission)? blocker\b|\bblocker\b|\bobservation\b|\bfill (?:reports?|results?|tool)\b|"
    r"\bpage-wide fill\b|\brepair instruction\b|\bowned-page\b|\brevision \d+\b|\bcontrol failure\b|\bquality repair\b|"
    r"\[(?:URL|email|number|secret) omitted\]|\brecovery budget\b|\bexecution budget\b|\bdraft restarts?\b|\bworker host\b"
)
# An internal id in brackets, or on its own, before the sentence is read: what is left is often a fine sentence.
_INTERNAL_ID = re.compile(r"\s*\((?:[^()]*\bf\d-[a-z0-9]+-[a-z0-9]+[^()]*)\)|\s*\bf\d-[a-z0-9]+-[a-z0-9]+\b", re.I)
_BRACKETED_LINK = re.compile(r"\s*\((?:[^()]*https?://[^()]*|\[URL omitted\])\)|\s*(?:at |on )?https?://\S+|\s*(?:at |on )?\[URL omitted\]", re.I)
_OWNER_RULE = re.compile(r";?\s*(?:the job was removed under the owner rule|jobs that ask are dropped)?\s*\(?\bowner rule\)?\s*$", re.I)
STUCK = "AXIOM got stuck on this site's pages."
PROBLEM = "Something went wrong on AXIOM's side. Try again."
# What such a note was about, when it says.
_DEVELOPER_TOPICS = (
    (re.compile(r"auth_required|employer sign-in|sign-in must", re.I), "The employer's sign-in did not finish."),
    (re.compile(r"resume_attached|resume attachment|resume file upload", re.I), "The site would not take the resume."),
    (re.compile(r"not_found=|page not found|\b404\b", re.I), "The posting is no longer available."),
)
_FRIENDLIER = ((re.compile(r"\bcomboboxe?s\b", re.I), "menus"), (re.compile(r"\bcombobox\b", re.I), "menu"))


def _without_internals(text):
    """A reason without what only the code reads: internal ids and links in brackets, and the note about an internal rule."""
    text = _BRACKETED_LINK.sub("", _INTERNAL_ID.sub("", text))
    text = _OWNER_RULE.sub("", text)
    for word, plain in _FRIENDLIER:
        text = word.sub(plain, text)
    return text.strip()


def is_developer_text(text):
    return bool(DEVELOPER.search(str(text or "")))


# A validator's report as text, which is what `HTTPException(422, str(exc))` in a route hands on: its first field, its words and its type.
_VALIDATOR_REPORT = re.compile(r"validation errors? for \S+\s+(?P<field>\S+)\s+(?P<msg>.+?)\s*\[type=(?P<type>\w+)", re.I | re.S)


def plain_error(text):
    """An API error's words: a sentence the app wrote stands, and anything only the code's authors can read becomes a plain one."""
    text = str(text or "")
    if found := _VALIDATOR_REPORT.search(text):
        return plain_validation([{"loc": found["field"].split("."), "msg": found["msg"], "type": found["type"]}])
    text = " ".join(text.split())
    return PROBLEM if not text or is_developer_text(text) else text


_FIELD_WORDS = {
    "missing": "is required",
    "string_too_short": "is too short",
    "string_too_long": "is too long",
    "too_short": "needs more entries",
    "too_long": "has too many entries",
    "int_parsing": "must be a whole number",
    "int_type": "must be a whole number",
    "float_parsing": "must be a number",
    "float_type": "must be a number",
    "bool_parsing": "must be yes or no",
    "bool_type": "must be yes or no",
    "date_parsing": "must be a date",
    "greater_than": "is too small",
    "greater_than_equal": "is too small",
    "less_than": "is too large",
    "less_than_equal": "is too large",
    "url_parsing": "must be a web address",
    "url_scheme": "must be a web address",
}


def plain_validation(errors):
    """What a validator refused, as a person would say it. `errors` is pydantic's list of {type, loc, msg}; the first says enough."""
    for error in errors or []:
        message = str(error.get("msg") or "")
        if str(error.get("type") or "") == "value_error":
            # The app's own sentence, which pydantic prefixes.
            said = re.sub(r"^Value error,\s*", "", message)
            if said and not is_developer_text(said):
                return said if said[-1] in ".!?" else said + "."
        names = [str(part) for part in error.get("loc") or [] if isinstance(part, str) and part not in {"body", "query", "path"}]
        name = (names[-1] if names else "").replace("_", " ").strip()
        said = _FIELD_WORDS.get(str(error.get("type") or ""), "isn't valid")
        return f"{name[:1].upper()}{name[1:]} {said}." if name else f"That {said}."
    return "That isn't valid."


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
    (re.compile(r"^No final submit control is on this page", re.I), "AXIOM could not find the Submit button on this page."),
    (re.compile(r"^Application already used its initial attempt and two retries", re.I), "It has used all three of its attempts."),
    (re.compile(r"^Employer sign-in must complete", re.I), "The employer's sign-in did not finish."),
    (re.compile(r"^(?:2Captcha|Captcha)\b.*unconfirmed", re.I | re.S), "The site's picture check could not be solved, so AXIOM can't tell whether it was sent."),
    (re.compile(r"^Unrecognized page", re.I), "AXIOM did not recognise this page."),
    (re.compile(r"^The browser did not advance within its recovery budget", re.I), "AXIOM could not get past this page in the time it allows."),
    (re.compile(r"hCaptcha token|twin browser|captcha token", re.I), "The site's picture check could not be solved."),
    (re.compile(r"^Workday session/server error", re.I), "The employer's site kept returning errors."),
    (re.compile(r"exceeded the \d+-second execution budget", re.I), "This attempt ran out of time."),
    (re.compile(r"^The worker host was overloaded", re.I), "This Mac was too busy, and the attempt ran out of time before anything was sent."),
    (re.compile(r"^Owner deleted", re.I), "You removed this job."),
    (re.compile(r"^The selected model provider is unavailable", re.I), "The AI service didn't answer."),
    (re.compile(r"^Model budget is exhausted", re.I), "Today's AI budget is used up."),
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
    original = (reason or "").strip()
    text = _LABEL.sub("", original)
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
    text = re.split(r"(?<=[.!?])\s|;\s|\s\|\s", _without_internals(text), maxsplit=1)[0].strip()
    if not text:
        return ""
    if is_developer_text(text):
        for topic, words in _DEVELOPER_TOPICS:
            if topic.search(original):
                return words
        return STUCK
    text = text[0].upper() + text[1:]
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text if text[-1] in ".!?…" else text + "."


# The job page's Activity: what happened, as a person follows it. The engine's own diary (model calls, timings, read-backs, its notes to
# itself) stays in the run's record and the download. A kind with None keeps its own sentence, if that sentence is plain.
ACTIVITY = {
    "queued": "Queued to start.",
    "form_page": "Filled in a page of the application.",
    "new_tab": "The application opened in a new tab.",
    "resume_replaced": "Attached the resume.",
    "document_upload": None,
    "submitting": "Pressed Submit.",
    "email_wait": None,
    "email_wait_ended": None,
    "email_matched": "The employer's email arrived.",
    "email_verified": "Entered the code from the employer's email.",
    "email_receipt": "The employer's confirmation email arrived.",
    "reconciled": "You recorded how this application ended.",
    "finished": None,
    "duplicate_posting": None,
    "question_revalidated": None,
    "automatic_recovery": "Started again from the saved draft after a temporary problem.",
    "answer_reused": None,
    "answer_inferred": None,
    "owner_values": None,
    "steer": "Opening the application for you to finish by hand.",
    "steer_ready": "The application is open for you in Chrome.",
    "steer_back": None,
    "fresh_try": None,
    "resume_refused": None,
    "job_control": None,
}
_OWNER_CONTROL = {"pause": "You paused this job.", "resume": "You asked to apply again.", "delete": "You removed this job."}


def activity(events):
    """The events a person reads, oldest first, each in plain words. Each event is a dict from db.record."""
    shown = []
    for event in events:
        kind = event.get("kind")
        if kind not in ACTIVITY:
            continue
        if kind == "job_control":
            said = _OWNER_CONTROL.get(str((event.get("data") or {}).get("action"))) if not (event.get("data") or {}).get("actor") else ""
        else:
            said = ACTIVITY[kind] or plain_sentence(event.get("message"), limit=240)
        if said and not is_developer_text(said):
            shown.append({"id": event.get("id"), "kind": kind, "created_at": event.get("created_at"), "message": said})
    return shown


def plain_report(report, reason=""):
    """A failure report as the job page shows it. Reports hold the engine's reason and, from before 2026-11-01, a model's diagnosis naming
    source files and shell commands. Its evidence stays in the record, not on the screen; a summary that only repeats the reason is empty."""
    if not report:
        return None
    cause = str(report.get("likely_cause") or "")
    summary = plain_sentence(report.get("summary"))
    return {
        "summary": "" if summary in {STUCK, plain_sentence(reason)} else summary,
        "likely_cause": "" if is_developer_text(cause) or "does not establish a more specific cause" in cause else cause,
        "next_steps": [str(step) for step in report.get("next_steps") or [] if not is_developer_text(step) and "browser trace" not in str(step)],
    }


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
