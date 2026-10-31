"""The key under which a confirmed or unproven submission is remembered, so the same posting is never applied to twice."""


def guard_key(identity):
    return "submission_guard:" + identity
