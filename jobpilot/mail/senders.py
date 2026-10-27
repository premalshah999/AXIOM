"""Which sender may deliver a code or link for which employer: known sender families, tenant tokens, Oracle attribution."""

from urllib.parse import urlsplit
import re


GREENHOUSE_SENDERS = {"greenhouse.io", "us.greenhouse-mail.io", "eu.greenhouse-mail.io", "anz.greenhouse.io"}


# iCIMS wraps the buttons of its account mail in its own click tracker: Schwab's "Reset your password" button went
# through clicks.icims.com and was rejected as token_format on every poll (2026-10-18/19).
# ClearCompany's sign-in button goes through its own short-link domain clrco.com (Allen Institute 2026-10-27: refused as
# token_format on every poll). Each tracker is trusted only for its own platform's rules.
TRACKER_PLATFORMS = {
    "https://clicks.icims.com": ("icims", "icims.com"),
    "https://tracking.icims.com": ("icims", "icims.com"),
    "https://clrco.com": ("clearcompany", "clearcompany.com"),
}
CLICK_TRACKERS = set(TRACKER_PLATFORMS)


def tracker_allowed(link_origin, link_origins, platform=""):
    """A tracker hop is followed only for its platform's rules; open_link still lands only on the rule's own origins.
    iCIMS behind an employer's own domain (Garmin's careers site, 2026-10-27) has no icims.com origin but is the same
    platform."""
    if link_origin not in TRACKER_PLATFORMS:
        return False
    name, host = TRACKER_PLATFORMS[link_origin]
    return platform == name or any((urlsplit(o).hostname or "").endswith(host) for o in link_origins)


# SuccessFactors serves sign-in, reset and activation pages from SAP's own career hosts whatever the employer's branded
# career site is: Nestle's reset mail linked career2.successfactors.eu and every poll rejected it (2026-10-24).
SF_PLATFORM_HOST = re.compile(
    r"^(?:career\d*|performancemanager\d*|hcm\d*)\.(?:successfactors\.(?:com|eu)|sapsf\.(?:com|eu|cn))$|"
    r"^career-hcm\d*\.ns2cloud\.com$",
    re.I,
)


def platform_link_allowed(link_origin, ats):
    """A SuccessFactors employer's mail may link SAP's career hosts; the message itself already passed the employer's
    sender, authentication, recipient and window checks."""
    return ats == "successfactors" and link_origin.startswith("https://") and bool(
        SF_PLATFORM_HOST.match(urlsplit(link_origin).hostname or "")
    )


# Multi-tenant ATS sender domains. Mail from them is attributed to one employer only by tenant
# identity (Workday names the tenant in the sender, e.g. mimecast@myworkday.com) or, when the
# message names no tenant, by the shared-sender lease that begin() holds for one run at a time.
TENANT_SENDERS = {"myworkday.com", "otp.workday.com"}


TENANT_SUFFIXES = ("oraclecloud.com", "icims.com", "taleo.net", "successfactors.com", "eightfold.ai")


GENERIC_LOCAL = re.compile(
    r"^(?:no-?reply|do-?not-?reply|donotreply|noreply\d*|notifications?|notify|mailer|info|support|"
    r"careers?|jobs?|recruit(?:ing|ment|er)?s?|talent|hr|otp|workday|alerts?|system|admin|apply|"
    r"candidates?|hello|team|accounts?|security|verify|verification)$"
)


GENERIC_LABELS = {
    "www",
    "careers",
    "career",
    "jobs",
    "job",
    "apply",
    "recruiting",
    "external",
    "candidate",
    "myworkdayjobs",
    "myworkdaysite",
    "myworkday",
    "workday",
    "oraclecloud",
    "oracle",
    "icims",
    "taleo",
    "successfactors",
    "sapsf",
    "eightfold",
    "jobs2web",
    "com",
    "net",
    "org",
    "edu",
    "gov",
    "hcmui",
    "candidateexperience",
    "sites",
    "inc",
    "corp",
    "corporation",
    "company",
    "group",
    "holdings",
    "technologies",
    "technology",
    "solutions",
    "services",
    "international",
    "global",
    "the",
    "and",
    "limited",
    "llc",
    "partners",
}


# Families whose verification mail often comes from the employer's own domain via the ATS.
EMPLOYER_SENDER_FAMILIES = {
    "oracle",
    "icims",
    "taleo",
    "successfactors",
    "eightfold",
    "smartrecruiters",
    "avature",
    "jobvite",
    "phenom",
    "custom",
}


def tenant_tokens(url, company=""):
    """Employer identity tokens from the job URL (tenant host/path) and the company name."""
    p = urlsplit(url)
    words = re.split(r"[^a-z0-9]+", (p.hostname or "").lower())
    if re.search(r"myworkdaysite\.com$|myworkdayjobs\.com$", p.hostname or ""):
        # wd1.myworkdaysite.com/recruiting/<tenant>/<site>
        words += re.split(r"[^a-z0-9]+", p.path.lower())[:4]
    words += re.split(r"[^a-z0-9]+", (company or "").lower())
    # "HealthEdge" -> healthedge, "My Funded Futures" -> myfundedfutures (an employer's domain label).
    words.append(re.sub(r"[^a-z0-9]", "", (company or "").lower()))
    out = []
    for w in words:
        if len(w) >= 3 and w not in GENERIC_LABELS and not re.fullmatch(r"wd\d+|fa|ocs|\d+", w):
            if w not in out:
                out.append(w)
    return out[:8]


def sender_allowed(domain, domains, tokens=(), employer_senders=False):
    """Exact rule domain or its subdomain; with employer senders, a domain the employer owns."""
    if any(domain == d or domain.endswith("." + d) for d in domains):
        return True
    if not employer_senders:
        return False
    # The employer's own domain: its registrable label equals the tenant or company name exactly
    # ("healthedge.com" for HealthEdge). Prefix look-alikes ("healthedge-careers.com") never pass.
    return employer_label(domain) in {t for t in tokens if len(t) >= 4}


def sender_domains_overlap(first, second):
    """Sender rules include subdomains, so their leases must cover the same subtree."""
    return any(a == b or a.endswith("." + b) or b.endswith("." + a) for a in first for b in second)


def employer_label(domain):
    labels = domain.lower().split(".")
    if len(labels) >= 3 and labels[-2] in {"co", "com", "org", "net", "ac", "gov"} and len(labels[-1]) == 2:
        return labels[-3]
    return labels[-2] if len(labels) >= 2 else ""


# Oracle tenants mostly mail their codes under their own identity (mailbox audit, 2026-10-18):
# - the employer's domain (recruitingnoreply@ford.com, noreply@honeywell.com, careers@talent.saic.com);
# - the tenant's pod address (edel.fa.sender@workflow…oraclecloud.com, exvu.fa.sender@oracle.gmfinancial.com);
# - the display name ("Goldman Sachs Recruiting" <gsrecruiting@oracle.com>).
# Codes are attributed per tenant, so tenants no longer wait for each other's verification.
ORACLE_SENDERS = {"oraclecloud.com", "oracle.com"}


ORACLE_INFRA = re.compile(r"(?:^|\.)(?:oracle|oraclecloud)\.com$")


NAME_STOP = {
    "the",
    "of",
    "and",
    "at",
    "for",
    "inc",
    "llc",
    "ltd",
    "co",
    "corp",
    "corporation",
    "company",
    "group",
    "plc",
}


GENERIC_FIRST = {
    "university",
    "bank",
    "national",
    "international",
    "global",
    "american",
    "united",
    "first",
    "general",
}


PLATFORM_NAMES = {"oracle", "oraclecloud"}


def oracle_pod(url):
    """The tenant pod in an Oracle Recruiting host: edel.fa.us2… -> edel, fa-exvu-saasfaprod1.fa.ocs… -> exvu."""
    host = (urlsplit(url).hostname or "").lower()
    if not host.endswith("oraclecloud.com"):
        return ""
    first = host.split(".")[0]
    pod = re.fullmatch(r"fa-([a-z0-9]+)-saasfaprod\d+", first)
    return pod[1] if pod else first


def employer_names(company):
    """Compact names an employer signs its mail with: "fordmotor", "ford", "ucsf" (initials of 3+ words)."""
    words = [w for w in re.findall(r"[a-z0-9]+", (company or "").lower()) if w not in NAME_STOP]
    names = set()
    if words:
        names.add("".join(words))
        if len(words[0]) >= 4 and words[0] not in GENERIC_FIRST:
            names.add(words[0])
        if len(words) >= 3:
            names.add("".join(w[0] for w in words))
    return {n for n in names if len(n) >= 3}


def oracle_attributed(address, display, text, company, pod, peers):
    """Whether an Oracle code mail is this tenant's while other tenants' Oracle code windows are open.

    Exact signals: this tenant's pod address or the employer's own domain. Name signals (display name, then body)
    count only for names no open peer shares."""
    local, _, domain = address.lower().partition("@")
    names = employer_names(company)
    if pod and re.fullmatch(re.escape(pod) + r"\.fa\.sender", local):
        return True
    if not ORACLE_INFRA.search(domain) and employer_label(domain) in names:
        return True
    shared = set().union(*(employer_names(other) for other, _ in peers)) if peers else set()
    mine = {n for n in names - shared if len(n) >= 4}
    shown = re.sub(r"[^a-z0-9]", "", (display or "").lower())
    if any(n in shown for n in mine):
        return True
    # "Powered by Oracle" footers make the platform's own name noise in a body.
    body = re.sub(r"[^a-z0-9]", "", (text or "").lower())
    peer_long = {n for other, _ in peers for n in employer_names(other) if len(n) >= 6} - PLATFORM_NAMES
    return any(n in body for n in mine - PLATFORM_NAMES if len(n) >= 6) and not any(
        n in body for n in peer_long
    )


ICIMS_SENDERS = {"icims.com"}
ICIMS_PORTAL_PREFIX = re.compile(
    r"^(?:careers?|jobs|campus|internal|external|staff|talent|uscareers|hourly|corporate)-", re.I
)


def icims_names(company, url):
    """Names an iCIMS employer's mail carries: its company name and its portal's tenant label
    (careers-ebscoind.icims.com -> ebscoind). A company recorded as an iCIMS host names nothing."""
    host = (urlsplit(url or "").hostname or "").lower()
    label = host.split(".")[0] if host.endswith(".icims.com") else ""
    tenant = re.sub(r"[^a-z0-9]", "", ICIMS_PORTAL_PREFIX.sub("", label))
    names = set() if re.search(r"\.icims\.", company or "", re.I) else employer_names(company)
    if len(tenant) >= 4:
        names.add(tenant)
    return names - {"icims", "career", "careers"}


def icims_attributed(subject, text, names, peer_names):
    """Whether a shared-sender iCIMS mail is this employer's while another employer's iCIMS window is open.

    iCIMS mails every employer from one sender and routes its buttons through tracking.icims.com, so neither the
    sender nor the link names the tenant; the subject does ("User account with EBSCO Information Services, LLC").
    The subject decides when it names exactly one side; otherwise the body must name this employer and no peer."""
    mine = {n for n in names - peer_names if len(n) >= 4}
    theirs = {n for n in peer_names - names if len(n) >= 4}
    for place, shortest in ((subject, 4), (text, 5)):
        compact = re.sub(r"[^a-z0-9]", "", (place or "").lower())
        own = any(n in compact for n in mine if len(n) >= shortest)
        other = any(n in compact for n in theirs if len(n) >= shortest)
        if own != other:
            return own
    return False


def tenant_conflict(address, domain, tokens):
    """True when a shared ATS sender visibly names a different tenant than this employer."""
    if not tokens:
        return False
    local = address.rpartition("@")[0].lower().split("+")[0]
    if domain in TENANT_SENDERS:
        compact = re.sub(r"[._-]", "", local)  # no_reply, no.reply, talent-acquisition
        return not (GENERIC_LOCAL.match(local) or GENERIC_LOCAL.match(compact)) and not any(
            t in local or t in compact for t in tokens
        )
    # Oracle's documented workflow transport names a data center, not an
    # employer. Recipient, DMARC, request window, one-use and shared-sender
    # lease checks still apply. Do not generalize to arbitrary Oracle hosts.
    if re.fullmatch(
        r"workflow\.(?:mail\.[a-z]{2,3}\d{1,2}\.oraclecloud\.com|"
        r"email\.[a-z]{2}-[a-z]+-\d{1,2}\.ocs\.oraclecloud\.com)",
        domain,
    ):
        return False
    for suffix in TENANT_SUFFIXES:
        if domain.endswith("." + suffix):
            labels = [x for x in domain[: -len(suffix) - 1].split(".") if x and x not in GENERIC_LABELS]
            # Region/pod labels (us2, em3, eu1) are infrastructure, not tenants.
            named = [
                x for x in labels if not re.fullmatch(r"wd\d+|fa|ocs|mail|email|[a-z]{2,3}\d{1,2}|\d+", x)
            ]
            return bool(named) and not any(t in x or x in t for x in named for t in tokens)
    return False


# Sender domains each ATS family uses for verification, reset and receipt mail. Subdomains of
# these are accepted (DMARC-aligned); multi-tenant ones additionally need tenant identity.
KNOWN_SENDERS = {
    "greenhouse": ["greenhouse.io", "us.greenhouse-mail.io", "eu.greenhouse-mail.io", "anz.greenhouse.io"],
    "workday": ["myworkday.com", "otp.workday.com"],
    "icims": ["icims.com"],
    "oracle": ["oraclecloud.com", "oracle.com"],
    "taleo": ["taleo.net"],
    # ns2cloud.com: SAP's government cloud (L3Harris's career-hcm20.ns2cloud.com, 2026-10-23); none of its five
    # password resets was ever matched, though the one Create showed the account exists.
    "successfactors": ["successfactors.com", "successfactors.eu", "sapsf.com", "ns2cloud.com"],
    "eightfold": ["eightfold.ai"],
    "smartrecruiters": ["smartrecruiters.com"],
    "avature": ["avature.net"],
    "jobvite": ["jobvite.com"],
    "adp": ["adp.com"],
    # Dayforce ID (dfid.dayforcehcm.com) registration and sign-in codes.
    "dayforce": ["dayforcehcm.com", "dayforce.com"],
}


# Platforms whose leases span employers on one sender rule. A start waits while another job holds its lease (Oracle:
# since per-tenant attribution, only a run for the same tenant), instead of starting a browser that can only defer at
# its own code step ("email sender busy", 2026-10-17/18).
SHARED_SENDER_ATS = {"oracle", "icims"}


def authenticated_sender(receiving_auth, domain):
    if not re.search(r"\bdmarc=pass\b", receiving_auth, re.I):
        return False
    if re.search(r"\bheader\.from=" + re.escape(domain) + r"(?:\s|;|$)", receiving_auth, re.I):
        return True
    # Workday's receiving result names workday.com for From: otp.workday.com.
    # Accept this parent alignment only with a passing DKIM signature for the
    # exact sender domain; a sibling/look-alike signature is insufficient.
    parent = re.search(r"\bheader\.from=([a-z0-9.-]+)(?:\s|;|$)", receiving_auth, re.I)
    return bool(
        parent
        and domain.endswith("." + parent[1].lower())
        and re.search(
            r"\bdkim=pass\b[^;]*\bheader\.i=@" + re.escape(domain) + r"(?:\s|;|$)",
            receiving_auth,
            re.I,
        )
    )
