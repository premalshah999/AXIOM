"""Small, measured controller alternatives sharing one owner and submit guard.

The model-led alternatives (reasoning_first, continuous_agent) were retired on 2026-10-14; a retained draft that still
names one reads as hybrid, which is what policy_for returns for any name it does not know.
"""

POLICIES = ("hybrid", "vision_assisted")


def policy_for(run):
    packet = run.get("packet", {}) if isinstance(run, dict) else run.packet
    value = packet.get("controller_policy", "hybrid")
    return value if value in POLICIES else "hybrid"


def validate_policy(value):
    if value not in POLICIES:
        raise ValueError("Unknown application controller policy")
    return value


def vision_due(run, step):
    return step >= (0 if policy_for(run) == "vision_assisted" else 2)
