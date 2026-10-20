"""Pure per-run timing summary computed from a run's event rows."""

from datetime import datetime


def _at(value):
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _seconds(start, end):
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 2)


def phase_timing(events):
    """Summarise one run's events: a list of (kind, created_at_iso, data dict) in insertion order.

    Returns {} for an empty list. Timing keys are omitted when their endpoints are absent.
    """
    rows = [(kind, _at(created), data if isinstance(data, dict) else {}) for kind, created, data in events]
    if not rows:
        return {}

    first_at = {}
    for kind, at, _ in rows:
        first_at.setdefault(kind, at)

    timing = {}
    finished = first_at.get("finished")
    if finished is not None:
        timing["segment_s"] = _seconds(rows[0][1], finished)

    queue = _seconds(first_at.get("queued"), first_at.get("browser"))
    if queue is not None:
        timing["queue_s"] = queue
    opened = _seconds(first_at.get("browser"), first_at.get("verification"))
    if opened is not None:
        timing["open_to_first_verify_s"] = opened

    model_calls = [data for kind, _, data in rows if kind == "model_call"]
    timing["model_calls"] = len(model_calls)
    timing["model_s"] = round(sum(_num(d.get("elapsed_seconds")) for d in model_calls), 2)
    timing["prompt_tokens"] = int(sum(_num(d.get("prompt_tokens")) for d in model_calls))
    timing["completion_tokens"] = int(sum(_num(d.get("completion_tokens")) for d in model_calls))

    timing["verify_rounds"] = sum(1 for kind, _, _ in rows if kind == "verification")
    timing["notify_errors"] = sum(1 for kind, _, _ in rows if kind == "notification_error")

    # Mail wait runs from the first email_wait to whichever end event comes first after it.
    wait_start = first_at.get("email_wait")
    ends = [at for kind in ("email_verified", "email_matched") if (at := first_at.get(kind)) is not None]
    ends = [at for at in ends if wait_start is not None and at >= wait_start]
    if ends:
        timing["mail_wait_s"] = _seconds(wait_start, min(ends))
    return timing
