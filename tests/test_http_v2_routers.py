"""HTTP smoke test of the three /api/v2 feature routers: each answers behind the same sign-in and error rules."""

from fastapi.testclient import TestClient


def test_the_v2_routers_need_a_sign_in(app):
    web = TestClient(app)
    for path in ("/api/v2/knowledge/summary", "/api/v2/resumes/master", "/api/v2/discovery/status"):
        assert web.get(path).status_code == 401, path


def test_knowledge_summary_and_facts_are_paged(client):
    summary = client.get("/api/v2/knowledge/summary")
    assert summary.status_code == 200 and "store_version" in summary.json()
    facts = client.get("/api/v2/knowledge/facts", params={"size": 5}).json()
    assert {"items", "total", "page", "size", "store_version"} <= set(facts)


def test_the_master_resume_starts_unapproved(client):
    answer = client.get("/api/v2/resumes/master")
    assert answer.status_code == 200 and isinstance(answer.json(), dict)


def test_discovery_status_and_feed_state(client):
    assert client.get("/api/v2/discovery/status").status_code == 200
    assert client.get("/api/v2/discovery/feeds").status_code == 200


def test_a_confirmed_answer_is_saved_listed_and_retired(client):
    saved = client.post(
        "/api/v2/knowledge/facts", json={"question": "Are you willing to work onsite?", "value": "Yes"}
    )
    assert saved.status_code == 200, saved.text
    listed = client.get("/api/v2/knowledge/facts", params={"q": "onsite"}).json()
    assert listed["total"] >= 1
    fact = next(i for i in listed["items"] if "onsite" in i["question"].lower())
    retired = client.post(f"/api/v2/knowledge/facts/{fact['id']}/retire", json={})
    assert retired.status_code == 200 and "store_version" in retired.json()


def test_an_unknown_attestation_is_a_422(client):
    assert (
        client.post("/api/v2/knowledge/attestations/unknown", json={"statement": "I confirm"}).status_code
        == 422
    )
    assert "items" in client.get("/api/v2/knowledge/attestations").json()


def test_knowledge_employers_are_paged(client):
    page = client.get("/api/v2/knowledge/employers").json()
    assert {"items", "total", "page", "size"} <= set(page)


def test_resume_reads_that_need_no_master_answer(client):
    assert client.get("/api/v2/resumes/evidence").status_code == 200
    assert client.get("/api/v2/resumes/editor/templates").status_code == 200
    assert client.get("/api/v2/resumes/artifacts").status_code == 200


def _an_uploaded_resume(client, resume_pdf, name="Example resume"):
    answer = client.post("/api/resumes", data={"name": name}, files={"file": (f"{name}.pdf", resume_pdf, "application/pdf")})
    assert answer.status_code == 200, answer.text
    return answer.json()["id"]


def test_tailoring_cannot_go_on_without_an_approved_master(client, service, resume_pdf):
    resume = _an_uploaded_resume(client, resume_pdf)
    service.db.set_setting("application_resume_policy", {"mode": "master", "resume_id": resume})
    refused = client.put("/api/v2/resumes/policy", json={"mode": "tailored"})
    assert refused.status_code == 409 and "approve" in refused.json()["detail"].lower()
    assert service.db.get_setting("application_resume_policy")["mode"] == "master"


def test_the_policy_switch_keeps_the_resume_it_sends(client, service, resume_pdf):
    resume = _an_uploaded_resume(client, resume_pdf)
    service.db.set_setting("application_resume_policy", {"mode": "master", "resume_id": resume})
    from jobpilot.features.resumes import tables

    with service.db.exclusive() as s:
        s.add(
            tables.RsMaster(
                id="ms_example",
                source_resume_id=resume,
                source_resume_sha="sha",
                origin="import",
                status="approved",
                doc={"name": "Alex Example", "contact": [], "sections": []},
                style={},
                verification={"faithful": True},
                model_calls=0,
            )
        )
    on = client.put("/api/v2/resumes/policy", json={"mode": "tailored"})
    assert on.status_code == 200, on.text
    assert on.json()["mode"] == "tailored" and on.json()["resume_id"] == resume
    assert client.get("/api/v2/resumes/master").json()["policy"]["mode"] == "tailored"
    off = client.put("/api/v2/resumes/policy", json={"mode": "master"})
    assert off.json()["mode"] == "master" and off.json()["resume_id"] == resume
    assert off.json()["automatic_tailoring"] is False and off.json()["source"] == "dashboard"


def test_the_policy_can_only_name_a_real_resume(client):
    assert client.put("/api/v2/resumes/policy", json={"mode": "master", "resume_id": "nope"}).status_code == 409
    assert client.put("/api/v2/resumes/policy", json={"mode": "everything"}).status_code == 422


def test_the_master_source_is_the_resume_the_policy_sends(client, service, resume_pdf):
    first = _an_uploaded_resume(client, resume_pdf)
    second = _an_uploaded_resume(client, resume_pdf + b"\n%second", name="Second resume")
    assert first != second
    for chosen, name in ((first, "Example resume"), (second, "Second resume")):
        service.db.set_setting("application_resume_policy", {"mode": "master", "resume_id": chosen})
        assert client.get("/api/v2/resumes/master").json()["source"] == {"id": chosen, "name": name}


def test_a_version_says_its_job_and_whether_an_application_went_out_with_it(client, service):
    from jobpilot.db import Job, Run
    from jobpilot.features.resumes.tables import RsArtifact

    with service.db.exclusive() as s:
        for job_id, company in (("job-sent", "ExampleSoft"), ("job-held", "Example Labs")):
            s.add(Job(id=job_id, identity=job_id, url="https://example.test/" + job_id, title="Engineer", company=company, location="Remote", description="", ats="general", status="ready", reason=""))
        s.flush()
        for artifact, job_id in (("ta_sent", "job-sent"), ("ta_held", "job-held")):
            s.add(
                RsArtifact(
                    id=artifact,
                    cache_key=artifact,
                    status="ready",
                    source_resume_id="r",
                    source_resume_sha="s",
                    profile_digest="p",
                    job_identity=job_id,
                    job_id=job_id,
                    description_sha="d",
                    policy_version="v",
                    selection=[],
                    validation={},
                )
            )
        s.add(Run(id="run-sent", job_id="job-sent", state="confirmed", mode="submit", packet={"resume_artifact_id": "ta_sent"}, finished_at="2026-10-07T07:38:12+00:00", receipt={}, reason=""))
        s.add(Run(id="run-other", job_id="job-held", state="cancelled", mode="submit", packet={"resume_artifact_id": "ta_held"}, receipt={}, reason=""))
    items = {a["artifact_id"]: a for a in client.get("/api/v2/resumes/artifacts").json()["items"]}
    assert items["ta_sent"]["job"]["company"] == "ExampleSoft"
    assert items["ta_sent"]["sent"] == {"state": "confirmed", "at": "2026-10-07T07:38:12+00:00"}
    # A run that was cancelled never sent it.
    assert items["ta_held"]["sent"] is None
    one = client.get("/api/v2/resumes/artifacts", params={"artifact_id": "ta_held"}).json()
    assert one["total"] == 1 and one["items"][0]["artifact_id"] == "ta_held"
