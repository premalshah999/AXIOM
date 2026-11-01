"""A company's icon, kept on this computer: found from the employer's own pages first, only an image that is the company's and
sharp enough is kept, and a miss shows its letter."""

import struct

import httpx
import pytest
from sqlalchemy import select

from jobpilot import logos
from jobpilot.db import Job
from jobpilot.logos import declared, domain_for, domain_in, image_info, logo, sharpness


def png(w=64, h=64, pad=40):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00" + b"0" * pad


PNG = png()


@pytest.mark.parametrize(
    "company,url,domain",
    [
        ("Two Sigma", "https://careers.twosigma.com/careers/x", "twosigma.com"),
        ("Toyota", "https://careers.toyota.com/us/en/job/1", "toyota.com"),
        ("Farmers Insurance", "https://jobs.farmersinsurance.com/job/1", "farmersinsurance.com"),
        ("Toyota", "https://toyota.wd503.myworkdayjobs.com/tmna/job/1", "toyota.com"),
        ("OpenTable", "https://job-boards.greenhouse.io/opentable/jobs/1", "opentable.com"),
        ("PepsiCo", "https://uscareers-pepsico.icims.com/jobs/1", "pepsico.com"),
        ("BerryDunn", "https://careers-berrydunn.icims.com/jobs/1", "berrydunn.com"),
        ("Example Labs, Inc.", "https://jobs.ashbyhq.com/example/1", "example.com"),
        ("Example", "https://careers.example.co.uk/job/1", "example.co.uk"),
    ],
)
def test_an_address_that_names_the_company_gives_its_domain(company, url, domain):
    assert domain_for(company, url) == domain


@pytest.mark.parametrize(
    "company,url",
    [
        ("WonderBotz", "https://www.adzuna.com/details/1"),  # A job board's own icon would be the wrong logo.
        ("Acme", "https://go.short.example/x"),
        ("Acme", "https://other.wd5.myworkdayjobs.com/x"),
        ("Acme", "https://job-boards.greenhouse.io/other/jobs/1"),
        ("", "https://careers.acme.com/1"),
        ("Acme", ""),
        ("Acme", "not a url"),
        ("A", "https://a.com/"),
    ],
)
def test_anything_else_asks_nobody(company, url):
    assert domain_for(company, url) is None


def test_a_posting_site_only_vouches_for_itself_not_for_the_company():
    assert domain_for("Example", "https://example.applytojob.com/apply/x") is None
    assert domain_for("Example", "https://example.dayforcehcm.com/x") is None


def test_the_posting_text_names_the_company_only_by_its_exact_name():
    assert domain_in("Example Corp", "Visit www.example.com/careers or mail jobs@example.com") == "example.com"
    assert domain_in("Example", "Part of www.examplegroup.com") is None  # a parent or partner is not the company
    assert domain_in("Example", "Apply at jobs.ashbyhq.com/example") is None
    assert domain_in("Ex", "www.ex.com") is None


@pytest.mark.parametrize(
    "body,ok",
    [
        (png(64, 64), True),
        (png(48, 48), True),
        (png(256, 160), True),  # a slightly wide mark still reads in a square tile
        (png(32, 32), False),  # blurry once drawn at 40 px on a retina screen
        (png(308, 100), False),  # a wordmark shrinks to an unreadable line
        (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", False),
        (b"<html>not an image</html>", False),
        (b"\x89PNG\r\n\x1a\n" + b"short", False),
        (png(64, 64, pad=200_000), False),  # too heavy to keep
        (b"", False),
    ],
)
def test_only_a_sharp_squarish_raster_image_is_kept(body, ok):
    assert bool(sharpness(body, logos.MIN_EDGE)) is ok


def test_an_ico_is_measured_by_its_largest_entry():
    entry = lambda w: bytes([w, w, 0, 0, 1, 0, 32, 0]) + struct.pack("<II", 100, 22)  # noqa: E731
    assert image_info(b"\x00\x00\x01\x00\x02\x00" + entry(16) + entry(64) + b"0" * 100)[:3] == ("ico", 64, 64)


PAGE = """<html><head>
<link rel="icon" href="https://cdn.example-ats.test/favicon.ico">
<link rel="icon" sizes="48x48" href="https://d2y4rc9q318ytb.cloudfront.net/accounts/abc/48x48/logo.png">
<link rel="icon" sizes="192x192" href="https://d2y4rc9q318ytb.cloudfront.net/accounts/abc/192x192/logo.png">
<link rel="apple-touch-icon" href="/apple.png">
<meta property="og:image" content="https://lever-client-logos.s3.us-west-2.amazonaws.com/x.png">
<meta property="og:image" content="https://www.workable.com/assets/facebook-preview.png">
</head></html>"""


def test_a_page_gives_the_image_its_system_serves_as_the_employers_own_never_the_systems_brand():
    assets, icons = declared(PAGE, "https://jobs.example-ats.test/acme/1")
    assert assets == [
        "https://d2y4rc9q318ytb.cloudfront.net/accounts/abc/192x192/logo.png",
        "https://d2y4rc9q318ytb.cloudfront.net/accounts/abc/48x48/logo.png",
        "https://lever-client-logos.s3.us-west-2.amazonaws.com/x.png",
    ]
    assert "https://www.workable.com/assets/facebook-preview.png" not in assets + icons  # a share card is not a logo
    assert icons[0] == "https://jobs.example-ats.test/apple.png"  # resolved against the page, apple-touch-icon first


def test_a_workday_tenant_logo_and_a_greenhouse_board_logo_are_the_employers():
    html = '<meta property="og:image" content="https://acme.wd5.myworkdayjobs.com/en-US/careers/assets/logo">'
    assert declared(html, "https://acme.wd5.myworkdayjobs.com/x")[0] == ["https://acme.wd5.myworkdayjobs.com/en-US/careers/assets/logo"]
    html = '<meta property="og:image" content="https://s2-recruiting.cdn.greenhouse.io/external_greenhouse_job_boards/logos/1/original/a.png">'
    assert declared(html, "https://job-boards.greenhouse.io/acme/jobs/1")[0]
    html = '<meta property="og:image" content="https://app.ashbyhq.com/api/images/org-theme-social/1/2">'
    assert declared(html, "https://jobs.ashbyhq.com/acme/1")[0] == []


class Net:
    """Stands in for the network: the pages and images it serves, and every address asked."""

    def __init__(self, monkeypatch, pages):
        self.pages, self.asked = pages, []
        monkeypatch.setattr(logos, "_get", self.get)

    async def get(self, client, url, limit, page=False, hops=3):
        self.asked.append(url)
        found = self.pages.get(url)
        if isinstance(found, Exception):
            raise found
        return (found, url) if found else None


@pytest.fixture
def net(monkeypatch):
    return lambda pages: Net(monkeypatch, pages)


LOGO = "https://acme.wd5.myworkdayjobs.com/careers/assets/logo"
HOME = b"<html><head><title>Acme | Careers</title></head><body>Hello</body></html>"
POSTING = "https://acme.wd5.myworkdayjobs.com/careers/job/1"
OG = f'<meta property="og:image" content="{LOGO}">'.encode()


async def test_the_employers_own_page_is_asked_first_and_nothing_else_when_it_has_the_logo(net):
    n = net({POSTING: OG, LOGO: png(128, 128)})
    assert await logos.find("Acme", [POSTING]) == (png(128, 128), "png")
    assert n.asked == [POSTING, LOGO]  # no third party


async def test_a_wordmark_on_the_page_falls_back_to_the_companys_own_icon_then_to_google(net):
    own = "https://acme.com/apple-touch-icon.png"
    n = net({POSTING: OG, LOGO: png(308, 100), "https://acme.com/": HOME, own: png(180, 180)})
    assert await logos.find("Acme", [POSTING]) == (png(180, 180), "png")
    assert not any("google.com" in url for url in n.asked)
    n = net({POSTING: OG, LOGO: png(308, 100), "https://acme.com/": HOME, logos.GOOGLE.format("acme.com"): png(64, 64)})
    assert await logos.find("Acme", [POSTING]) == (png(64, 64), "png")


async def test_google_is_not_asked_when_the_setting_forbids_third_parties_and_a_small_google_icon_is_passed_over(net):
    n = net({"https://acme.com/": HOME, logos.GOOGLE.format("acme.com"): png(64, 64)})
    assert await logos.find("Acme", [POSTING], third_party=False) is None
    assert not any("google.com" in url for url in n.asked)
    net({"https://acme.com/": HOME, logos.GOOGLE.format("acme.com"): png(16, 16)})
    assert await logos.find("Acme", [POSTING]) is None


async def test_a_company_no_address_vouches_for_is_asked_nothing_but_its_own_posting_page(net):
    n = net({"https://jobs.board.test/x": b"<html></html>"})
    assert await logos.find("Acme", ["https://jobs.board.test/x"]) is None
    assert n.asked == ["https://jobs.board.test/x"]


@pytest.mark.parametrize(
    "home",
    [
        b"<html><head><title>acme.com is for sale</title></head></html>",  # titled with the domain's own spelling only
        b"<html><head><title>Acme</title></head><body>This domain is parked free, courtesy of GoDaddy</body></html>",
        b"<html><head><title>Welcome</title></head></html>",  # does not name the company
        None,  # does not answer
    ],
)
async def test_a_guessed_address_that_is_parked_or_not_the_company_gets_no_icon_from_anyone(net, home):
    # "alarmcom.com" is only the tenant name plus .com: GoDaddy's icon is not the company's (2026-11-01).
    n = net({"https://acme.com/": home, logos.GOOGLE.format("acme.com"): png(64, 64), "https://acme.com/apple-touch-icon.png": png(180, 180)})
    assert await logos.find("Acme", [POSTING]) is None
    assert not any("google.com" in url or "apple-touch" in url for url in n.asked)


async def test_an_address_a_posting_lives_on_needs_no_guess_checked(net):
    n = net({"https://careers.acme.com/job/1": b"<html></html>", "https://acme.com/": b"", logos.GOOGLE.format("acme.com"): png(64, 64)})
    assert await logos.find("Acme", ["https://careers.acme.com/job/1"]) == (png(64, 64), "png")


async def test_every_source_failing_to_connect_is_an_error_but_one_answering_no_is_a_verdict(net):
    net({POSTING: httpx.ConnectError("offline"), "https://acme.com/": httpx.ConnectError("offline")})
    with pytest.raises(httpx.ConnectError):
        await logos.find("Acme", [POSTING])
    net({POSTING: httpx.ConnectError("slow page"), "https://acme.com/": HOME})
    assert await logos.find("Acme", [POSTING]) is None


async def test_a_closed_posting_leaves_the_next_one_to_answer(net):
    other = "https://acme.wd5.myworkdayjobs.com/careers/job/2"
    net({other: OG, LOGO: png(100, 100)})
    assert await logos.find("Acme", [POSTING, other]) == (png(100, 100), "png")


async def test_a_domain_only_the_posting_text_names_is_used_for_the_companys_own_site(net):
    n = net({"https://acme.com/": b"", "https://acme.com/apple-touch-icon.png": png(180, 180)})
    found = await logos.find("Acme", ["https://jobs.board.test/x"], ["Learn more at www.acme.com"])
    assert found == (png(180, 180), "png") and "https://acme.com/apple-touch-icon.png" in n.asked


@pytest.mark.parametrize("address", ["http://acme.com/x.png", "https://localhost/x.png", "https://10.0.0.1/x.png", "https://user:pw@acme.com/x.png"])
async def test_a_page_cannot_send_the_fetch_to_a_private_or_plain_http_address(address):
    async with httpx.AsyncClient() as client:
        assert await logos._get(client, address, 1000) is None


async def test_a_logo_is_found_once_and_kept(tmp_path, net):
    n = net({POSTING: OG, LOGO: PNG})
    assert await logo(tmp_path, "Acme", [POSTING]) == (PNG, "png")
    assert await logo(tmp_path, "Acme", [POSTING]) == (PNG, "png")
    assert n.asked == [POSTING, LOGO]
    assert (tmp_path / "logos" / "acme.png").read_bytes() == PNG


async def test_a_company_with_no_logo_is_not_asked_again_soon(tmp_path, net):
    n = net({})
    assert await logo(tmp_path, "Acme", [POSTING]) is None
    asked = len(n.asked)
    assert await logo(tmp_path, "Acme", [POSTING]) is None
    assert len(n.asked) == asked and (tmp_path / "logos" / "acme.none").exists()


async def test_being_offline_is_remembered_as_nothing(tmp_path, net):
    net({POSTING: httpx.ConnectError("offline"), "https://acme.com/": httpx.ConnectError("offline")})
    assert await logo(tmp_path, "Acme", [POSTING]) is None
    assert not (tmp_path / "logos" / "acme.none").exists()
    net({POSTING: OG, LOGO: PNG})
    assert await logo(tmp_path, "Acme", [POSTING]) == (PNG, "png")


@pytest.mark.parametrize("company", ["", "ab", "../../x", "!!"])
async def test_only_a_plain_company_name_reaches_the_disk_or_the_network(tmp_path, net, company):
    n = net({POSTING: OG, LOGO: PNG})
    assert await logo(tmp_path, company, [POSTING]) is None
    assert n.asked == []


@pytest.fixture
def jobs(service):
    with service.db.session() as s:
        for n, (company, url) in enumerate([("Acme", POSTING), ("Acme", "https://acme.wd5.myworkdayjobs.com/careers/job/2"), ("Other", "https://x.test/1")]):
            s.add(Job(identity=f"i{n}", url=url, title="Engineer", company=company))
        s.commit()


def test_the_endpoint_serves_a_validated_image_with_its_own_type_and_no_script_rights(client, config, jobs, net):
    net({POSTING: OG, LOGO: png(128, 128)})
    answer = client.get("/api/logo", params={"company": "Acme", "url": POSTING})
    assert answer.status_code == 200 and answer.headers["content-type"] == "image/png"
    assert answer.headers["x-content-type-options"] == "nosniff" and "default-src 'none'" in answer.headers["content-security-policy"]
    assert answer.content == png(128, 128)


def test_the_endpoint_asks_nobody_and_serves_nothing_when_logos_are_off(client, config, jobs, net):
    n = net({POSTING: OG, LOGO: PNG})
    config.company_logos = False
    assert client.get("/api/logo", params={"company": "Acme", "url": POSTING}).status_code == 404
    assert n.asked == []
    assert not (config.data_dir / "logos").exists()


def test_the_endpoint_only_looks_up_a_company_at_the_address_of_one_of_its_jobs(client, jobs, net):
    n = net({"https://evil.test/": b"<html></html>"})
    assert client.get("/api/logo", params={"company": "Acme", "url": "https://evil.test/"}).status_code == 404
    assert client.get("/api/logo", params={"company": "Nobody", "url": POSTING}).status_code == 404
    assert n.asked == []
