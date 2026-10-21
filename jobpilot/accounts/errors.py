"""Errors raised by account actions."""


class AccountActionError(ValueError):
    def __init__(self, message, blocker_type):
        self.blocker_type = blocker_type
        super().__init__(message)
