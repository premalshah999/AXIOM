"""SuccessFactors failure logs (2026-10-16): after Apply the navigator left the employer's handoff, sign-in
or application page with back/return_to_job and repeated Apply until the run stalled. Those pages are held;
the job search and error pages are not."""

from types import SimpleNamespace

from jobpilot.browser import BrowserEngine


def held(url, fields=(), text=""):
    engine = SimpleNamespace(page=SimpleNamespace(url=url))
    obs = {"fields": [{"label": label} for label in fields], "text": text}
    return BrowserEngine.successfactors_application_step(engine, obs)


def test_application_and_sign_in_steps_are_held():
    assert held("https://career4.successfactors.com/careers?company=Momentive")
    assert held(
        "https://career5.successfactors.eu/career?company=capgemitecP3&loginFlowRequired=true",
        ["Valid Email"],
    )
    assert held("https://career4.successfactors.com/portalcareer?company=ametekinc", ["* First Name"])


def test_job_search_error_pages_and_other_sites_are_not_held():
    assert not held(
        "https://career4.successfactors.com/career?company=ametekinc", ["Keywords", "Exact Match"]
    )
    assert not held(
        "https://career4.successfactors.com/career?company=x",
        text="An error occurred while processing your request.",
    )
    assert not held("https://jobs.ametek.com/job/Mount-Prospect-Software-Engineer-IL-60056/1426573500")


def test_candidate_profile_after_sign_in_is_not_held():
    # Bunge: after sign-in SuccessFactors showed the candidate profile; the posting's Apply was the way on.
    engine = SimpleNamespace(
        page=SimpleNamespace(url="https://career5.successfactors.eu/portalcareer?company=Bunge")
    )
    obs = {
        "fields": [{"label": "Language"}],
        "text": "",
        "controls": [
            {"label": "Profile Information"},
            {"label": "My Documents"},
            {"label": "Jobs Applied (1)"},
        ],
    }
    assert not BrowserEngine.successfactors_application_step(engine, obs)
