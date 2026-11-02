"""Optional Aggressive mode; importing registers it without changing defaults."""

from ..modes import register
from .pipeline import SPEC, run

register(SPEC)

__all__ = ["SPEC", "run"]
