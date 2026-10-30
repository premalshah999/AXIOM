"""Deterministic application driver: observe -> classify step -> act -> verify, with progress limits.

The model is used only to answer questions the profile/knowledge base cannot. Navigation, accounts,
email verification and submission are code paths with explicit success checks."""

import asyncio
import hashlib
import re
from ..db import now
import time
from collections import Counter
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Error as PlaywrightError

from ..service import Service
from ..accounts import Accounts
from ..captcha_observation import ARM_JS
from ..forms import AUTH, FINAL, LAUNCHER, SURVEY_DONE
from ..privacy import acknowledge_privacy_notice, dismiss_cookies
from ..mail_errors import MailPending, code_inline_wait

APPLY = re.compile(
    r"^(?:apply|apply now|apply here|apply for (?:this )?(?:job|position|role|opportunity)|apply online|"
    r"apply for this job online|start (?:your |my )?application|apply to (?:this )?(?:job|position)|"
    # XPO's SuccessFactors site (2026-10-21): "Apply now »" was no way forward and the guided agent took the run.
    r"i'?m interested|begin (?:your )?application|easy apply|apply on company (?:site|website)|apply today)"
    r"(?:\s*[>›→»])?$",
    re.I,
)
NOT_APPLY = re.compile(r"linkedin|indeed|seek\b|glassdoor|google|facebook|refer|share|save job|alert", re.I)
NEXT = re.compile(
    r"^(?:next|continue|save (?:and|&) continue|save (?:and|&) next|next step|proceed|continue to (?:next step|"
    r"application|apply)|review(?: (?:my )?application)?|go to review|next page|start|get started|continue application)"
    r"(?:\s*[>›→»])?$",
    re.I,
)
def confirmation_wording(text, committed):
    """Confirmation phrases on the page. Before this run submitted, a posting's "thank you for your interest" courtesy
    line is none: Rice's Oracle postings end "Thank you for your interest in employment with Rice University" above an
    Apply Now not yet rendered, and every run stopped there as a confirmation it had not produced (2026-10-23..25)."""
    found = [m[0] for m in CONFIRM_TEXT.finditer(text)]
    if committed:
        return found
    return [c for c in found if not re.fullmatch(r"thank you for your interest", c, re.I)]


CONFIRM_TEXT = re.compile(
    r"thank you for (?:applying|your (?:job )?application|your interest|submitting)|"
    r"application (?:has been |was )?(?:successfully )?(?:submitted|received|complete)|"
    r"we(?:'ve| have) received your application|successfully (?:applied|submitted)|"
    r"your application (?:is|has been) (?:complete|submitted|on its way|received)|"
    # The role named in between: "Congratulations! Your application for Software Engineer has been received" (Gem,
    # Imagen Technologies 2026-10-28: read as unconfirmed on three runs while the page showed it).
    r"your application for [^.!?\n]{1,140}? (?:has been|was|is) (?:successfully )?(?:submitted|received|sent|complete)\b|"
    r"you(?:'ve| have) (?:successfully )?applied|application submitted|"
    r"congratulations.{0,60}(?:submitted|applied|received)|thank you for sending us your online job submission|"
    r"your application (?:has been|was) sent|thanks? (?:you )?for (?:applying|your application)",
    re.I,
)
# Explicit statements only. "Already applied? Sign in" footers and drafts are not applications.
ALREADY = re.compile(
    r"we(?:['’]ve| have) already received your application for this (?:job|position|role)\b|"
    r"you already applied (?:for|to) this (?:job|position)(?:[.!]|$)|"
    r"you(?:'ve| have) already applied|you have previously applied|already submitted an application|"
    r"application for this (?:job|position|role) (?:has )?already been submitted|"
    # Workday posting page after an application (HPE 2026-10-15): "You applied for this job on October 15, 2026."
    r"you applied for this (?:job|position|role) on\b|"
    # iCIMS's job page for an applicant already in its pipeline (MCI 2026-10-24).
    r"you(?:'re| are) (?:currently )?submitted (?:to|for) this (?:job|position|role)",
    re.I,
)
EMBED_FRAME = re.compile(
    r"(?:^|\.)(?:youtube(?:-nocookie)?\.com|vimeo\.com|player\.vimeo\.com|wistia\.(?:com|net)|vidyard\.com|"
    r"google\.com|googleapis\.com|gstatic\.com|recaptcha\.net|hcaptcha\.com|challenges\.cloudflare\.com|"
    r"intercom\.io|drift\.com|zendesk\.com|livechatinc\.com|facebook\.com|twitter\.com|linkedin\.com)$",
    re.I,
)
PASSWORD_HISTORY = re.compile(
    r"(?:same as|different from|match(?:es)?|reuse|re-use|used) (?:(?:one of )?(?:your|a|the) )?(?:previous|last|old|recent|prior)\w* "
    r"(?:\d+ )?passwords?|password (?:has been|was) used (?:before|recently|previously)|password history",
    re.I,
)
AUTH_AGREEMENT = re.compile(
    r"\bI (?:have )?read and (?:agree|accept)|\bI (?:agree|consent) to (?:the |their )?(?:[\w-]+ ){0,3}"
    r"(?:terms|privacy|user agreement|policy|notice)|\bI accept (?:the )?(?:terms|privacy|user agreement)",
    re.I,
)
AUTH_MARKETING = re.compile(r"newsletter|marketing|promotion|job alerts?|text messages?|\bsms\b|talent community", re.I)
# A cookie banner announced as a dialog is never the page's step (ZincFive's ADP sign-in, 2026-10-27: the banner's
# "manage your preferences" link and "x" hid the email box and Continue, and three runs stopped on "only a privacy
# dialog with Close").
COOKIE_NOTICE = re.compile(
    r"\bcookies?\b.{0,240}\b(?:privacy|preferences|statement|policy|consent|settings)\b|\bcookie (?:settings|preferences)\b",
    re.I | re.S,
)
# A tax-credit (WOTC) questionnaire the application requires: Workday's "Take Assessment" step (ADP for Rentokil,
# TaxCreditCo for Tyson Foods), Oracle's "Tax Credits" section (EY for Community Health Systems, Deloitte for Caesars).
# AT&T's Workday step says only "This information is for tax purposes and will not be shared with AT&T" (2026-10-27).
TAX_CREDIT = re.compile(r"tax credit|work opportunity|\bwotc\b|\bfor tax purposes\b", re.I)
# Its forward controls finish only that questionnaire. EY's entry page says "Take Survey" (2026-10-27), Deloitte's
# "Agree", TaxCreditCo's last page "Submit form". Exits ("Finish later", "Save and exit") never continue it.
ASSESSMENT_FORWARD = re.compile(
    r"\s*(?:next|continue|submit|finish|done|complete|proceed|take|start|begin|agree|i agree|accept)\b.{0,30}", re.I
)
ASSESSMENT_RETURN = re.compile(r"\s*(?:return|back|go back) to (?:the |your )?(?:application|workday|job)\W*", re.I)
ASSESSMENT_EXIT = re.compile(r"\blater\b|\bexit\b|\bcancel\b|\bquit\b|\bclose\b|\bwithout\b|\bdecline\b|\bdisagree\b", re.I)


def question_set(obs):
    """What a questionnaire page asks: its address and its questions (labels with their options)."""
    return (obs.get("url", ""), tuple((f["label"], tuple(f.get("options") or [])) for f in obs.get("fields", [])))


CLOSED = re.compile(
    r"no longer (?:accepting (?:applications)?|available|active|open)|position has been filled|"
    # Phenom (PwC 2026-10-27): "We're sorry… the job you are trying to apply for has been filled."
    r"\b(?:job|position|role|opening|requisition|vacancy)\b[^.!?]{0,50}\bhas (?:already )?been filled\b|"
    r"job (?:posting |ad |listing |opening )?(?:has (?:been )?)?(?:closed|expired|removed)|this (?:job|position|requisition) (?:is )?"
    r"(?:closed|unavailable|no longer)|job not found|the page you are looking for (?:does not|doesn't) exist|"
    # Paylocity (Monti 2026-10-26): "We're sorry, that job does not exist or is not currently active."
    r"\b(?:that|this|the) (?:job|position|posting|requisition|opening) (?:you(?:'re| are) looking for )?"
    r"(?:does not|doesn't|no longer) exists?\b(?! yet)|"
    r"\b(?:that|this|the) (?:job|position|posting|requisition|opening) is not currently (?:active|available|open)",
    re.I,
)
# The employer's cap on this applicant's applications. TikTok (2026-10-28): "From 2026-07-01 to 2026-12-31, you can submit
# up to 2 job applications (including referrals) within the "Bachelor/Master Graduate - 2027 Start" recruitment project.
# You've reached the application limit." over a full form whose Submit is refused; three runs parked on it for the owner.
APPLICATION_LIMIT = re.compile(
    r"\byou(?:'ve| have) reached (?:the|your) (?:\w+ )?(?:job )?(?:application|applications|apply) limit\b|"
    r"\b(?:reached|exceeded) (?:the|your) (?:maximum|max) (?:number of )?(?:job )?applications\b|"
    r"\b(?:job )?application limit (?:has been |was |is )?(?:reached|exceeded)\b",
    re.I,
)
CHECK_EMAIL = re.compile(
    r"check your (?:email|inbox)|(?:verification|confirmation|activation) (?:email|link) (?:has been |was )?sent|"
    r"we(?:'ve| have)? (?:just )?sent (?:you )?(?:an? )?(?:email|link|verification email)|verify your (?:email|account)|"
    r"activate your account|confirm your email|account (?:needs to be|must be) verified",
    re.I,
)
CODE_TEXT = re.compile(
    r"verification code|security code|one.time (?:code|passcode|password|pin)|enter (?:the |your )?(?:\d.digit )?"
    r"(?:code|pin)|confirm your identity|we(?:'ve)? sent (?:a |you a )?(?:\d.digit )?(?:code|pin)|passcode",
    re.I,
)
STRONG_CODE_LABEL = re.compile(
    r"verif|security|authenticat|confirmation|one.time|e.?mail|\botp\b|passcode|validation|access code", re.I
)


def dedicated_code_step(fields, codes):
    """A code step is its own screen or modal: a digit-box group, or at most two other inputs.

    John Deere's (Eightfold) one-page application has a code-like field among name, email,
    phone and resume fields; treating it as an email-code step parked 10 runs on a mail
    that was never sent (2026-10-13)."""
    if len(codes) >= 4:
        return True
    if any(STRONG_CODE_LABEL.search(str(f.get("label", ""))) for f in codes):
        return True  # An explicit verification-code label keeps the mixed-step safety review.
    ids = {id(f) for f in codes}
    others = [
        f
        for f in fields
        if id(f) not in ids and f.get("type") not in {"hidden", "checkbox", "radio", "button"}
    ]
    return len(others) <= 2


def code_label(label):
    """A label without required-field markers: SAP's passcode field is "* Passcode:"."""
    return re.sub(r"^[\s*]+|[\s*]+$", "", str(label or ""))


CODE_LABEL = re.compile(
    r"^(?:(?:enter|type)(?: the| your)? )?(?:(?:email|verification|security|authentication|confirmation|one.time|"
    r"access)(?: verification)? )?(?:code|passcode|otp|pin(?: code)?)(?: \d+)?[: ]*$|^digit \d+$|^pin code \d$",
    re.I,
)
SMS = re.compile(
    r"(?:sent|texted).{0,60}(?:your (?:phone|mobile)|sms|text message)|(?:sms|text message) (?:code|verification)|"
    r"authenticator app|passkey|security key",
    re.I,
)
# A sign-in page's other half for applicants without an account (Avature: "Already registered?" | "First time here?").
FIRST_TIME = re.compile(
    r"first time here|new (?:candidate|user|applicant)s?\b(?! (?:sign|log) ?in)|not (?:yet )?registered\b|"
    r"new to (?:this|our) (?:site|portal|career)",
    re.I,
)
CREATE_LINK = re.compile(
    r"^(?:create (?:an? |your |my )?(?:new )?(?:candidate |applicant )?(?:account|profile)|sign up|register|"
    r"new user|join(?: now)?|new candidate|don'?t have an account\??.{0,20}|create account)[!.]?$",
    # Deloitte's Avature: "Create profile!" (2026-10-26). Textron's Taleo: "Create a New Account" (2026-10-29), which
    # the pattern missed: the one registration after an unanswered reset was never opened, and the reset was repeated.
    re.I,
)
SIGNIN_LINK = re.compile(
    r"^(?:sign in|log ?in|already have an account\??.{0,20}|returning (?:user|candidate)|existing (?:user|candidate))$",
    re.I,
)
FORGOT = re.compile(
    r"forgot (?:your )?password|reset (?:your )?password|can'?t sign in|trouble signing in", re.I
)
# A code step's own "send" control: "Get code" (ByteDance), "Send code", "Send verification code", "Request a code".
SEND_CODE = re.compile(
    r"^(?:get|send|request|email)(?: (?:me|a|an|the|my))?(?: (?:new|verification|security|one.time|email|login))*"
    r" (?:code|passcode|otp|pin)$",
    re.I,
)
CONSENT_MENUS = {"select", "combobox", "dropdown", "radio"}
CONSENT_LABEL = re.compile(r"acknowledg|agree|consent|accept|privacy|terms", re.I)
AUTH_BUTTON = re.compile(
    r"^(?:sign in|log ?in|create (?:an? )?account|register|sign up|submit|continue|next|verify|confirm|"
    r"send|reset(?: password)?|save|create profile|set password|change password)$",
    re.I,
)
BAD_CREDENTIALS = re.compile(
    r"invalid (?:user ?name|e-?mail|credentials|login|password|sign.?in)|incorrect (?:e-?mail|password|user|credentials)|"
    r"wrong (?:e-?mail|password|user ?name)|(?:e-?mail|password|credentials|user ?name) (?:is |are |was )?(?:incorrect|invalid|not valid)|"
    r"does(?: not|n'?t) match|unable to sign in|sign.?in failed|no account|account (?:was )?not found|not registered|"
    r"couldn'?t find (?:an |your )?account|does not correspond to a registered user|"
    # SAP SuccessFactors: "We couldn't authenticate you. Check your username and password."
    r"couldn'?t authenticate|could not authenticate|check your (?:user ?name|e-?mail)(?: and| or) password|"
    r"authenticat\w* (?:failed|unsuccessful)|"
    # Amazon (2026-10-27): "The login information you provided appears to be incorrect." went unrecognised, so the
    # refused password was never reset and seven applications waited for the owner.
    r"(?:login|log-in|sign.?in) (?:information|details|credentials)\b[^.]{0,40}\b(?:incorrect|invalid|not valid|not recogni[sz]ed)|"
    # Avature (Deloitte 2026-10-26): "The username or password may be incorrect, or access might be restricted."
    r"(?:user ?name|e-?mail|password|credentials) (?:may|might) be (?:incorrect|invalid|wrong)|"
    # Wording seen across career sites and unknown platforms (owner 2026-10-26: "texts like your account might be
    # locked would indicate there's no account or the password is wrong").
    r"(?:login|log.?in|sign.?in) (?:failed|unsuccessful|was unsuccessful|attempt failed)|"
    r"(?:we )?(?:can'?t|cannot|could ?n'?t|could not|were unable to) (?:sign|log) you in|unable to (?:log|sign) ?in|"
    r"(?:user ?name|e-?mail|login)(?: (?:and|or|/) ?password)?(?: combination)? (?:you entered |provided )?"
    r"(?:is|are|was|were) (?:incorrect|invalid|wrong|not correct|not valid|not recogni[sz]ed)|"
    r"the (?:password|credentials) you (?:entered|provided|supplied) (?:is|are|was|were) (?:incorrect|invalid|wrong)|"
    r"(?:invalid|incorrect|wrong) (?:login|sign.?in) (?:details|information|credentials)",
    # Taleo uses this wording for a username with no account, before offering New User.
    re.I,
)
# Avature (Electronic Arts, 2026-10-29): the login page of an emailed temporary password that was not used in time.
TEMPORARY_EXPIRED = re.compile(
    r"(?:temporary|one.time) password (?:has |is |was )?(?:expired|no longer valid|invalid)", re.I
)
NOT_FOUND = re.compile(
    r"no account|account (?:was )?not found|not registered|couldn'?t find (?:an |your )?account|"
    r"does not correspond to a registered user|(?:user|account) (?:does not|doesn'?t) exist|no user (?:found|with)|"
    # TikTok's careers login refuses an unregistered address as "Incorrect email" (its account creation never finished,
    # 2026-10-12): every later run signed in again and stopped. "Incorrect email or password" is a refused password.
    r"(?:incorrect|invalid|unknown|unrecognized) e-?mail(?: address)?(?! (?:address )?(?:and|or|/) ?password)\b|"
    r"(?:we )?(?:can'?t|cannot|could ?n'?t|could not) find (?:an? |your |any )?(?:account|user|profile|candidate)|"
    r"no (?:account|profile|user|candidate)(?: was| is)? (?:found|exists|registered|associated|matches)|"
    r"(?:e-?mail|user ?name)(?: address)? (?:is |was )?not (?:recogni[sz]ed|registered|found|associated with an account)|"
    r"(?:does ?n'?t|does not|did not) (?:have|match) an? (?:existing )?account|not associated with (?:an|any) account",
    re.I,
)
EXISTS = re.compile(
    r"already (?:exists|registered|in use|associated|taken|have an account)|account (?:already )?exists|"
    r"account with this e-?mail|e-?mail (?:address )?(?:is )?already|user ?name (?:is )?(?:already|taken|not available)",
    re.I,
)
LOCKED = re.compile(
    r"account (?:is |has been |may be |might be |was )?(?:locked|disabled|suspended|blocked)|"
    r"too many (?:failed |unsuccessful |sign.?in |login )?(?:attempts|tries|requests)|temporarily (?:locked|disabled|blocked)|"
    r"try again (?:in|after) \d+ (?:minutes?|hours?)|exceeded the (?:maximum|number of) (?:login |sign.?in )?attempts|"
    # Amazon (2026-10-27): "Sorry, you can't log in right now due to unsuccessful login attempts".
    r"can'?t (?:log|sign) ?in right now|(?:due to|after) (?:\w+ )?(?:unsuccessful|failed) (?:log.?in|sign.?in) attempts",
    re.I,
)
# A lock that a password reset lifts. Auth0's brute-force block (iCIMS Central) is cleared by changing the password, and
# its page says so: "... after multiple consecutive login attempts. Reset your password to unblock your account".
RESET_UNLOCKS = re.compile(
    r"reset(?:ting)? (?:your |the )?password to (?:unblock|unlock)|"
    r"(?:unblock|unlock) (?:it|your account) by (?:resetting|changing) (?:your |the )?password|"
    r"blocked after (?:multiple|several|too many) consecutive (?:log.?in|sign.?in) attempts",
    re.I,
)
PASSWORD_RULES = re.compile(
    r"password (?:must|should|needs to|requirements)|at least \d+ characters|must contain|not meet.{0,40}requirements",
    re.I,
)
NOT_APPLICATION = re.compile(
    r"search|keyword|filter|sort by|language|cookie|subscribe|job alert|newsletter", re.I
)
# A job search's radius picker beside its postal-code box. XPO's job page (2026-10-21) showed only "Search by Postal
# Code" and "Distance", was read as a form with no way forward, and the guided agent took every run.
SEARCH_RADIUS = re.compile(r"\s*(?:distance|radius|within)\s*:?\s*", re.I)


# A reset whose email has not come within this time is unanswered: the employer mails within seconds when it can.
UNANSWERED_RESET_SECONDS = 600
# Owner 2026-10-27: password resets through the mailbox are always allowed. A reset that went unanswered or was refused
# is requested again after this long instead of waiting for the owner, and the per-employer caps count one day only.
RESET_RETRY_SECONDS = 6 * 3600
RESET_CODE_INLINE_WAIT = 150
RESET_WINDOW_SECONDS = 24 * 3600
PASSWORD_UNKNOWN = (
    "This employer has an account for your email but refuses the saved password, and its password reset email never "
    "arrives. Reset the password in your own browser, then save it under Setup > Accounts"
)
# The page after a reset request that the employer took. SuccessFactors' Password Help: "If an account matches the
# information you entered, then you will receive an email with instructions on how to reset your password." (REA of
# L3Harris's career-hcm20.ns2cloud.com, 2026-10-25).
RESET_SENT = re.compile(
    r"(?:will|should) (?:soon )?receive an? (?:e-?mail|link|message)|"
    r"e-?mail (?:with|containing) (?:instructions|a (?:reset |password )?link)|"
    r"(?:we(?:'ve| have)?|has been|have been|was|were) (?:just )?sent (?:you )?(?:an? )?(?:e-?mail|link|instructions)|"
    r"check your (?:e-?mail|inbox)|if (?:an|the|this|your) (?:e-?mail|account) (?:matches|exists|is registered)|"
    r"(?:reset|recovery) (?:link|instructions|e-?mail) (?:has been |was |is )?(?:sent|on (?:its|the) way)",
    re.I,
)
# How long the page after "Forgot your password?" may take to replace the sign-in form.
RESET_STEP_SECONDS = 10
# A reset form that takes the new password beside the email: TikTok's careers site (2026-10-27) opens /reset-password
# with "Enter your email", "Set your password" and Continue. Its password box read as the sign-in form still shown, so
# four runs reported that the reset page "did not open". The page names itself a reset by its address or its wording.
RESET_FORM_PATH = re.compile(r"/(?:reset|forgot|forgotten|recover)[-_]?(?:your[-_]?)?password", re.I)
RESET_FORM_TEXT = re.compile(r"\b(?:reset|forgot(?:ten)?|recover)\s+(?:your\s+)?password\b", re.I)
RESET_SUBMIT = re.compile(r"reset(?: (?:my|your))? password", re.I)
# Reset requests the employer's page never confirmed; after these, the owner resets the password.
UNCONFIRMED_RESETS = 3
RESET_UNCONFIRMED = (
    "This employer refuses the saved password, and its password reset form never confirmed a reset request. Reset "
    "the password in your own browser, then save it under Setup > Accounts"
)


def reset_proven(record):
    """The employer's page confirmed a reset request for this account (request_reset saw it). Only then does a reset
    email that never came say anything about the account. L3Harris, Farmers, Westinghouse, Hershey's and Belden
    (SuccessFactors, 2026-10-21..25) recorded resets that the sign-in page never sent, and every later run stopped
    for an owner action built on that silence."""
    return bool((record or {}).get("reset_submitted_at"))


def unanswered_reset_record(record):
    """A reset the employer's page confirmed that nothing answered in UNANSWERED_RESET_SECONDS."""
    return bool(
        (record or {}).get("state") in {"reset_requested", "credentials_rejected", "password_unknown"}
        and reset_proven(record)
        and time.time() - float(record.get("reset_requested_at") or 0) > UNANSWERED_RESET_SECONDS
    )


# The one Create after unanswered resets is offered again after this long: Lenovo's (2026-10-28 22:43) was spent by a
# run the model provider stopped before its registration was sent, and every later run requested another reset.
ABSENT_CREATE_RETRY_SECONDS = 2 * 3600


def create_spent(record):
    """The one Create after unanswered resets was tried within ABSENT_CREATE_RETRY_SECONDS."""
    at = (record or {}).get("absent_create_at")
    if not at:
        return False
    try:
        return time.time() - datetime.fromisoformat(at).timestamp() < ABSENT_CREATE_RETRY_SECONDS
    except (TypeError, ValueError):
        return True


# Account states meaning "an account exists here": sign in rather than create.
KNOWN_ACCOUNT = {
    "authenticated",
    "verification_pending",
    "reset_requested",
    "password_reset",
    "exists",
    "locked",
    "credentials_rejected",
    "password_unknown",
}



async def enter_code(boxes, code, *, timeout=4000):
    """Put a one-time code into its field, or one digit per box of a split code. Split boxes are typed as key events:
    Marriott's six-box Oracle code showed every filled digit, but its component registered none and Verify did nothing
    (2026-10-25)."""
    if len(boxes) == 1:
        await boxes[0].fill(code, timeout=timeout)
        return
    for box, digit in zip(boxes, code):
        await box.click(timeout=timeout)
        await box.fill("", timeout=timeout)
        await box.press_sequentially(digit, delay=60, timeout=timeout)

class Step(StrEnum):
    JOB = "job_details"
    METHOD = "apply_method"
    EMAIL_ENTRY = "email_entry"
    SIGN_IN = "sign_in"
    CREATE_ACCOUNT = "create_account"
    VERIFY_LINK = "verify_link"
    EMAIL_CODE = "email_code"
    RESET_PASSWORD = "reset_password"
    NEW_PASSWORD = "new_password"
    CONSENT = "consent"
    FORM = "form"
    REVIEW = "review"
    CONFIRMATION = "confirmation"
    ALREADY_APPLIED = "already_applied"
    CLOSED = "closed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class Adapter:
    id = "custom"
    name = "Career site"
    max_steps = 45
    # Labels that mean "submit the whole application" on this ATS.
    final = FINAL
    next_ids: tuple[str, ...] = ()
    back_ids: tuple[str, ...] = ()
    apply_ids: tuple[str, ...] = ()
    # Element ids of the email step's own submit, whose label varies by employer.
    email_submit_ids: tuple[str, ...] = ()
    # How long a questionnaire page without questions or a way forward is given to open its pop-up.
    ASSESSMENT_SETTLE_SECONDS = 2.5

    def __init__(self, engine, ats_id=None, name=None):
        if ats_id:
            self.id = ats_id
        if name:
            self.name = name
        self.e = engine
        self.service, self.config = engine.service, engine.config
        self.job, self.run_record = engine.job, engine.run_record
        self.accounts = Accounts(self.service, engine.profile, self.job["url"])
        self.inbox = self.service.inbox
        self.signatures = Counter()
        self.sig_visits = Counter()
        self.attempts = Counter()
        self.step_no = 0
        self.challenge_since = time.time() - 90
        self.challenge = None  # The open mail request window (MailService challenge).
        self.committed = False
        self.before_commit = None
        self.commit_pages = set()  # Signatures of pages whose final control was clicked.
        self.post_commit_steps = 0
        self.apply_url = None
        self.last_step = None

    # ----- plumbing -----------------------------------------------------------------------------
    @property
    def form(self):
        return self.e.form

    @property
    def page(self):
        return self.e.form.page

    def emit(self, kind, message, data=None):
        self.e.emit(kind, message, data)

    def site_lock(self, obs):
        """A result when the page says the site has locked this applicant out for a while, else None."""
        return None

    def defer(self, reason, reviews=None, state="needs_review", *, blocker_type=None):
        reviews = reviews or []
        if (
            state == "needs_review"
            and self.config.browser_cdp_url
            and reviews
            and all(r.get("key") in {"session", "manual"} for r in reviews)
        ):
            # With the desktop Chrome, a browser-fixable blocker keeps its tab open for you.
            return {
                "state": "waiting_browser",
                "reason": reason[:900] + ". The browser tab is retained for recovery.",
                "reviews": reviews,
            }
        result = {"state": state, "reason": reason[:1000], "reviews": reviews}
        if blocker_type:
            result["blocker_type"] = blocker_type
        if state == "waiting_browser" and not reviews and blocker_type not in {"account", "email_wait"}:
            result["recovery_cause"] = "control_observation"
        return result

    @staticmethod
    def prepare_url(url):
        return url

    async def dismiss_overlays(self):
        await dismiss_cookies(self.page, authorized=self.form.resolver.profile.accept_all_application_terms)

    def handoff(self, url):
        """Career sites hand the application to the real ATS (Phenom -> Workday, site -> Taleo)."""
        from ..ats import detect
        from . import REGISTRY

        if self.in_assessment():
            return None  # A questionnaire "Secured by ADP" on adp.com is not a handoff to the ADP application adapter.
        current = detect(url).id
        if current != self.id and current in REGISTRY and not self.committed:
            return current
        return None

    def signature(self, step, obs):
        from ..browser_contracts import fingerprint

        return step.value + ":" + fingerprint(obs)

    def app_fields(self, obs):
        return [
            f
            for f in obs["fields"]
            if f["type"] == "file"
            or (
                not NOT_APPLICATION.search(f["label"])
                and not SEARCH_RADIUS.fullmatch(f["label"])
                and f["label"] != "Unlabelled field"
            )
        ]

    def buttons(self, obs, pattern, ids=()):
        hits = [
            b
            for b in obs["controls"]
            if (b["automation_id"] and b["automation_id"] in ids)
            or pattern.search(b["label"])
            or (b["aria_label"] and pattern.search(b["aria_label"]))
        ]
        return hits

    def find_next(self, obs):
        # A job-board listing's "Next page" (with "Previous page" and 1/2/3) pages
        # through other postings; it is not this application's Next (Veryable).
        labels = [c.get("label", "").strip().lower() for c in obs.get("controls", [])]
        pagination = "previous page" in labels or sum(label.isdigit() for label in labels) >= 2
        hits = [
            b
            for b in self.buttons(obs, NEXT, self.next_ids)
            if not self.final.search(b["label"])
            and not re.search(r"^back|previous|cancel|save (?:as )?draft", b["label"], re.I)
            and not (pagination and b["label"].strip().lower() == "next page")
        ]
        if not hits:
            return None
        rank = ["save and continue", "save & continue", "continue", "next", "review"]

        def score(b):
            label = b["label"].lower()
            return (
                0 if b["automation_id"] in self.next_ids else 1,
                next((i for i, r in enumerate(rank) if label.startswith(r)), len(rank)),
            )

        best = sorted(hits, key=score)
        top = [b for b in best if score(b) == score(best[0])]
        return top[-1]  # bottom navigation when duplicated top/bottom

    def find_final(self, obs):
        hits = [
            b
            for b in obs["controls"]
            if self.final.search(b["label"])
            and not b.get("stepper")  # Dayforce's progress step "Submit" is not the button (2026-10-16)
            and not re.search(
                r"code|search|feedback|question|draft|resume only|cancel|request|privacy|cookie",
                b["label"],
                re.I,
            )
        ]
        labels = {b["label"].strip().lower() for b in hits}
        if not hits:
            return None
        if len(labels) == 1:
            return hits[-1]
        return None

    async def observed_final(self, obs):
        if final := self.find_final(obs):
            return final
        observe = getattr(self.form, "application_submit_controls", None)
        prepared = await observe() if observe else []
        return prepared[0] if len(prepared) == 1 else None

    def find_apply(self, obs):
        hits = [
            b
            for b in obs["controls"]
            if (
                b["automation_id"] in self.apply_ids
                or APPLY.search(b["label"])
                or APPLY.search(b["aria_label"])
            )
            and not NOT_APPLY.search(b["label"] + " " + b["href"])
        ]
        if not hits:
            return None
        preferred = [b for b in hits if b["automation_id"] in self.apply_ids]
        return (preferred or hits)[0]

    def code_fields(self, obs):
        from ..email_browser import autocomplete_code_field

        frames = getattr(getattr(self, "form", None), "frames", None) or {}

        def embedded(f):
            # Wipro's SuccessFactors job page (2026-10-26) embeds a YouTube player; a field in it was taken for the
            # code box and the code was refused as "on an unexpected website". Media, map, chat and captcha frames are
            # never the employer's verification step.
            frame = frames.get(f["id"])
            return bool(frame) and bool(EMBED_FRAME.search(urlsplit(getattr(frame, "url", "") or "").hostname or ""))

        codes = [
            f
            for f in obs["fields"]
            if f["type"] in {"text", "tel", "number", "password"}
            and not embedded(f)
            and (
                CODE_LABEL.search(code_label(f["label"]))
                or autocomplete_code_field(f, obs["text"])
            )
        ]
        if not codes and CODE_TEXT.search(obs["text"]):
            boxes = [f for f in obs["fields"] if f["type"] in {"text", "tel", "number"} and not embedded(f)]
            digits = [f for f in boxes if f["maxlength"] == 1]
            if not 4 <= len(digits) <= 10:
                # react-code-input lets every box but the last take the whole code, so a paste fills them: TikTok's
                # six reset-code boxes read maxlength 6,6,6,6,6,1, were never seen as a code step, and the run waited
                # for a reset link while the code mail sat unmatched (2026-10-28).
                for size in range(4, 11):
                    group = [f for f in boxes if f["maxlength"] in {1, size}]
                    if (
                        len(group) == size
                        and sum(f["maxlength"] == 1 for f in group) == 1
                        and len({f["type"] for f in group}) == 1
                    ):
                        digits = group
                        break
            if 4 <= len(digits) <= 10:
                codes = digits
        return codes

    # ----- classification -----------------------------------------------------------------------
    def classify(self, obs):
        dialog = self.dialog_view(obs)
        if dialog is not None:
            # An open dialog (reset password, sign in, terms) is the step, not the page behind it.
            return self.classify_page(dialog)
        return self.classify_page(obs)

    @staticmethod
    def dialog_view(obs):
        """The observation restricted to the top-most open modal dialog, if it holds controls."""
        dialogs = [
            d
            for d in obs.get("dialogs", [])
            if d.get("field_ids")
            or (d.get("control_ids") and not COOKIE_NOTICE.search(str(d.get("text", "")) + " " + str(d.get("title", ""))))
        ]
        if not dialogs:
            return None
        top = dialogs[-1]
        fields = [f for f in obs["fields"] if f["id"] in top["field_ids"]]
        controls = [c for c in obs["controls"] if c["id"] in top["control_ids"]]
        if not fields and not [
            c for c in controls if not re.fullmatch(r"close|x|×|cancel", c["label"], re.I)
        ]:
            return None
        return {
            **obs,
            "fields": fields,
            "controls": controls,
            "text": top.get("text", ""),
            "headings": [top["title"]] if top.get("title") else [],
            "automation": top.get("automation", []),
            "dialog": True,
        }

    def in_assessment(self):
        """The tab left the application for the tax-credit questionnaire its launcher opened. Any page of this ATS is the
        application itself, even under another host of it (Workday's myworkdaysite.com)."""
        from ..ats import detect

        form = getattr(getattr(self, "e", None), "form", None)
        host = getattr(self, "assessment_from", "") or getattr(form, "assessment_origin", "")
        if not host or getattr(self, "committed", False):
            return False
        url = self.page.url
        if (urlsplit(url).hostname or "") == host:
            return False
        return self.id == "custom" or detect(url).id != self.id

    def assessment_launcher(self, obs, again=False):
        """The control that opens the tax-credit questionnaire this application requires: once per run, and once more
        when the application still reports the step incomplete after the questionnaire returned (`again`)."""
        attempts = getattr(self, "attempts", None)
        if getattr(self, "committed", False) or attempts is None or attempts["assessment"] >= (2 if again else 1):
            return None
        for control in obs.get("controls", []):
            label = control["label"].strip()
            if (
                control.get("automation_id") == "inlineAssessmentButton"
                or control.get("partner")  # Oracle's inline partner button, named by its block when it has no text
                or re.fullmatch(LAUNCHER, label, re.I)
            ):
                if TAX_CREDIT.search(label) or TAX_CREDIT.search(obs.get("text", "")):
                    return control
        return None

    async def launch_assessment(self, obs, again=False):
        """Open the questionnaire before the application's own submit: Rentokil's Workday answered Save and Continue
        past an untaken one with a server error (2026-10-24), and Community Health Systems' Oracle refused Submit with
        "This info is required" while its Tax Credits section was untaken (2026-10-28)."""
        take = self.assessment_launcher(obs, again)
        if not take:
            return False
        self.attempts["assessment"] += 1
        self.assessment_from = urlsplit(self.page.url).hostname or ""
        self.emit("assessment", "Opening the employer's tax-credit questionnaire", {"control": take["label"][:40]})
        await self.dismiss_overlays()
        # A launcher by its automation id or label: the run returns to this tab when a survey tab closes itself, and to
        # this address when the questionnaire replaced the page.
        await self.form.click(take["id"], step_control=True, launch=True)
        await self.form.settle(timeout=6)
        return True

    async def on_assessment(self):
        """One page of the tax-credit questionnaire: fill it from the profile, then its own Next/Submit/Finish."""
        self.attempts["assessment_pages"] += 1
        if self.attempts["assessment_pages"] > 40:
            return {
                "state": "waiting_browser",
                "reason": "The tax-credit questionnaire did not return to the application",
                "blocker_type": "control_failure",
                "recovery_cause": "control_observation",
            }
        before = await self.form.scan()
        report = await self.fill_page()
        if isinstance(report, dict) and report.get("state"):
            return report
        obs = await self.form.scan()
        controls = [c for c in obs["controls"] if not ASSESSMENT_EXIT.search(c["label"])]
        forward = next((c for c in controls if ASSESSMENT_FORWARD.fullmatch(c["label"].strip())), None) or next(
            (c for c in controls if ASSESSMENT_RETURN.fullmatch(c["label"].strip())), None
        )
        if not forward and question_set(obs) != question_set(before):
            # An answer that posts the page back moves the questionnaire on by itself: MAXIMUS's survey loads its next
            # question when a radio is clicked and never shows Next (Allison Transmission, PepsiCo 2026-10-25).
            self.emit("assessment", "The questionnaire moved to its next question")
            return None
        if not forward and not obs["fields"] and SURVEY_DONE.search(obs.get("text", "")):
            # Finished on its own host ("Thank you ... you may close this window"): back to the application.
            if await self.form.return_from_questionnaire():
                return None
        if not forward and not self.attempts["assessment_settle"]:
            # A page can open an informational pop-up after it renders: ADP's "Quick tip before you begin ... Okay" hid
            # Montenson's 12 questions and its Next, and this verdict came 1.9 s after the previous Next (2026-10-28).
            # One settled rescan, which acknowledges such a pop-up, before giving up.
            self.attempts["assessment_settle"] += 1
            await asyncio.sleep(self.ASSESSMENT_SETTLE_SECONDS)
            await self.form.settle(timeout=4)
            if question_set(await self.form.scan()) != question_set(obs):
                return None
            return await self.on_assessment()
        if not forward:
            return {"fallback": True, "reason": "No way forward in the tax-credit questionnaire"}
        self.emit("assessment", "Continuing the tax-credit questionnaire", {"control": forward["label"][:40]})
        self.attempts["assessment_settle"] = 0  # Each page gets its own settled rescan.
        # Not the application's submit: the tab is on the questionnaire's own host, which classify_page and commit
        # never treat as the application. Its "Submit form" passes the page guard like an account control.
        await self.dismiss_overlays()
        try:
            await self.form.click(forward["id"], auth_control=True, step_control=True)
            await self.form.settle(timeout=6)
        except PlaywrightError:
            if not self.form.page.is_closed():
                raise
        if self.form.page.is_closed():
            # TaxCreditCo closes its own tab after the final submit (Tyson Foods 2026-10-24).
            await self.form.return_to_opener()
            await self.form.settle(timeout=6)
        return None

    async def intermediate_submit(self):
        """The guided flow's submit while the questionnaire is open finishes the questionnaire, not the application.
        Tyson Foods (2026-10-24): it committed on the questionnaire's "Submit form", the survey tab closed, and the
        unsubmitted application was recorded as an unverifiable submission."""
        import json

        if not self.in_assessment():
            return None
        result = await self.on_assessment()
        outcome = {"executed": result is None, "intermediate_step": True, "label": "tax-credit questionnaire"}
        if isinstance(result, dict):
            outcome["reason"] = str(result.get("reason", ""))[:200]
            if result.get("state"):
                # A terminal outcome (questions for the owner, the page limit) ends the run like any other; the guided
                # flow stops on the engine's result at its next step.
                self.e.result = result
        return json.dumps(outcome)

    def classify_page(self, obs):
        if self.in_assessment():
            return Step.FORM  # Its Submit and thank-you page belong to the questionnaire, never the application.
        text = obs["text"]
        fields = self.app_fields(obs)
        # A returning applicant's login box beside the application form is not a sign-in step (scan.js sideLogin).
        passwords = [f for f in obs["fields"] if f["type"] == "password" and not f.get("side_login")]
        meaningful = [f for f in fields if f["type"] != "checkbox" and not f.get("side_login")]
        # A job page saying "thank you for your interest" still has an Apply button.
        if (
            confirmation_wording(text, self.committed)
            and len(meaningful) <= 1
            and not passwords
            and (self.committed or not self.find_apply(obs))
        ):
            return Step.CONFIRMATION
        if ALREADY.search(text) and not passwords and len(meaningful) <= 1 and not self.find_apply(obs):
            return Step.ALREADY_APPLIED
        if not self.committed and APPLICATION_LIMIT.search(text):
            return Step.CLOSED  # The form may still show; the employer refuses any further application (on_closed).
        codes = self.code_fields(obs)
        if codes and (self.committed or dedicated_code_step(obs["fields"], codes)):
            return Step.BLOCKED if SMS.search(text) else Step.EMAIL_CODE
        if (
            not passwords
            and FORGOT.search(text + " " + " ".join(obs.get("headings", [])))
            and [
                f
                for f in meaningful
                if f["type"] == "email" or re.search(r"e-?mail|user ?name", f["label"], re.I)
            ]
            and self.auth_button(obs, "reset")
        ):
            return Step.RESET_PASSWORD
        if len(passwords) == 1 and self.one_page_reset(obs) and self.auth_button(obs, "reset"):
            # TikTok's /reset-password (2026-10-27): Email, "Set your password", Continue. Read as a sign-in page, a
            # reopened run typed the old password into it and stopped at the website's picture puzzle.
            return Step.RESET_PASSWORD
        if self.new_password_step(obs):
            return Step.NEW_PASSWORD
        if passwords:
            # Some real sign-up forms ask once and retain a Sign in link in the header.
            create_path = re.search(
                r"/(?:create[-_]account|sign[-_]?up|register)(?:/|$)", urlsplit(obs.get("url", "")).path, re.I
            )
            create_control = any(
                re.fullmatch(r"create(?: (?:an? )?(?:account|profile))?|sign up|register", b["label"], re.I)
                and not b.get("href")
                for b in obs["controls"]
            )
            new_password = any(f.get("autocomplete") == "new-password" for f in passwords)
            return (
                Step.CREATE_ACCOUNT
                if len(passwords) >= 2 or create_path or (create_control and new_password)
                else Step.SIGN_IN
            )
        if CHECK_EMAIL.search(text) and len(meaningful) <= 1 and not self.find_apply(obs):
            return Step.VERIFY_LINK
        if not meaningful and CLOSED.search(text) and not self.find_apply(obs):
            return Step.CLOSED
        emails = [f for f in meaningful if f["type"] == "email" or re.search(r"e-?mail", f["label"], re.I)]
        if (
            emails
            and len(meaningful) == len(emails)
            and (self.find_next(obs) or self.buttons(obs, AUTH_BUTTON))
        ):
            return Step.EMAIL_ENTRY
        if meaningful or any(f["type"] == "file" for f in fields):
            return Step.FORM
        if self.find_final(obs) and not self.find_apply(obs):
            return Step.REVIEW
        if self.find_apply(obs):
            return Step.JOB
        if [
            b
            for b in obs["controls"]
            if re.search(r"^(?:i )?(?:accept|agree)(?: and continue)?$", b["label"], re.I)
        ]:
            return Step.CONSENT
        if self.find_next(obs):
            return Step.FORM
        return Step.UNKNOWN

    # ----- main loop ----------------------------------------------------------------------------
    async def drive(self):
        suspended_email = False
        try:
            return await self._drive()
        except MailPending:
            # BrowserEngine records the durable wait only after this exception
            # reaches it. Its result is still empty during our finally block;
            # cancelling here destroys the request before later mail can match.
            suspended_email = True
            raise
        finally:
            if not suspended_email:
                self.release_mail()

    async def _drive(self):
        # SuccessFactors and iCIMS have their own continuation handlers (the latter targets its Central origin).
        # All other adapters must reclaim the existing request before trying another sign-in or reset.
        if self.id not in {"successfactors", "icims"} and self.reset_mail_continuation():
            if not await self.arm_mail("password_reset"):
                return self.lease_busy()
            if not self.challenge:
                return self.defer("The saved password reset request could not be recovered", state="waiting_browser",
                                  blocker_type="email_wait")
            if result := await self.complete_password_reset(self.accounts.store.canonical(self.job["url"])):
                return result
        for _ in range(self.max_steps):
            recover = getattr(self.e, "recover_workday_error", None)
            if recover and await recover():
                continue
            if getattr(self.e, "result", None):
                return self.e.result
            await self.form.settle(timeout=10)
            await self.dismiss_overlays()
            if await self.captcha_open():
                # An open challenge (e.g. hCaptcha after an email step) is solved before anything
                # else; the scanner never shows CAPTCHA frames as fields or controls.
                if challenge := await self.solve_captcha():
                    return challenge
            obs = await self.form.scan()
            from ..browser_contracts import missing_posting_notice

            if not self.committed and missing_posting_notice(obs, self.job["url"]):
                return await self.on_closed(obs)
            shell_recovery = getattr(self.e, "recover_oracle_job_shell", None)
            if shell_recovery and await shell_recovery(obs):
                continue
            if await self.e.refresh_expired_entry(obs):
                continue
            moved = self.handoff(obs["url"])
            if moved:
                return {"handoff": moved}
            # Modal precedence: an open dialog is the current step; act only on its controls.
            obs = self.dialog_view(obs) or obs
            step = self.classify_page(obs)
            if step != Step.EMAIL_ENTRY:
                self.pending_auth_transition_source = None
            if self.committed and step not in {Step.CONFIRMATION, Step.ALREADY_APPLIED}:
                self.post_commit_steps += 1
                if self.post_commit_steps > 4:
                    return {
                        "state": "submission_unknown",
                        "reason": "Submitted, but no confirmation page appeared",
                    }
            sig = self.signature(step, obs)
            self.signatures[sig] = (
                self.signatures[sig] + 1 if sig == getattr(self, "last_signature", None) else 1
            )
            self.last_signature = sig
            self.step_no += 1
            await self.e.trace(self.step_no, step.value, obs)
            # JazzHR (10-27): the page alternated between three or four states (steps 3-45 hashed to four
            # observations), so the consecutive count never passed 1 and the loop ran all 45 steps over 160 s.
            self.sig_visits[sig] += 1
            if self.signatures[sig] > 3 or self.sig_visits[sig] > 6:
                return self.defer(
                    f"No progress on the {step.value.replace('_', ' ')} step: " + "; ".join(obs["errors"][:3])
                )
            if step != self.last_step:
                self.emit(
                    "step", f"{self.name}: {step.value.replace('_', ' ')}", {"url": self.e.redact(obs["url"])}
                )
            self.last_step = step
            result = await getattr(self, "on_" + step.value)(obs)
            if isinstance(result, dict):
                return result
        return self.defer("The application has more steps than the configured limit")

    def reset_mail_continuation(self):
        """Only resume a reset request made by an earlier run of this job."""
        handle = (getattr(self, "run_record", None) or {}).get("packet", {}).get("email_wait_resume", {})
        if (handle.get("kind") != "password_reset" or not handle.get("source_run_id")
                or not handle.get("challenge_id") or not self.accounts.store):
            return False
        from ..db import Run

        with self.service.db.session() as session:
            source = session.get(Run, handle["source_run_id"])
            return bool(source and source.job_id == self.job["id"])

    # ----- step handlers ------------------------------------------------------------------------
    async def on_job_details(self, obs):
        button = self.find_apply(obs)
        if not button:
            return {"fallback": True, "reason": "No apply control found"}
        self.attempts["apply"] += 1
        if self.attempts["apply"] > 3:
            return self.defer("The apply button did not open an application")
        try:
            await self.click(button)
        except ValueError as exc:
            if "stale" not in str(exc).lower():
                raise
            # Oracle re-renders its job page after the first scan (Verisk, Dallas County 2026-10-23: "Control is
            # stale" 7 s in, then a retry after backoff). The Apply control is found again and clicked once.
            button = self.find_apply(await self.form.scan())
            if not button:
                return {"fallback": True, "reason": "No apply control found"}
            await self.click(button)
        self.apply_url = self.page.url

    async def on_apply_method(self, obs):
        return {"fallback": True, "reason": "Unknown application method chooser"}

    async def on_consent(self, obs):
        buttons = [b for b in obs["controls"] if re.search(r"^(?:i )?(?:accept|agree)", b["label"], re.I)]
        for f in obs["fields"]:
            if f["type"] == "checkbox" and f["value"] != "true":
                await self.form.fill(f, "true")
        if not buttons:
            return {"fallback": True}
        await self.click(buttons[-1])

    def email_submit(self, obs):
        """The email step's own submit: Next, a platform's known submit id (its label varies by employer: iCIMS's
        "Next" or Schwab's "I Acknowledge the Privacy Notice"), else a generic account button."""
        known = [b for b in obs["controls"] if b.get("html_id") and b["html_id"] in self.email_submit_ids]
        return self.find_next(obs) or (known[0] if known else None) or next(iter(self.buttons(obs, AUTH_BUTTON)), None)

    async def email_agreement_answer(self, field):
        return (await self.form.resolver.resolve([field]))[0]

    async def on_email_entry(self, obs):
        if not self.accounts.email:
            return self.defer("Set ACCOUNT_EMAIL (or your profile email) so I can identify you to employers")
        self.attempts["email_entry"] += 1
        # Microsoft careers: "We couldn't find an account with that username" -> sign up.
        shown = " ".join(str(e) for e in obs.get("errors", []))
        if (
            self.attempts["email_entry"] > 1
            and NOT_FOUND.search(shown)
            and self.accounts.may_create
            and self.attempts["create"] == 0
            and await self.switch(obs, CREATE_LINK)
        ):
            return None
        if self.attempts["email_entry"] > 2:
            return self.defer("The employer did not accept the email address step")
        seen = set()

        async def agree(fields):
            """Tick each consent box the resolver accepts; return a deferral for a required one it cannot."""
            for f in fields:
                if f["id"] in seen:
                    continue
                if f["type"] != "checkbox":
                    if (
                        f.get("required")
                        and f.get("options")
                        and not f.get("value")
                        and f["type"] in CONSENT_MENUS
                        and CONSENT_LABEL.search(f["label"])
                    ):
                        # A consent asked as a menu (Atlassian's "By selecting continue ... you acknowledge"). Other
                        # menus on the step, such as Schwab's phone country code, are not agreements.
                        seen.add(f["id"])
                        answer = await self.email_agreement_answer(f)
                        if answer.disposition != "answer":
                            return self.defer(
                                "A required agreement on the email step needs your approval", [self.review_for(f)]
                            )
                        try:
                            await self.form.fill(f, answer.value)
                        except (PlaywrightError, ValueError):
                            return self.defer(
                                "A required agreement on the email step could not be selected", [self.review_for(f)]
                            )
                    continue
                if f["value"] == "true":
                    continue
                seen.add(f["id"])
                answer = await self.email_agreement_answer(f)
                if answer.disposition == "answer" and answer.value == "true":
                    await self.form.fill(f, "true")
                elif answer.disposition == "answer" and answer.value == "false":
                    continue  # A conditional box that does not apply stays unchecked.
                elif f["required"]:
                    return self.defer(
                        "A required agreement on the email step needs your approval", [self.review_for(f)]
                    )
            return None

        for f in obs["fields"]:
            # A box such as "Email me new jobs" names email too; it is an agreement, not the address (GuideWell's
            # Oracle email step 2026-10-26: "Checkbox answer must specify checked or unchecked").
            if f["type"] == "email" or (
                re.search(r"e-?mail", f["label"], re.I) and f["type"] in {"text", "search", "", None}
            ):
                await self.form.fill(f, self.accounts.email)
        if deferred := await agree(obs["fields"]):
            return deferred
        if not await self.arm_mail():
            return self.lease_busy()
        # Accepting Oracle's legal dialog recreates the entry controls.
        target = None
        for _ in range(20):
            obs = await self.form.scan()
            # Iridium (iCIMS) 2026-10-21: ticking the privacy-notice box reveals an "I accept" box; Next stays
            # disabled until it is ticked, and the step fell back to the guided flow without it.
            if deferred := await agree(obs["fields"]):
                return deferred
            target = self.email_submit(obs)
            if target:
                break
            await asyncio.sleep(0.2)
        if not target:
            return {"fallback": True}
        # A fast redirect can reach its account page before CAPTCHA polling
        # starts. Retain only this action's source and armed-request identity.
        self.auth_transition_source = (
            {"url": obs["url"], "stage": Step.EMAIL_ENTRY, "challenge_id": self.challenge["id"]}
            if self.challenge
            and self.challenge.get("id")
            and obs["url"] == self.page.url
            and self.classify_page(obs) == Step.EMAIL_ENTRY
            else None
        )
        try:
            await self.click(target, auth=True)
            if challenge := await self.after_auth_click():
                return challenge
            # Some providers mount after the immediate half-second poll.
            # Keep this request-bound source for the next loop's solver.
            self.pending_auth_transition_source = self.auth_transition_source
        finally:
            self.auth_transition_source = None
        await self.after_auth_dialogs()

    async def after_auth_dialogs(self):
        """Terms/privacy modals that must be accepted before continuing (Oracle, iCIMS)."""
        await self.form.settle(timeout=4)
        obs = await self.form.scan()
        agree = [
            b
            for b in obs["controls"]
            if re.fullmatch(r"(?:i )?(?:agree|accept)(?: and continue)?", b["label"], re.I)
        ]
        if agree and len(self.app_fields(obs)) <= 2:
            await self.click(agree[-1], auth=True)

    async def on_sign_in(self, obs):
        return await self.authenticate(obs, "sign_in")

    async def on_create_account(self, obs):
        return await self.authenticate(obs, "create")

    async def on_verify_link(self, obs):
        if not self.inbox.configured:
            return self.defer(
                "This employer emailed a verification link. Connect Gmail (GMAIL_APP_PASSWORD)."
            )
        if not self.mail_rule_known:
            return await self.discover_verification()
        self.attempts["verify_link"] += 1
        if self.attempts["verify_link"] > 2:
            return self.defer("The employer kept asking for email verification")
        token = await self.wait_email("link")
        if not token:
            return self.defer(self.mail_problem("verification link"))
        if token["kind"] != "link":
            self.finish_mail("failed", "Expected a verification link, received a code")
            return self.defer("The employer sent a code where the page expected a link")
        await self.open_link(token["value"])
        self.finish_mail("verified", "Website accepted the verification link")
        self.accounts.mark(self.page.url, "authenticated")
        self.emit("email_verified", "Employer email verification completed")
        await self.form.settle(timeout=8)
        obs = await self.form.scan()
        if self.classify(obs) in {Step.CONFIRMATION, Step.UNKNOWN, Step.VERIFY_LINK} or not obs["fields"]:
            await self.return_to_application()

    async def request_code(self, obs):
        """A code step that mails its code on demand: an empty email box beside the code box and a "Get code" button.
        ByteDance's sign-up (2026-10-27) showed exactly that after "Create account"; its email box was never typed into
        and "Get code" (disabled until then) never pressed, so each run waited 20 minutes for a code nobody had sent.
        True when the code was requested, a defer/challenge dict when that could not be done safely, else False."""
        view = self.dialog_view(obs) or obs
        codes = {f["id"] for f in self.code_fields(view)}
        empty = [
            f for f in view["fields"]
            if f["id"] not in codes and self.is_login_field(f) and not str(f.get("value") or "").strip()
        ]
        disabled = getattr(self.form, "meta", {}).get("disabled_actions", []) or []
        offered = [b["label"] for b in view["controls"]] + list(disabled)
        if not codes or not empty or not self.accounts.email or not any(SEND_CODE.search(x.strip()) for x in offered):
            return False
        try:
            for f in empty:
                await self.form.locator(f["id"]).fill(self.accounts.email, timeout=4000)
                await self.form.locator(f["id"]).blur()
        except (PlaywrightError, TimeoutError):
            return False
        await self.form.settle(timeout=2)
        after = await self.form.scan()
        view = self.dialog_view(after) or after
        send = [
            b for b in view["controls"]
            if SEND_CODE.search(b["label"].strip()) or SEND_CODE.search(str(b.get("aria_label") or "").strip())
        ]
        if len(send) != 1:
            return False
        if not await self.arm_mail("code"):
            return self.lease_busy()
        await self.click(send[0], auth=True)
        self.emit("email_requested", "Typed the email into the code step and requested its code")
        if challenge := await self.after_auth_click():
            return challenge
        # ByteDance's "Get code" opens its picture puzzle ("Select 2 objects that are the same shape", 2026-10-28) and
        # mails the code only once it is solved; the run waited for a code that was never sent.
        solved = self.attempts["inpage_puzzle"]
        for _ in range(4):
            if blocked := await self.solve_inpage_puzzle():
                return blocked
            if self.attempts["inpage_puzzle"] != solved:
                break
            await asyncio.sleep(0.75)
        return True

    async def on_email_code(self, obs):
        if not self.inbox.configured:
            return self.defer("This employer emailed a one-time code. Connect Gmail (GMAIL_APP_PASSWORD).")
        if not self.mail_rule_known:
            return await self.discover_verification()
        self.attempts["email_code"] += 1
        if self.attempts["email_code"] > 2:
            return self.defer("The verification code was not accepted")
        requested = await self.request_code(obs)
        if isinstance(requested, dict):
            return requested
        if requested:
            obs = await self.form.scan()
        # A persisted browser session can reopen an old PIN page without sending
        # another message. A new wait window alone cannot cause delivery, and
        # MailPending would park before the fallback resend below ever executes.
        # Keep correlated continuations intact; only request a fresh code when
        # this owner has no live request and the page offers exactly one resend.
        resuming = getattr(self, "run_record", {}).get("packet", {}).get("email_wait_resume")
        if not self.inbox.live(self.challenge):
            # The guided controller armed this run's request when it clicked the control that sent the code. A resend
            # mails a second code, which can void the first and trips Oracle's 30-minute verification lock: every
            # Oracle code step sent two (2026-10-25).
            self.challenge = self.inbox.live_request(self.e.run_id) or self.challenge
        if not resuming and not self.inbox.live(self.challenge) and not self.attempts["entry_resend"]:
            resend = [
                b
                for b in obs["controls"]
                if not b.get("disabled")
                and re.search(r"resend|send (?:a )?new code|send again", b["label"], re.I)
            ]
            if len(resend) == 1:
                if not await self.arm_mail("code"):
                    return self.lease_busy()
                self.attempts["entry_resend"] += 1
                await self.click(resend[0], auth=True)
                self.emit("email_requested", "Requested a fresh code for the reopened verification page")
        token = await self.wait_email("code")
        if not token:
            resend = [
                b
                for b in obs["controls"]
                if re.search(r"resend|send (?:a )?new code|send again", b["label"], re.I)
            ]
            if resend and self.attempts["email_code"] == 1:
                self.release_mail()
                if not await self.arm_mail("code"):
                    return self.lease_busy()
                await self.click(resend[0], auth=True)
                token = await self.wait_email("code")
            if not token:
                return self.defer(self.mail_problem("verification code"))
        if token["kind"] != "code":
            await self.open_link(token["value"])
            self.finish_mail("verified", "Website accepted the verification link")
            return None
        obs = await self.form.scan()
        fields = self.code_fields(obs)
        code = token["value"]
        if not fields:
            self.finish_mail("failed", "Code field disappeared")
            return self.defer("A code arrived but the page no longer shows a code field")
        if len(fields) > 1 and len(fields) != len(code):
            self.finish_mail("failed", "Code length mismatch")
            return self.defer("The code length does not match the website")
        for i, f in enumerate(fields):
            frame_url = self.form.frames[f["id"]].url
            landing = urlsplit(getattr(self.e, "landing_url", "") or "")
            # The job link's own redirect target is the employer's site (Dell, Oracle 2026-10-23: six runs refused
            # their code on enterpriseplatform.dell.com and Oracle locked verification).
            landed = (
                landing.scheme == "https"
                and urlsplit(frame_url).scheme == "https"  # never a code over plain HTTP
                and bool(landing.hostname)
                and (urlsplit(frame_url).hostname or "").lower() == landing.hostname.lower()
            )
            if not landed and not self.accounts.password_allowed(self.id, frame_url, self.job["url"]):
                self.finish_mail("failed", "Code field on an unexpected website")
                host = urlsplit(frame_url).hostname or "an unknown host"
                return self.defer(f"The code field is on an unexpected website ({host}); not entering the code")
        await enter_code([self.form.locator(f["id"]) for f in fields], code, timeout=4000)
        await asyncio.sleep(0.3)
        obs = await self.form.scan()
        if self.code_fields(obs):
            confirm = [
                b
                for b in obs["controls"]
                if re.search(r"^(?:verify|confirm|continue|next|submit|sign in|log in)", b["label"], re.I)
            ]
            if confirm:
                await self.click(confirm[-1], auth=True)
        await self.form.settle(timeout=6)
        after = await self.form.scan()
        # A rejected or expired PIN often clears/remounts its inputs. That is
        # a changed signature, not successful authentication. Wait for the
        # dedicated code step to actually disappear before recording success.
        for _ in range(12):
            if await self.code_accepted(after):
                break
            if re.search(
                r"(?:code|pin).{0,40}(?:incorrect|invalid|expired)|(?:incorrect|invalid|expired).{0,40}(?:code|pin)",
                " ".join(after.get("errors", [])),
                re.I,
            ):
                break
            await asyncio.sleep(0.25)
            after = await self.form.scan()
        if not await self.code_accepted(after):
            self.finish_mail("failed", "Website did not accept the code")
            return None  # The loop sees the same step again and retries or defers.
        self.finish_mail("verified", "Website accepted the verification code")
        store = getattr(self.accounts, "store", None)
        if store and store.state(self.page.url) in {"created_locally", "signing_in"}:
            # SAP's passcode completes account creation (2026-10-16): later runs must sign in,
            # not press Create again for an account that now exists.
            self.accounts.mark(self.page.url, "authenticated")
        self.emit("email_verified", "Entered the emailed verification code")

    async def code_accepted(self, obs):
        """A remount/loading screen is not evidence that a one-use code worked."""
        if obs.get("busy") or self.code_fields(obs):
            return False
        if any(f["type"] == "password" for f in obs["fields"]):
            return False
        explicit = re.search(
            r"email (?:address )?(?:has been |is )?verified|verification (?:complete|successful)|"
            r"successfully (?:verified|signed in)|you are (?:now )?signed in",
            obs.get("text", ""),
            re.I,
        )
        if explicit:
            return True
        step = self.classify(obs)
        return (step in {Step.FORM, Step.REVIEW} and bool(self.app_fields(obs))) or (
            step == Step.CONFIRMATION and bool(await self.form.proof())
        )

    async def on_reset_password(self, obs):
        """A reset dialog/page that asks for the account email (e.g. after Forgot password). A rerun that reopens on it
        after this account's reset already went unanswered registers once instead: Bain's and MetLife's Avature runs
        resumed on /ResetPassword and requested another reset that never came, every time (2026-10-28)."""
        url = self.auth_url(obs)
        record = self.accounts.record(url)
        if (
            record.get("state") in {"reset_requested", "password_unknown", "credentials_rejected"}
            and not record.get("confirmed_at")
            and reset_proven(record)
            and time.time() - float(record.get("reset_requested_at") or 0) > UNANSWERED_RESET_SECONDS
            and not create_spent(record)
            and self.accounts.may_create
            and self.attempts["create"] == 0
            and await self.switch(obs, CREATE_LINK)
        ):
            self.accounts.mark(url, "no_account", self.accounts.saved(url) or self.accounts.credentials(url),
                               absent_create_at=now())
            self.emit("account", "The reset went unanswered; opened the registration route once")
            return None
        return await self.request_reset(obs, "The password reset was requested from this page")

    def new_password_step(self, obs):
        """An explicit password change page, distinguished from account registration."""
        passwords = [f for f in obs["fields"] if f["type"] == "password"]
        text = " ".join([str(obs.get("text") or ""), *obs.get("headings", [])])
        return bool(
            len(passwords) == 2
            and any(re.search(r"\bnew password\b", f.get("label", ""), re.I) for f in passwords)
            and not any(self.is_login_field(f) for f in obs["fields"])
            and re.search(
                r"\b(?:reset|change)\b.{0,50}\b(?:new )?password\b|"
                r"\bset up (?:your )?new credentials\b.{0,60}\b(?:log|sign) ?in\b", text, re.I
            )
        )

    async def on_new_password(self, obs):
        return await self.set_new_password(self.auth_url(obs), obs, forced=True)

    async def on_form(self, obs):
        from ..browser_contracts import semantic_state

        if self.in_assessment():
            return await self.on_assessment()
        report = await self.fill_page()
        if isinstance(report, dict) and report.get("state"):
            # The readback reports an untaken launcher as an incomplete required step (Mortenson, Oracle 2026-10-27):
            # the questionnaire is taken first, whatever else the page still needs. Once more after it returned: ADP
            # finished Montenson's questionnaire and returned to Oracle, whose partner block still read untaken until
            # the assessment was opened again (2026-10-29).
            if await self.launch_assessment(await self.form.scan(), again=True):
                return None
            return report
        obs = await self.form.scan()
        if await self.launch_assessment(obs):
            return None
        # authenticate() hands over application forms that embed account fields; those classify as account steps
        # (iCIMS's candidate profile: password boxes beside the resume and name fields, saved by Submit Profile).
        # Only those two steps extend what this handler owns: a page that became an emailed-code step, a review or a
        # confirmation while it was filled has its own handler in the main loop, not a Next click.
        embedded = {self.classify(obs)} & {Step.CREATE_ACCOUNT, Step.SIGN_IN}
        nxt = self.find_next(obs)
        final = await self.observed_final(obs)
        if not nxt and final:
            if await self.form.intermediate_submit(final["id"], obs):
                await self.click(final, step=True)
                return None
            return await self.on_review(obs, report)
        if not nxt:
            return {"fallback": True, "reason": "No way forward from this page"}
        # Review each step while its controls are still editable. Reviewing only
        # on a read-only final page turned every correction into Back navigation
        # through the whole wizard (Blackstone, BCBS-MA, NVIDIA).
        gate = getattr(self.e, "quality_gate", None)
        if gate and (quality_blocker := await gate(current_page=True)):
            return quality_blocker
        if gate:
            obs = await self.form.scan()
            nxt = self.find_next(obs)
            if not nxt:
                return {"fallback": True, "reason": "No way forward from this page"}
        page = semantic_state(obs)["page"]
        if challenge := await self.solve_captcha():
            return challenge
        # The website callback may replace the form with authentication or a
        # different step. Use that current page rather than the pre-CAPTCHA ID.
        obs = await self.form.scan()
        step = self.classify(obs)
        # The iCIMS profile was refused here on every pass (ICE, iPipeline 2026-10-19): it classifies as account
        # creation, so Submit Profile was never pressed and the third pass reported a failed sign-in.
        if step != Step.FORM and step not in embedded:
            return None
        if semantic_state(obs)["page"] != page:
            return None  # The callback submitted this page; the next one is filled before anything is clicked.
        nxt = self.find_next(obs)
        if not nxt:
            if await self.observed_final(obs):
                return await self.on_review(obs, report)
            return {"fallback": True, "reason": "No way forward after CAPTCHA acceptance"}
        before = self.signature(step, obs)
        await self.click(nxt, step=True)
        after = await self.form.scan()
        if self.signature(self.classify(after), after) == before:
            # Server-side validation: answer what it flagged, then try once more.
            if after["errors"] or any(f["error"] for f in after["fields"]):
                repaired = await self.repair(after)
                if isinstance(repaired, dict):
                    return repaired

    async def revisit_history(self, blocker):
        """Follow observed Back controls to the exact question, not a guessed page name."""
        from ..answers import normalize
        from ..browser_contracts import fingerprint

        if self.committed or getattr(self.e, "armed", False):
            return False
        pending = ((blocker or {}).get("report") or {}).get("pending", [])
        targets = []
        for item in pending:
            if item.get("repair_action") != "revisit_history_row" or not item.get("question"):
                continue
            scope = item.get("scope") or {}
            key = "quality_revisit:" + repr((item["question"], sorted(scope.items())))
            if not self.attempts[key]:
                targets.append((key, item, scope))
            else:
                # Reopened before and flagged again: the correction did not stay; it is not offered a third walk.
                from ..answer_quality import stop_revisit

                stop_revisit(self.form, item)
        if not targets:
            return False
        seen = set()
        for step in range(7):
            obs = await self.form.scan()
            matched = []
            for key, item, scope in targets:
                fields = [f for f in obs.get("fields", [])
                          if normalize(f["label"]) == normalize(item["question"])
                          and (not item.get("type") or f.get("type") == item["type"])
                          and all(normalize(str(f.get(k, ""))) == normalize(str(v))
                                  for k, v in scope.items() if v not in (None, ""))]
                if len(fields) == 1:
                    matched.append(key)
            if matched:
                for key in matched:
                    self.attempts[key] += 1
                self.emit("quality_repair", "Reopened the observed question owning the supported correction",
                          {"fields": len(matched), "back_steps": step})
                return True
            signature = fingerprint(obs)
            if signature in seen or step == 6:
                break
            seen.add(signature)
            back = [b for b in obs.get("controls", []) if not b.get("disabled") and
                    (b.get("automation_id") in self.back_ids or
                     re.fullmatch(r"back|previous(?: step)?", b.get("label", ""), re.I))]
            if len(back) != 1:
                break
            await self.click(back[0])
            await self.form.settle(timeout=4)
        # A failed traversal is not a reason to walk the same pages forever: the audit reports these rows as exhausted
        # (a stop that names the field) instead of repeating the repair (Montenson, Belden 2026-10-29).
        from ..answer_quality import stop_revisit

        for key, item, _ in targets:
            self.attempts[key] += 1
            stop_revisit(self.form, item)
        return False

    async def on_review(self, obs, report=None):
        if self.committed and self.signature(Step.REVIEW, await self.form.scan()) in self.commit_pages:
            # The submitted page is still showing: never click its Submit a second time.
            return {
                "state": "submission_unknown",
                "reason": "Submission attempted; the page did not change and it was not submitted again",
            }
        if report is None:
            report = await self.form.verify()
        if report.get("auth_required"):
            return self.defer("Employer sign-in must complete before application submission")
        problems = [
            p
            for p in report["problems"]
            if not p.startswith("Resume attachment") or self.form.resume_fields()
        ]
        if problems:
            filled = await self.fill_page()
            if isinstance(filled, dict) and filled.get("state"):
                return filled
            report = await self.form.verify()
            problems = [p for p in report["problems"] if not p.startswith("Resume attachment")]
            if problems:
                return self.defer(
                    "; ".join(problems[:6]),
                    report.get("pending"),
                    state="waiting_browser",
                    blocker_type="validation",
                )
        if (self.form.resume_fields() or self.form.resume_seen) and not self.form.upload_verified:
            # Checked for the whole application, not only the page that had the upload.
            return self.defer("Resume attachment was not accepted")
        if not self.form.upload_verified or not report.get("verified_fields"):
            # Same rule as BrowserEngine.finish: never start a final submit without an
            # attached resume and verified answers. Microsoft's apply page reported
            # zero verified fields and no resume, and the adapter still clicked Submit.
            return {
                "state": "waiting_browser",
                "reason": "Final submit needs an attached resume and verified answers; observing the application again",
                "blocker_type": "validation",
                "recovery_cause": "control_observation",
                "recoverable": True,
            }
        if self.run_record["mode"] == "dry_run":
            return {
                "state": "dry_run_passed",
                "reason": f"Reached the final {self.name} submit step with verified answers; nothing submitted",
                "report": report,
            }
        obs = await self.form.scan()
        final = await self.observed_final(obs)
        if not final:
            return self.defer("Final submit control was not unique")
        return await self.commit(final)

    async def on_confirmation(self, obs):
        if self.committed:
            receipt = await self.form.proof()
            if receipt and receipt != self.before_commit:
                receipt["resume_sha256"] = self.run_record["packet"]["resume_sha"]
                return {"state": "confirmed", "reason": "Website confirmation captured", "receipt": receipt}
            return None  # Wait for a confirmation that this submission produced.
        if ALREADY.search(obs.get("text", "")):
            # This job's own page states the applicant already applied (MCI's iCIMS page: "You are currently submitted to
            # this job"): an earlier attempt went through, and nothing is resubmitted.
            return await self.on_already_applied(obs)
        # A confirmation-looking page this run did not produce is never recorded as an application.
        return self.defer(
            "The employer shows a confirmation, but this run did not submit; check whether you already applied",
            [
                {
                    "question": "Confirm whether you already applied to this job",
                    "options": [],
                    "key": "manual",
                    "reason": "Confirmation without a submission",
                }
            ],
        )

    async def on_already_applied(self, obs):
        if self.committed:
            return await self.on_confirmation(obs)
        return {
            "state": "already_applied",
            "reason": "The employer says you already applied to this job",
            "receipt": {
                "type": "employer_already_applied",
                "url": self.page.url,
                "evidence": (ALREADY.search(obs["text"]) or [""])[0][:200],
            },
        }

    async def on_closed(self, obs):
        if limit := APPLICATION_LIMIT.search(obs.get("text", "")):
            window = " ".join(obs["text"][max(0, limit.start() - 250) : limit.end()].split())
            notice = " ".join(re.split(r"(?<=[.!?])\s+", window)[-2:])
            return {
                "state": "closed",
                "reason": "The employer's application limit for this applicant is reached, so nothing was submitted: "
                + notice,
            }
        return {"state": "closed", "reason": "The posting is closed or no longer available"}

    async def on_blocked(self, obs):
        return self.defer(
            "This employer requires SMS/passkey verification. Run `jobpilot login URL` once to save a session.",
            [
                {
                    "question": "Sign in to this employer and import the browser session",
                    "options": [],
                    "key": "session",
                    "reason": "SMS or passkey verification",
                }
            ],
        )

    async def on_unknown(self, obs):
        return {"fallback": True, "reason": "Unrecognized page"}

    # ----- actions ------------------------------------------------------------------------------
    def is_final_control(self, button):
        return bool(self.final.search(button["label"]))

    async def click(self, button, auth=False, step=True):
        """Adapters click controls they classified themselves; the final submit goes through commit()."""
        if (
            self.is_final_control(button)
            and not auth
            and not self.committed
            and self.last_step
            not in {
                Step.EMAIL_CODE,
                Step.CONSENT,
                Step.JOB,
                Step.METHOD,
            }
            and not await self.form.intermediate_submit(button["id"], await self.form.scan())
        ):
            raise ValueError("Refusing to click a final submit control outside the commit step")
        await self.dismiss_overlays()
        before = set(self.page.context.pages)
        # An adapter that classified a "Submit ..." label as a step (iCIMS "Submit Profile") vouches for it; the form's
        # own guard sees only the label and refused it after the profile's CAPTCHA (America's Tire, Noblis 2026-10-26).
        vouched = bool(FINAL.search(button["label"])) and not self.is_final_control(button)
        result = await self.form.click(button["id"], auth_control=auth or vouched, step_control=step or auth)
        if set(self.page.context.pages) - before:
            self.emit("new_tab", "The application opened in a new tab")
        return result

    async def fill_page(self):
        report = await self.form.fill_current(accept_prefilled=True)
        if report["pending"]:
            facts = [q for q in report["pending"] if Service.review_kind(q) == "missing_fact"]
            if facts:
                # A control repair cannot make an absent fact appear. Surface
                # the actual questions once rather than spending another retry
                # on a form that still cannot be completed autonomously.
                if await self.e.ask(facts):
                    report = await self.form.fill_current(accept_prefilled=True)
                else:
                    return self.defer("Required application facts are missing", facts)
            control_errors = [q for q in report["pending"] if Service.review_kind(q) != "missing_fact"]
            if control_errors:
                report = await self.form.recover_controls(report)
                if report["ok"]:
                    return report
                control_errors = [
                    q for q in report.get("pending", []) if Service.review_kind(q) != "missing_fact"
                ]
            if control_errors:
                return {
                    "state": "waiting_browser",
                    "recoverable": True,
                    "blocker_type": "control_failure",
                    "recovery_cause": "control_observation",
                    "reason": "Known answers need browser recovery: "
                    + "; ".join(q["question"] for q in control_errors),
                }
            asked = await self.e.ask(report["pending"])
            if asked:
                report = await self.form.fill_current(accept_prefilled=True)
            if report["pending"]:
                return self.defer("Required answers need your review", report["pending"])
        if not report["ok"]:
            # One more pass catches fields revealed or re-rendered by the previous answers.
            report = await self.form.fill_current(accept_prefilled=True)
            if not report["ok"]:
                report = await self.form.recover_controls(report)
            if report["pending"]:
                return self.defer("Required answers need your review", report["pending"])
            fatal = [p for p in report["problems"] if not p.startswith("Resume attachment")]
            if fatal:
                report = await self.form.recover_controls(report)
                if report["ok"]:
                    return report
                return self.defer(
                    "; ".join(report["problems"][:6]), state="waiting_browser", blocker_type="validation"
                )
        return report

    async def repair(self, obs):
        self.attempts["repair:" + urlsplit(obs["url"]).path] += 1
        if self.attempts["repair:" + urlsplit(obs["url"]).path] > 2:
            return self.defer("The employer rejected the page: " + "; ".join(obs["errors"][:4]))
        flagged = [f for f in obs["fields"] if f["error"] or not f["valid"]]
        for f in flagged:
            self.form.expected.pop(f["id"], None)
        report = await self.fill_page()
        if isinstance(report, dict) and report.get("state"):
            return report
        obs = await self.form.scan()
        nxt = self.find_next(obs)
        if nxt:
            await self.click(nxt, step=True)
        return None

    async def solve_captcha(self):
        """None when the page is clear; a waiting_browser result when a check stays unsolved."""
        return await self.e.solve_captcha(adapter=self)

    async def captcha_open(self):
        from ..captcha_observation import challenge_frame, checkbox_frame

        try:
            return bool(await challenge_frame(self.page) or await checkbox_frame(self.page))
        except Exception:
            return False

    async def after_auth_click(self):
        """Security checks that appear right after an email/sign-in/create click."""
        await asyncio.sleep(0.5)
        if await self.captcha_open():
            return await self.solve_captcha()
        # A picture puzzle the website draws itself (TikTok's "Select 2 objects that are the same shape", see
        # captcha_inpage) has no provider frame for the check above.
        return await self.solve_inpage_puzzle()

    async def commit(self, final):
        if not self.committed and getattr(self.form, "referral_route", lambda: False)():
            return {
                "fallback": True,
                "reason": "Referral form cannot submit this job application; restore the application entry",
            }
        signup = getattr(self.form, "signup_form", None)
        if not self.committed and signup and await signup(final["id"]) is True:
            return {
                "fallback": True,
                "reason": "This is a talent-community signup, not the job application; open the job's Apply",
            }
        if quality_blocker := await self.e.quality_gate():
            if await self.revisit_history(quality_blocker):
                return None  # Walked back to the step that owns the flagged answer; the loop refills it.
            if any(p.get("repair_action") == "revisit_history_row" for p in quality_blocker["report"]["pending"]):
                # The rows that could not be reopened now report as exhausted: stop with the reason naming them.
                return await self.e.quality_gate() or quality_blocker
            return quality_blocker
        # The reviewer may have remounted a repaired input; use a fresh final ID.
        final = await self.observed_final(await self.form.scan())
        if not final and any(FINAL.search(x) for x in self.form.meta.get("disabled_actions", [])):
            # Dayforce (mPulse 2026-10-16): Submit stays disabled until "I agree to the Candidate
            # Acknowledgement" is ticked; the box is not marked required, so verify() passed.
            filled = await self.fill_page()
            if isinstance(filled, dict) and filled.get("state"):
                return filled
            final = await self.observed_final(await self.form.scan())
        if not final:
            return {
                "state": "waiting_browser",
                "reason": "Review changed the final control; observing the draft again",
                "recovery_cause": "control_observation",
            }
        if challenge := await self.solve_captcha():
            return challenge  # Unsolved check before anything was submitted.
        from ..widgets import ensure_uncovered

        try:
            await ensure_uncovered(self.form.locator(final["id"]))
        except ValueError:
            return {
                "state": "waiting_browser",
                "reason": "A dialog covers the final application control; resolve it before submission",
                "blocker_type": "control_failure",
                "recovery_cause": "control_observation",
                "recoverable": True,
            }
        self.before_commit = await self.form.proof()
        page = await self.form.scan()
        self.commit_pages.add(self.signature(Step.REVIEW, page))
        if not self.committed and (refused := await self.e.try_reserve_commit()):
            return refused
        self.e.armed = True
        self.committed = True
        for frame in self.page.frames:
            try:
                await frame.evaluate(ARM_JS)
            except Exception:
                pass
        self.emit("submitting", "Commit step started; automatic retries are disabled")
        self.e.commit_requests, self.e.commit_tracking, self.e.commit_inflight, self.e.refusal_said = [], 0, set(), []
        self.e.commit_url = getattr(self.page, "url", "")
        try:
            await self.form.click(final["id"], step_control=True)
        except ValueError as exc:
            # JPMorgan Oracle 2026-10-14: the captcha callback re-rendered the page and the final
            # control id went stale. That refusal happens before any click, so re-observe the one
            # final control and click it once.
            if "stale" not in str(exc).lower():
                raise
            fresh = await self.observed_final(await self.form.scan())
            if not fresh:
                raise
            await self.form.click(fresh["id"], step_control=True)
        # Dayforce's "Congratulations, your application has been submitted!" arrives after its
        # reCAPTCHA and server work, past 20 s (Interactive Brokers, York 2026-10-16).
        for _ in range(120 if self.job.get("ats") == "dayforce" else 40):
            await asyncio.sleep(0.5)
            receipt = await self.form.proof()
            if receipt and receipt != self.before_commit:
                receipt["resume_sha256"] = self.run_record["packet"]["resume_sha"]
                return {"state": "confirmed", "reason": "Website confirmation captured", "receipt": receipt}
            if rejection := (await self.validation_rejection() or await self.form.rejection()):
                if rejection.get("category") == "already_applied":
                    self.e.armed = self.committed = False
                    await self.e.release_reservation()
                    return {"state": "already_applied",
                            "reason": "The employer says an application is already on file for this job",
                            "receipt": rejection}
                if rejection.get("category") == "validation":
                    attempts = getattr(self, "validation_attempts", 0)
                    repair = await self.form.repair_validation(rejection) if attempts < 2 else None
                    if repair and repair["ok"] and repair["changed"]:
                        try:
                            self.service.authorize_validation_retry(self.e.run_id, rejection)
                        except ValueError as exc:
                            self.e.armed = self.committed = False
                            return {
                                "state": "waiting_browser",
                                "reason": str(exc),
                                "receipt": rejection,
                                "recovery_cause": "validation_controls",
                            }
                        self.validation_attempts = attempts + 1
                        next_final = await self.observed_final(await self.form.scan())
                        if next_final:
                            return await self.commit(next_final)
                    self.e.armed = False
                    self.committed = False
                    self.e.validation_refusal = rejection
                    # Refused, so nothing was accepted. A reservation kept "submitting" turned every later outcome
                    # into an uncertain submission (Caesars' Oracle "This info is required" 2026-10-25).
                    await self.e.release_reservation()
                    return {
                        "state": "waiting_browser",
                        "reason": "Employer requested field corrections; automatic repair could not complete them",
                        "blocker_type": "validation",
                        "recovery_cause": "validation_controls",
                        "receipt": rejection,
                        "reviews": (repair or {}).get("pending", []),
                    }
                if rejection.get("category") == "employer_limit":
                    self.e.armed = self.committed = False
                    return await self.e.employer_limit_result(rejection)
                if rejection.get("category") == "security":
                    # Lever: "There was an error verifying your application. Please try again."
                    # Nothing was accepted; a fresh form (restart_rejected_form) may try again.
                    self.e.armed = self.committed = False
                    await self.e.release_reservation()
                    return {
                        "state": "waiting_browser",
                        "reason": "The website's security check refused the submission and asked to try again; "
                        "nothing was accepted",
                        "receipt": rejection,
                        "blocker_type": "validation",
                        "recovery_cause": "site_temporary",
                        "recoverable": True,
                    }
                return {
                    "state": "failed",
                    "reason": "Employer website explicitly rejected the submission",
                    "receipt": rejection,
                }
            if await self.solve_captcha():
                return {
                    "state": "submission_unknown",
                    "reason": "Submission attempted but a security check was not accepted; "
                    "automatic resubmission is disabled",
                }
            obs = await self.form.scan()
            step = self.classify(obs)
            verifier = getattr(self.e, "email_verification", None)
            if step == Step.EMAIL_CODE or (verifier is not None and verifier.state(obs)[0]):
                # Post-submit verification (Greenhouse security code; Starbucks/Eightfold "Check your
                # email" modal, shown with "Missing or Invalid OTP" before any code, 2026-10-14).
                return None
            if (
                step in {Step.FORM, Step.REVIEW}
                and self.app_fields(obs)
                and not obs["errors"]
                and self.signature(Step.REVIEW, obs) not in self.commit_pages
            ):
                # A genuinely new page after the commit (survey, EEO). Continue the loop.
                return None
            if obs["errors"] and step in {Step.FORM, Step.REVIEW}:
                break
        if already := self.e.refused_as_already_applied():
            self.e.armed = self.committed = False
            await self.e.release_reservation()
            self.e.emit_commit_requests()
            return already
        from ..browser import application_created

        if created := application_created(getattr(self.e, "commit_requests", None), self.e.job["id"], self.e.run_id):
            self.e.emit_commit_requests()
            return {"state": "confirmed", "reason": "The platform answered the application request as created",
                    "receipt": created}
        sent_nothing = await self.e.commit_sent_nothing()
        self.e.emit_commit_requests()
        if sent_nothing:
            # Eightfold answered Submit with a 400 and kept the form (PayPal, Micron 2026-10-25): every such run
            # read as an uncertain submission that can never be retried, though the employer accepted nothing.
            self.e.armed = self.committed = False
            await self.e.release_reservation()
            return {
                "state": "waiting_browser",
                "reason": "The employer accepted nothing from the submit and the form is still open; nothing was submitted",
                "blocker_type": "validation",
                "recovery_cause": "validation_controls",
            }
        # An authenticated acknowledgement email can still confirm it later (receipts.py).
        return {
            "state": "submission_unknown",
            "reason": "Submission attempted, but no explicit receipt was captured",
        }

    async def validation_rejection(self):
        return None

    async def open_link(self, url):
        # Navigations during the link visit may only reach the employer rule's origins.
        rule = (self.challenge or {}).get("rule") or {}
        allowed = set(rule.get("link_origins", []))
        from ..mail import origin as link_origin, platform_link_allowed, tracker_allowed

        try:
            platform = "icims" if "icims.com" in rule.get("sender_domains", []) else ""
            if allowed and tracker_allowed(link_origin(url), allowed, platform):
                # iCIMS's click tracker is only the first hop; it must redirect to the rule's own origin.
                allowed.add(link_origin(url))
            if allowed and platform_link_allowed(link_origin(url), (getattr(self, "job", None) or {}).get("ats")):
                allowed.add(link_origin(url))  # SAP's career host serves this SuccessFactors employer's reset page
        except ValueError:
            pass
        self.e.verification_origins = allowed or None
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=25000)
        finally:
            self.e.verification_origins = None
        await self.form.settle(timeout=8)
        # Remove tokens from the address bar before anything else observes the URL.
        try:
            await self.page.evaluate(
                "() => {const u=new URL(location.href);for(const k of [...u.searchParams.keys()])"
                "if(/token|code|secret|ticket|key/i.test(k))u.searchParams.delete(k);history.replaceState(null,'',u);}"
            )
        except Exception:
            pass

    async def return_to_application(self):
        target = self.apply_url or self.job["url"]
        await self.page.goto(target, wait_until="domcontentloaded", timeout=25000)

    # ----- email ------------------------------------------------------------------------------
    def mail_job(self, kind):
        """Identity that owns this observed email request, not a redirect allowance."""
        return self.job

    async def arm_mail(self, kind="auto"):
        """Open the request window BEFORE the action that makes the employer send mail.

        MailService serializes runs whose employers share a sender domain (the lease), so one
        run can never consume another's code."""
        self.challenge_since = None
        self.mail_request_kind = kind
        if self.inbox.live(self.challenge) or not self.inbox.configured:
            return True
        try:
            # Briefly allow a finishing sibling to release; longer waits defer before another browser start.
            # Shared-sender platforms wait for the holder's whole code step (inbox.lease_wait_cap).
            started = time.monotonic()
            self.challenge = await self.inbox.arm(
                self.e.run_id,
                self.mail_job(kind),
                self.accounts.email,
                kind,
                since=self.challenge_since,
                patience=max(self.config.mail_wait_seconds, code_inline_wait(self.id)) + 30,
            )
            if (waited := time.monotonic() - started) > 5:
                self.e.extend(waited)
        except ValueError as exc:
            # Another application holds this sender: sending now could hand our code to it.
            self.challenge = None
            self.emit("mail_wait", str(exc)[:200])
            return False
        self.challenge_since = self.challenge.get("since") if self.challenge else None
        return True  # Armed, or no sender rule for this employer (nothing can be matched).

    def lease_busy(self):
        return self.defer(
            "Another application kept the same email sender busy; nothing was sent. Requeue this job",
            state="timed_out",
        ) | {"blocker_type": "email_wait", "recovery_cause": "sender_busy",
             "mail_request_kind": getattr(self, "mail_request_kind", "auto")}

    def release_mail(self):
        # Close it even when its deadline has passed: a stale pending window must never be
        # handed back by the next begin().
        if self.challenge:
            if getattr(self.e, "result", None) and self.e.result.get("waiting_kind") == "email":
                return
            self.inbox.finish(self.challenge, "cancelled", "No email was needed")
        self.challenge = None

    def finish_mail(self, state, reason):
        if self.challenge:
            self.inbox.finish(self.challenge, state, reason)
            packet = getattr(self, "run_record", {}).get("packet", {})
            if packet.get("email_wait_resume", {}).get("challenge_id") == self.challenge["id"]:
                packet.pop("email_wait_resume", None)
        self.challenge = None

    def mail_problem(self, what):
        reason = self.inbox.reason(self.challenge) if self.challenge else ""
        if self.challenge is None and not self.mail_rule_known:
            return f"No trusted sender is known for this employer's {what}; add a rule in Settings › Email"
        return f"The {what} email did not arrive or was not unique" + (f" ({reason})" if reason else "")

    async def discover_verification(self):
        """Use the same bounded authenticated discovery as the guided controller.

        Unknown portals previously stopped before this existing capability ran.
        Discovery still requires an employer-bound link; a naked code cannot
        establish an unfamiliar sender's identity, and no secret reaches a model.
        """
        try:
            handled = await self.e.email_verification.handle()
        except ValueError as exc:
            return self.defer(str(exc), state="waiting_browser", blocker_type="email_wait")
        if not handled:
            return {"fallback": True, "reason": "Verification page requires broader observation"}
        return None

    @property
    def mail_rule_known(self):
        return bool(self.service.mail.rule_for(self.job["url"]))

    async def wait_email(self, kind):
        if not self.inbox.live(self.challenge):
            # The site may have sent the mail during navigation; the window starts before that.
            self.challenge = None
            try:
                self.challenge = await self.inbox.arm(
                    self.e.run_id, self.mail_job(kind), self.accounts.email, kind, since=self.challenge_since
                )
            except ValueError as exc:
                self.emit("mail_wait", str(exc)[:200])
                return None
        if not self.challenge:
            return None
        self.service.mail.narrow(self.challenge["id"], kind)
        self.emit("email_wait", f"Waiting for the employer's verification {kind}")
        inline = code_inline_wait(self.id) if kind == "code" else 3
        if getattr(self, "reset_in_progress", False) and (
            kind in {"code", "auto"} or (kind == "password_reset" and getattr(self, "one_page_confirming", False))
        ):
            # A reset's code page cannot be reopened: a parked run's continuation requests another reset, whose new
            # code replaces this one (Amazon 2026-10-27: three resets, each code "Incorrect" in the next run). Its code
            # is awaited here, as Oracle's are. A one-page reset waits the same way for its code or link (TikTok
            # 2026-10-28: the code came 8 s after the run parked).
            inline = max(inline, RESET_CODE_INLINE_WAIT)
        self.e.extend(max(min(self.config.mail_wait_seconds, 120), inline))
        token = await self.inbox.wait(self.challenge, max_wait_seconds=inline)
        if token:
            self.emit("email_matched", "Matched a trusted verification email", {"kind": token["kind"]})
        return token

    # ----- accounts ---------------------------------------------------------------------------
    def review_for(self, field, reason="Needs your approval"):
        from ..answers import answer_key

        return {
            "question": field["label"],
            "options": field["options"],
            "key": answer_key(field),
            "reason": reason,
        }

    def auth_url(self, obs):
        """The page that owns the password fields (the account belongs to that origin)."""
        passwords = [f for f in obs["fields"] if f["type"] == "password"]
        # The observation may be older than the page (a failed sign-in reloads it).
        return (passwords[0].get("frame_url") if passwords else None) or self.page.url

    async def authenticate(self, obs, mode):
        from ..agent_settings import require

        require(self.service.db, "accounts")
        if not self.accounts.ready:
            return self.defer("Add your email to the profile so I can identify you to employers")
        url = self.auth_url(obs)
        record = self.accounts.record(url)
        known = record.get("state") in KNOWN_ACCOUNT
        if mode == "create" and not self.accounts.may_create and not known:
            return self.defer(
                "This employer requires an account. Enable “Create and reuse employer accounts” in your profile.",
                [
                    {
                        "question": "Allow account creation for employers that require one",
                        "options": [],
                        "key": "session",
                        "reason": "Account required",
                    }
                ],
            )
        # The one registration after an unanswered reset: this sign-up page was opened for it (create_spent, state
        # password_unknown), or an account never confirmed reaches it with the unanswered reset on record.
        registering = mode == "create" and (
            (create_spent(record) and record.get("state") == "password_unknown")
            or (not create_spent(record) and not record.get("confirmed_at") and unanswered_reset_record(record))
        )
        if registering and not create_spent(record):
            self.accounts.mark(url, "no_account", self.accounts.saved(url) or self.accounts.credentials(url),
                               absent_create_at=now())
            self.emit("account", "The reset went unanswered; registering this email once")
        self.attempts[mode] += 1
        if self.attempts[mode] > 2 or self.attempts["sign_in"] + self.attempts["create"] > 4:
            problems = [p for p in getattr(self.form, "last_problems", []) if not p.startswith("Needs an approved answer")]
            if problems and any(f["type"] == "file" for f in obs["fields"]):
                # These account fields belong to an application form that keeps failing its own checks; that is the
                # failure. Schwab and Iridium (2026-10-21) each ended "Could not sign in" over a graduation year and a
                # resume upload loop.
                return self.defer("; ".join(problems[:6]), state="waiting_browser", blocker_type="validation")
            return await self.recover(obs, "Signing in did not succeed")
        button = self.auth_button(obs, mode)
        if (
            button
            and self.embedded_form_submit(button)
            and not getattr(self.form, "uploaded", True)
            and any(f["type"] == "file" for f in obs["fields"])
        ):
            # An application form with embedded account fields: attach the resume before typing the password. iCIMS
            # posts the upload and reloads the profile, which clears typed passwords (Iridium 2026-10-21).
            await self.form.fill_current(accept_prefilled=True)
            obs = await self.form.scan()
        passwords = [f for f in obs["fields"] if f["type"] == "password"]
        for f in passwords:
            frame_url = self.form.frames[f["id"]].url
            if not self.accounts.password_allowed(self.id, frame_url, self.job["url"]):
                return self.defer("A password was requested on an unexpected website; not entering it")
        if (
            mode == "create"
            and known
            # The one registration after an unanswered reset stays on its sign-up page: Simplify's /auth/register was
            # opened for it and turned back to sign-in at once because the record read "reset_requested" (2026-10-29).
            and not registering
            and not self.attempts["signin_switch"]
            and await self.switch(obs, SIGNIN_LINK)
        ):
            # A known account on a sign-up page: sign in instead of re-creating it.
            self.attempts["signin_switch"] += 1
            self.attempts["create"] -= 1
            return None
        alternate = self.accounts.alternative_credentials(url) if mode == "sign_in" else None
        location = urlsplit(url)
        alternate_key = f"alternate_password:{location.scheme}://{location.netloc.lower()}"
        if alternate and record.get("state") == "reset_requested" and not self.attempts[alternate_key]:
            self.attempts[alternate_key] += 1
            self.emit("auth", "Trying the configured password for this existing account")
        using_alternate = bool(alternate and self.attempts[alternate_key])
        credentials = alternate if using_alternate else self.accounts.credentials(url)
        # Only skip a recently and explicitly rejected login for a known account. The identifier is included in
        # the opaque context so changing a configured username permits a fresh attempt. Legacy records lack this
        # context and are not assumed to refer to the identifier currently shown on the page.
        identifiers = [self.login_value(f, credentials) for f in obs["fields"] if self.is_login_field(f)]
        context = hashlib.sha256("\0".join(identifiers).encode()).hexdigest()
        try:
            rejection_age = (datetime.now(UTC) - datetime.fromisoformat(record.get("rejected_at", ""))).total_seconds()
        except (ValueError, TypeError):
            rejection_age = -1
        if (mode == "sign_in" and record.get("confirmed_at")
                and record.get("state") == "credentials_rejected" and 0 <= rejection_age < 3600
                and record.get("rejected_login_context") == context
                and record.get("rejected_credentials") == self.accounts.credential_fingerprint(url, credentials)
                and not alternate):
            self.emit("auth", "This account recently rejected the same login; using password recovery")
            return await self.recover(obs, "The employer recently rejected the saved login")
        # Identity fields on sign-up forms (name, country, consent) come from the profile.
        others = [
            f
            for f in obs["fields"]
            if f["type"] not in {"password"}
            and not self.is_login_field(f)
            and not AUTH.search(f["label"])
            and (f["required"] or f["type"] == "checkbox")
        ]
        if others:
            answers = await self.form.resolver.resolve(others)
            for a in answers:
                f = self.form.fields.get(a.field_id)
                if not f:
                    continue
                if a.disposition == "answer":
                    try:
                        await self.form.fill(f, a.value)
                    except Exception:
                        pass
                elif f["required"]:
                    return self.defer("The account form needs an answer", [self.review_for(f, a.reason)])
        for f in obs["fields"]:
            if self.is_login_field(f):
                await self.form.locator(f["id"]).fill(self.login_value(f, credentials), timeout=4000)
        for f in passwords:
            await self.form.locator(f["id"]).fill(credentials["password"], timeout=4000)
            await self.form.locator(f["id"]).blur()
        profile = getattr(getattr(self.form, "resolver", None), "profile", None)
        await self.tick_auth_agreements(obs, profile)
        if mode == "create" and await acknowledge_privacy_notice(
            getattr(self.form, "page", None), authorized=bool(getattr(profile, "accept_all_application_terms", False))
        ):
            self.emit("policy_accepted", "Acknowledged the employer's privacy notice (applicant pre-approved terms)")
        button = self.auth_button(obs, mode)
        if not button:
            return {"fallback": True, "reason": "No sign-in control found"}
        resume = any(f["type"] == "file" for f in obs["fields"])
        if self.embedded_form_submit(button) and (resume or len(others) > 6):
            # An application form with an embedded "create a password" section: its Submit is the
            # application submit, so it goes through review/commit like any other final step. A signed-in account
            # keeps its state: the profile's login boxes are shown to returning candidates too (Rivian 2026-10-27).
            if not known:
                self.accounts.mark(url, "created_locally", credentials)
            for f in passwords:
                await self.form.locator(f["id"]).evaluate("e=>e.dataset.jpEmbeddedAccountReady='true'")
            self.emit("auth", "Account fields are part of the application form")
            return await self.on_form(await self.form.scan())
        if mode == "create":
            if not await self.arm_mail():
                return self.lease_busy()
            # Saved before the click: the same shared login, so a crash cannot lose it.
            self.accounts.mark(url, "created_locally", credentials)
        temporary = bool(mode == "sign_in" and not using_alternate and self.accounts.store.temporary(url))
        await self.click(button, auth=True)
        if challenge := await self.after_auth_click():
            return challenge
        outcome, detail = await self.auth_outcome(forced_ok=temporary)
        self.emit("auth", f"{mode.replace('_', ' ')}: {outcome}")
        if outcome == "new_password":
            # The temporary password signed in and the employer demands the real one: on_new_password sets the shared
            # password. The temporary login is never saved as the account's.
            self.release_mail()
            return None
        if outcome == "ok":
            self.release_mail()
            self.accounts.mark(url, "authenticated", credentials, temporary_password=None, temporary_password_at=None)
            return None
        if outcome == "verify":
            self.accounts.mark(url, "verification_pending", credentials)
            return None
        self.release_mail()
        if outcome == "locked":
            self.accounts.mark(url, "locked", credentials, error=detail[:200])
            fresh = await self.form.scan()
            said = " ".join(fresh.get("errors", [])) + " " + str(fresh.get("text", ""))
            if RESET_UNLOCKS.search(said) and any(FORGOT.search(b["label"]) for b in fresh["controls"]):
                # Brute-force protection that a new password lifts: iCIMS Central "Your account has been blocked after
                # multiple consecutive login attempts. Reset your password to unblock your account" (By Light,
                # 2026-10-24..27). Parked as "locked", every later run met the same block and nothing ever reset it.
                self.emit("auth", "The employer unblocks the account through a password reset; resetting it")
                return await self.recover(fresh, detail)
            return self.defer(
                "The employer locked the account after failed sign-ins: " + detail,
                state="waiting_browser",
                blocker_type="account",
            )
        if outcome == "password_rules":
            return self.defer(
                "The shared account password does not meet this employer's rules: " + detail,
                state="waiting_browser",
                blocker_type="account",
            )
        if outcome == "exists" and mode == "create":
            self.accounts.mark(url, "exists", credentials)
            if await self.switch(obs, SIGNIN_LINK):
                return None
            return await self.recover(obs, detail)
        if temporary and outcome != "new_password":
            self.accounts.store.clear_temporary(url)  # Refused or expired: never tried again; a new reset follows
        if outcome == "bad_credentials" and mode == "sign_in":
            # An unanswered reset stays settled: overwriting it with "credentials_rejected" requested another reset on
            # every run (L3Harris, 2026-10-21..23: four resets, none delivered).
            # A confirmed reset that nothing answered settles the same way. Textron, Lenovo, Publicis and Simplify
            # (2026-10-28/29): the refused sign-in overwrote "reset_requested" with "credentials_rejected", recover()
            # never saw the reset as unanswered, and another reset (6 a day per job, none ever delivered) was requested
            # on every run instead of every RESET_RETRY_SECONDS.
            unanswered_reset = (
                record.get("state") == "reset_requested"
                and reset_proven(record)
                and time.time() - float(record.get("reset_requested_at") or 0) > UNANSWERED_RESET_SECONDS
            )
            unknown = record.get("state") == "password_unknown" or unanswered_reset
            self.accounts.mark(
                url,
                "password_unknown" if unknown else "credentials_rejected",
                self.accounts.saved(url) or credentials,
                rejected_credentials=self.accounts.credential_fingerprint(url, credentials),
                rejected_login_context=context,
                rejected_at=now(),
                **({"password_unknown_at": now()} if unanswered_reset else {}),
            )
            if alternate and not using_alternate and self.attempts[mode] < 2:
                self.attempts[alternate_key] += 1
                self.emit("auth", "Trying the configured password for this existing account")
                return None
            # "No account / couldn't find an account" means create one, even when an
            # earlier rejection stored this tenant as known (a reset cannot help).
            not_found = bool(NOT_FOUND.search(str(detail or "")))
            # SuccessFactors' reset page: "If you do not receive an email then no matching account was found." A reset
            # that went unanswered means this email has no account on the tenant (a saved login from a registration
            # that never finished): create one, once. "Account already exists" restores the saved login. Avature
            # (Lenovo, MetLife, Bain) and Taleo (Textron) say the same and reset on every rerun (2026-10-28).
            absent = (
                unknown
                and reset_proven(record)
                and not create_spent(record)
                # iCIMS Central's signup is owned by its adapter (try_central_signup keeps the transaction).
                and not (unanswered_reset and getattr(self, "central_transaction", lambda *_: None)(self.page.url))
            )
            # The same silence where nothing ever showed that an account exists (no Create, sign-in or "already
            # registered"): Bain's, EA's and Intuit's career sites answer a reset with "If the email address you entered
            # is associated with an account in our records, you will receive an email", and four to six confirmed resets
            # each brought nothing (2026-10-26/27). The email has no account there: create one, once. The record is not
            # marked as an account (no_account), so the sign-up page is not turned back to sign-in.
            unproven = (
                not absent
                and not record.get("confirmed_at")
                and record.get("state") in {"reset_requested", "credentials_rejected"}
                and reset_proven(record)
                and time.time() - float(record.get("reset_requested_at") or 0) > UNANSWERED_RESET_SECONDS
            )
            if (
                (not known or not_found or absent or unproven)
                and self.accounts.may_create
                and self.attempts["create"] == 0
            ):
                # Only spend the one registration attempt when a route was
                # actually taken. Older runs set this flag even when no link
                # existed, permanently blocking a later resume-first route.
                routed = not create_spent(record) and await self.switch(obs, CREATE_LINK)
                if not routed and (absent or unproven) and not create_spent(record):
                    # The registration route is the only way out of a reset that never arrives; name what the page
                    # offered so a wording the pattern misses (Textron's "Create a New Account") shows in the trail.
                    seen = [str(c.get("label") or "")[:40] for c in (await self.form.scan()).get("controls", [])
                            if c.get("label")]
                    self.emit("account", "The reset went unanswered but the page offers no registration link I "
                              "recognize", {"controls": seen[:15]})
                if routed:
                    if absent or unproven:
                        # Nothing proves an account here when none was ever confirmed: the sign-up page is not turned
                        # back to sign-in (no_account).
                        never_seen = unproven or (unanswered_reset and not record.get("confirmed_at"))
                        self.accounts.mark(url, "no_account" if never_seen else "password_unknown",
                                           self.accounts.saved(url) or credentials, absent_create_at=now())
                        self.emit("account", "The reset went unanswered; opened the registration route once")
                    return None
                if await self.first_time_route(obs, url, credentials):
                    return None
            return await self.recover(obs, detail)
        return None

    async def first_time_route(self, obs, url, credentials):
        """A sign-in page whose other half starts a new candidate's application with a resume upload. Intuit's Avature
        page (2026-10-26/27) reads "Already registered?" beside "First time here?" with only "Without Resume", "Copy &
        Paste" and "My Computer" under it: no create-account link, so a refused sign-in for an account never seen there
        went to a password reset that never arrived, on every run. The resume goes into that upload instead; the
        application creates the account. True when the route was taken (once per run)."""
        text = " ".join([obs.get("text", ""), *obs.get("headings", [])])
        if self.attempts["first_time"] or not FIRST_TIME.search(text):
            return False
        current = await self.form.scan()
        resume_fields = getattr(self.form, "resume_fields", None)
        uploads = [f for f in (resume_fields() if resume_fields else []) if f.get("widget") != "filechooser"]
        path = getattr(self.form, "resume_path", None)
        if len(uploads) != 1 or not path:
            return False
        self.attempts["first_time"] += 1
        self.emit("account", "This site starts a new candidate's application with a resume upload beside its sign-in; "
                  "uploading the resume there")
        # Some registration pages expose the file input while its panel and
        # Continue action are still hidden. Choose the observed local-file
        # route before attaching; setting the input alone cannot open it.
        local_upload = [c for c in current.get("controls", []) if re.fullmatch(
            r"(?:my computer|from (?:my |your )?computer|upload (?:a |your )?resume)", c["label"].strip(), re.I
        )]
        if len(local_upload) == 1:
            await self.form.locator(local_upload[0]["id"]).click(timeout=4000)
            await self.form.scan()
            uploads = [f for f in resume_fields() if f.get("widget") != "filechooser"]
            if len(uploads) != 1:
                return False
        await self.form.locator(uploads[0]["id"]).set_input_files(str(path), timeout=15000)
        if not self.accounts.record(url).get("confirmed_at"):
            # The resume route was taken: a later create-password step must not return to sign-in.
            self.accounts.mark(url, "no_account", self.accounts.saved(url) or credentials)
        await self.form.wait_upload()
        self.form.uploaded = True
        after = await self.form.scan()
        if self.sign_in_shown(after) and (proceed := self.find_next(after)):
            # The upload stayed on the page: its own Next/Continue sends it (never the sign-in button).
            if not re.search(r"\b(?:sign|log) ?in\b", proceed["label"], re.I):
                await self.click(proceed)
        return True

    def login_value(self, f, credentials):
        """A "User name"/"User ID" field gets the applicant's username when one is configured."""
        username = getattr(getattr(getattr(self, "e", None), "profile", None), "account_username", "")
        if (
            username
            and f["type"] != "email"
            and re.search(r"user ?name|user ?id", f["label"] + " " + f.get("automation_id", ""), re.I)
            and not re.search(r"e-?mail", f["label"], re.I)
        ):
            return username
        return credentials["email"]

    def is_login_field(self, f):
        return f["type"] == "email" or (
            f["type"] in {"text", "email"}
            and re.search(
                r"e-?mail|user ?name|user ?id|login(?: id)?|sign.?in name",
                f["label"] + " " + f["automation_id"],
                re.I,
            )
            and not re.search(r"first|last|name of|full name", f["label"], re.I)
        )

    def embedded_form_submit(self, button):
        """The account button submits the application form it sits in (its own Submit), so the form owns the page."""
        return bool(self.final.search(button["label"]))

    def auth_button(self, obs, mode):
        prefer = {
            "sign_in": r"^(?:sign in|log ?in|continue|next|submit)$",
            "create": r"^(?:create(?: (?:an? )?account)?|register|sign up|create profile|submit|continue|next)$",
            "reset": r"^(?:submit|send|send (?:a |the )?(?:reset )?(?:link|email|code|instructions)|send reset link|"
            r"reset(?: (?:my )?password)?|request (?:a )?reset|continue|set password|change password|save|"
            r"update password)$",
        }[mode]
        hits = [
            b
            for b in obs["controls"]
            if re.search(prefer, b["label"], re.I) or re.search(prefer, b["aria_label"], re.I)
        ]
        if not hits:
            hits = [b for b in obs["controls"] if AUTH_BUTTON.search(b["label"])]
        if not hits and mode == "reset":
            # Taleo's "Forgot your password?" page (User Name, Email Address) submits with "OK" beside "Cancel"; the reset
            # step found no submit and the fallback model pressed Cancel (Textron, 2026-10-27).
            hits = [b for b in obs["controls"] if re.fullmatch(r"\s*ok\s*", b["label"], re.I)]
        if not hits and mode == "reset" and self.new_password_step(obs):
            hits = [b for b in obs["controls"] if re.fullmatch(r"\s*next\s*", b["label"], re.I)]
        # Workday overlays the real button with a click_filter that must receive the click.
        filters = [b for b in hits if b["automation_id"] == "click_filter"]
        primary = [b for b in hits if "submit" in b["automation_id"].lower()]
        actions = [b for b in hits if not b.get("href")]
        return (filters or primary or actions or hits or [None])[0]

    async def auth_outcome(self, timeout=12, *, forced_ok=False):
        """forced_ok: a sign-in that lands on the employer's forced new-password page is "new_password" at once (the
        page is waited on for 12 s otherwise), not a success: the credential it accepted is still temporary."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await self.form.settle(timeout=4)
            obs = await self.form.scan()
            text = " ".join(obs["errors"]) + "\n" + obs["text"]
            errors = " ".join(obs["errors"]) or text
            if TEMPORARY_EXPIRED.search(text):
                return "bad_credentials", TEMPORARY_EXPIRED.search(text).group()
            if forced_ok and not obs["errors"] and self.new_password_step(obs):
                return "new_password", ""
            if LOCKED.search(errors):
                return "locked", LOCKED.search(errors).group()
            # SuccessFactors' sign-in page asks "Already have an account?"; read as a refusal, Boehringer's sign-in
            # went to a password reset (2026-10-16).
            if exists := EXISTS.search(re.sub(r"already have an account\s*\?", " ", errors, flags=re.I)):
                return "exists", exists.group()
            if obs["errors"] and PASSWORD_RULES.search(errors):
                return "password_rules", obs["errors"][0][:200]
            if BAD_CREDENTIALS.search(errors) and (
                obs["errors"] or any(f["type"] == "password" for f in obs["fields"])
            ):
                return "bad_credentials", BAD_CREDENTIALS.search(errors).group()
            if CHECK_EMAIL.search(text) and not any(f["type"] == "password" for f in obs["fields"]):
                return "verify", ""
            if self.code_fields(obs):
                return "verify", ""
            if not any(f["type"] == "password" for f in obs["fields"]):
                return "ok", ""
            await asyncio.sleep(0.4)
        return "unknown", "; ".join(obs["errors"][:2])

    async def switch(self, obs, pattern):
        # The page usually changed since `obs` (a failed sign-in reloads it); observe it again.
        obs = await self.form.scan()
        links = [b for b in obs["controls"] if pattern.search(b["label"]) or pattern.search(b["aria_label"])]
        if not links:
            return False
        await self.click(links[0], auth=True)
        return True

    async def recover(self, obs, detail):
        """Forgot-password through the connected mailbox, then set the shared password.

        Requested once per employer per run and at most once every five minutes; a reset whose
        email never arrived does not count against the limit. Saved credentials are kept until
        the website confirms the new password."""
        url = self.auth_url(obs)
        record = self.accounts.record(url)
        manual = [
            {
                "question": "Sign in to this employer and import the browser session",
                "options": [],
                "key": "session",
                "reason": detail[:200],
            }
        ]
        unanswered = (
            record.get("state") == "reset_requested"
            and record.get("reset_requested_at")
            and time.time() - float(record["reset_requested_at"]) > UNANSWERED_RESET_SECONDS
        )
        recent = time.time() - float(record.get("reset_requested_at") or 0) < RESET_WINDOW_SECONDS
        if record.get("state") == "password_unknown" and not unanswered:
            since = str(record.get("password_unknown_at") or "")
            try:
                waited = (datetime.now(UTC) - datetime.fromisoformat(since)).total_seconds() if since else 0
            except ValueError:
                waited = 0
            if waited > RESET_RETRY_SECONDS:
                self.emit("auth", "Requesting this employer's password reset again (resets are always allowed)")
                record = {**record, "state": "credentials_rejected", "reset_requested_at": 0, "reset_unconfirmed": 0}
                recent = False
        if (unanswered or record.get("state") == "password_unknown") and not reset_proven(record):
            # The silence proves nothing when the employer never confirmed the request (reset_proven): request it again,
            # up to UNCONFIRMED_RESETS times a day.
            if record.get("reset_unconfirmed", 0) >= UNCONFIRMED_RESETS and recent:
                return self.defer(
                    RESET_UNCONFIRMED,
                    [{"question": RESET_UNCONFIRMED, "options": [], "key": "manual", "reason": detail[:200]}],
                    state="waiting_browser",
                    blocker_type="account",
                )
            self.emit("auth", "The employer never confirmed the earlier password reset request; requesting it again")
            unanswered = False
            record = {**record, "state": "credentials_rejected", "reset_requested_at": 0}
        if unanswered or record.get("state") == "password_unknown":
            # The employer holds an account for this email, refuses the saved password, and mails nothing for a reset
            # (SuccessFactors: "If you do not receive an email then no matching account was found"). L3Harris, Entergy,
            # Farmers and Westinghouse (2026-10-21/22) repeated sign-in and reset on every run. Only the owner can
            # recover it; a password saved under Setup > Accounts (state password_reset) is used from then on.
            if unanswered:
                self.accounts.mark(url, "password_unknown", self.accounts.saved(url), password_unknown_at=now())
            return self.defer(
                PASSWORD_UNKNOWN,
                [{"question": PASSWORD_UNKNOWN, "options": [], "key": "manual", "reason": detail[:200]}],
                state="waiting_browser",
                blocker_type="account",
            )
        if not self.config.account_password_reset or self.attempts["reset"]:
            return self.defer(
                f"Could not sign in to this employer ({detail})",
                manual,
                state="waiting_browser",
                blocker_type="account",
            )
        if not self.inbox.configured or not await self.inbox.rule(
            self.mail_job("password_reset"), self.accounts.email
        ):
            # Without a trusted sender, a reset email could never be matched: do not burn a reset.
            return self.defer(
                f"Could not sign in ({detail}). Connect Gmail and an email rule for this employer to "
                "recover the account automatically.",
                manual,
                state="waiting_browser",
                blocker_type="account",
            )
        from ..runtime import engine_version

        if record.get("reset_failed_engine") == engine_version():
            return self.defer(
                "This employer's new-password step failed on this build; no further reset emails are requested",
                manual,
                state="waiting_browser",
                blocker_type="account",
            )
        if time.time() - record.get("reset_requested_at", 0) < 300:
            return self.defer(
                "A password reset was requested recently; awaiting delivery",
                manual,
                state="waiting_browser",
                blocker_type="email_wait",
            )
        if record.get("resets", 0) >= 3 and recent:
            return self.defer(
                "Password resets for this employer are exhausted; sign in manually once",
                manual,
                state="waiting_browser",
                blocker_type="account",
            )
        self.attempts["reset"] += 1
        obs = await self.form.scan()
        forgot = [b for b in obs["controls"] if FORGOT.search(b["label"])]
        if not forgot and not obs["controls"] and TEMPORARY_EXPIRED.search(obs.get("text", "")):
            # EA's expired temporary password leaves a page with no controls (2026-10-29): the sign-in page, where
            # "Forgot your password?" lives, is opened again.
            await self.return_to_application()
            obs = await self.form.scan()
            forgot = [b for b in obs["controls"] if FORGOT.search(b["label"])]
        if not forgot and await self.switch(obs, SIGNIN_LINK):
            # "Forgot password" usually lives on the sign-in page, not the sign-up page.
            obs = await self.form.scan()
            forgot = [b for b in obs["controls"] if FORGOT.search(b["label"])]
        if not forgot:
            return self.defer(
                f"Could not sign in ({detail}) and no password reset was offered",
                state="waiting_browser",
                blocker_type="account",
            )
        self.emit("auth", "Resetting the employer password through your mailbox")
        return await self.request_reset(await self.open_reset_step(forgot[0]), detail)

    async def tick_auth_agreements(self, obs, profile):
        """Tick a sign-in or sign-up page's own terms box ("I have read and agree to the User Agreement and Applicant
        Privacy Policy") when the applicant pre-approved routine terms. TikTok (2026-10-27, REA): the box has no label,
        and with it unticked Sign in sent nothing; four runs read the unchanged form as a failed sign-in. Marketing,
        alert and text-message boxes are never ticked."""
        if not getattr(profile, "accept_all_application_terms", False):
            return
        for f in obs["fields"]:
            if f["type"] != "checkbox" or f.get("value") == "true":
                continue
            try:
                text = await self.form.locator(f["id"]).evaluate(
                    "e => (e.labels?.[0]?.innerText || e.closest('label')?.innerText || e.parentElement?.innerText"
                    " || e.parentElement?.parentElement?.innerText || '').replace(/\\s+/g, ' ').slice(0, 400)"
                )
            except Exception:
                continue
            text = f"{f.get('label', '')} {text}"
            if AUTH_AGREEMENT.search(text) and not AUTH_MARKETING.search(text):
                try:
                    await self.form.fill(f, "true")
                    self.emit("policy_accepted", "Agreed to the sign-in page's terms (applicant pre-approved terms)")
                except Exception:
                    pass

    @staticmethod
    def sign_in_shown(obs):
        """The sign-in form is still the step: a password box, and no open dialog without one."""
        view = Adapter.dialog_view(obs)
        return any(f["type"] == "password" and not f.get("side_login") for f in (view or obs)["fields"])

    def one_page_reset(self, obs):
        """A password reset form that asks for the email and the new password together (see RESET_FORM_PATH). The sign-in
        page never is one: it still offers its "Forgot password?" control. Wording counts only outside the page's
        controls, so a sign-in page's own "Reset password" link does not name it a reset form."""
        view = self.dialog_view(obs) or obs
        fields = view["fields"]
        if not any(f["type"] == "password" for f in fields) or not any(self.is_login_field(f) for f in fields):
            return False
        if any(FORGOT.search(c["label"]) and not RESET_SUBMIT.fullmatch(c["label"].strip()) for c in view["controls"]):
            return False
        if any(c.get("href") and RESET_SUBMIT.fullmatch(c["label"].strip()) for c in view["controls"]):
            return False
        path = urlsplit(obs.get("url") or getattr(getattr(self, "page", None), "url", "") or "").path
        if re.search(r"/(?:create[-_]?account|sign[-_]?up|register)(?:/|$)", path, re.I):
            return False
        text = " ".join(view.get("headings", [])) + " " + str(view.get("text") or "")
        for label in {c["label"] for c in view["controls"] if c["label"].strip()}:
            text = text.replace(label, " ")
        return bool(RESET_FORM_PATH.search(path) or RESET_FORM_TEXT.search(text))

    async def open_reset_step(self, forgot):
        """Click "Forgot your password?" and wait for the page that replaces the sign-in form.

        L3Harris, Farmers, Westinghouse, Hershey's and Belden (SuccessFactors, 2026-10-21..25): the scan after the click
        still showed the sign-in page; the reset step typed the email there and pressed its only button, "Sign In",
        with no password ("A password is required"), and the run reported a reset that was never requested. A link
        that has not opened its page in time is followed by its address, on the sign-in page's own website."""
        before = self.page.url
        try:
            await self.click(forgot, auth=True)
        except (PlaywrightError, TimeoutError):
            # Deloitte's Avature "Forgot your password?" is a script-only link the pointer click never reached (6 s
            # timeouts on every run, 2026-10-26); its own click event runs the same handler.
            await self.form.locator(forgot["id"]).evaluate("e => e.click()", timeout=5000)
        deadline = time.monotonic() + RESET_STEP_SECONDS
        while True:
            await self.form.settle(timeout=3)
            obs = await self.form.scan()
            if not self.sign_in_shown(obs) or self.one_page_reset(obs) or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.5)
        href = str(forgot.get("href") or "").strip()
        if (
            self.sign_in_shown(obs)
            and not self.one_page_reset(obs)
            and href
            and not href.lower().startswith(("#", "javascript:"))
        ):
            from ..mail import origin

            target = urljoin(self.page.url or before, href)
            if origin(target) == origin(self.page.url or before):
                self.emit("auth", "Opened the password reset page by its link address")
                await self.page.goto(target, wait_until="domcontentloaded", timeout=30000)
                await self.form.settle(timeout=6)
                obs = await self.form.scan()
        return obs

    def reset_button(self, view):
        """The reset step's own submit. Never the sign-in or sign-up action auth_button falls back to: on a sign-in page
        that is "Sign In", pressed with no password (see open_reset_step)."""
        button = self.auth_button(view, "reset")
        if button and AUTH_BUTTON.search(button["label"]) and re.fullmatch(
            r"sign in|log ?in|create (?:an? )?account|register|sign up|create profile", button["label"].strip(), re.I
        ):
            return None
        return button

    async def reset_outcome(self, timeout=10, *, one_page=False):
        """("sent", "") when the employer's page confirms the reset request, ("refused", its message) when the request
        form came back with an error, else ("unknown", ""). A one-page reset may instead ask for the code it emailed
        ("code", ""), or stop on a picture puzzle that was not solved ("puzzle", the blocker's reason)."""
        deadline = time.monotonic() + timeout
        while True:
            if one_page and (blocked := await self.solve_inpage_puzzle()):
                return "puzzle", blocked["reason"]
            await self.form.settle(timeout=4)
            obs = await self.form.scan()
            view = self.dialog_view(obs) or obs
            asking = [f for f in view["fields"] if self.is_login_field(f)]
            errors = [str(x) for x in obs.get("errors", []) if str(x).strip()]
            self.reset_seen = obs  # the page the employer answered with, for the "Reset request page" event
            if one_page and self.code_fields(obs) and not errors:
                return "code", ""
            if RESET_SENT.search(view.get("text", "") or obs.get("text", "")) and not errors:
                return "sent", ""
            if asking and errors:
                return "refused", errors[0][:200]
            if one_page:
                if not errors and not self.one_page_reset(obs):
                    return "sent", ""  # The form gave way to the next page (a sign-in page, a confirmation).
            elif not asking and not errors and not self.sign_in_shown(obs):
                return "sent", ""  # The request form was replaced by the next page.
            if time.monotonic() >= deadline:
                return "unknown", ""
            await asyncio.sleep(0.5)

    def new_password(self, url, passwords, obs):
        """The password a reset sets: the configured login, else the shared one within the form's stated maximum
        (Westinghouse: 18 characters)."""
        from ..accounts import PASSWORD_MAXIMUM

        configured = self.accounts.icims_credentials(url)
        maxima = [f.get("maxlength", -1) for f in passwords if f.get("maxlength", -1) > 0]
        maxima += [int(n) for n in PASSWORD_MAXIMUM.findall(obs.get("text", "")) if 1 <= int(n) <= 256]
        credentials = configured or self.accounts.store.shared(max_length=min(maxima) if maxima else None)
        if maxima and not configured and len(credentials["password"]) > min(maxima):
            credentials = {**credentials, "password": self.accounts.store.generated(max(12, min(min(maxima), 16)))}
        return credentials, configured, maxima

    async def solve_inpage_puzzle(self):
        """None when no website-drawn picture puzzle is open, or once it is solved; a captcha blocker otherwise. Two
        puzzles per run at most (each bounded by CAPTCHA_IMAGE_ROUNDS paid answers)."""
        from ..captcha_inpage import find_puzzle, solve_inpage

        try:
            if not await find_puzzle(self.page):
                return None
        except Exception:
            return None
        blocker = {"state": "waiting_browser", "blocker_type": "captcha", "recovery_cause": "security_challenge"}
        if self.attempts["inpage_puzzle"] >= 2:
            return {**blocker, "reason": "The website's picture puzzle was not accepted twice"}
        self.attempts["inpage_puzzle"] += 1
        solved, reason = await solve_inpage(self.config, self.page, self.emit)
        if solved:
            return None
        return {**blocker, "reason": reason + ". The employer page remains open in Chrome."}

    async def request_reset(self, obs, detail):
        """Explicit reset-email step: fill the email, open the request window, click, confirm the page took the
        request, then wait for the email."""
        url = self.auth_url(obs) if any(f["type"] == "password" for f in obs["fields"]) else self.page.url
        def cooling_down():
            try:
                requested = float(self.accounts.record(url).get("reset_requested_at") or 0)
            except (TypeError, ValueError):
                return True  # An unreadable timestamp cannot safely establish that a new request is due.
            return bool(requested and time.time() - requested < RESET_RETRY_SECONDS)

        def cooldown_result():
            return self.defer(
                "A password reset was requested recently; no additional reset will be requested during the cooldown",
                state="waiting_browser", blocker_type="email_wait",
            )

        # Resumed mail is reclaimed by _drive before this request path. This guard covers both a fresh recovery and a
        # run reopened directly on the reset page, including requests whose page did not confirm submission.
        if cooling_down():
            return cooldown_result()
        view = self.dialog_view(obs) or obs
        one_page = self.one_page_reset(obs)
        if self.sign_in_shown(obs) and not one_page:
            return self.defer(
                f"Could not sign in ({detail}) and the password reset page did not open",
                state="waiting_browser",
                blocker_type="account",
            )
        emails = [f for f in view["fields"] if self.is_login_field(f)]
        if not emails:
            return self.defer("The password reset step has no email field")
        for f in emails:
            await self.form.locator(f["id"]).fill(self.login_value(f, {"email": self.accounts.email}), timeout=4000)
            await self.form.locator(f["id"]).blur()
        credentials = None
        if one_page:
            # The new password goes in with the request; the emailed code or link only confirms it.
            passwords = [f for f in view["fields"] if f["type"] == "password"]
            credentials, _, _ = self.new_password(url, passwords, view)
            for f in passwords:
                if not self.accounts.password_allowed(self.id, self.form.frames[f["id"]].url, self.job["url"]):
                    return self.defer("The reset page is on an unexpected website; not entering the password")
                await self.type_password(self.form.locator(f["id"]), credentials["password"])
        button = self.reset_button(view)
        if not button:
            return self.defer("The password reset step has no submit control")
        self.release_mail()
        # A one-page reset confirms the new password with an emailed code (TikTok, 2026-10-28: "TikTok verification code:
        # 754570"), which a password_reset window refuses as a malformed reset link; its window takes a code or a link.
        if not await self.arm_mail("auto" if one_page else "password_reset"):
            return self.lease_busy()
        if not self.challenge:
            return self.defer("The reset email could not be matched safely right now; retry later")
        # A sibling may have sent the reset while this run waited for the sender lease.
        if cooling_down():
            self.release_mail()
            return cooldown_result()
        previous = self.accounts.saved(url)
        record = self.accounts.record(url)
        self.accounts.mark(url, "reset_requested", previous, reset_requested_at=time.time())
        self.reset_in_progress = True
        await self.click(button, auth=True)
        if challenge := await self.after_auth_click():
            return challenge
        outcome, refusal = await self.reset_outcome(one_page=one_page)
        # What was asked and what the page answered, without values: six confirmed resets a day for Textron, Lenovo and
        # Farmers (2026-10-29) brought no mail, and nothing recorded which fields the page offered or what it said.
        after = getattr(self, "reset_seen", None)
        after = after if isinstance(after, dict) else {}
        said = re.sub(r"\S+@\S+", "<email>", " ".join([*map(str, after.get("headings", [])), str(after.get("text") or "")]))
        self.emit("auth", "Reset request page", {
            "fields": [{"label": str(f["label"])[:40],
                        "filled_with": "username" if self.login_value(f, {"email": self.accounts.email})
                        != self.accounts.email else "email"} for f in emails],
            "outcome": outcome,
            "page_said": said[:240],
        })
        if outcome == "puzzle":
            self.finish_mail("failed", "Reset puzzle not solved")
            return self.defer(
                "The password reset stopped at the website's picture puzzle: " + refusal,
                state="waiting_browser",
                blocker_type="captcha",
            )
        if one_page and outcome != "refused":
            confirmed = outcome in {"code", "sent"}
            self.accounts.mark(url, "reset_requested", previous, reset_requested_at=time.time(),
                               reset_submitted_at=now() if confirmed else None,
                               reset_unconfirmed=0 if confirmed else record.get("reset_unconfirmed", 0) + 1)
            self.emit("auth", "Employer password reset requested", {"confirmed": confirmed, "one_page": True})
            return await self.confirm_one_page_reset(url, credentials)
        if outcome == "refused":
            self.finish_mail("failed", "Reset request refused")
            return self.defer(
                "The employer refused the password reset request: " + refusal,
                state="waiting_browser",
                blocker_type="account",
            )
        if outcome == "sent":
            self.accounts.mark(url, "reset_requested", previous, reset_requested_at=time.time(),
                               reset_submitted_at=now(), reset_unconfirmed=0)
        else:
            self.accounts.mark(url, "reset_requested", previous, reset_requested_at=time.time(),
                               reset_submitted_at=None, reset_unconfirmed=record.get("reset_unconfirmed", 0) + 1)
        self.emit("auth", "Employer password reset requested", {"confirmed": outcome == "sent"})
        return await self.complete_password_reset(url)

    async def type_password(self, locator, password):
        """Type a new password key by key: rule checklists ("at least one upper case letter") and their Submit gates
        listen for keystrokes, which a value set in one step does not send."""
        await locator.fill("", timeout=4000)
        await locator.press_sequentially(password, delay=25, timeout=15000)
        try:
            await locator.evaluate("e => e.blur()")
        except Exception:
            pass

    async def nudge_reset_form(self, field_id, how):
        """Submit the new-password form another way after its button sent nothing; True when something was tried."""
        try:
            el = self.form.locator(field_id)
            if how == "enter":
                await el.press("Enter", timeout=3000)
                return True
            # The form's own submit button is marked as this auth action for the page guard, then submits its form.
            return bool(await el.evaluate(
                "e => { const f = e.form; if (!f) return false;"
                " const s = [...f.querySelectorAll('button,input[type=submit]')].find(b => /submit|save|reset|change|"
                "update|continue/i.test(b.innerText || b.value || ''));"
                " if (!s) { f.submit(); return true; }"
                " s.dataset.jpAuthControl = 'true'; s.dataset.jpControlLabel = (s.innerText || s.value || '').trim();"
                " s.dataset.jpFinalAuth = 'true'; f.__jpAllowedControl = s;"
                " if (f.requestSubmit) f.requestSubmit(s); else s.click(); return true; }"
            ))
        except Exception:
            return False

    async def save_reset_page(self):
        """Keep the new-password page's markup for diagnosis. Input values are properties, not markup: no password."""
        try:
            folder = getattr(self.e, "dir", None)
            if folder is not None:
                html = await self.page.content()
                (folder / "reset_page.html").write_text(html[:400_000])
        except Exception:
            pass

    async def confirm_one_page_reset(self, url, credentials):
        """A one-page reset already holds the new password; the employer confirms it with an emailed code typed on its
        page, or a link. Its credentials are saved only after the page took the confirmation."""
        page = await self.form.scan()
        self.one_page_confirming = True
        asks_code = bool(self.code_fields(page) or CODE_TEXT.search(page.get("text", "")))
        token = await self.wait_email("code" if asks_code else "auto")
        if not token:
            reason = self.inbox.reason(self.challenge)
            self.finish_mail("failed", "No reset email")
            return self.defer(
                "The password reset email did not arrive or was not unique" + (f" ({reason})" if reason else ""),
                state="waiting_browser",
            )
        self.emit("auth", "Employer password reset email received")
        if token["kind"] == "link":
            await self.open_link(token["value"])
            page = await self.form.scan()
            boxes = [f for f in page["fields"] if f["type"] == "password"]
            if boxes and not any(self.is_login_field(f) for f in page["fields"]):
                # The link opened a new-password form after all: it takes the same password.
                for f in boxes:
                    if not self.accounts.password_allowed(self.id, self.form.frames[f["id"]].url, self.job["url"]):
                        self.finish_mail("failed", "Reset form on an unexpected website")
                        return self.defer("The reset page is on an unexpected website; not entering the password")
                    await self.type_password(self.form.locator(f["id"]), credentials["password"])
                if button := self.auth_button(page, "reset"):
                    await self.click(button, auth=True)
        else:
            page = await self.form.scan()
            codes = self.code_fields(page)
            if not codes and CODE_TEXT.search(page.get("text", "")):
                # TikTok's "Verify Email Account" shows six bare boxes under "Enter the code in the email sent to …".
                blank = [f for f in page["fields"] if f["type"] in {"text", "tel", "number"} and not f.get("value")
                         and not self.is_login_field(f)]
                codes = blank if len(blank) == 1 or 4 <= len(blank) <= 10 else []
            for f in codes:
                if not self.accounts.password_allowed(self.id, self.form.frames[f["id"]].url, self.job["url"]):
                    self.finish_mail("failed", "Code field on an unexpected website")
                    return self.defer("The reset code field is on an unexpected website; not entering the code")
            if codes:
                await enter_code([self.form.locator(f["id"]) for f in codes], token["value"], timeout=4000)
            elif CODE_TEXT.search(page.get("text", "")) and await self.page.evaluate(
                "() => { const e = document.activeElement; return !!e && e.matches('input,[contenteditable=true]')"
                " && !/password|email/i.test((e.type || '') + ' ' + (e.name || '') + ' ' + (e.id || '')); }"
            ):
                # The boxes draw over one input the scan cannot see; the page keeps it focused for typing.
                await self.page.keyboard.type(token["value"], delay=80)
            else:
                self.finish_mail("failed", "Code field disappeared")
                return self.defer("A reset code arrived but the page no longer shows a code field")
            await asyncio.sleep(0.3)
            page = await self.form.scan()
            if self.code_fields(page) and (button := self.auth_button(self.dialog_view(page) or page, "reset")):
                await self.click(button, auth=True)
        outcome, detail = await self.auth_outcome()
        page = await self.form.scan()
        if outcome == "unknown" and not page.get("errors") and not self.code_fields(page) and not self.one_page_reset(page):
            outcome = "ok"  # Back on the sign-in form: the new password is set; signing in proves it.
        self.emit("auth", f"reset: {outcome}")
        if outcome != "ok":
            from ..runtime import engine_version

            self.finish_mail("failed", "Reset confirmation not accepted")
            self.accounts.mark(url, self.accounts.record(url).get("state") or "reset_requested", self.accounts.saved(url),
                               reset_failed_engine=engine_version())
            return self.defer("The employer did not accept the password reset confirmation: " + (detail or outcome))
        self.finish_mail("verified", "Employer accepted the password reset")
        record = self.accounts.record(url)
        self.accounts.mark(url, "password_reset", credentials, resets=record.get("resets", 0) + 1)
        self.attempts["sign_in"] = self.attempts["create"] = 0
        self.emit("auth", "Employer accepted the new account password")
        await self.form.settle(timeout=6)
        if self.classify(await self.form.scan()) not in {Step.SIGN_IN, Step.FORM, Step.METHOD}:
            await self.return_to_application()
        return None

    async def complete_password_reset(self, url):
        """Consume this owner's request; save credentials only after acceptance."""
        token = await self.wait_email("password_reset")
        if not token:
            reason = self.inbox.reason(self.challenge)
            self.finish_mail("failed", "No reset email")
            # Nothing was delivered: allow another request after the cooldown, keep the count.
            return self.defer(
                "The password reset email did not arrive or was not unique"
                + (f" ({reason})" if reason else ""),
                state="waiting_browser",
            )
        self.emit("auth", "Employer password reset email received")
        if token["kind"] == "password":
            page = await self.form.scan()
            if not self.code_fields(page):
                return await self.use_temporary_password(url, token["value"])
            # A "one-time password" typed into the code boxes the page shows is that page's code.
            token = {"kind": "code", "value": token["value"]}
        if token["kind"] == "link":
            await self.open_link(token["value"])
        else:
            # The reset is confirmed by an emailed code typed on the page, which then asks for the new password.
            page = await self.form.scan()
            codes = self.code_fields(page)
            if not codes:
                self.finish_mail("failed", "Reset email had no link")
                return self.defer("The reset email held a code but the page shows no code field")
            for f in codes:
                if not self.accounts.password_allowed(self.id, self.form.frames[f["id"]].url, self.job["url"]):
                    self.finish_mail("failed", "Code field on an unexpected website")
                    return self.defer("The reset code field is on an unexpected website; not entering the code")
            await enter_code([self.form.locator(f["id"]) for f in codes], token["value"], timeout=4000)
            await asyncio.sleep(0.3)
            page = await self.form.scan()
            if self.code_fields(page) and (button := self.auth_button(self.dialog_view(page) or page, "reset")):
                await self.click(button, auth=True)
            await self.form.settle(timeout=6)
        obs = await self.form.scan()
        return await self.set_new_password(url, obs)

    async def use_temporary_password(self, url, password):
        """The employer answered the reset with a TEMPORARY PASSWORD instead of a link (Avature's "Password activation":
        Electronic Arts, Lenovo, MetLife, Bain, Intuit, 2026-10-29). It expires within hours, so this run signs in with
        it now: the sealed temporary login is the next sign-in's credential (Accounts.credentials), and the forced
        new-password page that follows takes the shared password and replaces it (on_new_password)."""
        if not self.accounts.store.set_temporary(url, {"email": self.accounts.email, "password": password}):
            self.finish_mail("failed", "Temporary password could not be kept")
            return self.defer("The employer mailed a temporary password but its account tenant is not observed")
        self.finish_mail("verified", "Employer mailed a temporary password")
        self.emit("auth", "Employer emailed a temporary password; signing in with it")
        self.attempts["sign_in"] = self.attempts["create"] = 0
        self.reset_in_progress = False
        await self.form.settle(timeout=6)
        if self.classify(await self.form.scan()) not in {Step.SIGN_IN, Step.FORM, Step.METHOD}:
            await self.return_to_application()
        return None

    async def set_new_password(self, url, obs, *, forced=False):
        """Complete an observed new-password page; persist its credential only on acceptance."""
        passwords = [f for f in obs["fields"] if f["type"] == "password"]
        if not passwords:
            self.finish_mail("failed", "Reset link did not open a password form")
            return self.defer("The password reset link did not open a new-password form")
        # The new password obeys the reset form's stated maximum (Westinghouse: 18 characters).
        credentials, configured, maxima = self.new_password(url, passwords, obs)
        if forced and not configured and PASSWORD_HISTORY.search(obs.get("text", "")):
            # An explicit history rule makes reusing the account password inappropriate. Keep this value local until
            # the employer accepts the change, so a refused action never replaces the saved credential.
            credentials = {**credentials, "password": self.accounts.store.generated(
                max(12, min(min(maxima), 16)) if maxima else 16)}
        for f in passwords:
            if not self.accounts.password_allowed(self.id, self.form.frames[f["id"]].url, self.job["url"]):
                self.finish_mail("failed", "Reset form on an unexpected website")
                return self.defer("The reset page is on an unexpected website; not entering the password")
            await self.type_password(self.form.locator(f["id"]), credentials["password"])
        if forced:
            # Disabled Next is omitted by the scanner until both fields satisfy the page's checks.
            obs = await self.form.scan()
        button = self.auth_button(obs, "reset")
        if not button:
            self.finish_mail("failed", "No new-password submit control")
            return self.defer("The new-password form had no submit control")
        await self.click(button, auth=True)
        outcome, detail = await self.auth_outcome()
        if outcome == "unknown":
            # L3Harris (SuccessFactors, 2026-10-21..27): Submit on the new-password form sent no request and the form
            # stayed; every later run failed sign-in and mailed the owner another reset link. The same form gets Enter
            # in its last password box, then its own submit, before the reset is given up.
            await self.save_reset_page()
            for nudge in ("enter", "submit"):
                if not await self.nudge_reset_form(passwords[-1]["id"], nudge):
                    continue
                outcome, detail = await self.auth_outcome()
                self.emit("auth", f"reset after {nudge}: {outcome}")
                if outcome != "unknown":
                    break
        if outcome in {"unknown", "password_rules"} and not configured:
            page = await self.form.scan()
            if PASSWORD_HISTORY.search(" ".join(page.get("errors", [])) + " " + page.get("text", "")):
                # A reset refuses a password the account used before; this employer gets its own new password.
                credentials = {**credentials, "password": self.accounts.store.generated(
                    max(12, min(min(maxima), 16)) if maxima else 16)}
                fresh = [f for f in page["fields"] if f["type"] == "password"]
                for f in fresh:
                    await self.type_password(self.form.locator(f["id"]), credentials["password"])
                if fresh and (again := self.auth_button(page, "reset")):
                    await self.click(again, auth=True)
                    outcome, detail = await self.auth_outcome()
                    self.emit("auth", f"reset with a new password: {outcome}")
        self.emit("auth", f"reset: {outcome}")
        if outcome == "password_rules":
            self.finish_mail("failed", "Password rules")
            return self.defer("The shared account password does not meet this employer's rules: " + detail)
        if outcome != "ok":
            from ..runtime import engine_version

            self.finish_mail("failed", "New password not accepted")
            # One reset per employer per build: each failed new-password step mailed the owner another link
            # (L3Harris 2026-10-27: "I'm getting password reset links all the time").
            self.accounts.mark(url, self.accounts.record(url).get("state") or "reset_requested", self.accounts.saved(url),
                               reset_failed_engine=engine_version())
            return self.defer("The employer did not accept the new password: " + (detail or outcome))
        self.finish_mail("verified", "Employer accepted the password reset")
        record = self.accounts.record(url)
        # The temporary password the employer mailed is spent by this change.
        self.accounts.mark(url, "password_reset", credentials, resets=record.get("resets", 0) + 1,
                           temporary_password=None, temporary_password_at=None)
        self.attempts["sign_in"] = self.attempts["create"] = 0
        self.emit("auth", "Employer accepted the new account password")
        await self.form.settle(timeout=6)
        obs = await self.form.scan()
        if self.classify(obs) not in {Step.SIGN_IN, Step.FORM, Step.METHOD}:
            await self.return_to_application()
        return None
