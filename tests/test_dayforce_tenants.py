"""Every Dayforce employer is served from jobs.dayforcehcm.com. The host alone ("domain:dayforcehcm.com")
had merged York Space, NAVEX, mPulse and others into Sarnova: its 13 old runs filled the employer cap for
all of them, and every Dayforce application shared one pacing partition (2026-10-16)."""

from jobpilot.admission import employer_partition
from jobpilot.features.knowledge.employers import tenant_alias

YORK = "https://jobs.dayforcehcm.com/en-US/yss/candidateportal/jobs/4268"
MPULSE = "https://jobs.dayforcehcm.com/mpulse/candidateportal/jobs/338"


def test_each_dayforce_namespace_is_its_own_employer_tenant():
    assert tenant_alias(YORK) == "dayforce:yss"
    assert tenant_alias(MPULSE) == "dayforce:mpulse"
    assert tenant_alias("https://us261.dayforcehcm.com/CandidatePortal/en-US/yss") is None


def test_each_dayforce_namespace_is_its_own_pacing_partition():
    york = employer_partition({"ats": "dayforce", "url": YORK})
    mpulse = employer_partition({"ats": "dayforce", "url": MPULSE})
    assert york == "jobs.dayforcehcm.com/yss" and mpulse == "jobs.dayforcehcm.com/mpulse"
