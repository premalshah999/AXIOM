"""Owner questions on the web app (owner 2026-10-23).

"fix telegram, a lot of questions it asks are almost always incomplete, does not have dropdowns, and the screenshots
don't show the right questions, also it's not smart, it cannot infer details or answers from my telegram answers
(cannot even get the intent of the field, i.e. if it is uploading something), and also, the webpage has stopped taking
answers."

Production on 2026-10-23: 225 open reviews matched the "Needs you" query; the 100 oldest were read and only then
narrowed to each job's latest run, all 100 were on superseded runs, and the page showed "Nothing waiting" while 37
questions were pending. Reviews kept only the field's bare label ("End Date" five times for one run, "C", "Search
for:"), an upload was asked as text, and engine reasons ("Repair agent: … ValueError …", "disclosure_scope") were shown
under questions.
"""

import sqlite3

import pytest

from jobpilot import owner_questions as oq
from jobpilot.db import Database, Job, Review, Run, Setting


def add_job(service, job_id, *, company="Acme", state="waiting_answer", run_id=None, created="2026-10-23T00:00:00"):
    with service.db.exclusive() as s:
        if not s.get(Job, job_id):
            s.add(
                Job(
                    id=job_id,
                    identity=job_id,
                    url=f"https://careers.example.test/{job_id}",
                    title="Data Engineer",
                    company=company,
                    ats="custom",
                    status=state,
                )
            )
        s.add(
            Run(
                id=run_id or "run-" + job_id,
                job_id=job_id,
                state=state,
                mode="submit",
                packet={},
                created_at=created,
            )
        )
    return run_id or "run-" + job_id


def add_review(service, review_id, run_id, question, options=(), *, key=None, reason="", context=None, created=None):
    with service.db.exclusive() as s:
        s.add(
            Review(
                id=review_id,
                run_id=run_id,
                question=question,
                options=list(options),
                key=key or "answers-v9:" + review_id,
                reason=reason,
                context=context or {},
                **({"created_at": created} if created else {}),
            )
        )


# ----- presentation --------------------------------------------------------------------------------------------------
def test_a_bare_history_row_label_names_its_row():
    context = {"history_kind": "work", "history_identity": {"company": "Globex", "title": "Analyst"}}
    assert oq.display("End Date", context) == "Work history: Globex · Analyst › End Date"
    assert oq.display("C", {"section": "Programming languages"}) == "Programming languages › C"
    # An element id is not a section the owner can read.
    assert oq.display("C", {"section": "workExperience-3"}) == "C"
    assert oq.display("What is your country of birth?", {"section": "Personal details"}).startswith("Personal details")


def test_upload_widget_text_is_not_part_of_the_question():
    noisy = (
        "Please upload a cover letter. * Drop files here or cover-letter.pdf successfully uploaded cover-letter.pdf "
        "2.64 KB Successfully Uploaded!"
    )
    assert oq.tidy(noisy) == "Please upload a cover letter."
    assert oq.tidy("* Do you have 5 years of experience? *") == "Do you have 5 years of experience?"


@pytest.mark.parametrize(
    "question, options, context, reason, kind",
    [
        ("Please upload your unofficial transcript.", [], {}, "", "upload"),
        ("Upload a file (5MB max)", [], {}, "", "upload"),
        ("Please share a work sample of your research", [], {}, "Required document upload", "upload"),
        ("Portfolio", [], {"type": "file"}, "", "upload"),
        ("Highest level of education", ["— Make a Selection —"], {}, "", "text"),
        ("Highest level of education", ["— Make a Selection —", "Master's Degree"], {}, "", "choice"),
        ("What is your country of birth?", [], {}, "", "text"),
    ],
)
def test_the_kind_of_answer_a_question_takes(question, options, context, reason, kind):
    assert oq.kind(question, options, context, reason) == kind


def test_placeholders_and_duplicates_are_not_choices():
    assert oq.choices(["— Make a Selection —", "Yes", "yes", "No Results", "No"]) == [(1, "Yes"), (4, "No")]


def test_a_typed_reply_maps_to_the_nearest_option_and_only_an_exact_one_is_taken_unasked():
    race = [
        "0 - White (not Hispanic or Latino) (United States of America)",
        "2 - Asian (Not Hispanic or Latino) (United States of America)",
    ]
    assert oq.match_option(["Yes", "No"], "no") == (1, True)
    assert oq.match_option(race, "asian") == (1, False)
    assert oq.match_option(["January", "February", "March"], "2") == (1, False)
    assert oq.match_option(["Eastern Cape", "Gauteng", "Western Cape"], "western cape province") == (2, False)
    assert oq.match_option(["Eastern Cape", "Gauteng"], "Maryland") is None


def test_owner_reasons_are_plain_words():
    assert "ValueError" not in oq.owner_reason("Repair agent: browser control crashed (ValueError …)")
    assert oq.owner_reason("disclosure_scope") == "Your saved profile doesn't answer this yet."
    assert "file" in oq.owner_reason("Required document upload", "upload")


@pytest.mark.parametrize(
    "reason, expected",
    [
        ("Clean twin browser's hCaptcha token delivered to website callback.", "security check"),
        ("Central identifier step did not reach its password screen", "sign-in"),
        ("Workday session/server error persisted after two draft restarts", "kept returning errors"),
        ("Browser control failed at forms.py:1234; application tab retained for recovery", "couldn't finish"),
        ("The employer explicitly rejected the account credentials", "sign-in"),
    ],
)
def test_browser_blockers_reach_the_owner_in_plain_words(reason, expected):
    text = oq.owner_blocker(reason)
    assert expected in text
    assert not any(word in text for word in ("twin", "callback", "forms.py", "identifier step", "draft restart"))


def test_a_bare_row_label_is_saved_with_its_row_and_an_ordinary_question_keeps_its_wording():
    row = {"history_kind": "education", "history_identity": {"school": "State University"}}
    assert oq.knowledge_question("End date month", row) == "Education: State University › End date month"
    assert oq.knowledge_question("What is your country of birth?", {"section": "About you"}) == (
        "What is your country of birth?"
    )


# ----- the web app ---------------------------------------------------------------------------------------------------
def test_needs_you_lists_questions_of_latest_runs_even_behind_a_hundred_stale_ones(client, service):
    """Reproduces "the webpage has stopped taking answers": the list came back empty."""
    for n in range(101):
        old = add_job(service, f"old{n}", run_id=f"old-run-{n}", created="2026-10-01T00:00:00")
        add_job(service, f"old{n}", run_id=f"new-run-{n}", state="failed", created="2026-10-02T00:00:00")
        add_review(service, f"stale{n:03d}", old, "Stale question?", created="2026-10-01T00:00:01")
    live = add_job(service, "live")
    add_review(
        service,
        "live-q",
        live,
        "End Date",
        reason="Repair agent: The run never reached submission (ValueError)",
        context={"history_kind": "work", "history_identity": {"company": "Globex", "title": "Analyst"}},
        created="2026-10-23T00:00:01",
    )
    reviews = client.get("/api/snapshot").json()["reviews"]
    assert [r["id"] for r in reviews] == ["live-q"]
    shown = reviews[0]
    assert shown["display_question"] == "Work history: Globex · Analyst › End Date"
    assert shown["kind"] == "text" and "ValueError" not in shown["why"] + shown["reason"]


def test_a_placeholder_only_choice_takes_a_typed_answer_and_a_real_choice_list_does_not(client, service):
    run = add_job(service, "edu")
    add_review(service, "edu-q", run, "What is the highest level of education you have completed?",
               ["— Make a Selection —"])
    add_review(service, "yn-q", run, "Are you over 18?", ["Yes", "No"])
    assert client.post("/api/reviews/edu-q", json={"answer": "Master's Degree"}).status_code == 200
    refused = client.post("/api/reviews/yn-q", json={"answer": "Maybe"})
    assert refused.status_code == 400 and refused.json()["detail"] == "Choose one of the offered answers"


def test_answer_messages_are_plain_words(client, service):
    run = add_job(service, "msg")
    add_review(service, "msg-q", run, "What is your country of birth?")
    message = client.post("/api/reviews/msg-q", json={"answer": "Freedonia"}).json()["message"]
    assert message.startswith("Saved.")
    assert "queue guard" not in message and "knowledge base" not in message and "deferred" not in message
    again = client.post("/api/reviews/msg-q", json={"answer": "Freedonia"})
    assert again.status_code == 400 and again.json()["detail"] == "That question is already answered."


def test_one_history_rows_answer_is_kept_with_its_row_and_not_given_to_another_row(client, service):
    run = add_job(service, "rows")
    first = {"history_kind": "work", "history_identity": {"company": "Globex", "title": "Analyst"}}
    second = {"history_kind": "work", "history_identity": {"company": "Initech", "title": "Engineer"}}
    add_review(service, "row-1", run, "End Date", key="answers-v9:end-date", context=first)
    add_review(service, "row-2", run, "End Date", key="answers-v9:end-date", context=second)
    assert client.post("/api/reviews/row-1", json={"answer": "06/2024"}).status_code == 200
    with service.db.session() as s:
        assert s.get(Review, "row-2").answer is None
    profile = service.db.get_setting("profile")
    assert "answers-v9:end-date" not in profile.get("approved_answers", {})
    assert profile["reviewed_answers"][-1]["question"] == "Work history: Globex · Analyst › End Date"


def test_an_upload_question_takes_a_file_kept_for_the_next_form_asking_for_it(client, service, config):
    run = add_job(service, "upload")
    add_review(service, "doc-q", run, "Please upload your unofficial transcript.", reason="Required document upload")
    typed = client.post("/api/reviews/doc-q", json={"answer": "I have it"})
    assert typed.status_code == 400 and "needs a file" in typed.json()["detail"]
    add_review(service, "doc-2", run, "Please upload your unofficial transcript.", key="answers-v9:doc-q",
               reason="Required document upload")
    sent = client.post(
        "/api/reviews/doc-2/file", files={"file": ("transcript.pdf", b"%PDF-1.4 synthetic", "application/pdf")}
    )
    assert sent.status_code == 200, sent.text
    assert "attach this file" in sent.json()["message"]
    path = oq.document_for(service.db, config.data_dir, "Academic transcript (PDF)", "Another Employer")
    assert path is not None and path.name == "transcript.pdf" and path.read_bytes() == b"%PDF-1.4 synthetic"
    profile = service.db.get_setting("profile") or {}
    assert not any("transcript" in a["question"].casefold() for a in profile.get("reviewed_answers", []))
    wrong = client.post("/api/reviews/doc-2/file", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert wrong.status_code == 400


def test_a_file_for_a_typed_question_is_refused(client, service):
    run = add_job(service, "typed")
    add_review(service, "typed-q", run, "What is your country of birth?")
    refused = client.post("/api/reviews/typed-q/file", files={"file": ("a.pdf", b"%PDF", "application/pdf")})
    assert refused.status_code == 400 and "typed answer" in refused.json()["detail"]


def test_answering_a_question_answers_the_same_question_waiting_on_other_jobs(client, service):
    """Three open "racial/ethnic background" questions waited although the owner had answered that exact question for
    another form: answers were shared only between reviews with the same field key (2026-10-23)."""
    options = ["Asian", "Black or African American", "White", "I prefer not to say"]
    first, second = add_job(service, "form-a"), add_job(service, "form-b", company="Initech")
    add_review(service, "a-q", first, "How would you describe your racial/ethnic background?", options)
    add_review(service, "b-q", second, "How would you describe your racial/ethnic background? *",
               ["Select...", *options])
    add_review(service, "b-row", second, "End Date",
               context={"history_kind": "work", "history_identity": {"company": "Globex"}})
    assert client.post("/api/reviews/a-q", json={"answer": "Asian"}).status_code == 200
    with service.db.session() as s:
        assert s.get(Review, "b-q").answer == "Asian"
        assert s.get(Review, "b-row").answer is None


async def test_open_questions_already_answered_elsewhere_are_settled_without_asking(service):
    from jobpilot.knowledge import save

    run = add_job(service, "sweep")
    save(service.db, "Graduation Date or Anticipated Graduation Date", "05/2026")
    add_review(service, "grad", run, "Graduation Date or Anticipated Graduation Date")
    add_review(service, "bare", run, "Date of Birth")
    service.db.set_setting("telegram_question_sent:grad", {"message_id": 1})  # Asked long ago.
    assert await service.settle_open_questions() == 1
    with service.db.session() as s:
        assert s.get(Review, "grad").answer == "05/2026" and s.get(Review, "bare").answer is None


def test_an_existing_reviews_table_gains_the_context_column(tmp_path):
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE reviews (id VARCHAR PRIMARY KEY, run_id VARCHAR, question TEXT, options JSON, key VARCHAR, "
        "reason TEXT, answer TEXT, created_at VARCHAR)"
    )
    connection.execute("INSERT INTO reviews VALUES ('r1','run','Q?','[]','k','', NULL, '2026-10-01')")
    connection.commit()
    connection.close()
    db = Database(f"sqlite:///{path}")
    Database(f"sqlite:///{path}")  # A second start finds the column and leaves it.
    with db.session() as s:
        review = s.get(Review, "r1")
        assert review.context is None and review.question == "Q?"
        assert s.get(Setting, "control") is not None


def test_every_question_says_what_kind_of_field_it_is_and_what_to_send():
    """Owner, 2026-10-23: "it does not tell the type of field, what input is required"."""
    from jobpilot.owner_questions import requirement

    assert requirement("Graduation date", [], {"type": "text", "widget": "date-text", "date_format": "month-year",
                                               "required": True}) == "Type MM/YYYY · required"
    assert requirement("Years of Python", [], {"type": "number"}) == "Type a number"
    assert requirement("Why us?", [], {"type": "textarea", "maxlength": 500}) == "Paragraph: a few sentences, at most 500 characters"
    assert requirement("Are you 18?", ["Yes", "No"], {"type": "radio"}) == "Yes / No"
    assert requirement("Country", ["India", "United States", "Canada"], {"type": "select"}) == "Dropdown: pick one of 3"
    assert requirement("Please upload your unofficial transcript.", [], {"type": "file", "accept": ".pdf,.docx"}) \
        == "File upload: send the document (.pdf, .docx)"


def test_adp_tax_credit_follow_ups_are_screener_questions():
    from jobpilot.answers.policy_rules import WOTC

    # Generic assistance needs the observed screener's TANF context (covered by FormSession tests).
    assert not WOTC.search("Have you or someone in your household received assistance at least 9 months in the past 18 months")
    for question in ("Have you received a conditional certification from the state workforce agency (SWA)?",
                     "Have you served in the U.S. military?"):
        assert WOTC.search(question), question


def _owner(service, **extra):
    from jobpilot.answers import Resolver
    from jobpilot.schemas import EducationEntry, Profile

    profile = Profile(
        education=[EducationEntry(school="State University", degree="Master of Science", end="2026-05")],
        facts={"us_citizen": False, "current_immigration_status_US": "OPT; expiration July 2029."},
        answer_rules={"history_day": "first"},
        **extra,
    )
    return Resolver(profile, service.db, service.config)


def test_profile_facts_answer_what_went_to_the_owner(service):
    """2026-10-23 questions the profile answers: Axtria's degree completion date, Quora's exact graduation date,
    Cruise Planner's non-immigrant category, Laansu's Public Trust clearability."""
    resolver = _owner(service)
    completion = resolver.local({"id": "a", "label": "Completion date of your highest degree (mm/yyyy)", "type": "text",
                                 "options": [], "required": True})
    assert completion.value == "05/2026"
    exact = resolver.local({"id": "b", "label": "Graduation Date or Anticipated Graduation Date", "type": "date",
                            "options": [], "required": True})
    assert exact.value == "2026-05-01"
    category = resolver.local({"id": "c", "label": "What non-immigrant category are you in (e.g., H-1B, F-1 OPT, L-2, etc.)?",
                               "type": "text", "options": [], "required": True})
    assert category.value == "F-1 OPT"
    clearance = resolver.local({"id": "d", "label": "Are you clearable for a Public Trust Clearance?", "type": "select",
                                "options": ["-- No answer --", "Yes", "No"], "required": True})
    assert clearance.value == "No"
