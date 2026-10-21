import pytest

from jobpilot.adapters.base import Step
from support_icims import LOGIN, adapter, observation


def returning_page(service):
    url = "https://careers-acme.icims.com/jobs/42/engineer/job"
    obs = observation(url, password=False, email=False)
    obs["fields"] = [{"id": "locale", "type": "select", "label": "url", "name": "url",
                      "html_id": "footer-language-selector", "required": False}]
    obs["controls"] = [
        {"id": "return", "label": "Log back in!", "href": "/jobs/42/engineer/login?loginOnly=1&redirect=App"},
        {"id": "apply", "label": "Apply for this job onlineApply", "href": url + "?mode=apply&apply=yes"},
    ]
    value = adapter(service, obs)
    return value, obs


def test_atlassian_locale_menu_does_not_hide_the_job_entry(service):
    """Atlassian, run f0cc54b43e3d4d58b3e4da34b3a8c546, 2026-10-21: footer URL select caused FORM."""
    value, obs = returning_page(service)
    assert value.classify(obs) == Step.JOB
    assert value.find_apply(obs)["id"] == "apply"
    value.accounts.mark(value.job["url"], "created_locally", {"email": "alex@example.test", "password": "SyntheticLocal9!"})
    assert value.find_apply(obs)["id"] == "return"
    obs["controls"][0]["href"] = "https://careers-other.icims.com/jobs/42/engineer/login?loginOnly=1"
    assert value.find_apply(obs)["id"] == "apply"


def test_vtg_apply_here_uses_the_same_job_apply_protocol(service):
    """VTG, run 153ca3d84d6645c487578111845d7430, 2026-10-21: Apply HereApply did not match labels."""
    value, obs = returning_page(service)
    obs["fields"] = []
    obs["controls"][1]["label"] = "Apply HereApply"
    assert value.find_apply(obs)["id"] == "apply"
    assert value.classify(obs) == Step.JOB
    obs["controls"][1]["href"] = "https://careers-other.icims.com/jobs/42/engineer/job?mode=apply&apply=yes"
    assert value.find_apply(obs) is None


def test_authenticated_job_frame_uses_apply_despite_wrapper_login(service):
    """Atlassian, run f0cc54b43e3d4d58b3e4da34b3a8c546, 2026-10-21: returning route ends after sign-in."""
    value, obs = returning_page(service)
    value.accounts.mark(value.job["url"], "authenticated")
    obs["controls"] += [
        {"id": "wrapper", "label": "Log in", "href": "/login", "frame_url": obs["url"]},
        {"id": "logout", "label": "Log Out", "href": "/logout", "frame_url": obs["url"] + "?in_iframe=1"},
    ]
    assert value.find_apply(obs)["id"] == "apply"


async def test_atlassian_new_employer_password_survives_returning_login(service):
    """Atlassian, run f0cc54b43e3d4d58b3e4da34b3a8c546, 2026-10-21: use saved per-employer password."""
    service.config.icims_login = "SyntheticDifferentTenant9!"
    obs = observation(LOGIN, email=False)
    obs["controls"][0].update(aria_label="", automation_id="")
    value = adapter(service, obs)
    saved = {"email": "alex@example.test", "password": "SyntheticLocal9!"}
    value.accounts.mark(value.job["url"], "created_locally", saved)
    await value.on_sign_in(obs)
    value.locators["password"].fill.assert_awaited_once_with(saved["password"], timeout=4000)
    assert value.accounts.saved(LOGIN) == saved
    assert value.accounts.record(LOGIN)["state"] == "authenticated"


@pytest.mark.parametrize("foreign", ["https://example.test/login", "https://careers-other.icims.com/jobs/42"])
def test_other_employer_has_no_access_to_saved_local_password(service, foreign):
    """Atlassian, run f0cc54b43e3d4d58b3e4da34b3a8c546, 2026-10-21: credentials stay tenant-scoped."""
    value, _ = returning_page(service)
    value.accounts.mark(value.job["url"], "created_locally", {"email": "alex@example.test", "password": "SyntheticLocal9!"})
    assert value.accounts.saved(foreign) is None


@pytest.mark.parametrize(
    "status,shape,page,holds",
    [
        (302, [["PortalProfileFields.Resume_TEMP", 148604], ["PortalProfileFields.Resume_File", "file::0"]], "eeo", True),
        (302, [["PortalProfileFields.Resume_File", "file:pdf:111452"]], "questions", True),
        (200, [["PortalProfileFields.Resume_File", "file:pdf:111452"]], "eeo", False),  # refused, not accepted
        (302, [["PortalProfileFields.Resume_TEMP", 0], ["PortalProfileFields.Resume_File", "file::0"]], "eeo", False),
        (302, [["PortalProfileFields.Resume_TEMP", 148604]], "candidate", False),  # the profile itself uploads
    ],
)
def test_a_later_step_uses_the_resume_the_accepted_profile_holds(service, status, shape, page, holds):
    """VTG, runs 153ca3d84d6645c487578111845d7430 and 118a5c2ad2094cbeb5ed2273d45ff10b, 2026-10-21: a resumed run's final
    submit was refused for want of a resume attachment although the profile had been posted with it (302)."""
    from types import SimpleNamespace

    from jobpilot.db import Event, Job, Run

    with service.db.exclusive() as s:
        job = Job(identity=f"vtg-{status}-{page}-{len(shape)}", url="https://careers-acme.icims.com/jobs/42/engineer/job",
                  company="Acme", title="Engineer")
        s.add(job)
        s.flush()
        run = Run(job_id=job.id, state="needs_review", mode="submit", packet={})
        s.add(run)
        s.flush()
        s.add(Event(run_id=run.id, kind="form_post", message="post",
                    data={"path": "/jobs/42/engineer/candidate", "status": status, "shape": shape}))
        job_id = job.id
    url = f"https://careers-acme.icims.com/jobs/42/engineer/{page}?in_iframe=1"
    obs = {"url": url, "fields": [{"id": "g", "type": "select", "label": "Gender", "frame_url": url}], "controls": []}
    value, _ = returning_page(service)
    value.job = {**value.job, "id": job_id}
    value.form.resume_fields = lambda: []
    value.form.upload_verified = False
    value.emit = lambda *args: None
    assert value.employer_holds_resume(obs) is holds
    value.adopt_employer_resume(obs)
    assert bool(getattr(value.form, "resume_on_file", False)) is holds


@pytest.mark.parametrize("portal,holds", [("careers-acme.icims.com", True), ("careers-other.icims.com", False)])
def test_the_profile_resume_belongs_to_the_employer_portal_not_one_job(service, portal, holds):
    """Quest, runs 4f9d3ea8296d4596a7696a9a7b09664a and 8beb39ca8d3b49b5b4b181e47774dfc8, 2026-10-21: one job was
    confirmed with the profile and resume; the next job opened at self-identification and its final submit was
    refused for want of a resume. Another employer's profile proves nothing."""
    from jobpilot.db import Event, Job, Run

    with service.db.exclusive() as s:
        earlier = Job(identity="first-" + portal, url=f"https://{portal}/jobs/7/analyst/job", company="Acme", title="A")
        current = Job(identity="next-" + portal, url="https://careers-acme.icims.com/jobs/42/engineer/job",
                      company="Acme", title="Engineer")
        s.add_all([earlier, current])
        s.flush()
        run = Run(job_id=earlier.id, state="confirmed", mode="submit", packet={})
        s.add(run)
        s.flush()
        s.add(Event(run_id=run.id, kind="form_post", message="post", data={
            "path": "/jobs/7/analyst/candidate", "status": 302,
            "shape": [["PortalProfileFields.Resume_File", "file:pdf:111452"]]}))
        current_id = current.id
    url = "https://careers-acme.icims.com/jobs/42/engineer/form?form=Voluntary_Self_Identification_Forms&isPacket=1"
    obs = {"url": url, "fields": [{"id": "g", "type": "select", "label": "Gender", "frame_url": url}], "controls": []}
    value, _ = returning_page(service)
    value.job = {**value.job, "id": current_id}
    value.form.resume_fields = lambda: []
    assert value.employer_holds_resume(obs) is holds


@pytest.mark.parametrize("minutes_ago,route", [(1, "return"), (20, "apply")])
def test_an_unanswered_central_reset_sends_the_next_run_through_apply(service, minutes_ago, route):
    """Emory, runs ecea351ee9f94c0490faa797f55142e2 .. dd5c6ee4c331494384df9d92baf7db0b, 2026-10-21: Central showed the
    password step and "Check Your Email", no reset email came, and every run took the returning route and reset
    again. A reset still in flight keeps the returning route; an unanswered one lets the portal decide."""
    import time

    value, obs = returning_page(service)
    value.accounts.mark(value.job["url"], "reset_requested", None, reset_requested_at=time.time() - minutes_ago * 60)
    assert value.find_apply(obs)["id"] == route
