"""Publicis (iCIMS Central EU, 2026-10-29): authenticate() passes forced_ok to auth_outcome, and iCIMS's override did
not accept it, so every iCIMS sign-in failed with "Worker error: TypeError". Every override keeps the base signature."""

import inspect

from jobpilot.adapters import base, icims


def test_every_auth_outcome_override_accepts_the_base_keywords():
    expected = set(inspect.signature(base.Adapter.auth_outcome).parameters)
    for cls in (icims.ICIMS,):
        assert set(inspect.signature(cls.auth_outcome).parameters) >= expected, cls.__name__
