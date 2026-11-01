"""What a page says a new password must be, and a random password that meets it. Pure text work, no database.

The owner (2026-11-01): one main password is tried first; an employer that refuses it (rules not met, "different from
your previous 3 passwords", too like the name) gets a random one that meets the page's own stated rules, kept for that
employer only. Lenovo's "must differ from your previous 3 passwords" is the known live case.
"""

from dataclasses import dataclass, field
import re
import secrets

LOWER, UPPER, DIGITS = "abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789"
SPECIALS = "!@#$%&*?"  # the symbols nearly every policy that wants one accepts
AMBIGUOUS = "O0Il1o"

# The employer turned the password down for a reason a different password can answer. Sign-in refusals are not here.
REFUSAL = re.compile(
    r"password (?:must|should|needs to|requirements|policy|is too)|"
    r"(?:new )?password (?:you entered )?(?:is|was) (?:too |not )?(?:weak|short|common|simple|similar|acceptable|strong enough)|"
    r"at least \d+ characters|must contain|must not contain|must (?:be )?(?:different|differ)|"
    r"too (?:similar|close)|not meet.{0,40}requirements|(?:previous|last|prior|recent|old) \d* ?passwords?|"
    r"(?:same as|reuse|re-use)[^.]{0,30}password|password history|"
    r"cannot (?:contain|include|be based on)|can'?t (?:contain|include)",
    re.I,
)


@dataclass
class Rules:
    minimum: int = 12
    maximum: int = 16
    upper: bool = True
    lower: bool = True
    digit: bool = True
    special: bool = True
    # Characters the page names as the only symbols it accepts; empty: any of SPECIALS.
    specials: str = ""
    avoid: list = field(default_factory=list)


def refused(text: str) -> bool:
    """The page's words are a password rule, a history rule or a similarity rule."""
    return bool(REFUSAL.search(text or ""))


def read(text: str, avoid=(), fallback_maximum=None) -> Rules:
    """The rules a page states in `text`. What it does not state, a policy nearly every site meets is assumed."""
    t = " ".join((text or "").split())
    rules = Rules(avoid=[a for a in (str(x).lower() for x in avoid) if len(a) >= 3])
    if found := re.findall(r"(?:at least|minimum(?: of)?|min(?:imum)?\.? length(?: of)?|no (?:fewer|less) than)\s*(\d{1,2})\s*(?:or more )?(?:characters|chars|symbols|digits|long)?", t, re.I):
        rules.minimum = max(8, min(max(int(n) for n in found), 64))
    if found := re.findall(
        r"(?:no more than|not (?:be )?longer than|must not exceed|cannot exceed|at most|up to|maximum(?: length)?(?: is| of)?|"
        r"max(?:imum)? length[: ]+|between \d+ and)\s*(\d{1,3})\s*(?:characters|chars)?", t, re.I
    ):
        top = min(int(n) for n in found)
        if top >= rules.minimum:
            rules.maximum = top
    if fallback_maximum and fallback_maximum >= 8:
        rules.maximum = min(rules.maximum, fallback_maximum)
    if rules.maximum < rules.minimum:
        rules.minimum = max(8, rules.maximum)
    # 16 is plenty and the longest the common policies accept; a page that allows more is not asked for more.
    rules.maximum = min(rules.maximum, max(rules.minimum, 16))
    if listed := re.search(r"(?:special characters?|symbols?|punctuation)[^.:]{0,40}:\s*((?:\S\s?){2,24}?)(?:[.\n]|$)", t, re.I):
        chosen = "".join(c for c in listed.group(1) if c in "!@#$%^&*()_+-=[]{};:,.<>?/~`|\\'\"" and c not in "\\'\"<>`|")
        rules.specials = chosen
    if re.search(r"no (?:special|symbol)|(?:only|just) (?:letters|alphanumeric)|alphanumeric (?:characters )?only|"
                 r"(?:cannot|can'?t|must not) (?:contain|include|use) (?:any )?(?:special|symbols?)", t, re.I):
        rules.special = False
    return rules


def generate(rules: Rules) -> str:
    """A random password meeting `rules`: every class it asks for, no run of one character, nothing it must avoid."""
    length = max(rules.minimum, min(rules.maximum, 16))
    specials = rules.specials or SPECIALS
    classes = [c for c, on in ((UPPER, rules.upper), (LOWER, rules.lower), (DIGITS, rules.digit), (specials, rules.special)) if on]
    pool = "".join(classes) or LOWER + UPPER + DIGITS
    pool = "".join(c for c in pool if c not in AMBIGUOUS) or pool
    for _ in range(200):
        chars = [secrets.choice([c for c in cls if c not in AMBIGUOUS] or cls) for cls in classes]
        chars += [secrets.choice(pool) for _ in range(length - len(chars))]
        # Letters first: some policies want the first character to be a letter.
        secrets.SystemRandom().shuffle(chars)
        letters = [i for i, c in enumerate(chars) if c.isalpha()]
        if letters and not chars[0].isalpha():
            chars[0], chars[letters[0]] = chars[letters[0]], chars[0]
        password = "".join(chars)
        low = password.lower()
        if re.search(r"(.)\1{2,}", password) or any(a in low for a in rules.avoid):
            continue
        if re.search(r"(?:012|123|234|345|456|567|678|789|abc|bcd|cde|def|qwe|pass)", low):
            continue
        return password
    return "".join(secrets.choice(pool) for _ in range(length))


def meets(password: str, rules: Rules) -> bool:
    low = password.lower()
    return (
        rules.minimum <= len(password) <= rules.maximum
        and (not rules.upper or any(c.isupper() for c in password))
        and (not rules.lower or any(c.islower() for c in password))
        and (not rules.digit or any(c.isdigit() for c in password))
        and (not rules.special or any(not c.isalnum() for c in password))
        and not any(a in low for a in rules.avoid)
    )


# Remaining sign-in tries, or the lock threshold, as a page states them.
TRIES_LEFT = re.compile(r"\b(\d{1,2}|one|two|three|four|five)\s+(?:more\s+)?(?:sign.?in |log.?in )?(?:attempts?|tries|chances)\s+(?:remaining|left|remain)", re.I)
LOCK_AFTER = re.compile(
    r"(?:locked|blocked|disabled|suspended)[^.]{0,40}after\s+(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:\w+\s+){0,2}(?:attempts?|tries)|after\s+(\d{1,2}|three|four|five|six|seven|eight|nine|ten)\s+(?:\w+\s+){0,2}"
    r"(?:attempts?|tries)[^.]{0,40}(?:locked|blocked|disabled|suspended)",
    re.I,
)
_NUMBER = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _number(value):
    return int(value) if value.isdigit() else _NUMBER.get(value.lower(), 0)


def tries_left(text: str):
    """How many sign-in tries the page says remain, else None."""
    found = TRIES_LEFT.search(text or "")
    return _number(found.group(1)) if found else None


def lock_threshold(text: str):
    """How many failed sign-ins the page says lock the account, else None."""
    found = LOCK_AFTER.search(text or "")
    return _number(next(g for g in found.groups() if g)) if found else None


def may_try_again(failures: int, text: str) -> bool:
    """One more sign-in with a different password is safe: never the last try the page allows."""
    left = tries_left(text)
    if left is not None and left < 2:
        return False
    limit = lock_threshold(text)
    return not (limit and failures + 1 >= limit)
