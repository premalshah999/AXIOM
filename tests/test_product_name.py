"""The app is AXIOM. A Telegram question signed by the project's old name reads as somebody else's bot in a demo. Only what a person
reads is held here: the name in a browser-recovery reason and in a User-Agent is matched elsewhere or seen only by servers."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "jobpilot"


@pytest.mark.parametrize("path", ["telegram.py", "telegram_workers.py", "accounts/overview.py", "cli.py", "api/app.py", "api/routes/frontend.py"])
def test_what_a_person_reads_says_axiom(path):
    assert "ApplyPilot" not in (ROOT / path).read_text()
