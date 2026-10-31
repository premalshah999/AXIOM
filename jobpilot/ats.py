import hashlib
import functools
import json
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
    "career.sonepar.com",
    "careers-americas.hyundai.com",
    "careers.acer.com",
    "careers.acuitybrands.com",
    "careers.acuityinc.com",
    "careers.bauschlomb.com",
    "careers.belden.com",
    "careers.capgemini.com",
    "careers.cintas.com",
    "careers.clariant.com",
    "careers.coloplast.com",
    "careers.dentsplysirona.com",
    "careers.dominionenergy.com",
    "careers.duke.edu",
    "careers.endress.com",
    "careers.eon.com",
    "careers.ey.com",
    "careers.hcltech.com",
    "careers.hexion.com",
    "careers.hfsinclair.com",
    "careers.huntingtoningalls.com",
    "careers.knorr-bremse.com",
    "careers.kuehne-nagel.com",
    "careers.leonardodrs.com",
    "careers.liebherr.com",
    "careers.lyondellbasell.com",
    "careers.mahle.com",
    "careers.mccormick.com",
    "careers.metso.com",
    "careers.nrgenergy.com",
    "careers.omron.com",
    "careers.orkla.com",
    "careers.owenscorning.com",
    "careers.pge.com",
    "careers.phillips66.com",
    "careers.quality-ai.com",
    "careers.reckitt.com",
    "careers.sap.com",
    "careers.swissre.com",
    "careers.syncreon.com",
    "careers.teleflex.com",
    "careers.thehersheycompany.com",
    "careers.timken.com",
    "careers.vestas.com",
    "careers.vw.com",
    "careers.wartsila.com",
    "careers.westinghousenuclear.com",
    "careers.wipro.com",
    "corningjobs.corning.com",
    "jobdetails.nestle.com",
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
    "jobs.exxonmobil.com",
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
    "jobs.nexteraenergy.com",
    "jobs.nucor.com",
    "jobs.nutrien.com",
    "jobs.ottobock.com",
    "jobs.paccar.com",
    "jobs.rwe.com",
    "jobs.schaeffler.com",
    "jobs.sick.com",
    "jobs.supermicro.com",
    "jobs.telefonica.com",
    "jobs.ulalaunch.com",
    "jobs.voith.com",
    "jobs.volvocars.com",
    "jobs.volvogroup.com",
    "jobs.xpo.com",
    "jobs.zf.com",
    "recruiting.southernglazers.com",
    "www.careerprofile.epiroc.com",
    "www.careers.zurich.com",
    "www.optimumcareers.com",
)


NAMED_TIER = "named platform; the generic driver runs it"

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
            "sapsf.eu",
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
    # Named platforms with no driver of their own (2026-10-31: 31 jobs on Pinpoint, Comeet, Gem, CareerPuck, ClearCompany,
    # JobDiva and Simplify read as custom sites). The generic driver runs them exactly as it runs custom sites (LABEL_ONLY),
    # so the label is true and the guidance reaches the page, and nothing else about their runs moves.
    *(
        ATS(i, n, d, g, NAMED_TIER)
        for i, n, d, g in (
            ("pinpoint", "Pinpoint", ("pinpointhq.com",), "Single-page form with react-select pickers; it prints the file's name."),
            ("comeet", "Comeet", ("comeet.com", "comeet.co"), "Single-page form on Comeet's page or in the employer's embed."),
            ("gem", "Gem", ("jobs.gem.com",), "Single-page form with resume upload and screening questions."),
            ("careerpuck", "CareerPuck", ("careerpuck.com",), "A board over Greenhouse: its form is Greenhouse's, in an iframe after Apply."),
            ("clearcompany", "ClearCompany", ("clearcompany.com",), "Signing in is an emailed link (clrco.com tracker); the form follows."),
            ("jobdiva", "JobDiva", ("jobdiva.com", "jobdiva.co.uk"), "Candidate portal that asks for a sign-in."),
            ("dover", "Dover", ("app.dover.com",), "Hosted candidate site; the generic driver reads its controls."),
            ("csod", "Cornerstone", ("csod.com",), "Hosted candidate site; the generic driver reads its controls."),
            ("brassring", "IBM Kenexa BrassRing", ("brassring.com",), "Hosted candidate site; the generic driver reads its controls."),
            ("neogov", "NEOGOV", ("governmentjobs.com",), "Hosted candidate site; the generic driver reads its controls."),
            # Job boards: the posting is the employer's, and Apply leads there.
            ("simplify", "Simplify", ("simplify.jobs",), "Job board whose Apply leads to the employer's posting; follow it."),
            ("adzuna", "Adzuna", ("adzuna.com",), "Job board whose Apply for this job leads to the employer's posting; follow it."),
        )
    ),
]

# Career-site front ends host job pages, not applications: a script of theirs says nothing about where the application
# is made (PwC, Ascension and Crowe's Phenom sites apply on Workday or Oracle; 2026-10-31).
VENDORS = frozenset({"phenom"})
# Job boards that link out to the employer's own posting.
AGGREGATORS = frozenset({"simplify", "adzuna"})
# Named platforms the generic driver runs. Wherever a custom site is special-cased (its sender rule, its worker lane, the
# questionnaire and requisition checks), these are too, so that being named changes the label and nothing else.
LABEL_ONLY = frozenset(a.id for a in REGISTRY if a.tier == NAMED_TIER)


def custom_like(ats_id):
    """A custom career site, or a named platform that only the generic driver runs."""
    return ats_id == "custom" or ats_id in LABEL_ONLY


def unsettled(ats_id):
    """A label that has not found the application's platform: a custom site, a named platform with no driver, a
    career-site vendor or a job board. The page still has to say where the application is."""
    return custom_like(ats_id) or ats_id in VENDORS

# What a page shows of the platform behind it besides its URLs. A page-level fingerprint (a meta tag, a stylesheet) means
# the whole site is that platform's portal; an embed fingerprint (a script, a marker element) means the platform's widget
# sits on the employer's page. Each was read off saved pages (2026-10-31): Two Sigma, EA and Intuit's Avature meta tags and
# avacdn.net assets; Microsoft's and AARP's Eightfold stylesheets (a branded domain with no eightfold.ai in sight); the
# Pinpoint CDN on four tenants including a custom domain; Greenhouse's grnhse_app/grnhse_iframe on Roblox, Abnormal, Lyft's
# CareerPuck and Databricks; the iCIMS iframe holder on eight career sites; Comeet's widget loader.
PAGE_FINGERPRINTS = (
    ("avature", "meta", re.compile(r"^avature\.portal\.")),
    ("avature", "link", re.compile(r"\.avacdn\.net/")),
    ("eightfold", "link", re.compile(r"eightfold-font-base\.css|//static(?:-[a-z]+)?\.vscdn\.net/")),
    ("pinpoint", "link", re.compile(r"//d2n5ied94mazop\.cloudfront\.net/")),
)
EMBED_FINGERPRINTS = (
    ("greenhouse", "dom", re.compile(r"^grnhse_(?:app|iframe)$")),
    ("icims", "dom", re.compile(r"^icims_content_iframe$")),
    ("comeet", "dom", re.compile(r"^comeet-jsapi$")),
    ("eightfold", "script", re.compile(r"//static(?:-[a-z]+)?\.vscdn\.net/")),
    ("pinpoint", "script", re.compile(r"//d2n5ied94mazop\.cloudfront\.net/")),
)
DOM_MARKERS = sorted({"grnhse_app", "grnhse_iframe", "icims_content_iframe", "comeet-jsapi"})

# Company career sites often embed or link to the real ATS; the page reveals it.
EMBED_JS = (
    r"""() => {
  const out = [], add = (k, v, cap) => { if (v && out.filter((e) => e[0] === k).length < cap) out.push([k, v]); };
  for (const f of document.querySelectorAll('iframe[src]')) add('frame', f.src, 60);
  for (const a of document.querySelectorAll('a[href]')) {
    const t = (a.innerText || a.getAttribute('aria-label') || '').trim();
    if (/^apply|apply now|apply for/i.test(t)) add('apply', a.href, 60);
  }
  for (const s of document.querySelectorAll('script[src]')) add('script', s.src, 250);
  for (const l of document.querySelectorAll('link[rel~=stylesheet][href],link[rel=preload][href],link[rel=modulepreload][href]'))
    add('link', l.href, 120);
  for (const m of document.querySelectorAll('meta[name],meta[property]')) {
    add('meta', (m.getAttribute('name') || m.getAttribute('property')) + '=' + (m.content || '').slice(0, 120), 80);
  }
  for (const id of __DOM__) if (document.getElementById(id)) add('dom', id, 20);
  return out;
}""".replace("__DOM__", json.dumps(DOM_MARKERS))
)


def application_platform(ats_id):
    """Where an application is made: not a custom site, a career-site vendor or a job board."""
    return not (unsettled(ats_id) or ats_id in AGGREGATORS)


def classify_embedded(entries):
    """(ats id, url or None, how) for what a page shows of the platform behind it, or (None, None, "").

    A real application link beats a vendor's script: PwC, Ascension and Crowe's Phenom pages carry Phenom's scripts and
    apply on Workday or Oracle, yet a script-only hit returned "phenom" and no application link was ever followed
    (2026-10-31). So a frame (the form itself) comes first, then an Apply link, then the page's own fingerprints (meta,
    stylesheets), then embedded widgets (scripts, marker elements). Vendors and job boards are never an answer. A named
    platform that only the generic driver runs is an answer for the label alone, never a place to navigate to: the page's
    form already works where it is, and a real platform's hit outranks it."""
    hits = []
    for rank, kind in enumerate(("frame", "apply", "meta", "link", "script", "dom")):
        for k, value in entries:
            if k != kind:
                continue
            ats_id, url = None, None
            if kind in {"frame", "apply", "script"} and (found := detect(value)).id != "custom":
                ats_id, url = found.id, value
            if ats_id is None:
                key = value.partition("=")[0] if kind == "meta" else value
                ats_id = next((n for n, fk, rx in PAGE_FINGERPRINTS + EMBED_FINGERPRINTS if fk == kind and rx.search(key)), None)
            if not ats_id or ats_id in VENDORS or ats_id in AGGREGATORS:
                continue
            if kind == "frame" and not re.search(r"embed|job_app|in_iframe|apply|jobs/\d|careers", value, re.I):
                continue
            if kind == "script" or ats_id in LABEL_ONLY:
                url = None
            hits.append((ats_id in LABEL_ONLY, rank, ats_id, url, kind))
    if not hits:
        return None, None, ""
    *_, ats_id, url, kind = min(hits, key=lambda h: h[:2])
    return ats_id, url, kind


def detect_embedded(entries):
    """Return (ats, url) for an embedded application frame or apply link to a known ATS, or the platform a page shows."""
    ats_id, url, _ = classify_embedded(entries)
    return ats_id, url


# Avature on an employer's own domain: its locale-prefixed portal actions (jobs.lenovo.com/en_US/careers/JobDetail,
# jobs.ea.com/en_US/careers/Login, metlifecareers.com/en_US/ml/ApplicationMethods) or its folder pages
# (careers.bain.com/jobs/FolderDetail). Read as custom sites, their reset mail from <tenant>.avature.net was never
# trusted and seven employers' resets "did not arrive" (2026-10-28).
AVATURE_PATH = re.compile(
    r"^/(?:[a-z]{2}_[A-Z]{2}/[A-Za-z0-9_-]+/(?:JobDetail|FolderDetail|ApplicationMethods|Login|Register|"
    r"SearchJobs|PrepareForApply|ProfileForm|ApplicationEEO)\b|[A-Za-z0-9_-]+/(?:FolderDetail|ApplicationMethods)\b)"
)


def detect(url: str) -> ATS:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    for ats in REGISTRY:
        if any(host == x or host.endswith("." + x) for x in ats.domains):
            return ats
    if AVATURE_PATH.match(parts.path or ""):
        return next(ats for ats in REGISTRY if ats.id == "avature")
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


@functools.lru_cache(maxsize=65536)
def identity_variants(url: str):
    """Every identity a job at this URL may be stored or guarded under: today's, then the ones from before the platform
    hint was dropped and before SmartRecruiters/SuccessFactors requisition ids. Rows and `submission_guard:` settings
    written earlier still carry the older hashes.

    Cached like url_identity: the readiness overview and every autopilot tick parse each fresh posting's URL three
    times to check its submission guard (0.5 s of a 2.3 s Batch-page read with 7,600 jobs, 2026-10-22)."""
    variants = [
        canonicalize(url)[1],
        canonicalize(url, keep_platform_hint=True)[1],
        canonicalize(url, requisition_ids=False)[1],
    ]
    return tuple(dict.fromkeys(variants))


# Above the ~17,000 stored jobs so a scan over all of them never evicts itself. This one sat full at 65,536 entries of two
# URL-length strings each, ~25 MB holding postings scans had rejected long ago (heap census, 2026-10-30).
@functools.lru_cache(maxsize=32768)
def url_identity(url: str) -> str:
    """canonicalize(url)[1], cached. Admission compares one job with every job on its platform, once per waiting
    click per tick, on the event loop the browsers share (2026-10-21)."""
    return canonicalize(url)[1]


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
        if match and requisition_ids:
            # One requisition posted for several locations or career sites carries a -N suffix (_1214365,
            # _1214365-1, _1214365-3) and sometimes another site path; Workday counts one application for all.
            # HPE and KLA (2026-10-22) answered four reposts "already applied".
            identity = f"workday:{p.hostname}:{re.sub(r'(?<=[0-9])-[0-9]{1,2}$', '', match[2])}"
        elif match:
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
