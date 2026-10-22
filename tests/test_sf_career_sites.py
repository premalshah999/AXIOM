"""SuccessFactors Career Site Builder on employer domains (2026-10-15): careers.ey.com, jobs.cargill.com
and similar hosts were saved as custom career sites, so neither the SuccessFactors handling nor the
owner's ATS focus applied to their jobs."""

import re

from sqlalchemy import select

from jobpilot.ats import canonicalize, detect
from jobpilot.db import Job


def test_career_site_builder_hosts_are_successfactors():
    for url in (
        "https://careers.ey.com/ey/job/Dallas-SDC-Back-End-Engineer-TX-75270/1442523233/",
        "https://jobs.cargill.com/job/Atlanta-Cloud-Platform-Engineer-Geor-30340/1373146857/",
        "https://careers.capgemini.com/job/Dallas-MLOps-Engineer-TX/1234567/",
    ):
        assert detect(url).id == "successfactors"
    assert detect("https://www.ey.com/en_us/careers").id == "custom"


def test_saved_custom_jobs_take_the_recognized_ats(service):
    url = "https://careers.capgemini.com/job/Dallas-MLOps-Engineer-TX/1234567/"
    with service.db.session() as s:
        s.add(Job(url=url, identity=canonicalize(url)[1], ats="custom", title="MLOps Engineer"))
        s.add(Job(url="https://example.org/jobs/1", identity="example:1", ats="custom", title="Analyst"))
    service.relabel_custom_ats()
    with service.db.session() as s:
        labels = {job.url: job.ats for job in s.scalars(select(Job))}
    assert labels[url] == "successfactors"
    assert labels["https://example.org/jobs/1"] == "custom"


def test_an_apply_control_with_a_trailing_arrow_opens_the_application():
    """XPO (jobs.xpo.com, 2026-10-21): "Apply now »" was read as no way forward and the guided agent took three runs."""
    from types import SimpleNamespace

    from jobpilot.adapters.base import Adapter

    controls = [
        {"id": "search", "label": "Search Jobs", "aria_label": "", "href": "/search/", "automation_id": ""},
        {"id": "apply", "label": "Apply now »", "aria_label": "", "href": "/talentcommunity/apply/1419245900/",
         "automation_id": ""},
    ]
    assert Adapter.find_apply(SimpleNamespace(apply_ids=()), {"controls": controls})["id"] == "apply"
    controls[1]["label"] = "Apply nowhere"
    assert Adapter.find_apply(SimpleNamespace(apply_ids=()), {"controls": controls}) is None


def test_a_job_page_with_only_its_job_search_is_a_job_page():
    """XPO (2026-10-21): "Search by Postal Code" and a "Distance" picker made the posting a form with no way forward."""
    from types import SimpleNamespace

    from jobpilot.adapters.base import Adapter, Step

    obs = {
        "url": "https://jobs.xpo.com/job/Boston-Machine-Learning-Engineer-Hybrid-MA-02110/1422080400",
        "text": "Machine Learning Engineer", "headings": [], "errors": [],
        "fields": [{"id": "z", "type": "text", "label": "Search by Postal Code", "required": False},
                   {"id": "d", "type": "select", "label": "Distance", "required": False}],
        "controls": [{"id": "apply", "label": "Apply now »", "aria_label": "", "href": "/talentcommunity/apply/1/",
                      "automation_id": ""}],
    }
    value = Adapter.__new__(Adapter)
    value.apply_ids, value.next_ids, value.committed = (), (), False
    value.final = re.compile(r"^submit$", re.I)
    assert Adapter.classify_page(value, obs) == Step.JOB
    obs["fields"][1]["label"] = "Distance you are willing to commute"
    assert Adapter.classify_page(value, obs) == Step.FORM
