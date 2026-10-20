"""Sanitized mailbox errors and durable wait handles; never contain a token or link."""

CODE_INLINE_WAIT_SECONDS = 30
# Platforms whose code page cannot be reopened: a parked run's continuation lands on the email step again, which
# sends a new code, so every code slower than the inline wait parks again (GM Financial's Oracle application on
# 2026-10-18: the code took 65 s, two runs, two code requests). Oracle codes take 14 s median and 59 s p90; the
# run holds the shared sender while it waits either way.
CODE_INLINE_WAIT_BY_ATS = {"oracle": 150}


def code_inline_wait(ats):
    return CODE_INLINE_WAIT_BY_ATS.get(ats or "", CODE_INLINE_WAIT_SECONDS)


class MailError(ValueError):
    pass


class MailAuthenticationError(MailError):
    pass


class MailTransportError(MailError):
    pass


class MailOperationError(MailError):
    pass


class MailCursorUpgrade(MailOperationError):
    """Restart search after a provider's obsolete folder-local cursor is found."""


class MailMessageError(MailError):
    def __init__(self, reason="unreadable_message"):
        self.reason = reason
        super().__init__("A mailbox message could not be processed")


class MailPending(Exception):
    def __init__(self, challenge):
        self.challenge = {
            key: challenge[key]
            for key in ("id", "request_id", "generation", "kind", "since", "expires")
            if key in challenge
        }
        self.challenge["challenge_id"] = challenge.get("challenge_id", challenge.get("id", ""))
        super().__init__("Waiting for a correlated employer verification email")


class MailSenderBusy(Exception):
    """No mail was requested: another run owns this sender's request window."""

    def __init__(self, kind="auto"):
        self.kind = kind
        super().__init__("Another application is verifying with this sender")

    def result(self, armed=False):
        return {
            "state": "submission_unknown" if armed else "timed_out",
            "reason": "Another application kept the same email sender busy; no verification request was sent",
            "blocker_type": "email_wait",
            "recovery_cause": "" if armed else "sender_busy",
            "mail_request_kind": self.kind,
        }
