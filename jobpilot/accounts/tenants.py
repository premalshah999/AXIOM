"""Which employer tenant an account belongs to, from the application URL."""

from urllib.parse import parse_qs
from urllib.parse import urlsplit
import re
from ..mail import origin


# sapsf.eu: SAP's newer EU data centres (Wipro's career55.sapsf.eu, 2026-10-27: its passcode box was refused as "an
# unexpected website" after the code mail had matched).
# ns2cloud.com: SAP's government cloud (L3Harris's career-hcm20.ns2cloud.com, 2026-10-25), also in AUTH_HOSTS.
SF_HOSTS = (".successfactors.com", ".successfactors.eu", ".sapsf.com", ".sapsf.eu", ".ns2cloud.com")


def sf_company(url):
    """The SuccessFactors tenant a page names, as (career host origin, company), else None. Westinghouse's sign-in
    page carries company= and career_company= (2026-10-16); its portal pages carry neither."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not (parts.hostname or "").lower().endswith(SF_HOSTS):
        return None
    query = parse_qs(parts.query)
    for name in ("company", "career_company"):
        values = query.get(name, [])
        if len(values) == 1 and values[0]:
            return origin(url), values[0]
    return None


def account_tenant(url, job_url=""):
    """Only explicit tenant identities or the originating employer site scope shared hosts."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host == "login.icims.com":
        # Central is a shared authentication host, not a shared employer
        # account. A different tenant's rejection/reset/signup says nothing
        # about this employer's candidate profile.
        try:
            employer = urlsplit(job_url)
            if (
                employer.scheme == "https"
                and employer.hostname
                and employer.hostname.lower() != host
                and employer.port in {None, 443}
                and not employer.username
                and not employer.password
            ):
                return "employer:" + origin(job_url)
        except ValueError:
            pass
        return "unresolved_shared_tenant"
    if host == "workforcenow.adp.com":
        for candidate in (url, job_url):
            parsed = urlsplit(candidate)
            if (parsed.hostname or "").lower() != host:
                continue
            query = {key.lower(): value for key, value in parse_qs(parsed.query).items()}
            tenant = query.get("cid", [])
            if len(tenant) == 1 and tenant[0]:
                return "cid:" + tenant[0]
        return "unresolved_shared_tenant"
    if host.endswith(SF_HOSTS):
        for candidate in (url, job_url):
            if tenant := sf_company(candidate):
                return "company:" + tenant[1]
        if job_url and origin(job_url) != origin(url):
            return "employer:" + origin(job_url)
        return "unresolved_shared_tenant"
    if host.endswith(".myworkdaysite.com"):
        for candidate in (url, job_url):
            parsed = urlsplit(candidate)
            if (parsed.hostname or "").lower() == host:
                match = re.search(r"/(?:recruiting|recruit)/([^/]+)/", parsed.path)
                if match:
                    return "recruiting:" + match[1]
        return "unresolved_shared_tenant"
    return ""


# Hosts where an ATS legitimately asks for the candidate password.
AUTH_HOSTS = {
    "workday": ["myworkdayjobs.com", "myworkdaysite.com", "myworkday.com", "workday.com"],
    "oracle": ["oraclecloud.com"],
    "icims": ["icims.com"],
    "taleo": ["taleo.net"],
    # ns2cloud.com: SAP's government cloud serves SuccessFactors sign-in and password pages for employers such as
    # L3Harris (career-hcm20.ns2cloud.com, 2026-10-25); without it the new password of a finished reset was refused.
    "successfactors": [
        "successfactors.com", "successfactors.eu", "sapsf.com", "sapsf.eu", "jobs2web.com", "ns2cloud.com"
    ],
    "eightfold": ["eightfold.ai"],
    "smartrecruiters": ["smartrecruiters.com"],
    "avature": ["avature.net"],
    "jobvite": ["jobvite.com"],
    "adp": ["adp.com"],
}


def registrable(host):
    parts = (host or "").lower().split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "ac", "gov"} and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# Other companies' sign-ins that a page can lead to, the job's own employer included: Apple's postings sign in on idmsa.apple.com, and an
# Apple ID is not a candidate account. The saved and shared passwords are never theirs. Whole domains first, then single hosts of a
# company whose other hosts are ordinary sites.
IDENTITY_PROVIDER = re.compile(
    r"(?:^|\.)(?:linkedin\.com|facebook\.com|github\.com|twitter\.com|x\.com|indeed\.com|live\.com|microsoftonline\.(?:com|us)|"
    r"okta\.com|oktapreview\.com|okta-emea\.com|auth0\.com|onelogin\.com|pingidentity\.com|pingone\.(?:com|eu)|duosecurity\.com|"
    r"amazoncognito\.com|b2clogin\.com|ciamlogin\.com)$|"
    r"^(?:accounts\.google\.com|appleid\.apple\.com|idmsa\.apple\.com|iforgot\.apple\.com|account\.apple\.com|"
    r"login\.microsoft\.com|account\.microsoft\.com|login\.windows\.net|login\.yahoo\.com)$",
    re.I,
)
# Outside assessment and questionnaire vendors, and the host a platform or a vendor gives its questionnaire (wotc.adp.com,
# taxcredit.icims.com). A launcher's wording is not what makes one: "Go to assessment" reached a vendor's login and typed.
ASSESSMENT_HOST = re.compile(
    r"(?:^|\.)(?:predictiveindex\.com|hirevue\.com|codility\.com|hackerrank\.com|hackerearth\.com|codesignal\.com|shl\.com|shlglobal\.com|"
    r"pymetrics\.(?:ai|com)|criteriacorp\.com|testgorilla\.com|harver\.com|wonderlic\.com|plum\.io|traitify\.com|ondemandassessment\.com|"
    r"cut-e\.com|karat\.com|mettl\.com|modernhire\.com|vervoe\.com|sparkhire\.com|taxcreditco\.com)$|"
    r"^(?:wotc|taxcredit|tax-credit|assess|assessment|assessments)\.",
    re.I,
)


def outside_sign_in(host):
    """"identity" or "assessment" for a host no saved password is ever typed into, whoever's posting led there, else ""."""
    host = (host or "").rstrip(".")
    if IDENTITY_PROVIDER.search(host):
        return "identity"
    return "assessment" if ASSESSMENT_HOST.search(host) else ""
