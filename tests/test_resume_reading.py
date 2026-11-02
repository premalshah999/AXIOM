"""A resume AXIOM can't read faithfully is said out loud (2026-11-02): friends' resumes were cut off in transcription, or differed from the PDF
only in where a gap fell, and tailoring quietly carried on from a resume rebuilt from the profile, with one long bullet per job and no projects."""

import asyncio
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from jobpilot.db import Job, Resume
from jobpilot.features.knowledge.writing import LONG_REPLIES, configured_model
from jobpilot.features.resumes import master, pipeline
from jobpilot.features.resumes.editor import ResumeDoc
from jobpilot.features.resumes.evidence import build as evidence_build

JOBS = [("Widget Engineer", "Zephyr Dynamics"), ("Gadget Analyst", "Birch Labs")]
BULLETS = ["Reworked telemetry pipeline serving warehouse clients", "Reduced incident count across regional deployments"]
LINES = [
    "Alex Example",
    "alex@example.test | (555) 010-0100",
    "EXPERIENCE",
    *[x for title, company in JOBS for x in (f"{title} at {company}", *BULLETS)],
    "PROJECTS",
    "Quartz Atlas",
    "Built geospatial lookups for public transit records",
    "SKILLS",
    "Languages: Python, Rust, SQL",
]


def text_pdf(lines=LINES, pages=1):
    out = io.BytesIO()
    page = canvas.Canvas(out)
    for _ in range(pages):
        y = 760
        for line in lines:
            page.drawString(50, y, line)
            y -= 14
        page.showPage()
    page.save()
    return out.getvalue()


def word_per_line_pdf(lines=LINES):
    """Each word on its own baseline, as some LaTeX classes write them."""
    out = io.BytesIO()
    page = canvas.Canvas(out)
    y = 760
    for line in lines:
        x = 50
        for n, word in enumerate(line.split()):
            page.setFont("Helvetica", 9)
            page.drawString(x, y + 0.7 * (n % 2), word)
            x += page.stringWidth(word, "Helvetica", 9) + 2.5
        y -= 14
    page.save()
    return out.getvalue()


def picture_page(page):
    image = io.BytesIO()
    Image.new("L", (200, 200), 200).save(image, "PNG")
    image.seek(0)
    page.drawImage(ImageReader(image), 0, 0, 600, 800)


def scan_pdf(footer="Scanned with ExampleScan - www.example.test - free scanning app"):
    out = io.BytesIO()
    page = canvas.Canvas(out)
    picture_page(page)
    page.drawString(30, 12, footer)
    page.save()
    return out.getvalue()


def half_scan_pdf():
    out = io.BytesIO()
    page = canvas.Canvas(out)
    y = 760
    for line in LINES:
        page.drawString(50, y, line)
        y -= 14
    page.showPage()
    picture_page(page)
    page.save()
    return out.getvalue()


def locked(user=""):
    reader = PdfReader(io.BytesIO(text_pdf()))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(user_password=user, owner_password="owner-secret", permissions_flag=0)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def upload(client, content, query=""):
    return client.post("/api/resumes" + query, data={"name": "resume", "roles": ""}, files={"file": ("r.pdf", content, "application/pdf")})


def whole_copy():
    return {
        "name": "Alex Example",
        "contact": ["alex@example.test", "(555) 010-0100"],
        "sections": [
            {
                "id": "exp",
                "title": "EXPERIENCE",
                "kind": "experience",
                "text": "",
                "entries": [
                    {"id": f"e{i}", "heading": f"{title} at {company}", "subheading": "", "dates": "", "location": "", "bullets": [{"id": f"b{i}{k}", "text": b} for k, b in enumerate(BULLETS)]}
                    for i, (title, company) in enumerate(JOBS)
                ],
            },
            {
                "id": "proj",
                "title": "PROJECTS",
                "kind": "projects",
                "text": "",
                "entries": [{"id": "p1", "heading": "Quartz Atlas", "subheading": "", "dates": "", "location": "", "bullets": [{"id": "pb", "text": "Built geospatial lookups for public transit records"}]}],
            },
            {"id": "sk", "title": "SKILLS", "kind": "skills", "text": "Languages: Python, Rust, SQL", "entries": []},
        ],
    }


def resume_row(service, content):
    """An upload as the route stores it, file and text."""
    import hashlib

    sha = hashlib.sha256(content).hexdigest()
    folder = service.config.data_dir / "resumes"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (sha + ".pdf")).write_bytes(content)
    text = master.readable_text(PdfReader(io.BytesIO(content)))
    with service.db.exclusive() as s:
        s.add(Resume(id="res", name="resume", filename=sha + ".pdf", sha256=sha, roles=[], text=text))
    return {"id": "res", "sha": sha, "text": text}


def add_job(service):
    with service.db.exclusive() as s:
        s.add(Job(id="j1", identity="j1", url="https://jobs.example.test/1", title="Data Engineer", company="ExampleSoft", description="Python and SQL pipelines for finance teams.", resume_id="res", status="ready", score=90))


def counting(reply=None, error=None):
    calls = []

    async def model(output_type, system, user):
        calls.append(user)
        if error:
            raise error
        return output_type(**(reply if reply is not None else whole_copy()))

    model.calls = calls
    return model


# ---------- the reply that was cut off, on the real model path ----------
class Honest(BaseHTTPRequestHandler):
    """An OpenAI-compatible model that stops at max_tokens as a real one does: past the cap the reply is cut off mid-JSON."""

    reply = ""
    limits: list = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        asked = json.loads(self.rfile.read(int(self.headers["content-length"])))
        limit = asked.get("max_tokens") or asked.get("max_completion_tokens")
        Honest.limits.append(limit)
        whole, cut = Honest.reply, len(Honest.reply) / 4 > limit
        body = json.dumps(
            {
                "id": "x",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "stub",
                "choices": [{"index": 0, "finish_reason": "length" if cut else "stop", "message": {"role": "assistant", "content": whole[: limit * 4] if cut else whole}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": min(limit, len(whole) // 4), "total_tokens": 10 + min(limit, len(whole) // 4)},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def honest(service, monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Honest)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(Honest, "limits", [])
    monkeypatch.setattr(service.config, "llm_provider", "deepseek")
    monkeypatch.setattr(service.config, "deepseek_api_key", "stub")
    monkeypatch.setattr(service.config, "deepseek_base_url", f"http://127.0.0.1:{server.server_port}/v1")
    yield configured_model(service.db, service.config)
    server.shutdown()
    server.server_close()


def big_resume():
    """Two pages, whose whole copy is well past 2000 tokens and inside 9000."""
    jobs = [(f"Senior Widget Engineer {i} at Zephyr Dynamics {i}", [f"Reworked telemetry pipeline number j{i}b{k} serving warehouse clients across regions with fewer incidents and measured throughput gains" for k in range(6)]) for i in range(12)]
    projects = [(f"Quartz Atlas {i}", [f"Built geospatial lookups variant p{i}b{k} for public transit records" for k in range(2)]) for i in range(4)]
    skills = "Languages: Python, Rust, SQL\nTools: Docker, Kubernetes, Terraform"
    lines = ["Alex Example", "EXPERIENCE"] + [x for head, bullets in jobs for x in (head, *bullets)] + ["PROJECTS"] + [x for head, bullets in projects for x in (head, *bullets)] + ["SKILLS", *skills.split("\n")]
    out = io.BytesIO()
    page = canvas.Canvas(out)
    y = 760
    for line in lines:
        page.setFont("Helvetica", 8)
        page.drawString(30, y, line)
        y -= 10
        if y < 40:
            page.showPage()
            y = 760
    page.save()
    entry = lambda head, bullets: {"heading": head, "bullets": [{"text": b} for b in bullets]}
    copy = {
        "name": "Alex Example",
        "sections": [
            {"title": "EXPERIENCE", "kind": "experience", "entries": [entry(*j) for j in jobs]},
            {"title": "PROJECTS", "kind": "projects", "entries": [entry(*j) for j in projects]},
            {"title": "SKILLS", "kind": "skills", "text": skills},
        ],
    }
    return out.getvalue(), copy, jobs, projects


def test_a_big_copy_comes_through_whole_on_the_real_model_path(service, honest):
    pdf, copy, jobs, projects = big_resume()
    resume = resume_row(service, pdf)
    Honest.reply = json.dumps(copy)
    assert len(Honest.reply) / 4 > 2000
    doc, _, origin = asyncio.run(pipeline.source_document(service.db, service.config, honest, resume))
    assert origin == "import" and Honest.limits == [9000]
    experience, made, skills = doc.sections
    assert [(e.heading, [b.text for b in e.bullets]) for e in experience.entries] == jobs
    assert [(e.heading, [b.text for b in e.bullets]) for e in made.entries] == projects
    assert "Terraform" in skills.text


def test_a_reply_cut_off_at_the_cap_is_said_and_nothing_else_is_tailored(service, honest, monkeypatch):
    """The root cause of 2026-11-01, kept honest by shrinking the cap back to what it was."""
    monkeypatch.setitem(LONG_REPLIES, "Imported", (2000, 170))
    pdf, copy, *_ = big_resume()
    resume_row(service, pdf)
    add_job(service)
    service.db.set_setting("profile", {"name": "Alex Example", "work": [{"company": "Other Inc", "title": "Rebuilt From Profile", "description": "One long bullet."}]})
    Honest.reply = json.dumps(copy)
    result = asyncio.run(pipeline.prepare_resume(service.db, service.config, "j1", mode="normal", model=honest))
    assert_stopped(result, "cut off", "Plain Apply")
    assert master.current_master(service.db) is None and set(Honest.limits) == {2000}


# ---------- every section survives the copy ----------
def test_a_whole_resume_gets_room_in_the_reply(monkeypatch):
    seen = {}

    async def structured(config, db, output_type, system, user, run_id=None, max_tokens=0, timeout=0):
        seen.update(max_tokens=max_tokens, timeout=timeout)
        return output_type()

    monkeypatch.setattr("jobpilot.models.structured", structured)
    asyncio.run(configured_model(None, None)(master.Imported, "s", "u"))
    assert seen == {"max_tokens": 9000, "timeout": 170}


def test_the_copy_keeps_every_job_bullet_project_and_skill(service):
    resume = resume_row(service, text_pdf())
    row = asyncio.run(master.import_master(service.db, service.config, counting(), resume["id"]))
    doc = ResumeDoc.model_validate(row["doc"])
    assert row["verification"]["faithful"], row["verification"]
    exp, proj, skills = doc.sections
    assert [e.heading for e in exp.entries] == [f"{t} at {c}" for t, c in JOBS]
    assert all([b.text for b in e.bullets] == BULLETS for e in exp.entries)
    assert proj.entries[0].heading == "Quartz Atlas" and len(proj.entries[0].bullets) == 1
    assert skills.text == "Languages: Python, Rust, SQL"


def test_a_copy_written_in_a_looser_shape_is_still_those_words():
    copy = whole_copy()
    for section in copy["sections"]:
        for entry in section["entries"]:
            entry["bullets"] = [b["text"] for b in entry["bullets"]]
            entry["location"] = None
    copy["sections"][-1]["text"] = ["Languages: Python, Rust, SQL", "Tools: Git"]
    copy["contact"] = "alex@example.test"
    doc = master._normalize(master.Imported(**copy))
    assert doc.sections[0].entries[0].bullets[1].text == BULLETS[1]
    assert doc.sections[0].entries[0].location == ""
    assert doc.sections[-1].text == "Languages: Python, Rust, SQL\nTools: Git"
    assert doc.contact == ["alex@example.test"]


def test_every_part_of_a_copy_has_its_own_id():
    copy = whole_copy()
    for section in copy["sections"]:
        section["id"] = "same"
        for entry in section["entries"]:
            entry["id"] = "same"
            for bullet in entry["bullets"]:
                bullet["id"] = "b1"
    doc = master._normalize(master.Imported(**copy))
    ids = [s.id for s in doc.sections] + [e.id for s in doc.sections for e in s.entries] + [b.id for s in doc.sections for e in s.entries for b in e.bullets]
    assert len(ids) == len(set(ids))


def test_a_contact_line_of_many_items_keeps_every_word():
    copy = {**whole_copy(), "contact": [f"link{i}.example.test" for i in range(12)]}
    doc = master._normalize(master.Imported(**copy))
    assert len(doc.contact) == 8 and "link11.example.test" in doc.contact[-1]


def test_a_long_section_of_plain_lines_fits():
    copy = whole_copy()
    copy["sections"].append({"title": "PUBLICATIONS", "kind": "text", "text": "A paper on transit records. " * 200, "entries": []})
    assert master._normalize(master.Imported(**copy)).sections[-1].title == "PUBLICATIONS"


def test_more_than_a_copy_can_hold_is_said_in_a_persons_words(service):
    resume = resume_row(service, text_pdf())
    copy = whole_copy()
    copy["sections"][0]["entries"][0]["bullets"] = [{"text": f"bullet {i}"} for i in range(30)]
    with pytest.raises(ValueError) as refused:
        asyncio.run(master.import_master(service.db, service.config, counting(copy), resume["id"]))
    assert "under EXPERIENCE" in str(refused.value) and "validation" not in str(refused.value)


def test_two_columns_go_to_the_model_whole():
    out = io.BytesIO()
    page = canvas.Canvas(out)
    left = [f"Reworked telemetry pipeline number {i} serving" for i in range(14)]
    right = [f"Quartz Atlas project {i} built" for i in range(14)]
    for x, column in ((40, left), (380, right)):
        for i, line in enumerate(column):
            page.drawString(x, 760 - i * 14, line)
    page.save()
    text = master.layout_text(out.getvalue(), "")
    assert "\n".join(left) in text and "\n".join(right) in text


def test_one_column_with_dates_at_the_right_keeps_its_layout():
    out = io.BytesIO()
    page = canvas.Canvas(out)
    for i in range(14):
        page.drawString(40, 760 - i * 14, f"Reworked telemetry pipeline number {i} serving warehouse clients")
    page.drawString(40, 560, "Widget Engineer at Zephyr Dynamics")
    page.drawString(480, 560, "Jan 2020 - Dec 2021")
    page.save()
    text = master.layout_text(out.getvalue(), "")
    assert "Dynamics   " in text


# ---------- what counts as the same words ----------
def copy_doc(**changes):
    return ResumeDoc.model_validate(master._normalize(master.Imported(**{**whole_copy(), **changes})).model_dump())


SOURCE = "\n".join(LINES)


def test_where_a_gap_falls_is_not_a_difference():
    for source in (SOURCE.replace("EXPERIENCE", "EXPERIEN CE"), SOURCE.replace("PROJECTS", "P R O J E C T S"), SOURCE.replace("Reworked telemetry", "Reworkedtelemetry"), SOURCE.replace("telemetry", "teleme-\ntry")):
        report = master.verify(source, copy_doc())
        assert report["faithful"], report


def test_a_ligature_is_the_letters_it_stands_for():
    assert master.tokens("Certiﬁed workﬂow") == master.tokens("Certified workflow")


@pytest.mark.parametrize(
    "drop",
    [
        lambda c: c["sections"].pop(1),  # The projects are gone.
        lambda c: c["sections"][0]["entries"][1]["bullets"].clear(),  # One job's bullets are gone.
        lambda c: c["sections"][0]["entries"].pop(0),  # A job is gone.
        lambda c: c["sections"][2].update(text="Languages: Python"),  # Skills are gone.
    ],
)
def test_a_lost_section_is_still_a_difference(drop):
    copy = whole_copy()
    drop(copy)
    report = master.verify(SOURCE, master._normalize(master.Imported(**copy)))
    assert not report["faithful"], report


def test_an_invented_word_is_still_a_difference():
    copy = whole_copy()
    copy["sections"][0]["entries"][0]["bullets"][0]["text"] = "Revamped telemetry pipeline serving warehouse clients"
    copy["sections"][2]["text"] = "Languages: Python, Rust, SQL, Kubernetes, Terraform, Ansible"
    assert not master.verify(SOURCE, master._normalize(master.Imported(**copy)))["faithful"]


# ---------- a PDF read one word per line ----------
def test_a_pdf_read_one_word_per_line_gets_its_lines_back():
    reader = PdfReader(io.BytesIO(word_per_line_pdf()))
    assert master.one_word_per_line("\n".join(p.extract_text() for p in reader.pages))
    text = master.readable_text(reader)
    assert not master.one_word_per_line(text)
    for line in LINES:
        assert line in text
    # Word boundaries come through, which the layout reading of these PDFs glues together.
    assert master.layout_text(word_per_line_pdf(), "") == text


def test_an_ordinary_pdf_is_read_as_before():
    reader = PdfReader(io.BytesIO(text_pdf()))
    assert master.readable_text(reader) == "\n".join(p.extract_text() for p in reader.pages)


# ---------- tailoring says why it stopped, and never uses another resume ----------
def assert_stopped(result, *words):
    assert result.status == "failed" and result.artifact is None
    assert result.error.code == "resume_unread"
    for word in words:
        assert word in result.error.detail, result.error.detail


def test_a_copy_that_comes_back_cut_off_stops_tailoring_in_plain_words(service):
    resume_row(service, text_pdf())
    add_job(service)
    # The profile holds a whole history: it used to be quietly tailored instead.
    service.db.set_setting("profile", {"name": "Alex Example", "work": [{"company": "Other Inc", "title": "Rebuilt From Profile", "description": "One long bullet."}]})
    model = counting(error=ValueError("Exceeded maximum retries (1) for output validation"))
    result = asyncio.run(pipeline.prepare_resume(service.db, service.config, "j1", mode="normal", model=model))
    assert_stopped(result, "cut off", "Plain Apply")


def test_a_timeout_says_it_was_a_timeout(service):
    resume_row(service, text_pdf())
    add_job(service)
    result = asyncio.run(pipeline.prepare_resume(service.db, service.config, "j1", mode="normal", model=counting(error=TimeoutError())))
    assert_stopped(result, "took too long")


def test_a_copy_that_differs_names_what_is_missing_and_where_to_go(service):
    resume_row(service, text_pdf())
    add_job(service)
    short = whole_copy()
    short["sections"].pop(1)
    result = asyncio.run(pipeline.prepare_resume(service.db, service.config, "j1", mode="normal", model=counting(short)))
    assert_stopped(result, "doesn't match your PDF", "Open Resumes", "plain Apply")
    assert any(word in result.error.detail for word in ("quartz", "atlas", "geospatial"))


def test_a_copy_with_added_words_says_that_rather_than_that_all_came_through():
    words = master.different_words({"coverage": 1.0, "added_ratio": 0.2, "missing": [], "added": ["kubernetes", "terraform"]})
    assert "kubernetes" in words and "came through" not in words


def test_the_same_pdf_isnt_paid_for_twice_unless_the_person_asks(service):
    resume = resume_row(service, text_pdf())
    short = whole_copy()
    short["sections"].pop(1)
    model = counting(short)
    for _ in range(2):
        with pytest.raises(pipeline.ResumeUnread):
            asyncio.run(pipeline.source_document(service.db, service.config, model, resume))
    assert len(model.calls) == 1
    with pytest.raises(pipeline.ResumeUnread):
        asyncio.run(pipeline.source_document(service.db, service.config, model, resume, fresh=True))
    assert len(model.calls) == 2


def test_trying_again_reads_it_again_and_can_succeed(service):
    resume = resume_row(service, text_pdf())
    short = whole_copy()
    short["sections"].pop(1)
    with pytest.raises(pipeline.ResumeUnread):
        asyncio.run(pipeline.source_document(service.db, service.config, counting(short), resume))
    doc, _, origin = asyncio.run(pipeline.source_document(service.db, service.config, counting(), resume, fresh=True))
    assert origin == "import" and any(s.kind == "projects" for s in doc.sections)


def test_a_faithful_two_page_copy_is_used_again_without_another_call(service):
    resume = resume_row(service, text_pdf())
    model = counting()
    asyncio.run(pipeline.source_document(service.db, service.config, model, resume))
    with service.db.exclusive() as s:
        row = master.current_master(service.db)
        row = s.get(master.RsMaster, row.id)
        row.verification = {**row.verification, "render_fill": {"pages": 2, "fill": 0.4}}
    _, _, origin = asyncio.run(pipeline.source_document(service.db, service.config, model, resume))
    assert origin == "import" and len(model.calls) == 1


def test_a_copy_the_person_edited_is_never_buried_by_another_reading(service):
    resume = resume_row(service, text_pdf())
    short = whole_copy()
    short["sections"].pop(1)
    asyncio.run(pipeline.source_document(service.db, service.config, counting(), resume))
    asyncio.run(master.save_master(service.db, service.config, master._normalize(master.Imported(**short)), master.Style()))
    model = counting()
    with pytest.raises(pipeline.ResumeUnread) as stopped:
        asyncio.run(pipeline.source_document(service.db, service.config, model, resume, fresh=True))
    assert "Open Resumes" in stopped.value.detail and not model.calls


def test_a_copy_approved_from_an_earlier_resume_is_named_as_that(service):
    resume = resume_row(service, text_pdf())
    row = asyncio.run(master.import_master(service.db, service.config, counting(), resume["id"]))
    master.approve(service.db, row["id"])
    assert asyncio.run(pipeline.source_document(service.db, service.config, counting(), resume))[2] == "master"
    newer = {**resume, "sha": "another-upload"}
    assert asyncio.run(pipeline.source_document(service.db, service.config, counting(), newer))[2] == "master_other"


def test_a_resume_too_long_to_copy_is_refused_before_it_costs_anything(service):
    long_lines = [f"Reworked telemetry pipeline number {i} serving warehouse clients across regions with fewer incidents" for i in range(52)]
    resume = resume_row(service, text_pdf(long_lines, pages=8))
    model = counting()
    with pytest.raises(pipeline.ResumeUnread) as stopped:
        asyncio.run(pipeline.source_document(service.db, service.config, model, resume))
    assert "too long" in stopped.value.detail and not stopped.value.retryable and not model.calls


def test_the_import_button_says_what_happened_in_plain_words(client, service, monkeypatch):
    assert upload(client, text_pdf()).status_code == 200
    monkeypatch.setattr(service.config, service.config.llm_provider + "_api_key", "k")

    async def cut_off(*_a, **_k):
        raise ValueError("Exceeded maximum retries (1) for output validation")

    monkeypatch.setattr("jobpilot.features.knowledge.writing.configured_model", lambda *_a, **_k: cut_off)
    reply = client.post("/api/v2/resumes/master/import", json={})
    assert reply.status_code == 409 and "cut off" in reply.json()["detail"]


# ---------- what the upload says ----------
def test_a_pdf_that_is_only_restricted_opens_and_one_that_wants_a_password_says_so(client):
    assert upload(client, locked()).status_code == 200
    refused = upload(client, locked(user="open-secret"))
    assert refused.status_code == 400 and "password" in refused.json()["detail"]


def test_a_scan_with_a_scanners_footer_is_still_a_picture(client):
    refused = upload(client, scan_pdf())
    assert refused.status_code == 400 and "picture of your resume" in refused.json()["detail"]


def test_a_pdf_whose_words_read_as_symbols_says_so(client):
    refused = upload(client, text_pdf(["(cid:12)(cid:45)(cid:77)(cid:9) (cid:81)(cid:3)(cid:18) %&'()*+ #$%^&*" for _ in range(12)]))
    assert refused.status_code == 400 and "symbols" in refused.json()["detail"]


def test_one_page_of_a_two_page_resume_being_a_picture_is_said(client):
    reply = upload(client, half_scan_pdf())
    assert reply.status_code == 200
    assert "Page 2" in reply.json()["warnings"][0] and "picture" in reply.json()["warnings"][0]


def test_an_ordinary_resume_has_nothing_to_say(client):
    assert upload(client, text_pdf()).json()["warnings"] == []


def test_a_very_long_pdf_says_only_the_start_is_read(client):
    reply = upload(client, text_pdf([f"Reworked telemetry pipeline number {i} serving warehouse clients across regions" for i in range(52)], pages=11))
    assert reply.status_code == 200 and "only the start" in reply.json()["warnings"][0]


def test_a_word_per_line_upload_stores_readable_lines(client, service):
    assert upload(client, word_per_line_pdf()).status_code == 200
    with service.db.session() as s:
        stored = s.query(Resume).one().text
    assert "Widget Engineer at Zephyr Dynamics" in stored


# ---------- the reading in the setup guide ----------
def extract(client, service, monkeypatch, content, reply=None):
    resume_id = upload(client, content).json()["id"]
    seen = {}

    async def structured(config, db, output_type, system, user, run_id=None, max_tokens=0, timeout=0):
        seen.update(system=system, user=user)
        return output_type(**(reply or {}))

    monkeypatch.setattr("jobpilot.models.structured", structured)
    answer = client.post("/api/profile/extract?resume_id=" + resume_id, json={})
    assert answer.status_code == 200, answer.text
    return answer.json(), seen


def test_a_long_resume_read_only_in_part_says_so(client, service, monkeypatch):
    lines = [f"Reworked telemetry pipeline number {i} serving warehouse clients across regions" for i in range(52)]
    found, seen = extract(client, service, monkeypatch, text_pdf(lines, pages=6))
    assert "first" in found["notice"] and "%" in found["notice"] and len(seen["user"]) == 16000


def test_a_short_resume_has_no_notice(client, service, monkeypatch):
    found, _ = extract(client, service, monkeypatch, text_pdf())
    assert found["notice"] == ""


def test_projects_are_read_and_each_bullet_keeps_its_own_line(client, service, monkeypatch):
    found, seen = extract(client, service, monkeypatch, text_pdf(), {"projects": [{"name": "Quartz Atlas", "description": "Built geospatial lookups"}]})
    assert found["projects"] == [{"name": "Quartz Atlas", "description": "Built geospatial lookups"}]
    assert "one per line" in seen["system"]


def test_more_history_than_a_profile_holds_doesnt_stop_the_next_step(client, service, monkeypatch):
    found, _ = extract(client, service, monkeypatch, text_pdf(), {"work": [{"company": f"Co {i}"} for i in range(40)], "skills": [f"S{i}" for i in range(300)]})
    assert len(found["work"]) == 30 and len(found["skills"]) == 200
    profile = client.get("/api/profile").json()
    profile.update(work=found["work"], skills=found["skills"])
    assert client.put("/api/profile", json=profile).status_code == 200


def test_a_project_note_with_a_dotted_name_keeps_its_whole_name():
    inv = evidence_build({"evidence": [{"id": "n1", "text": "Project: Node.js Tracker\nBuilt a tracker for transit riders\nWrote its test suite"}]})
    assert [p.title for p in inv.projects] == ["Node.js Tracker"]
    assert [u.text for u in inv.units] == ["Built a tracker for transit riders", "Wrote its test suite"]
    old = evidence_build({"evidence": [{"id": "n2", "text": "Project: Atlas. Built a lookup tool for riders."}]})
    assert [p.title for p in old.projects] == ["Atlas"] and [u.text for u in old.units] == ["Built a lookup tool for riders."]
