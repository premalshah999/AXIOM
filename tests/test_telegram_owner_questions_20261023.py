"""Telegram owner questions (owner 2026-10-23).

"a lot of questions it asks are almost always incomplete, does not have dropdowns, and the screenshots don't show the
right questions, also it's not smart, it cannot infer details or answers from my telegram answers (cannot even get the
intent of the field, i.e. if it is uploading something) ... Plus a lot of error and debugging texts from telegram".

Three days of production questions: 5 of 62 had more than the eight options shown as buttons, 5 had option text cut at
50 characters, 5 were uploads asked as text, a document sent in reply was ignored (only text was read), a typed reply
that was not an exact option was refused, the same question on a second job was asked again, double taps answered
"This review is invalid or already resolved", and replies read "Application remains protected by its queue guard".
"""

import httpx

from jobpilot import telegram
from jobpilot.db import Job, Review, Run
from jobpilot.knowledge import save

OWNER = "42"
PROVINCES = [
    "Eastern Cape", "Free State", "Gauteng", "KwaZulu-Natal", "Limpopo",
    "Mpumalanga", "Northern Cape", "North West", "Western Cape",
]


def enable(service):
    service.config.telegram_bot_token, service.config.telegram_user_id = "123:test", OWNER


def seed(service, review_id="q", question="Which province do you live in?", options=(), *, reason="", context=None,
         job_id="job", company="Acme", key=None):
    with service.db.exclusive() as s:
        if not s.get(Job, job_id):
            s.add(Job(id=job_id, identity=job_id, url=f"https://careers.example.test/{job_id}", title="Engineer",
                      company=company, ats="custom", status="waiting_answer"))
            s.add(Run(id="run-" + job_id, job_id=job_id, state="waiting_answer", mode="submit",
                      packet={"worker_slot": None}))
        s.add(Review(id=review_id, run_id="run-" + job_id, question=question, options=list(options),
                     key=key or "answers-v9:" + review_id, reason=reason, context=context or {}))


class Calls:
    def __init__(self, monkeypatch, results=None):
        self.items, self.results = [], results or {}
        monkeypatch.setattr(telegram, "api", self.api)

    async def api(self, _config, method, payload):
        self.items.append((method, payload))
        return self.results.get(method, {"message_id": len(self.items)})

    def of(self, method):
        return [payload for name, payload in self.items if name == method]


def update(text=None, *, reply_to=None, callback=None, document=None, message_id=500):
    sender = {"id": int(OWNER)}
    chat = {"id": int(OWNER), "type": "private"}
    if callback:
        return {"update_id": 1, "callback_query": {"id": "cb", "from": sender, "data": callback,
                                                    "message": {"message_id": message_id, "chat": chat}}}
    message = {"message_id": 900, "from": sender, "chat": chat}
    if text is not None:
        message["text"] = text
    if document:
        message["document"] = document
    if reply_to:
        message["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": 1, "message": message}


async def test_every_option_is_listed_and_buttons_are_paged(service, monkeypatch):
    enable(service)
    seed(service, options=["Select...", *PROVINCES])
    calls = Calls(monkeypatch)
    await telegram.ask_next_question(service)
    payload = calls.of("sendMessage")[0]
    for number, name in enumerate(PROVINCES, 1):
        assert f"{number}. {name}" in payload["text"]
    rows = payload["reply_markup"]["inline_keyboard"]
    assert len(rows) == 9 and rows[-1][-1] == {"text": "More ▶", "callback_data": "ap:q:1"}
    assert rows[0][0] == {"text": "1. Eastern Cape", "callback_data": "a:q:1"}  # index 0 is the placeholder
    assert all(len(b["callback_data"].encode()) <= 64 for row in rows for b in row)
    await telegram._handle_update(service, update(callback="ap:q:1"))
    edit = calls.of("editMessageReplyMarkup")[0]
    assert edit["message_id"] == 500
    assert edit["reply_markup"]["inline_keyboard"][0][0]["text"] == "9. Western Cape"


async def test_a_long_option_keeps_its_full_text_in_the_message(service, monkeypatch):
    enable(service)
    long = "2 - Asian (Not Hispanic or Latino) (United States of America)"
    seed(service, question="Race?", options=["0 - White (not Hispanic or Latino) (United States of America)", long])
    calls = Calls(monkeypatch)
    await telegram.ask_next_question(service)
    payload = calls.of("sendMessage")[0]
    assert "2. " + long in payload["text"]
    assert payload["reply_markup"]["inline_keyboard"][1][0]["text"].endswith("…")


async def test_a_question_names_its_history_row_and_is_plain(service, monkeypatch):
    enable(service)
    seed(service, question="End Date", reason="No employment row context to identify which end date",
         context={"history_kind": "work", "history_identity": {"company": "Globex", "title": "Analyst"}})
    calls = Calls(monkeypatch)
    await telegram.ask_next_question(service)
    text = calls.of("sendMessage")[0]["text"]
    assert "Work history: Globex · Analyst › End Date" in text
    assert "THIS message" not in text and "/query" not in text and "row context" not in text


async def test_a_tapped_answer_is_saved_once_and_its_buttons_go_away(service, monkeypatch):
    enable(service)
    seed(service, options=PROVINCES)
    calls = Calls(monkeypatch)
    reply = await telegram._handle_update(service, update(callback="a:q:2"))
    with service.db.session() as s:
        assert s.get(Review, "q").answer == "Gauteng"
    assert reply.startswith("✅ Gauteng\nSaved.")
    assert calls.of("editMessageReplyMarkup")[0]["reply_markup"] == {"inline_keyboard": []}
    again = await telegram._handle_update(service, update(callback="a:q:2"))
    assert again == "That question is already answered."


async def test_a_typed_reply_is_matched_to_the_nearest_option_and_confirmed(service, monkeypatch):
    enable(service)
    seed(service, options=PROVINCES)
    service.db.set_setting("telegram_question_messages", {"77": "q"})
    calls = Calls(monkeypatch)
    reply = await telegram._handle_update(service, update("western cape province", reply_to=77))
    assert reply == "Did you mean “Western Cape”?"
    confirm = next(p for p in calls.of("sendMessage") if p["text"] == reply)
    buttons = confirm["reply_markup"]["inline_keyboard"]
    assert buttons[0][0]["callback_data"] == "a:q:8" and buttons[1][0]["callback_data"] == "ap:q:0"
    with service.db.session() as s:
        assert s.get(Review, "q").answer is None  # Not saved until the owner confirms.
    exact = await telegram._handle_update(service, update("gauteng", reply_to=77))
    assert exact.startswith("Saved.")
    with service.db.session() as s:
        assert s.get(Review, "q").answer == "Gauteng"


async def test_an_unmatched_typed_reply_gets_the_choices_back(service, monkeypatch):
    enable(service)
    seed(service, options=PROVINCES)
    service.db.set_setting("telegram_question_messages", {"77": "q"})
    calls = Calls(monkeypatch)
    reply = await telegram._handle_update(service, update("Maryland", reply_to=77))
    assert reply.startswith("I couldn't match that")
    sent = next(p for p in calls.of("sendMessage") if p["text"] == reply)
    assert sent["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "a:q:0"


async def test_an_upload_question_asks_for_a_file_and_takes_a_document_reply(service, monkeypatch, config):
    enable(service)
    seed(service, question="Please upload your unofficial transcript.", reason="Required document upload")
    calls = Calls(monkeypatch, {"getFile": {"file_path": "documents/file_1.pdf"}})
    await telegram.ask_next_question(service)
    asked = calls.of("sendMessage")[0]["text"]
    assert asked.startswith("📎 ") and "with the file" in asked
    mid = service.db.get_setting("telegram_question")["message_id"]
    typed = await telegram._handle_update(service, update("I have one", reply_to=mid))
    assert "needs a file" in typed
    downloads = []

    def handler(request):
        downloads.append(str(request.url).split("/file/")[1].split("/", 1)[1])
        return httpx.Response(200, content=b"%PDF-1.4 synthetic transcript")

    real = httpx.AsyncClient
    monkeypatch.setattr(
        telegram.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    document = {"file_id": "F1", "file_name": "transcript.pdf", "file_size": 29}
    reply = await telegram._handle_update(service, update(document=document, reply_to=mid))
    assert reply.startswith("📎 Saved.") and downloads == ["documents/file_1.pdf"]
    with service.db.session() as s:
        assert s.get(Review, "q").answer == "File: transcript.pdf"
    saved = config.data_dir / "owner_documents"
    assert [p.name for p in saved.rglob("*.pdf")] == ["transcript.pdf"]


async def test_a_document_for_a_typed_question_is_not_used(service, monkeypatch):
    enable(service)
    seed(service, question="What is your country of birth?")
    service.db.set_setting("telegram_question_messages", {"77": "q"})
    Calls(monkeypatch)
    reply = await telegram._handle_update(service, update(document={"file_id": "F", "file_name": "a.pdf"}, reply_to=77))
    assert reply == "That question needs a typed answer, not a file."


async def test_a_question_already_answered_for_another_job_is_not_asked_again(service, monkeypatch):
    enable(service)
    save(service.db, "Which province in South Africa do you currently live in?", "Gauteng", PROVINCES)
    seed(service, question="Which province in South Africa do you currently live in?", options=PROVINCES,
         job_id="second", company="Initech")
    calls = Calls(monkeypatch)
    await telegram.ask_next_question(service)
    assert calls.of("sendMessage") == []
    with service.db.session() as s:
        assert s.get(Review, "q").answer == "Gauteng"


async def test_a_bare_label_is_never_answered_from_another_forms_answer(service, monkeypatch):
    enable(service)
    save(service.db, "End date month", "June")
    seed(service, question="End date month", options=["May", "June"])
    calls = Calls(monkeypatch)
    await telegram.ask_next_question(service)
    assert len(calls.of("sendMessage")) == 1


def test_internal_errors_are_not_forwarded_to_the_owner():
    from jobpilot.admission import QueueBlocked

    assert telegram.owner_error(RuntimeError("ConnectTimeout at forms.py:12")) == (
        "I couldn't do that right now. Please try again in a minute."
    )
    assert telegram.owner_error(
        QueueBlocked("Application intake is temporarily held", outcome="deferred")
    ) == "It will start on its own as soon as it can."
    assert telegram.owner_error(ValueError("That job was removed")) == "That job was removed."


async def test_the_owners_words_are_mapped_to_an_option_by_the_model(service, monkeypatch):
    """Owner, 2026-10-23: "from my answer, there should be an AI to infer that answer, to look up in dropdowns"."""
    enable(service)
    options = ["U.S. Citizen", "U.S. National", "Lawful Permanent Resident (Green Card Holder)", "Asylee", "Refugee",
               "None of the above"]
    seed(service, question="Are you a U.S. Citizen, U.S. National, Lawful Permanent Resident, Asylee, or Refugee?",
         options=options)
    service.db.set_setting("telegram_question_messages", {"77": "q"})
    Calls(monkeypatch)
    seen = {}

    async def model(_service, question, kind, offered, context, text):
        seen.update(kind=kind, offered=offered, text=text)
        return telegram.FieldReply(option="None of the above", quote="Indian citizen")

    monkeypatch.setattr(telegram, "interpret_reply", model)
    reply = await telegram._handle_update(service, update("I'm an Indian citizen on OPT", reply_to=77))
    assert reply == "Did you mean “None of the above”?"
    assert seen == {"kind": "choice", "offered": options, "text": "I'm an Indian citizen on OPT"}
    with service.db.session() as s:
        assert s.get(Review, "q").answer is None  # confirmed with one tap, never saved on the model's word


async def test_a_date_reply_is_saved_in_the_shape_the_field_wants(service, monkeypatch):
    enable(service)
    seed(service, question="Graduation Date", context={"type": "text", "widget": "date-text",
                                                      "date_format": "month-year"})
    service.db.set_setting("telegram_question_messages", {"77": "q"})
    Calls(monkeypatch)

    async def model(_service, question, kind, offered, context, text):
        return telegram.FieldReply(value="05/2026", quote="may 2026")

    monkeypatch.setattr(telegram, "interpret_reply", model)
    reply = await telegram._handle_update(service, update("may 2026", reply_to=77))
    assert "Saved as “05/2026” (the form wants MM/YYYY)" in reply
    with service.db.session() as s:
        assert s.get(Review, "q").answer == "05/2026"


async def test_the_question_says_what_to_send(service, monkeypatch):
    from jobpilot import telegram as tg

    payload = tg.question_payload(service, "q", "Years of experience with Python", [], {"type": "number",
                                  "required": True}, "", "Acme", "Engineer")
    assert "🔹 Type a number · required" in payload["text"]
