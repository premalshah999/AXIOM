"""Text patterns that tell a registration, sign-in or account-exists page apart, and the fields they ask for."""

import re


# States that prove the employer holds an account for this email. "signing_in" and "created_locally" prove nothing.
CONFIRMED_ACCOUNT = {
    "authenticated", "password_reset", "exists", "locked", "verification_pending", "password_unknown"
}


# A Create refused because the email is registered (AMETEK 2026-10-18: "Account Already Exists"). Prompts such as
# "Already have an account?" on SuccessFactors' sign-in page are not refusals.
ACCOUNT_EXISTS = re.compile(
    r"account (?:already )?exists|already (?:exists|registered|in use|taken)|"
    r"(?:e-?mail|user ?name)(?: address)? (?:is )?already (?:in use|registered|taken|exists)",
    re.I,
)


PASSWORD_MAXIMUM = re.compile(
    r"password (?:must |should )?(?:not be longer than|must not exceed|cannot exceed|maximum(?: length)?(?: is| of)?|"
    r"max(?:imum)? length[: ]+)\s*(\d{1,3})\s*characters",
    re.I,
)


# Registration that continues after its first page (Dayforce ID: security questions, emailed code, multifactor setup).
# The account does not exist yet there; marking it authenticated sent the next pass to "Sign In" (2026-10-16).
REGISTRATION_STEP = re.compile(r"security.?questions|/verif|mfa.?setup|multi.?factor", re.I)


REGISTRATION_HEADING = re.compile(
    r"^\s*(?:security questions|enter (?:the )?verification code|verify your e-?mail|multi.?factor authentication|"
    r"set up multi.?factor)",
    re.I,
)


ACCOUNT_EXISTS_STEP = re.compile(r"/verif|mfa.?setup|multi.?factor", re.I)


ACCOUNT_EXISTS_HEADING = re.compile(
    r"^\s*(?:enter (?:the )?verification code|verify your e-?mail|multi.?factor authentication|set up multi.?factor)",
    re.I,
)


def registration_state(creating, path, headings, password_fields):
    """The account state an account action's result proves: "verification_pending" once a registration has reached its
    code or multifactor step, "authenticated" once a sign-in or one-page registration has left the account form, else
    None (still a registration step, or the form came back)."""
    continuing = creating and (
        REGISTRATION_STEP.search(path) or any(REGISTRATION_HEADING.search(h) for h in headings)
    )
    if continuing and (
        ACCOUNT_EXISTS_STEP.search(path) or any(ACCOUNT_EXISTS_HEADING.search(h) for h in headings)
    ):
        return "verification_pending"
    if not password_fields and not continuing:
        return "authenticated"
    return None


# The page's own required-field check: nothing reached the server, so it says nothing about whether an account
# exists. Westinghouse (SuccessFactors) showed "A password is required." and the engine created a second account for
# an email that already had one (2026-10-16/17).
REQUIRED_INPUT = re.compile(
    r"\b(?:password|e-?mail(?: address)?|user ?name|login)\b[^.]{0,20}\bis required\b|complete all required fields",
    re.I,
)


LOGIN_IDENTIFIER = re.compile(
    # ADP Workforce Now (ZincFive 2026-10-16) asks for "Email or Mobile Number".
    r"(?:(?:enter|type)(?: your)? |your )?(?:valid )?e.?mail(?: address)?(?: or (?:mobile|phone|cell)(?: phone)?(?: number)?)?"
    r"|user ?name|login",
    re.I,
)


def login_identifier(field):
    """An account form's email or user-name input. SuccessFactors sign-in labels it "Valid Email"
    (Capgemini) or "Email Address:" (EY); left unfilled, the employer rejected the login and cleared
    the password, and the run looped on the sign-in page (2026-10-15)."""
    return field["type"] == "email" or bool(
        LOGIN_IDENTIFIER.fullmatch(re.sub(r"[\s:*✱]+$", "", field["label"]))
    )


SECURITY_QUESTION = re.compile(
    r"^\s*(?:security|secret|challenge) question(?:\s*(?:#|no\.?)?\s*\d+)?\s*\*?\s*$", re.I
)


# "Challenge Question Response" (PeopleAdmin's account form, NC State 2026-10-26).
SECURITY_ANSWER = re.compile(
    r"^\s*(?:(?:security|secret|challenge) )?(?:question )?(?:answer|response)(?:\s*(?:#|no\.?)?\s*\d+)?\s*\*?\s*$",
    re.I,
)


GUEST = re.compile(
    r"apply without (?:an |a )?account|apply as (?:a )?guest|continue as (?:a )?guest|"
    r"guest (?:apply|application)|apply without signing in|continue without (?:an |a )?account",
    re.I,
)
