"""The same Aggressive renderer using an explicitly selected, existing TeX bundle.

AXIOM_TEST_TEX_BINARY must point to a non-production bundle. This test never
starts an app, rebuilds its bundle, or writes into that TeX tree.
"""

import asyncio
import os
from pathlib import Path

import pytest

from jobpilot.features.resumes import editor
from jobpilot.features.resumes.aggressive.pipeline import run
from test_aggressive_pipeline import JOB, PROFILE, context


def test_existing_bundle_compiles_all_aggressive_type_and_parse_checks(tmp_path, monkeypatch):
    binary = os.environ.get("AXIOM_TEST_TEX_BINARY", "")
    if not binary or not Path(binary).is_file():
        pytest.skip("Set AXIOM_TEST_TEX_BINARY to a non-production bundled pdflatex")
    resolved = Path(binary).resolve()
    assert not resolved.is_relative_to(Path("/Applications/AXIOM.app"))
    monkeypatch.setenv("PATH", str(Path(binary).parent) + ":/usr/bin:/bin")
    monkeypatch.setattr(editor, "_AVAILABLE", None)
    source, ctx = context(tmp_path)
    result = asyncio.run(run(JOB, source, PROFILE, ctx))
    assert result.report["parse"]["ok"]
    assert result.report["preview_rules"]["typography"]["ok"]
    assert result.pdf_path.is_file()
