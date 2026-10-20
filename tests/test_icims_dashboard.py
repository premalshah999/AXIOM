"""iCIMS candidate dashboard rows (Eide Bailly 2026-10-19 showed "Incomplete" for an application that was not sent).

A row is a receipt only when its status says the application was received. The row text also carries the job title, and
"submitted" occurs inside "not submitted" and "unsubmitted", so the first reading (any of those words anywhere in the
row) confirmed an incomplete application, or marked it already applied and ended it for good.
"""

from collections import Counter
from types import SimpleNamespace

import pytest

from jobpilot.adapters.icims import ICIMS, dashboard_status

TITLE = "Junior Data Analyst"


@pytest.mark.parametrize(
    ("row", "title", "status"),
    [
        (f"2026-20717 {TITLE} Received Submission", TITLE, "Received Submission"),
        (f"2026-20717 {TITLE} Under Review", TITLE, "Under Review"),
        (f"2026-20717 {TITLE} Submitted 10/19/2026", TITLE, "Submitted"),
        (f"2026-20717 {TITLE} Incomplete", TITLE, None),
        (f"2026-20717 {TITLE} Incomplete - application not submitted", TITLE, None),
        (f"2026-20717 {TITLE} Not Submitted", TITLE, None),
        (f"2026-20717 {TITLE} Unsubmitted draft", TITLE, None),
        # Action links sit on every row, whatever its status.
        (f"2026-20717 {TITLE} Received Submission Continue Application Withdraw", TITLE, "Received Submission"),
        # The title is not a status: a posting named after the word used to confirm its own incomplete row.
        ("2026-20717 Submitted Claims Analyst Incomplete", "Submitted Claims Analyst", None),
        ("2026-20717 Submitted Claims Analyst Received Submission", "Submitted Claims Analyst", "Received Submission"),
        ("", TITLE, None),
        (None, TITLE, None),
    ],
)
def test_a_dashboard_row_is_a_receipt_only_when_its_status_says_received(row, title, status):
    assert dashboard_status(row, title) == status


class Dashboard(ICIMS):
    """on_dashboard with the page replaced by one scripted dashboard row."""

    def __init__(self, row, *, title=TITLE, submits=0):
        async def back():
            self.returned = True

        async def page_title():
            return "Dashboard"

        self.returned = False
        self.row = row
        self.job = {"url": "https://careers-example.icims.com/jobs/20717/junior-data-analyst/job", "title": title}
        self.step_submits, self.committed, self.attempts = submits, False, Counter()
        form = SimpleNamespace(page=SimpleNamespace(title=page_title), evidence_context=lambda: {})
        self.e = SimpleNamespace(form=form, return_to_job=back, emit=lambda *a, **k: None)

    async def dashboard_row(self, navigate=True):
        return "https://careers-example.icims.com/jobs/dashboard", self.row


async def test_a_received_row_is_this_runs_receipt_only_after_it_walked_the_steps():
    mine = Dashboard(f"2026-20717 {TITLE} Received Submission", submits=1)
    result = await mine.on_dashboard({})
    assert result["state"] == "confirmed" and result["receipt"]["type"] == "explicit_confirmation_page"
    # Not this run's submission: it was applied before, which is a different outcome and not a receipt.
    earlier = await Dashboard(f"2026-20717 {TITLE} Received Submission").on_dashboard({})
    assert earlier["state"] == "already_applied" and earlier["receipt"]["type"] == "employer_already_applied"


@pytest.mark.parametrize("submits", [0, 1])
@pytest.mark.parametrize("row", [f"2026-20717 {TITLE} Incomplete - application not submitted", f"2026-20717 {TITLE} Not Submitted"])
async def test_an_unfinished_row_neither_confirms_nor_ends_the_job_as_applied(row, submits):
    page = Dashboard(row, submits=submits)
    assert await page.on_dashboard({}) is None  # continue the application from the posting
    assert page.returned is True


async def test_a_job_title_containing_submitted_does_not_confirm_an_incomplete_row():
    page = Dashboard("2026-20717 Submitted Claims Analyst Incomplete", title="Submitted Claims Analyst", submits=1)
    assert await page.on_dashboard({}) is None and page.returned is True
