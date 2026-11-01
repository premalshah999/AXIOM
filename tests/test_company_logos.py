"""A company's icon, kept on this computer: found from the employer's own pages first, only an image that is the company's and
sharp enough is kept, and a miss shows its letter."""

import struct

import httpx
import pytest

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
        if isinstance(found, tuple):  # (body, the address it ended on after a redirect)
            return found
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


async def test_a_wordmark_on_the_page_gives_way_to_the_companys_own_square_icon_then_to_google(net):
    own = "https://acme.com/apple-touch-icon.png"
    n = net({POSTING: OG, LOGO: png(308, 100), "https://acme.com/": HOME, own: png(180, 180)})
    assert await logos.find("Acme", [POSTING]) == (png(180, 180), "png")
    assert not any("google.com" in url for url in n.asked)
    n = net({POSTING: OG, LOGO: png(308, 100), "https://acme.com/": HOME, logos.GOOGLE.format("acme.com"): png(64, 64)})
    assert await logos.find("Acme", [POSTING]) == (png(64, 64), "png")


async def test_a_wide_wordmark_is_still_the_companys_logo_when_no_square_symbol_exists(net):
    # 358 of 885 companies without a logo on 2026-11-01 had one declared by their own posting page, refused only for being wide.
    net({POSTING: OG, LOGO: png(308, 100), "https://acme.com/": HOME})
    assert await logos.find("Acme", [POSTING]) == (png(308, 100), "png")
    net({POSTING: OG, LOGO: png(900, 100), "https://acme.com/": HOME})
    assert await logos.find("Acme", [POSTING]) is None  # a strip, not a logo


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
    net({"https://careers.acme.com/job/1": b"<html></html>", "https://acme.com/": b"", logos.GOOGLE.format("acme.com"): png(64, 64)})
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
    assert len(n.asked) == asked and (tmp_path / "logos" / f"acme.miss{logos.RULES}").exists()


async def test_being_offline_is_remembered_for_minutes_not_a_month(tmp_path, net, monkeypatch):
    n = net({POSTING: httpx.ConnectError("offline"), "https://acme.com/": httpx.ConnectError("offline")})
    assert await logo(tmp_path, "Acme", [POSTING]) is None
    assert not (tmp_path / "logos" / f"acme.miss{logos.RULES}").exists()
    asked = len(n.asked)
    assert await logo(tmp_path, "Acme", [POSTING]) is None and len(n.asked) == asked  # not hammered
    monkeypatch.setattr(logos, "RETRY_OFFLINE_SECONDS", 0)
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


def kept_logo(config, name="acme", ext="png", body=PNG):
    folder = config.data_dir / "logos"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.{ext}").write_bytes(body)


def test_the_endpoint_serves_a_kept_image_with_its_own_type_a_year_of_cache_and_no_script_rights(client, config, jobs, net):
    n = net({})
    kept_logo(config, body=png(128, 128))
    states = client.get("/api/logos", params={"name": ["Acme"]}).json()["logos"]["Acme"]
    assert states["state"] == "ready"
    answer = client.get("/api/logo", params={"company": "Acme", "v": states["v"]})  # no address: a job's drawer knows only the name
    assert answer.status_code == 200 and answer.headers["content-type"] == "image/png"
    assert answer.headers["x-content-type-options"] == "nosniff" and "default-src 'none'" in answer.headers["content-security-policy"]
    assert "immutable" in answer.headers["cache-control"] and "31536000" in answer.headers["cache-control"]
    assert answer.content == png(128, 128)
    assert n.asked == []  # serving never looks anything up


def test_an_unknown_company_is_queued_not_fetched_while_the_page_waits(client, config, jobs, net, monkeypatch):
    n = net({POSTING: OG, LOGO: png(128, 128)})
    queued = []
    monkeypatch.setattr(logos.Shelf, "request", lambda self, company, lookup: queued.append((company, lookup)))
    # Before: the first GET /api/logo fetched the posting page, the company's site and Google inside the request, on the shared loop.
    first = client.get("/api/logos", params={"name": ["Acme", "Acme", "Nobody Inc"]}).json()
    assert first["logos"]["Acme"]["state"] == "pending" and first["logos"]["Nobody Inc"]["state"] == "pending"
    assert [c for c, _ in queued] == ["Acme", "Nobody Inc"]  # once per company, however many rows name it
    assert n.asked == []  # nothing was fetched while the page waited
    lookup = queued[0][1]
    assert sorted(u for u, _ in lookup("Acme")) == sorted([POSTING, "https://acme.wd5.myworkdayjobs.com/careers/job/2"])
    assert lookup("Nobody Inc") == []  # the worker looks at this company's own postings only
    assert client.get("/api/logo", params={"company": "Acme"}).status_code == 404  # nothing kept, and no lookup from here


def test_every_request_is_answered_with_nothing_when_logos_are_off(client, config, jobs, net):
    n = net({POSTING: OG, LOGO: PNG})
    config.company_logos = False
    assert client.get("/api/logos", params={"name": ["Acme"]}).json() == {"off": True, "logos": {}}
    assert client.get("/api/logo", params={"company": "Acme"}).status_code == 404
    assert n.asked == [] and not (config.data_dir / "logos").exists()


@pytest.fixture
def shelf(tmp_path):
    return logos.Shelf(tmp_path)


def postings(rows):
    return lambda company: rows.get(company, [])


async def test_the_background_queue_looks_a_company_up_once_however_many_rows_name_it(shelf, net):
    n = net({POSTING: OG, LOGO: png(128, 128)})
    shelf.scan()
    for _ in range(5):
        shelf.request("Acme", postings({"Acme": [(POSTING, "")]}))
    shelf.request("ACME", postings({"ACME": [(POSTING, "")]}))  # the same company spelled differently
    await shelf.queue.join()
    assert shelf.state("acme") == "ready" and n.asked == [POSTING, LOGO]
    shelf.request("Acme", postings({}))  # known now: not queued again
    assert shelf.waiting == set()


async def test_a_company_nobody_has_a_job_at_is_never_looked_up_and_a_miss_is_remembered(shelf, net):
    n = net({})
    shelf.scan()
    shelf.request("Made Up Name", postings({}))
    shelf.request("Acme", postings({"Acme": [(POSTING, "")]}))
    await shelf.queue.join()
    assert n.asked == [POSTING, "https://acme.com/"]  # only the real company, and only its own pages
    assert shelf.state("acme") == "none" and shelf.state("madeupname") == "later"
    asked = len(n.asked)
    shelf.request("Acme", postings({"Acme": [(POSTING, "")]}))
    assert len(n.asked) == asked and not shelf.waiting


async def test_a_verdict_from_older_rules_is_asked_again(shelf, net):
    (shelf.folder).mkdir(parents=True)
    (shelf.folder / "acme.none").touch()  # written when every wide wordmark was refused
    shelf.scan()
    assert shelf.state("acme") is None and not (shelf.folder / "acme.none").exists()


def real_png(size, color=(200, 30, 30, 255)):
    from PIL import Image
    import io

    out = io.BytesIO()
    Image.new("RGBA", size, color).save(out, "PNG")
    return out.getvalue()


def test_a_big_logo_is_kept_as_a_small_webp_the_size_a_tile_draws():
    pytest.importorskip("PIL")
    big = real_png((1000, 1000))
    body, ext = logos.shrink(big, "png")
    info = image_info(body)
    assert ext == "webp" and info[1:] == (logos.MAX_EDGE, logos.MAX_EDGE) and len(body) < 2_000 < len(big) or len(big) < 2_000
    wide, _ = logos.shrink(real_png((800, 200)), "png")
    assert image_info(wide)[1:] == (logos.MAX_EDGE, logos.MAX_EDGE // 4)  # shape kept


async def test_a_heavy_logo_is_accepted_once_it_can_be_shrunk(net):
    pytest.importorskip("PIL")
    import io
    import os

    from PIL import Image

    noisy = Image.frombytes("RGB", (700, 700), os.urandom(700 * 700 * 3))
    out = io.BytesIO()
    noisy.save(out, "PNG")
    assert len(out.getvalue()) > logos.MAX_IMAGE_BYTES  # was refused outright for weighing over 150 KB
    net({POSTING: OG, LOGO: out.getvalue()})
    body, ext = await logos.find("Acme", [POSTING])
    assert ext == "webp" and len(body) < 60_000


async def test_old_full_size_files_are_shrunk_by_the_worker_and_the_original_goes(shelf, net):
    pytest.importorskip("PIL")
    shelf.folder.mkdir(parents=True)
    (shelf.folder / "acme.png").write_bytes(real_png((600, 600)))
    shelf.scan()
    assert shelf.state("acme") == "ready" and shelf.legacy == ["acme"]
    shelf.request("Other", postings({}))  # any work starts the worker
    await shelf.queue.join()
    await __import__("asyncio").sleep(0.3)
    assert (shelf.folder / "acme.webp").is_file() and not (shelf.folder / "acme.png").exists()
    assert shelf.read("acme")[1] == "webp"


SVG_OK = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="40" fill="url(#g)"/></svg>'


@pytest.mark.parametrize(
    "svg,ok",
    [
        (SVG_OK, True),
        (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="120" height="40"><rect/></svg>', False),  # wide: square only
        (b'<svg viewBox="0 0 10 10"><script>alert(1)</script></svg>', False),
        (b'<svg viewBox="0 0 10 10" onload="x()"><rect/></svg>', False),
        (b'<svg viewBox="0 0 10 10"><image href="https://evil.test/x.png"/></svg>', False),
        (b'<svg viewBox="0 0 10 10"><foreignObject><div/></foreignObject></svg>', False),
        (b"<html><body>Not found</body></html>", False),
    ],
)
def test_an_svg_is_kept_only_when_it_carries_nothing_that_runs_or_loads(svg, ok):
    assert bool(logos.judge(svg, logos.MIN_EDGE)) is ok


def test_an_svg_wordmark_is_wide_but_welcome_when_nothing_square_exists():
    wide = b'<svg viewBox="0 0 120 40"><rect/></svg>'
    assert logos.judge(wide, logos.MIN_EDGE, wide_ok=True)[:2] == ("svg", "wide")


STRUCTURED = """<script type="application/ld+json">{"@type":"JobPosting","hiringOrganization":{"@type":"Organization","name":"%s","sameAs":"https://www.acmelabs.test/","logo":"https://cdn.example.test/acme/logo.png"}}</script>"""


def test_a_postings_structured_data_vouches_for_the_logo_and_site_only_under_the_companys_own_name():
    mine = STRUCTURED % "Acme Inc."
    assert logos.employer_logos(mine, "https://jobs.example.test/1", "Acme") == (["https://cdn.example.test/acme/logo.png"], ["acmelabs.test"])
    # A page whose organization is another company (an aggregator, a parent, a client) vouches for nothing.
    assert logos.employer_logos(STRUCTURED % "Other Holdings", "https://jobs.example.test/1", "Acme") == ([], [])
    assert logos.employer_logos(STRUCTURED % "Acme", "https://jobs.example.test/1", "Zeta") == ([], [])


def test_a_share_image_named_logo_counts_only_on_an_address_that_names_the_company():
    ok = '<meta property="og:image" content="https://www.acme.com/themes/img/acme-logo-careers.png">'
    banner = '<meta property="og:image" content="https://www.acme.com/themes/img/career-site-background.jpg">'
    other = '<meta property="og:image" content="https://www.ats-vendor.test/img/logo.png">'
    assert logos.employer_logos(ok, "https://x.oraclecloud.com/1", "Acme")[0] == ["https://www.acme.com/themes/img/acme-logo-careers.png"]
    assert logos.employer_logos(banner, "https://x.oraclecloud.com/1", "Acme")[0] == []
    assert logos.employer_logos(other, "https://x.oraclecloud.com/1", "Acme")[0] == []


async def test_a_company_site_the_posting_declares_is_vouched_for_without_a_guess(net):
    page = (STRUCTURED % "Zeta Labs").replace("acmelabs.test", "zetalabs.test").encode()
    url = "https://jobs.example.test/job/1"
    net({url: page, "https://zetalabs.test/": b"", "https://zetalabs.test/apple-touch-icon.png": png(180, 180)})
    # Nothing in the job's own address names the company; the posting's own structured data does.
    assert await logos.find("Zeta Labs", [url]) == (png(180, 180), "png")


async def test_the_page_is_read_without_a_full_html_parse(net, monkeypatch):
    import sys

    net({POSTING: OG, LOGO: png(128, 128)})
    sys.modules.pop("bs4", None)
    monkeypatch.setitem(sys.modules, "bs4", None)  # an import of it now fails: the logo path must not need it
    assert await logos.find("Acme", [POSTING]) == (png(128, 128), "png")


async def test_a_guessed_address_that_sends_the_visitor_to_another_site_is_not_the_company(net):
    # superapp.com led to a LinkedIn page titled "SuperApp | LinkedIn" and the company was shown with LinkedIn's icon (2026-11-01).
    page = b"<html><head><title>Acme | LinkedIn</title></head></html>"
    net({"https://acme.com/": (page, "https://www.linkedin.com/company/acme")})
    async with httpx.AsyncClient() as client:
        assert await logos._verified(client, "acme.com", "Acme") is False
    net({"https://acme.com/": (HOME, "https://www.acme.com/en/")})
    async with httpx.AsyncClient() as client:
        assert await logos._verified(client, "acme.com", "Acme") is True


def test_a_logo_drawn_in_white_is_not_kept_for_a_light_tile():
    pytest.importorskip("PIL")
    import io

    from PIL import Image

    white = Image.new("RGBA", (200, 60), (0, 0, 0, 0))
    for x in range(20, 180):
        for y in range(20, 40):
            white.putpixel((x, y), (255, 255, 255, 255))
    out = io.BytesIO()
    white.save(out, "PNG")
    assert logos.shrink(out.getvalue(), "png") is None  # a header logo for a dark page: invisible on the plate
    assert logos.shrink(real_png((200, 200)), "png") is not None
    assert logos.judge(b'<svg viewBox="0 0 10 10"><path fill="#FFFFFF" d="M0 0h10v10z"/></svg>', logos.MIN_EDGE) is None
    assert logos.judge(b'<svg viewBox="0 0 10 10"><path fill="#fff"/><path d="M0 0"/></svg>', logos.MIN_EDGE)


@pytest.mark.parametrize("site", ["https://acme.breezy.hr", "https://www.paylocity.com/", "https://careers.phenompeople.com", "https://www.unrelated-co.test"])
def test_a_postings_site_that_is_its_systems_or_unrelated_to_the_company_is_not_the_companys(site):
    page = (STRUCTURED % "Acme").replace("https://www.acmelabs.test/", site)
    assert logos.employer_logos(page, "https://acme.breezy.hr/p/1", "Acme")[1] == []


def test_a_postings_site_that_contains_the_companys_name_is_the_companys():
    page = (STRUCTURED % "Handshake").replace("https://www.acmelabs.test/", "https://joinhandshake.com/")
    assert logos.employer_logos(page, "https://jobs.ashbyhq.com/handshake/1", "Handshake")[1] == ["joinhandshake.com"]
