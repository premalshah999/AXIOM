"""docs/KNOWLEDGE.md tells a new applicant which fact keys to confirm; each must be a key the code reads.

New installation, 2026-10-21: the table spelled `work_authorization_US` and `requires_future_sponsorship_US`, but the
setup guide writes, and the answer code reads, `work_authorization_us` and `requires_future_sponsorship_us`; a friend
who typed the documented spelling into Setup > Profile > Facts saved a fact nothing used. The table also listed keys
the guide never writes without saying where to add them."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def documented_keys():
    text = (ROOT / "docs" / "KNOWLEDGE.md").read_text()
    section = text.split("## Setting up a new applicant", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("|") and not line.startswith("|---")]
    keys = []
    for row in rows[1:]:
        cells = row.strip("|").split("|")
        keys += re.findall(r"`([a-z_A-Z]+)`", cells[1])
    return keys


def source_text():
    parts = [p.read_text() for p in (ROOT / "jobpilot").rglob("*.py")]
    parts += [p.read_text() for p in (ROOT / "frontend" / "src").glob("*.ts*")]
    return "\n".join(parts)


# Read by pattern (knowledge.relevant_fact matches a fragment of the key), not by exact name.
BY_PATTERN = {"export_control_restricted_country_history": "export_control", "company_relationships": "relationship"}


def test_every_documented_key_is_spelled_the_way_the_code_spells_it():
    source = source_text()
    keys = documented_keys()
    assert len(keys) >= 20
    missing = [
        key
        for key in keys
        if (BY_PATTERN[key] not in source if key in BY_PATTERN else f'"{key}"' not in source and f"{key}:" not in source)
    ]
    assert not missing, f"docs/KNOWLEDGE.md names fact keys the code never uses: {missing}"
