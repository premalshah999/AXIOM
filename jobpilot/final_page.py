"""What a saved final page can prove, read from text only (no browser).

The engine's own receipt rules (forms.proof) run on the live page. A final page booked later by hand or by the repair
agent has only its saved text, so the same strictness is applied to that text: an acknowledgement of THIS application,
no open form, and no employer view of an application that was already on file. A false confirmation is much worse than
a false "unknown": anything unclear is left unconfirmed."""

import re
from urllib.parse import parse_qsl, urlsplit

# Statements that the application was accepted, in the employer's own words (English, German, French, Spanish).
ACKNOWLEDGEMENT = re.compile(
    r"thank you for (?:applying|submitting|your (?:job )?(?:application|submission))|"
    r"\bapplication(?: for [^.!?\n]{1,140}?)? (?:has been |was |is )?(?:successfully )?(?:submitted|received|sent)\b|"
    r"\bsubmitted (?:your )?application\b|we(?:'ve| have)? received your application|"
    r"successfully (?:applied|submitted)|\byour application is in\b|"
    r"bewerbung(?: [^.!?\n]{1,160}?)? (?:wurde |ist )?(?:erfolgreich )?(?:eingereicht|versendet|abgeschickt|übermittelt|"
    r"gesendet|eingegangen)\b|vielen dank für (?:ihre|deine) bewerbung|"
    r"candidature (?:a (?:bien )?été )?(?:envoyée|soumise|transmise|reçue|enregistrée)|merci pour votre candidature|"
    r"(?:solicitud|candidatura|postulación) (?:ha sido |fue )?(?:enviada|recibida|registrada)",
    re.I,
)
# The employer says an application is already on file, so this page is no receipt of a new one. "Not submitted" and
# "previous application" notices (Shield AI 2026-10-29: "Your application was already submitted. We received your
# previous application on October 23") must never read as an acknowledgement.
ALREADY_ON_FILE = re.compile(
    r"\balready (?:been )?(?:submitted|applied|received)\b|\bpreviously (?:applied|submitted)\b|"
    r"\bprevious (?:application|submission)\b|\byou(?:'re| are) (?:currently )?submitted (?:to|for) this\b|"
    r"\byou applied for this (?:job|position|role) on\b",
    re.I,
)
# Words that make an acknowledgement something else. Before it: a future, a condition or an example ("After you apply, your
# application will be received"). Anywhere in its sentence: a negation, a failure or an application already on file.
HEDGE_BEFORE = re.compile(
    r"\b(?:will|would|should|could|may|might|once|until|before|after|if|when|example|no|without)\b", re.I
)
NEGATION = re.compile(
    r"\b(?:not|never|cannot|unable|fail(?:ed|ure)?|errors?|issues?|already|previous(?:ly)?)\b|n['’]t\b", re.I
)
# Controls and notices of a page that is still a form, a review step or the employer's view of a stored application.
FORM_SIGNAL = re.compile(
    r"^\s*(?:button|input|a)\b[^:\n]*:\s*(?:submit(?: (?:my )?application)?|apply(?: now)?|next|continue|"
    r"save (?:and|&) continue|review(?: application)?|send (?:my )?application|complete (?:my )?application|"
    r"withdraw(?: my)? application)\s*$|"
    r"\bwithdraw (?:my )?application\b|\bissues? that needs? to be fixed\b|\bfields? to fix\b|"
    r"\bthis field is required\b|\bplease correct the errors?\b|\bstep \d+ of \d+\b",
    re.I | re.M,
)


def applied_view_url(url):
    """SuccessFactors' read-only view of a stored application (My Applications -> the job; ASSA ABLOY 2026-10-27).
    browser.portal_lists_application applies the same test to the live page and also reads its title and Withdraw."""
    query = dict(parse_qsl(urlsplit(str(url or "")).query))
    return query.get("subNavBarLevel") == "JOB_APPLIED" and query.get("fbja_action") == "fbja_viewApp"


def acknowledges(text):
    """The first sentence of `text` that states this application was accepted, or None. A sentence that hedges,
    negates or speaks of an application already on file does not count."""
    text = str(text or "")
    for match in ACKNOWLEDGEMENT.finditer(text):
        start = max(text.rfind(c, 0, match.start()) for c in ".!?\n") + 1
        ends = [i for i in (text.find(c, match.end()) for c in ".!?\n") if i >= 0]
        sentence = text[start : min(ends) if ends else len(text)]
        if not HEDGE_BEFORE.search(text[start : match.start()]) and not NEGATION.search(sentence):
            return match[0]
    return None


def final_page_verdict(evidence, *, url="", outline=""):
    """("acknowledged" | "already_applied" | "rejected", why) for a final page booked as this run's receipt.

    `evidence` is what the booker quotes, `url` the last page the run observed and `outline` the saved page text (empty
    when none was kept). The outline only ever vetoes: a banner can sit where the dump cannot read it, so its absence
    from the text proves nothing, while a Submit button or a Withdraw notice in it proves the page is not a receipt."""
    from .adapters.base import ALREADY

    if applied_view_url(url):
        return "already_applied", "The page is the employer's view of an application already on file"
    if not acknowledges(evidence):
        if ALREADY.search(evidence) or ALREADY_ON_FILE.search(evidence):
            return "already_applied", "The page says an application was already on file"
        return "rejected", "The evidence quotes no acknowledgement of this application"
    if outline:
        if FORM_SIGNAL.search(outline):
            return "rejected", "The saved page is still a form, a review step, a listing or an applied-application view"
        if ALREADY.search(outline) and not ACKNOWLEDGEMENT.search(outline):
            return "already_applied", "The saved page says an application was already on file"
    return "acknowledged", ""
