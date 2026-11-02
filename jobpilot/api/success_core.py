"""The rules of the success number, on plain rows with no database, so one piece of code scores the live runs and a saved copy.

Every attempted job lands in exactly one bucket, named by a code from the run's own fields (its state, its blocker type, the
owner's deleted-job setting, the receipt), never by reading a failure sentence:

- confirmed: some run of the window ended confirmed (an employer's "already applied" page that AXIOM never committed to is not);
- excluded, with the reason: `EXCLUDED` below, one code per job, tested in that order. Only these leave the eligible count;
- failed: everything else, split by "state:blocker" so nothing hides in a total.

eligible = confirmed + failed. Reported side by side (success.py): attempted, raw (confirmed / attempted), filtered (confirmed /
eligible), first try, strict (waiting-on-owner and in-progress jobs counted as misses) and the share of eligible jobs confirmed
on strong evidence. A confirmation is only as good as its receipt: `evidence` ranks it.
"""

from collections import Counter
from math import floor

TARGET = 90
# Why a job is not the engine's to confirm. Order matters: the first that applies is the job's one reason.
EXCLUDED = {
    "already_on_file": "the employer holds an application AXIOM never sent (its own already-applied notice, or a weak receipt with no commit of AXIOM's behind it)",
    "in_progress": "its last run is still running, queued, or was cut short by a restart and will resume",
    "employer_limit": "the employer refuses more applications from this applicant",
    "sensitive_id_required": "it asks for an SSN or government ID, which the owner's rule drops",
    "closed": "the engine ended it as closed: posting gone or restricted, or the employer's application limit reached (its reason is free text, so this bucket is the engine's own word)",
    "cancelled": "the application was cancelled before anything was sent (a cancel that names a blocker is a failure)",
    "paused_by_owner": "the owner paused it",
    "waiting_on_owner": "it waits for an answer or fact only the owner can give",
}
# What the first success.py report called "closed": jobs that ended by a rule, the employer or the owner's decision.
ENDED = ("employer_limit", "sensitive_id_required", "closed", "cancelled", "paused_by_owner")
IN_FLIGHT = {"queued", "running", "submitting", "waiting_email"}
# Weak receipts that speak of an application without proving AXIOM made it. With no commit of AXIOM's on the job they leave it
# "already on file". A bare confirmed run with no receipt at all (never stored today) is only graded weak: it stays counted.
NEEDS_OWN_COMMIT = {"existing_application_view", "final_page_quote", "dashboard_status", "applicant_recorded", "page_text_rejected"}
RANK = {"strong": 0, "medium": 1, "weak": 2}
FALSE = (None, "", "false", "False", "null", "0", 0, False)


def pct(part, whole):
    """A percentage to one decimal, or None with nothing to divide by. Never rounded up to a whole number: 89.6 is not 90."""
    return floor(1000 * part / whole) / 10 if whole else None


def yes(value):
    return value not in FALSE


def evidence(r):
    """(tier, kind) of what proved one confirmed run, from its receipt's own fields (`r`: type, web, ean, res, mea, sel, mail,
    trusted, auth).

    strong: the employer's page said so (and its email, when one matched), or a trusted email alone; medium: the platform
    answered the submission request as created; weak: everything that is a later claim about an application (the employer's
    own record of an application that may not be ours, a page the engine itself rejected, a note by the applicant or the
    repair agent, a final page quoted rather than captured). A receipt type this table does not know is weak."""
    kind_of = r.get("type") or ""
    if yes(r.get("ean")) or yes(r.get("res")) or yes(r.get("mea")) or kind_of == "employer_already_applied":
        return "weak", "existing_application_view"
    if kind_of == "invalid_page_text" or r.get("sel") == "[data-automation-id=similar-jobs-heading]":
        return "weak", "page_text_rejected"
    if kind_of in ("user_reconciled", "owner_submitted", "unsent_reconciled", "reviewed_employer_confirmation_capture"):
        return "weak", "applicant_recorded"
    if kind_of == "final_page_confirmation":
        return "weak", "final_page_quote"
    if kind_of == "employer_dashboard_status":
        return "weak", "dashboard_status"
    mail = yes(r.get("mail")) and r.get("trusted") in (None, "", "true", "True", "1", 1, True) and r.get("auth") in (
        None, "", "true", "True", "1", 1, True
    )
    if kind_of == "explicit_confirmation_page" and yes(r.get("web")):
        return "strong", "page_and_email" if mail else "page_only"
    if kind_of == "application_created":
        return ("strong", "platform_and_email") if mail else ("medium", "platform_response")
    if not kind_of and yes(r.get("mail")):
        return ("strong", "email_only") if mail else ("weak", "email_untrusted_sender")
    return "weak", "unrecognised"


def classify_jobs(rows, committed=None, since=""):
    """Jobs (one dict each, with `outcome`) from run rows ordered by job, created_at, id, for the jobs first tried since `since`.

    The cohort is the jobs first attempted in the window, with every run they have had since. A job first tried a week ago and
    confirmed on a retry today belongs to last week's cohort: counted by its last-seven-days runs alone, its retry reads as a
    first try (205 of the week's 1,822 jobs, 136 of them confirmed).

    A row: job, ats, state, created, interrupted (a stop of the app, which sent nothing), blocker (the run's blocker type),
    receipt (the fields `evidence` reads). `committed`: {job: created_at of its earliest run that began a commit}. An
    weak receipt that only speaks of an application (an employer's already-applied page, a quoted final page, the applicant's own note) confirms a job only when
    AXIOM's own commit came first; otherwise someone else's application stands and the job is "already on file". A job the owner deleted after a failed
    run stays a failure: deleting a stuck job (94 of the week's 1,822) does not make the engine's miss the owner's."""
    committed = committed or {}
    jobs = {}
    for r in rows:
        j = jobs.setdefault(r["job"], {"id": r["job"], "ats": r["ats"], "ok": False, "dup": False, "first": None, "ev": None, "started": None})
        state = r["state"]
        if state == "confirmed":
            tier, kind = evidence(r.get("receipt") or {})
            if kind in NEEDS_OWN_COMMIT and not (r["job"] in committed and committed[r["job"]] <= r["created"]):
                state = "already_applied"
            elif j["ev"] is None or RANK[tier] < RANK[j["ev"][0]]:
                j["ev"] = (tier, kind)
        j["ok"] |= state == "confirmed"
        j["dup"] |= state == "already_applied"
        if j["first"] is None and state != "dry_run_passed" and not r["interrupted"]:
            j["first"], j["started"] = state, r["created"]
        j["last"], j["interrupted"], j["blocker"] = state, bool(r["interrupted"]), r.get("blocker") or ""
        j["last_at"] = r["created"]
    out = []
    for j in jobs.values():
        # A job whose only runs were dry runs was never tried; one only interrupted has its retry coming, and counts as tried.
        if j["first"] is None and j["last"] != "dry_run_passed":
            j["first"], j["started"] = j["last"], j["last_at"]
        if j["first"] is not None and j["started"] >= since:
            j["outcome"], j["failure"] = outcome(j)
            out.append(j)
    return out


def outcome(j):
    """(outcome, failure): one of "confirmed", "failed" or an EXCLUDED code; for a failed job, "state:blocker"."""
    last, blocker = j["last"], j["blocker"]
    if j["ok"]:
        return "confirmed", None
    if j["dup"]:
        return "already_on_file", None
    if last in IN_FLIGHT or j["interrupted"]:
        return "in_progress", None
    if blocker == "employer_limit":
        return "employer_limit", None
    if blocker == "sensitive_id":
        return "sensitive_id_required", None
    if last == "closed":
        return "closed", None
    if last == "cancelled" and not blocker:
        return "cancelled", None
    if last == "paused":
        return "paused_by_owner", None
    if last == "waiting_answer" or blocker == "missing_fact":
        return "waiting_on_owner", None
    return "failed", f"{last}:{blocker or 'unclassified'}"


def tally(jobs):
    n = len(jobs)
    by = Counter(j["outcome"] for j in jobs)
    confirmed, failed = by["confirmed"], by["failed"]
    eligible = confirmed + failed
    first = sum(j["first"] == "confirmed" for j in jobs)
    tiers = Counter(j["ev"][0] for j in jobs if j["ok"])
    ended = sum(by[c] for c in ENDED)
    return {
        # The first report's keys, same meaning where the meaning was sound.
        "tried": n,
        "closed": ended,
        "already_applied": by["already_on_file"],
        "owner_only": by["waiting_on_owner"],
        "eligible": eligible,
        "confirmed": confirmed,
        "final_pct": pct(confirmed, eligible),
        "first_try_pct": pct(first, eligible),
        "meets_target": eligible > 0 and 100 * confirmed >= TARGET * eligible,
        # The figures side by side.
        "attempted": n,
        "raw_pct": pct(confirmed, n),
        "strict_pct": pct(confirmed, eligible + by["waiting_on_owner"] + by["in_progress"]),
        "strong_pct": pct(tiers["strong"], eligible),
        "first_try": first,
        "failed": failed,
        "excluded": {code: by[code] for code in EXCLUDED},
        "failures": dict(Counter(j["failure"] for j in jobs if j["outcome"] == "failed").most_common()),
        "evidence": {
            "strong": tiers["strong"],
            "medium": tiers["medium"],
            "weak": tiers["weak"],
            "kinds": dict(Counter(j["ev"][1] for j in jobs if j["ok"]).most_common()),
        },
    }
