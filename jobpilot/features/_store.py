"""Feature-owned tables on the shared engine.

A separate DeclarativeBase keeps these tables out of the core metadata: creating
them is additive and never alters a table the application already owns.
"""

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.orm import DeclarativeBase


class FeatureBase(DeclarativeBase):
    pass


def create_tables(engine):
    """Idempotent: create missing feature tables only (no ALTER, no DROP)."""
    # Import the table modules so their classes register on FeatureBase.
    from .discovery import tables as _d  # noqa: F401
    from .knowledge import tables as _k  # noqa: F401
    from .resumes import tables as _r  # noqa: F401

    FeatureBase.metadata.create_all(engine)


def drop_tables(engine, prefixes=("kn_", "rs_", "dc_")):
    """Rollback helper for tests/maintainers: drops feature tables only."""
    tables = [t for t in FeatureBase.metadata.sorted_tables if t.name.startswith(tuple(prefixes))]
    FeatureBase.metadata.drop_all(engine, tables=tables)


def now():
    return datetime.now(UTC).isoformat()


def new_id(prefix=""):
    return prefix + uuid4().hex


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def norm(text) -> str:
    """Casefolded NFKC text with collapsed whitespace; punctuation is kept."""
    value = unicodedata.normalize("NFKC", str(text or "")).casefold()
    value = value.translate(str.maketrans({"’": "'", "‘": "'", "≤": "<=", "≥": ">=", "−": "-"}))
    return re.sub(r"\s+", " ", value).strip()


WORD = re.compile(r"[a-z0-9][a-z0-9+#./-]*")


def words(text) -> list[str]:
    return WORD.findall(norm(text))
