"""Run states the service groups by."""

ACTIVE = {"queued", "running", "submitting"}
RETRYABLE = {"needs_review", "timed_out", "failed", "waiting_browser", "waiting_email"}
TERMINAL = {
    "paused",
    "confirmed",
    "dry_run_passed",
    "needs_review",
    "timed_out",
    "submission_unknown",
    "failed",
    "cancelled",
    "already_applied",
    "closed",
    "skipped",
    "waiting_answer",
    "waiting_browser",
    # Parked on a durable wait for an emailed code or link; a continuation run finishes the application. Not a failure.
    "waiting_email",
}
