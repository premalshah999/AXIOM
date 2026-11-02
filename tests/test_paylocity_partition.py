"""Paylocity's recruiting portal is one host for every employer: its jobs are different employers' for busy, cooldown and circuits."""

from jobpilot.admission import employer_partition


def job(company, n):
    return {"url": f"https://recruiting.paylocity.com/Recruiting/Jobs/Details/{n}", "ats": "paylocity", "company": company}


def test_different_employers_on_the_portal_do_not_share_a_partition():
    assert employer_partition(job("Alpha Robotics", 1)) != employer_partition(job("Beta Labs", 2))


def test_the_same_employer_shares_it_whatever_the_posting_or_spelling():
    assert employer_partition(job("Alpha Robotics", 1)) == employer_partition(job(" alpha   robotics ", 2))


def test_a_company_less_job_keeps_the_hosts_partition():
    assert employer_partition(job("", 3)) == "recruiting.paylocity.com"


def test_an_ordinary_employer_site_is_still_its_host():
    site = {"url": "https://careers.example.test/jobs/1", "ats": "custom", "company": "Alpha Robotics"}
    assert employer_partition(site) == "careers.example.test"
