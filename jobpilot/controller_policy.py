"""Small, measured controller alternatives sharing one owner and submit guard."""

POLICIES = ("hybrid", "reasoning_first", "vision_assisted", "continuous_agent")


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
