"""Gmail endpoints and challenge states."""

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


TOKEN_URL = "https://oauth2.googleapis.com/token"


API_URL = "https://gmail.googleapis.com/gmail/v1/users/me"


LIVE = {"pending", "matched", "consumed"}


# Oracle codes arrive in 14 s (median), 59 s (p90): an unmatched wait older than this, whose run
# has stopped, must not hold the shared sender for its whole durable window.
STALE_CODE_HOLD = 300
