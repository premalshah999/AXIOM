"""Whether this Mac is on battery, from `pmset`. Its own module: the supervisor reads it, and so do the screens (jobpilot/waiting.py)."""

import re
import shutil
import subprocess


def power_status():
    """{'battery': bool, 'percent': int, 'remaining': '0:50'}, or {} where pmset does not exist."""
    if not shutil.which("pmset"):
        return {}
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    level = re.search(r"(\d+)%", out)
    remaining = re.search(r"(\d+:\d+) remaining", out)
    return {
        "battery": "Battery Power" in out,
        "percent": int(level.group(1)) if level else None,
        "remaining": remaining.group(1) if remaining else "",
    }
