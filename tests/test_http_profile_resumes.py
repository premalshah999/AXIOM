"""HTTP contract of the profile and uploaded-resume routes."""


def test_the_profile_round_trips_without_the_server_owned_answer_stores(client):
    saved = client.put("/api/profile", json={"name": "Alex Example", "email": "alex@example.test"})
    assert saved.status_code == 200 and saved.json() == {"saved": True}
    body = client.get("/api/profile").json()
    assert body["name"] == "Alex Example" and body["email"] == "alex@example.test"
    assert not {"reviewed_answers", "approved_answers", "forgotten_answers"} & set(body)


def test_saving_the_profile_keeps_answers_the_dashboard_does_not_hold(client, service):
    client.put("/api/profile", json={"name": "Alex Example"})
    stored = service.db.get_setting("profile")
    stored["approved_answers"] = {"q": "kept"}
    service.db.set_setting("profile", stored)
    client.put("/api/profile", json={"name": "Alex Example", "email": "alex@example.test"})
    assert service.db.get_setting("profile")["approved_answers"] == {"q": "kept"}


def test_the_address_and_its_facts_stay_in_step(client, service):
    client.put("/api/profile", json={"facts": {"city": "Springfield"}})
    assert service.db.get_setting("profile")["address"]["city"] == "Springfield"
    # Editing the structured address wins over the stale fact beside it, and the other way round.
    client.put("/api/profile", json={"facts": {"city": "Springfield"}, "address": {"city": "Rivertown"}})
    saved = service.db.get_setting("profile")
    assert (saved["address"]["city"], saved["facts"]["city"]) == ("Rivertown", "Rivertown")
    client.put("/api/profile", json={"facts": {"city": "Lakeside"}, "address": {"city": "Rivertown"}})
    saved = service.db.get_setting("profile")
    assert (saved["address"]["city"], saved["facts"]["city"]) == ("Lakeside", "Lakeside")


def test_propose_history_needs_an_uploaded_resume(client):
    answer = client.post("/api/profile/extract", json={})
    assert answer.status_code == 400 and answer.json() == {"detail": "Upload a resume first"}


def test_organizing_evidence_without_a_model_key_is_a_409(client):
    # A RuntimeError in a handler is a 409 with the message (the shared error mapping).
    answer = client.post("/api/profile/organize", json={})
    assert answer.status_code == 409 and "no api key" in answer.json()["detail"].lower()


def test_upload_list_download_and_delete_a_resume(client, resume_pdf):
    uploaded = client.post(
        "/api/resumes",
        data={"name": "Backend", "roles": "Python, Backend"},
        files={"file": ("resume.pdf", resume_pdf, "application/pdf")},
    )
    assert uploaded.status_code == 200
    resume = uploaded.json()
    assert resume["name"] == "Backend" and resume["roles"] == ["Python", "Backend"]
    assert "text" not in resume and "filename" not in resume
    assert [r["id"] for r in client.get("/api/resumes").json()] == [resume["id"]]
    download = client.get(f"/api/resumes/{resume['id']}/download")
    assert download.status_code == 200 and download.content == resume_pdf
    assert download.headers["content-type"] == "application/pdf"
    assert client.delete(f"/api/resumes/{resume['id']}").json() == {"deleted": True}
    assert client.get("/api/resumes").json() == []
    assert client.delete(f"/api/resumes/{resume['id']}").status_code == 404


def test_the_same_resume_is_not_uploaded_twice(client, resume_pdf):
    files = {"file": ("resume.pdf", resume_pdf, "application/pdf")}
    assert client.post("/api/resumes", data={"name": "A"}, files=files).status_code == 200
    again = client.post(
        "/api/resumes", data={"name": "B"}, files={"file": ("again.pdf", resume_pdf, "application/pdf")}
    )
    assert again.status_code == 400 and "already uploaded" in again.json()["detail"]


def test_a_file_that_is_not_a_pdf_is_refused(client):
    answer = client.post(
        "/api/resumes",
        data={"name": "Notes"},
        files={"file": ("notes.pdf", b"plain text", "application/pdf")},
    )
    assert answer.status_code == 400


def test_download_of_an_unknown_resume_is_404(client):
    assert client.get("/api/resumes/unknown/download").status_code == 404
