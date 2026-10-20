"""`.env.example` is the one place a new installation learns every setting. It must stay complete, truthful and
free of anyone's real values (AXIOM is public)."""

import re
from pathlib import Path

from jobpilot.config import Settings

EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"
# Read by Docker Compose / scripts/setup.sh, not by the app.
COMPOSE_ONLY = {"POSTGRES_PASSWORD", "APP_PORT", "APP_BIND", "COMPOSE_PROJECT_NAME", "GIT_SHA"}
# Active lines whose value deliberately differs from the code default (each is explained in the file).
RECOMMENDED_OVERRIDES = {"deepseek_thinking"}
ENTRY = re.compile(r"^(#\s)?([A-Z][A-Z0-9_]*)=(.*)$")
SECRET = re.compile(r"(_KEY|TOKEN|PASSWORD|SECRET|LOGIN)$")


def entries():
    found = {}
    for line in EXAMPLE.read_text().splitlines():
        if match := ENTRY.match(line):
            found[match.group(2)] = (bool(match.group(1)), match.group(3))
    return found


def setting_names():
    return {name.upper() for name in Settings.model_fields}


def alias_names():
    """Other accepted names for a setting (ANTHROPIC_API_KEY, ACCOUNT_PASSWORD). They are explained in prose, not
    listed as entries, so nobody sets a value twice."""
    return {
        choice.upper()
        for name, field in Settings.model_fields.items()
        for choice in getattr(field.validation_alias, "choices", [])
        if isinstance(choice, str) and choice.upper() != name.upper()
    }


def test_every_setting_is_documented():
    missing = sorted(setting_names() - set(entries()))
    assert not missing, f"Add these settings to .env.example: {missing}"
    text = EXAMPLE.read_text()
    assert not [alias for alias in alias_names() if alias not in text], (
        "mention each alias next to its setting"
    )


def test_no_stale_or_misspelled_entries():
    unknown = sorted(set(entries()) - setting_names() - COMPOSE_ONLY)
    assert not unknown, f".env.example lists variables nothing reads: {unknown}"


def test_the_example_loads_as_is():
    loaded = Settings(_env_file=EXAMPLE)
    assert loaded.llm_provider == "deepseek"
    assert loaded.deepseek_thinking == "disabled"
    assert loaded.browser_cdp_url == ""


def test_documented_defaults_equal_the_code_defaults(tmp_path):
    """Uncomment every default shown in the file: nothing may change except the recommended overrides."""
    lines = []
    for line in EXAMPLE.read_text().splitlines():
        match = ENTRY.match(line)
        if match and match.group(1) and match.group(3) != "":
            line = f"{match.group(2)}={match.group(3)}"
        lines.append(line)
    uncommented = tmp_path / "all-defaults.env"
    uncommented.write_text("\n".join(lines) + "\n")
    shown, built_in = Settings(_env_file=uncommented), Settings(_env_file=None)
    drift = {
        name
        for name in Settings.model_fields
        if getattr(shown, name) != getattr(built_in, name) and name not in RECOMMENDED_OVERRIDES
    }
    assert not drift, f".env.example shows a default that differs from jobpilot/config.py: {sorted(drift)}"


def test_secrets_are_blank_and_no_personal_data():
    for name, (_, value) in entries().items():
        if SECRET.search(name):
            assert value == "", f"{name} must be blank in .env.example"
    text = EXAMPLE.read_text()
    for address in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text):
        assert address.endswith("@example.com"), f"personal-looking address in .env.example: {address}"


def test_blank_active_lines_are_all_string_settings():
    """pydantic rejects an empty string for numbers, booleans and enums, so only string settings may be blank."""
    for name, (commented, value) in entries().items():
        if commented or value != "" or name in COMPOSE_ONLY:
            continue
        field = Settings.model_fields[name.lower()] if name.lower() in Settings.model_fields else None
        assert field is None or field.annotation is str, (
            f"{name} is active and blank but is not a string setting"
        )
