"""Non-secret version tags used to compare runs from the same implementation."""

from functools import lru_cache
import hashlib
import json
from pathlib import Path


@lru_cache(maxsize=1)
def engine_version():
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for file in sorted([*root.rglob("*.py"), *root.rglob("*.js")]):
        digest.update(str(file.relative_to(root)).encode())
        digest.update(file.read_bytes())
    return "execution-v2-" + digest.hexdigest()[:12]


def profile_version(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]
