import hashlib
import re
from dataclasses import asdict, dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def oracle_job_catalog(url):
    """The CX search/recommendation page is never a job application form."""
    try:
        parsed = urlsplit(url)
        trusted_port = parsed.port in {None, 443}
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and (parsed.hostname or "").endswith(".oraclecloud.com")
        and not parsed.username
        and not parsed.password
        and trusted_port
        and re.fullmatch(r"/hcmUI/CandidateExperience/[^/]+/sites/[^/]+/jobs/?", parsed.path)
    )


@dataclass(frozen=True)
class ATS:
    id: str
    name: str
    domains: tuple[str, ...]
    guidance: str
    tier: str = "fixture-tested adapter; live validation pending"


# SuccessFactors Career Site Builder on employer domains: the job pages and the application wizard
# stay on these hosts (each answers /services/rss/job/ with its job feed; verified 2026-10-15).
SUCCESSFACTORS_CAREER_SITES = (
    "basf.jobs",
    "careers.acer.com",
    "careers.acuitybrands.com",
    "careers.acuityinc.com",
    "careers.capgemini.com",
    "careers.clariant.com",
    "careers.coloplast.com",
    "careers.dentsplysirona.com",
    "careers.endress.com",
    "careers.eon.com",
    "careers.ey.com",
    "careers.hcltech.com",
    "careers.hexion.com",
    "careers.knorr-bremse.com",
    "careers.kuehne-nagel.com",
    "careers.liebherr.com",
    "careers.lyondellbasell.com",
    "careers.mahle.com",
    "careers.mccormick.com",
    "careers.metso.com",
    "careers.omron.com",
    "careers.orkla.com",
    "careers.owenscorning.com",
    "careers.pge.com",
    "careers.reckitt.com",
    "careers.sap.com",
    "careers.swissre.com",
    "careers.syncreon.com",
    "careers.teleflex.com",
    "careers.timken.com",
    "careers.vestas.com",
    "careers.vw.com",
    "careers.wartsila.com",
    "careers.westinghousenuclear.com",
    "careers.wipro.com",
    "corningjobs.corning.com",
    "jobs.adidas-group.com",
    "jobs.ambu.com",
    "jobs.ametek.com",
    "jobs.amway.com",
    "jobs.atos.net",
    "jobs.barry-callebaut.com",
    "jobs.bayer.com",
    "jobs.bbraun.com",
    "jobs.boehringer-ingelheim.com",
    "jobs.bostonscientific.com",
    "jobs.bunge.com",
    "jobs.cargill.com",
    "jobs.cemex.com",
    "jobs.dana.com",
    "jobs.danfoss.com",
    "jobs.dsv.com",
    "jobs.eastman.com",
    "jobs.engie.com",
    "jobs.entergy.com",
    "jobs.erieinsurance.com",
    "jobs.farmersinsurance.com",
    "jobs.ferrero.com",
    "jobs.festo.com",
    "jobs.grundfos.com",
    "jobs.harley-davidson.com",
    "jobs.harting.com",
    "jobs.hatch.com",
    "jobs.ifm.com",
    "jobs.janushenderson.com",
    "jobs.kellanova.com",
    "jobs.kraton.com",
    "jobs.kuka.com",
    "jobs.l3harris.com",
    "jobs.lear.com",
    "jobs.lincolnfinancial.com",
    "jobs.netapp.com",
    "jobs.nutrien.com",
    "jobs.ottobock.com",
    "jobs.rwe.com",
    "jobs.schaeffler.com",
    "jobs.sick.com",
    "jobs.telefonica.com",
    "jobs.voith.com",
    "jobs.volvocars.com",
    "jobs.volvogroup.com",
    "jobs.xpo.com",
    "jobs.zf.com",
    "recruiting.southernglazers.com",
)


REGISTRY = [
    ATS(
        "greenhouse",
        "Greenhouse",
        ("greenhouse.io", "greenhouse.com"),
        "Application may be in an iframe. Upload resume, wait for parsing, then verify custom dropdowns and EEO.",
    ),
    ATS(
        "lever",
        "Lever",
        ("lever.co",),
        "Open the /apply form. Verify the resume remains attached after any later edits.",
    ),
    ATS(
        "ashby",
        "Ashby",
        ("ashbyhq.com",),
        "Open Apply. Reinspect conditional questions after selections; comboboxes require committed options.",
    ),
    ATS(
        "workday",
        "Workday",
        ("myworkdayjobs.com", "myworkdaysite.com"),
        "Deterministic adapter: Apply Manually (or Use My Last Application), shared-login sign-in or account "
        "creation with emailed verification, My Information, My Experience rows, questions, disclosures, "
        "self-identify, Review. Use authenticate for sign-in pages and verify_email for codes/links.",
    ),
    ATS(
        "oracle",
        "Oracle Recruiting",
        # Oracle's own careers site runs Oracle Recruiting Cloud (email PIN, apply-flow sections).
        ("oraclecloud.com", "careers.oracle.com"),
        "Deterministic adapter: email + terms, emailed PIN for returning candidates, apply-flow sections with "
        "pills and searchable selects, resume, e-signature, Submit. Use verify_email for the PIN.",
    ),
    ATS(
        "icims",
        "iCIMS",
        ("icims.com",),
        "Deterministic adapter: loads the in_iframe content directly, email step, sign-in or profile "
        "creation with the shared login (reset by email when needed), resume, profile, screening, Submit.",
    ),
    ATS(
        "smartrecruiters",
        "SmartRecruiters",
        ("smartrecruiters.com",),
        "Use the hosted application page, not a JSON API URL. Upload CV and inspect screening questions.",
    ),
    ATS(
        "taleo",
        "Taleo",
        ("taleo.net",),
        "Deterministic adapter: Login with the shared email as user name or New User registration, privacy "
        "agreement, Save and Continue pages, Review and Submit.",
    ),
    ATS("workable", "Workable", ("workable.com",), "Inspect application and screening fields after upload."),
    ATS("bamboohr", "BambooHR", ("bamboohr.com",), "Inspect application fields and document upload status."),
    ATS(
        "eightfold",
        "Eightfold",
        ("eightfold.ai",),
        "Apply opens a resume-first form. Upload, wait for parsing, then verify prefilled fields. "
        "Some tenants email a one-time code.",
    ),
    ATS(
        "successfactors",
        "SAP SuccessFactors",
        (
            "successfactors.com",
            "successfactors.eu",
            "sapsf.com",
            "jobs2web.com",
            *SUCCESSFACTORS_CAREER_SITES,
        ),
        "Sign in or create a candidate account, accept the data privacy statement, then one long form "
        "whose final button is Apply.",
    ),
    ATS("jobvite", "Jobvite", ("jobvite.com",), "Single or multi-step form; upload resume first."),
    ATS("avature", "Avature", ("avature.net",), "Multi-step portal, often with registration."),
    ATS("phenom", "Phenom", ("phenompeople.com",), "Career site front end; Apply hands off to the real ATS."),
    ATS("adp", "ADP Workforce Now", ("adp.com",), "Multi-step with optional account creation."),
    ATS(
        "ukg",
        "UKG / UltiPro",
        ("ultipro.com", "ukg.com", "ukg.net"),
        "Multi-step; sign in or continue as guest.",
    ),
    ATS("paylocity", "Paylocity", ("paylocity.com",), "Single long form with screening questions."),
    ATS("dayforce", "Dayforce", ("dayforcehcm.com",), "Candidate account and multi-step form."),
    ATS("jazzhr", "JazzHR", ("applytojob.com",), "Single-page form."),
    ATS("breezy", "Breezy HR", ("breezy.hr",), "Single-page form."),
    ATS("recruitee", "Recruitee", ("recruitee.com",), "Single-page form."),
    ATS("teamtailor", "Teamtailor", ("teamtailor.com",), "Single-page form, sometimes with email code."),
    ATS("personio", "Personio", ("personio.de", "personio.com"), "Single-page form."),
    ATS("rippling", "Rippling", ("rippling.com", "rippling-ats.com"), "Single-page form."),
]

# Company career sites often embed or link to the real ATS; the page reveals it.
EMBED_JS = r"""() => {
  const urls = [];
  for (const f of document.querySelectorAll('iframe[src]')) urls.push(['frame', f.src]);
  for (const a of document.querySelectorAll('a[href]')) {
    const t = (a.innerText || a.getAttribute('aria-label') || '').trim();
    if (/^apply|apply now|apply for/i.test(t)) urls.push(['apply', a.href]);
  }
  for (const s of document.querySelectorAll('script[src]')) urls.push(['script', s.src]);
  return urls.slice(0, 400);
}"""


def detect_embedded(entries):
    """Return (ats, url) for an embedded application frame or apply link to a known ATS."""
    for kind in ("frame", "apply", "script"):
        for k, url in entries:
            if k != kind:
                continue
            ats = detect(url)
            if ats.id == "custom":
                continue
            if kind == "script":
                return ats.id, None
            if kind == "frame" and not re.search(r"embed|job_app|in_iframe|apply|jobs/\d|careers", url, re.I):
                continue
            return ats.id, url
    return None, None


def detect(url: str) -> ATS:
    host = (urlsplit(url).hostname or "").lower()
    for ats in REGISTRY:
        if any(host == x or host.endswith("." + x) for x in ats.domains):
            return ats
    return ATS("custom", "Custom career site", (), "Inspect the application flow using observed controls.")


def icims_requisition_alias(left, right):
    """Protect career/talent aliases with the same employer and requisition.

    Existing identities and account state remain separate. Missing company
    metadata, unrelated tenants and different requisitions are not aliases.
    """
    if left.ats != "icims" or right.ats != "icims":
        return False
    if not left.company.strip() or left.company.strip().casefold() != right.company.strip().casefold():
        return False

    def key(url):
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if not host.endswith(".icims.com"):
            return None
        req = re.search(r"/jobs/(\d+)(?:/|$)", parsed.path)
        tenant = re.sub(r"^(?:careers?|talent)-", "", host)
        return (tenant, req[1]) if req else None

    first = key(left.url)
    return first is not None and first == key(right.url)


ICIMS_INTERNAL_PORTAL = re.compile(r"^(?:employees|internal|intranet)-([a-z0-9-]+)\.icims\.com$", re.I)


# Job feeds append the platform they read a posting from ("?ats=successfactors", "?ats=pinpointhq"). The employer's
# page ignores it, and it made one requisition two jobs: ASSA ABLOY's AI Scientist existed with and without the hint,
# 81 runs in all, and neither job's budget, history or duplicate protection knew about the other (2026-10-14..16).
# Only a platform name is dropped; any other value of `ats` is somebody's functional parameter and stays.
PLATFORM_HINTS = frozenset({a.id for a in REGISTRY} | {"pinpointhq"})


def platform_hint(key, value):
    return key.lower() == "ats" and value.strip().casefold() in PLATFORM_HINTS


def identity_variants(url: str):
    """Every identity a job at this URL may be stored or guarded under: today's, then the ones from before the platform
    hint was dropped and before SmartRecruiters/SuccessFactors requisition ids. Rows and `submission_guard:` settings
    written earlier still carry the older hashes."""
    variants = [
        canonicalize(url)[1],
        canonicalize(url, keep_platform_hint=True)[1],
        canonicalize(url, requisition_ids=False)[1],
    ]
    return tuple(dict.fromkeys(variants))


def canonicalize(url: str, *, keep_platform_hint=False, requisition_ids=True):
    p = urlsplit(url.strip())
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("Provide an http(s) application URL without embedded credentials")
    if detect(url).id == "smartrecruiters" and p.hostname == "api.smartrecruiters.com":
        raise ValueError("Use the applicant-facing SmartRecruiters job URL, not the API URL")
    # Preserve functional query parameters and SPA fragments. Drop tracking only.
    query = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
        and k.lower() not in {"source", "ref", "referrer", "sourceid", "jr_id"}
        and (keep_platform_hint or not platform_hint(k, v))
    ]
    path = p.path.rstrip("/")
    ats = detect(url).id
    if ats == "lever":
        path = re.sub(r"/apply$", "", path)
    if ats == "icims" and (internal := ICIMS_INTERNAL_PORTAL.match(p.hostname or "")):
        # Employee-only portals stop outside candidates at an employee sign-in. The same requisition
        # is posted on the employer's public careers portal (employees-hrblock -> careers-hrblock).
        p = p._replace(netloc=f"careers-{internal[1]}.icims.com")
    clean = urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(sorted(query)), p.fragment))
    params = dict(query)
    identity = clean
    if ats == "greenhouse":
        match = re.search(r"^/([^/]+)/jobs/(\d+)", path)
        board = match[1] if match else params.get("for")
        job_id = match[2] if match else params.get("token") or params.get("gh_jid")
        if board and job_id:
            identity = f"greenhouse:{board}:{job_id}"
    elif ats == "workday":
        identity_path = re.sub(r"/apply(?:/[^/?#]*)?$", "", path)
        match = re.search(r"/([^/]+)/job/.+_([^/]+)$", identity_path)
        if match:
            identity = f"workday:{p.hostname}:{match[1].casefold()}:{match[2]}"
    elif ats == "icims":
        match = re.search(r"/jobs/(\d+)(?:/|$)", path)
        if match:
            identity = f"icims:{p.hostname}:{match[1]}"
    elif ats == "ashby":
        match = re.match(r"^/([^/]+)/([0-9a-f-]{36})(?:/application)?$", path, re.I)
        if match:
            identity = f"ashby:{match[1].casefold()}:{match[2].casefold()}"
    elif ats == "smartrecruiters" and requisition_ids:
        # One posting, two URLs: /Intuitive/744000156658124 and /Intuitive/744000156658124-full-stack-engineer?oga=true
        # were two jobs, and the same application was submitted twice (2026-10-21).
        match = re.match(r"^/([^/]+)/(\d{6,})(?:-[^/]*)?$", path)
        if match:
            identity = f"smartrecruiters:{match[1].casefold()}:{match[2]}"
    elif ats == "successfactors" and requisition_ids:
        # Career Site Builder URLs carry a title slug before the requisition id (/job/<slug>/<id>/).
        match = re.search(r"/job/[^/]+/(\d{6,})$", path)
        if match:
            identity = f"successfactors:{p.hostname}:{match[1]}"
    if identity == clean and params.get("gh_jid"):
        identity = f"{p.hostname}:greenhouse:{params['gh_jid']}"
    return clean, hashlib.sha256(identity.encode()).hexdigest()


def catalog():
    return [asdict(x) for x in REGISTRY]
